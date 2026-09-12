# -*- coding: utf-8 -*-
"""
Reading Explorer 单元学习器 —— 本地一体化服务器（零第三方依赖）

作用：
  1. 托管项目根目录的静态文件（等价于 python -m http.server）
  2. 提供 /api/youdao 词典代理：用 appKey/appSecret 做 v3 签名后转发有道接口
     —— 浏览器直连有道会被 CORS 拦截，且 appSecret 不能暴露在页面里，
        所以统一由本机 (127.0.0.1) 代理完成签名与转发。

启动：
  python scripts/server.py [端口]      # 默认 8080
然后访问 http://localhost:8080/index.html

凭据来源（二选一）：
  - 页面「设置」里填写的有道 appKey / appSecret（请求头带过来）
  - 环境变量 YOUDAO_APP_KEY / YOUDAO_APP_SECRET
"""

import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PORT = 8080
UPSTREAM_TIMEOUT = 10  # 秒

YD_DICT_URL = 'https://openapi.youdao.com/v2/dict'   # 有道词典 API（需联系商务开通）
YD_TRANS_URL = 'https://openapi.youdao.com/api'      # 文本翻译 API（控制台可自助开通，兜底用）
YD_WEB_URL = 'https://dict.youdao.com/jsonapi'       # 有道词典网页版公开 JSON（免 key，富词条：音标/中英释义/双语例句）

# 词典服务未开通 / 不支持时，自动降级到翻译 API 的错误码
FALLBACK_CODES = {'104', '110', '301', '302', '390001'}

# 简单限流：60 秒内最多请求数（保护账户配额，防止孩子连点）
_recent = deque()
RATE_WINDOW = 60.0
RATE_MAX = 120

# 词典 API 未开通（110 等）时，本进程后续直接走翻译 API，省去每词一次的无效请求
_dict_unavailable = False


# ---------------- 有道 v3 签名 ----------------

def _input_of(q):
    q = q if isinstance(q, str) else str(q)
    if len(q) <= 20:
        return q
    return q[:10] + str(len(q)) + q[-10:]


def _sign(app_key, app_secret, q, salt, curtime):
    src = app_key + _input_of(q) + salt + curtime + app_secret
    return hashlib.sha256(src.encode('utf-8')).hexdigest()


def _auth_params(app_key, app_secret, q):
    salt = str(uuid.uuid4())
    curtime = str(int(time.time()))
    return {
        'appKey': app_key,
        'salt': salt,
        'curtime': curtime,
        'signType': 'v3',
        'sign': _sign(app_key, app_secret, q, salt, curtime),
    }


def _post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST')
    with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _get_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))


# ---------------- 返回结构归一化 ----------------

def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _collect_i(obj, out):
    """递归收集 ec.trs[*].tr[*].l.i 里的文本（有道 ec 词典的经典结构）。
    i 可能是字符串（"n. 苹果"）或数组（["n.", "苹果"]），数组要合成一条。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'i':
                if isinstance(v, str):
                    candidates = [v]
                elif isinstance(v, list):
                    candidates = [' '.join(x for x in v if isinstance(x, str))]
                else:
                    candidates = []
                for item in candidates:
                    item = item.strip()
                    if item and item not in out:
                        out.append(item)
            else:
                _collect_i(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_i(v, out)


def _find_examples(obj, out, depth=0):
    """递归寻找例句（同时含英文句子与中文翻译的对象）。"""
    if depth > 8:
        return
    if isinstance(obj, dict):
        en = obj.get('sentence') or obj.get('sentenceBold') or obj.get('sentenceSample')
        zh = obj.get('translation')
        if isinstance(en, str) and en.strip():
            out.append({'en': en.strip(), 'zh': zh.strip() if isinstance(zh, str) else ''})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _find_examples(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _find_examples(v, out, depth + 1)


def _norm_ec(j, q):
    """归一化有道词典 API (dicts=ec) 的返回。"""
    result = j.get('result') or {}
    ec = result.get('ec') or {}

    word_arr = ec.get('word') or []
    w0 = word_arr[0] if word_arr and isinstance(word_arr[0], dict) else {}
    basic = ec.get('basic') if isinstance(ec.get('basic'), dict) else {}

    explains = []
    # 路径 A：word[0].trs → l.i
    if w0.get('trs'):
        _collect_i(w0['trs'], explains)
    # 路径 B：basic.explains / basic.explain（文档表格给出的字段）
    if not explains:
        for key in ('explains', 'explain'):
            for item in _as_list(basic.get(key)):
                if isinstance(item, str) and item.strip():
                    explains.append(item.strip())

    examples = []
    _find_examples(w0, examples)
    examples = examples[:3]

    def pick(*keys):
        for src in (w0, basic):
            for k in keys:
                v = src.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return ''

    return {
        'ok': True,
        'source': 'dict',
        'word': q,
        'phonetic': pick('phonetic', 'phone'),
        'ukPhonetic': pick('ukPhonetic', 'ukphone'),
        'usPhonetic': pick('usPhonetic', 'usphone'),
        'explains': explains,
        'enDefs': [],
        'zh': explains[0] if explains else '',
        'examples': examples,
        'errorCode': str(j.get('errorCode', '0')),
    }


def _norm_translate(j, q):
    """归一化有道文本翻译 API /api 的返回（兜底）。"""
    basic = j.get('basic') if isinstance(j.get('basic'), dict) else {}
    explains = [s.strip() for s in _as_list(basic.get('explains'))
                if isinstance(s, str) and s.strip()]
    trans = [s.strip() for s in _as_list(j.get('translation'))
             if isinstance(s, str) and s.strip()]
    return {
        'ok': True,
        'source': 'trans',
        'word': q,
        'phonetic': basic.get('phonetic', '') or '',
        'ukPhonetic': basic.get('uk-phonetic', '') or basic.get('ukPhonetic', '') or '',
        'usPhonetic': basic.get('us-phonetic', '') or basic.get('usPhonetic', '') or '',
        'explains': explains,
        'enDefs': [],
        'zh': (explains[0] if explains else (trans[0] if trans else '')),
        'examples': [],
        'errorCode': '0',
    }


def _norm_web(j, q):
    """归一化有道词典网页版 jsonapi 的返回（免 key 的富词条）。
    取：ec（音标+中文释义）、ee/WordNet（英文释义）、blng_sents_part（双语例句）。"""
    ec = j.get('ec') if isinstance(j.get('ec'), dict) else None
    word_arr = (ec or {}).get('word') or []
    w0 = word_arr[0] if word_arr and isinstance(word_arr[0], dict) else {}

    explains = []
    if w0.get('trs'):
        _collect_i(w0['trs'], explains)
    explains = [s for s in explains if s and not s.isspace()]

    # 英文释义（WordNet）：每个词性取第一条，最多 2 条
    en_defs = []
    ee = j.get('ee') if isinstance(j.get('ee'), dict) else None
    ee_word = (ee or {}).get('word') or {}
    for block in _as_list(ee_word.get('trs')):
        if not isinstance(block, dict):
            continue
        trs = _as_list(block.get('tr'))
        for tr in trs:
            l = (tr or {}).get('l') if isinstance(tr, dict) else None
            s = l.get('i') if isinstance(l, dict) else None
            if isinstance(s, list):
                s = ' '.join(x for x in s if isinstance(x, str))
            if isinstance(s, str) and s.strip():
                pos = block.get('pos') or ''
                en_defs.append((pos + ' ' + s.strip()) if pos else s.strip())
                break
        if len(en_defs) >= 2:
            break

    # 双语例句：取纯文本句（sentence）与中文翻译，最多 2 条
    examples = []
    blng = j.get('blng_sents_part') if isinstance(j.get('blng_sents_part'), dict) else None
    for pair in _as_list((blng or {}).get('sentence-pair')):
        if not isinstance(pair, dict):
            continue
        en_s = (pair.get('sentence') or '').strip()
        zh_s = (pair.get('sentence-translation') or '').strip()
        if en_s:
            examples.append({'en': en_s, 'zh': zh_s})
        if len(examples) >= 2:
            break

    rp = w0.get('return-phrase')
    real_word = ''
    if isinstance(rp, dict) and isinstance(rp.get('l'), dict) and isinstance(rp['l'].get('i'), str):
        real_word = rp['l']['i'].strip()

    if not explains and not en_defs:
        return None

    def voice(v):
        v = (v or '').strip()
        if not v:
            return ''
        if v.startswith('http'):
            return v
        # 字段形如 "cucumber&type=2"，拼出真人发音 MP3（type=2 美式 / type=1 英式）
        return 'https://dict.youdao.com/dictvoice?audio=' + v

    return {
        'ok': True,
        'source': 'web',
        'word': real_word or q,
        'phonetic': '',
        'ukPhonetic': w0.get('ukphone', '') or '',
        'usPhonetic': w0.get('usphone', '') or '',
        'usSpeech': voice(w0.get('usspeech')),
        'ukSpeech': voice(w0.get('ukspeech')),
        'explains': explains[:6],
        'enDefs': en_defs,
        'zh': explains[0] if explains else '',
        'examples': examples,
        'errorCode': '0',
    }


def query_youdao(q, app_key, app_secret, dicts='ec'):
    """主流程：网页版富词典（免 key）→ 付费词典 API → 文本翻译兜底。返回 (payload, http_status)。"""
    global _dict_unavailable

    # 1) 优先：网页版公开 jsonapi，含音标/中英释义/双语例句，无需 key
    try:
        jw = _get_json(YD_WEB_URL + '?q=' + urllib.parse.quote(q))
        data = _norm_web(jw, q)
        if data:
            print('[youdao] web   q=%-20s explains=%d en=%d eg=%d'
                  % (q, len(data['explains']), len(data['enDefs']), len(data['examples'])))
            return data, 200
        print('[youdao] web   q=%-20s 无词条，走兜底' % q)
    except Exception as e:
        print('[youdao] web   q=%-20s error=%s' % (q, e))

    # 2) 兜底：付费接口（需 appKey/appSecret）
    def do_translate(fallback_code):
        if not app_key or not app_secret:
            return {'ok': False, 'errorCode': 'NO_KEY', 'source': 'web'}, 200
        # 通用文本翻译 API（同一套 appKey/secret + v3 签名）
        fields2 = {'q': q, 'from': 'en', 'to': 'zh-CHS'}
        fields2.update(_auth_params(app_key, app_secret, q))
        try:
            j2 = _post_form(YD_TRANS_URL, fields2)
            code2 = str(j2.get('errorCode', '0'))
            print('[youdao] trans q=%-20s errorCode=%s' % (q, code2))
            if code2 == '0':
                data = _norm_translate(j2, q)
                if data['explains'] or data['zh']:
                    return data, 200
            return {'ok': False, 'errorCode': code2, 'source': 'trans'}, 200
        except Exception as e:
            print('[youdao] trans error:', e)
            return {'ok': False, 'errorCode': fallback_code, 'source': 'trans'}, 200

    if _dict_unavailable:
        return do_translate('110')

    if not app_key or not app_secret:
        return do_translate('NO_KEY')

    lang = 'en'
    fields = {'q': q, 'langType': lang, 'dicts': dicts}
    fields.update(_auth_params(app_key, app_secret, q))

    try:
        j = _post_form(YD_DICT_URL, fields)
    except Exception as e:
        print('[youdao] dict error:', e)
        return do_translate('UPSTREAM_ERROR')

    code = str(j.get('errorCode', '0'))
    print('[youdao] dict  q=%-20s errorCode=%s' % (q, code))

    if code == '0':
        data = _norm_ec(j, q)
        if data['explains'] or data['zh']:
            return data, 200
        # code=0 但没内容（未收录），也尝试一次翻译兜底
    elif code not in FALLBACK_CODES:
        return {'ok': False, 'errorCode': code, 'source': 'dict'}, 200
    else:
        _dict_unavailable = True  # 本进程后续直接走翻译
        print('[youdao] 词典 API 不可用（%s），本会话后续直接走文本翻译' % code)

    return do_translate(code)


# ---------------- HTTP 服务 ----------------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        # HTML 入口页禁止缓存，避免更新后用户仍跑旧页面（甚至命中别的项目残留缓存）
        if self.path.split('?', 1)[0].endswith(('.html', '/')) or self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # 静态文件请求不刷屏；词典请求单独打印

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_api(self, q, app_key, app_secret):
        q = (q or '').strip()
        if not q or len(q) > 64:
            self._json({'ok': False, 'errorCode': 'BAD_Q'}, 400)
            return
        # appKey/appSecret 可为空：网页版富词典免 key，仅兜底翻译需要凭据

        now = time.time()
        while _recent and now - _recent[0] > RATE_WINDOW:
            _recent.popleft()
        if len(_recent) >= RATE_MAX:
            self._json({'ok': False, 'errorCode': 'RATE_LIMITED'}, 429)
            return
        _recent.append(now)

        try:
            payload, status = query_youdao(q, app_key, app_secret)
            self._json(payload, status)
        except urllib.error.HTTPError as e:
            print('[youdao] HTTPError', e.code)
            self._json({'ok': False, 'errorCode': 'HTTP_%s' % e.code}, 502)
        except Exception as e:
            print('[youdao] error:', type(e).__name__, e)
            self._json({'ok': False, 'errorCode': 'UPSTREAM_ERROR'}, 502)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/youdao':
            qs = urllib.parse.parse_qs(parsed.query)
            q = (qs.get('q') or [''])[0]
            self._handle_api(q,
                             self.headers.get('X-YD-Key') or os.environ.get('YOUDAO_APP_KEY', ''),
                             self.headers.get('X-YD-Secret') or os.environ.get('YOUDAO_APP_SECRET', ''))
            return
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != '/api/youdao':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length).decode('utf-8') or '{}') if length else {}
            q = body.get('q', '') if isinstance(body, dict) else ''
        except Exception:
            self._json({'ok': False, 'errorCode': 'BAD_BODY'}, 400)
            return
        self._handle_api(q,
                         self.headers.get('X-YD-Key') or os.environ.get('YOUDAO_APP_KEY', ''),
                         self.headers.get('X-YD-Secret') or os.environ.get('YOUDAO_APP_SECRET', ''))


class _HTTPServerV6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    os.chdir(ROOT)

    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    servers = [httpd]
    # 同时监听 IPv6 回环 ::1 —— 否则 localhost 先解析到 ::1 会空等约 2 秒再回退 IPv4
    try:
        httpd6 = _HTTPServerV6(('::1', port), Handler)
        servers.append(httpd6)
    except OSError:
        httpd6 = None

    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    print('Reading Explorer 学习器已启动：  http://localhost:%d/index.html' % port)
    print('（词典代理 /api/youdao 已就绪；按 Ctrl+C 停止）')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')


if __name__ == '__main__':
    main()
