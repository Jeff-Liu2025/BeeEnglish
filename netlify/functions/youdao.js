// ============================================================
// Netlify Function：/api/youdao 查词代理
// 由 netlify.toml 把 /api/youdao 路由到本函数（rewrite 保留 POST）。
// 逻辑与本地 scripts/server.py 保持一致：
//   1) 优先有道词典网页版公开 jsonapi（免 key，音标/中英文释义/双语例句/真人发音）
//   2) 未收录时走有道付费接口兜底（需环境变量或请求头提供凭据）：
//      词典 API v2/dict →（未开通自动降级）文本翻译 API /api
// 凭据优先级：Netlify 环境变量 YOUDAO_APP_KEY / YOUDAO_APP_SECRET > 请求头
// ============================================================
const crypto = require('crypto');

const YD_WEB_URL = 'https://dict.youdao.com/jsonapi';
const YD_DICT_URL = 'https://openapi.youdao.com/v2/dict';
const YD_TRANS_URL = 'https://openapi.youdao.com/api';
const FALLBACK_CODES = new Set(['104', '110', '301', '302', '390001']);
const UPSTREAM_TIMEOUT = 10000;

// 尽力而为的限流（每个函数实例独立计数）：60 秒内最多 120 次，防连点刷量
const _recent = [];
const RATE_WINDOW = 60000, RATE_MAX = 120;
// 词典 API 未开通时，本实例后续直接走翻译，省一次无效请求
let _dictUnavailable = false;

function asList(v) { return v == null ? [] : (Array.isArray(v) ? v : [v]); }

/* 递归收集 ec.trs[*].tr[*].l.i（字符串或 [词性, 释义] 数组） */
function collectI(obj, out) {
  if (Array.isArray(obj)) { obj.forEach(function (v) { collectI(v, out); }); return; }
  if (obj && typeof obj === 'object') {
    for (const k of Object.keys(obj)) {
      if (k === 'i') {
        let cands = [];
        if (typeof obj[k] === 'string') cands = [obj[k]];
        else if (Array.isArray(obj[k])) cands = [obj[k].filter(function (x) { return typeof x === 'string'; }).join(' ')];
        for (const c of cands) { const t = c.trim(); if (t && out.indexOf(t) < 0) out.push(t); }
      } else collectI(obj[k], out);
    }
  }
}

/* ---------- 有道 v3 签名：sign = sha256(appKey + input(q) + salt + curtime + appSecret) ---------- */
function inputOf(q) { return q.length <= 20 ? q : q.slice(0, 10) + String(q.length) + q.slice(-10); }
function authParams(key, secret, q) {
  const salt = crypto.randomUUID();
  const curtime = String(Math.floor(Date.now() / 1000));
  const sign = crypto.createHash('sha256')
    .update(key + inputOf(q) + salt + curtime + secret, 'utf8').digest('hex');
  return { appKey: key, salt: salt, curtime: curtime, signType: 'v3', sign: sign };
}

async function postForm(url, fields) {
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, UPSTREAM_TIMEOUT);
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(fields).toString(),
      signal: ctrl.signal
    });
    return await r.json();
  } finally { clearTimeout(timer); }
}

async function getJson(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(function () { ctrl.abort(); }, UPSTREAM_TIMEOUT);
  try {
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' }, signal: ctrl.signal });
    return await r.json();
  } finally { clearTimeout(timer); }
}

/* ---------- 归一化：网页版富词条 ---------- */
function normWeb(j, q) {
  const ec = (j && j.ec && typeof j.ec === 'object') ? j.ec : null;
  const wordArr = (ec && ec.word) || [];
  const w0 = (wordArr[0] && typeof wordArr[0] === 'object') ? wordArr[0] : {};

  const explains = [];
  if (w0.trs) collectI(w0.trs, explains);

  // 英文释义（WordNet）：每个词性取第一条，最多 2 条
  const enDefs = [];
  const ee = (j && j.ee && typeof j.ee === 'object') ? j.ee : null;
  const eeWord = (ee && ee.word) || {};
  for (const block of asList(eeWord.trs)) {
    if (!block || typeof block !== 'object') continue;
    for (const tr of asList(block.tr)) {
      const l = tr && tr.l;
      let s = l ? l.i : null;
      if (Array.isArray(s)) s = s.filter(function (x) { return typeof x === 'string'; }).join(' ');
      if (typeof s === 'string' && s.trim()) {
        enDefs.push((block.pos ? block.pos + ' ' : '') + s.trim());
        break;
      }
    }
    if (enDefs.length >= 2) break;
  }

  // 双语例句：最多 2 条
  const examples = [];
  const blng = (j && j.blng_sents_part && typeof j.blng_sents_part === 'object') ? j.blng_sents_part : null;
  for (const pair of asList(blng && blng['sentence-pair'])) {
    if (!pair || typeof pair !== 'object') continue;
    const en = (pair.sentence || '').trim();
    const zh = (pair['sentence-translation'] || '').trim();
    if (en) examples.push({ en: en, zh: zh });
    if (examples.length >= 2) break;
  }

  let real = '';
  if (w0['return-phrase'] && w0['return-phrase'].l && typeof w0['return-phrase'].l.i === 'string')
    real = w0['return-phrase'].l.i.trim();
  if (!explains.length && !enDefs.length) return null;

  function voice(v) {
    v = (v || '').trim();
    if (!v) return '';
    if (v.indexOf('http') === 0) return v;
    return 'https://dict.youdao.com/dictvoice?audio=' + v; // 形如 "word&type=2"
  }

  return {
    ok: true, source: 'web', word: real || q,
    phonetic: '',
    ukPhonetic: w0.ukphone || '', usPhonetic: w0.usphone || '',
    usSpeech: voice(w0.usspeech), ukSpeech: voice(w0.ukspeech),
    explains: explains.slice(0, 6), enDefs: enDefs,
    zh: explains[0] || '', examples: examples, errorCode: '0'
  };
}

/* ---------- 归一化：付费文本翻译 API 兜底 ---------- */
function normTranslate(j, q) {
  const basic = (j && j.basic && typeof j.basic === 'object') ? j.basic : {};
  const explains = asList(basic.explains).filter(function (s) { return typeof s === 'string' && s.trim(); })
    .map(function (s) { return s.trim(); });
  const trans = asList(j && j.translation).filter(function (s) { return typeof s === 'string' && s.trim(); })
    .map(function (s) { return s.trim(); });
  return {
    ok: true, source: 'trans', word: q,
    phonetic: basic.phonetic || '',
    ukPhonetic: basic['uk-phonetic'] || basic.ukPhonetic || '',
    usPhonetic: basic['us-phonetic'] || basic.usPhonetic || '',
    explains: explains, enDefs: [],
    zh: explains[0] || trans[0] || '', examples: [], errorCode: '0'
  };
}

/* ---------- 主流程 ---------- */
async function query(q, key, secret) {
  // 1) 网页版富词典（免 key）
  try {
    const jw = await getJson(YD_WEB_URL + '?q=' + encodeURIComponent(q));
    const data = normWeb(jw, q);
    if (data) { console.log('[youdao] web  q=%s explains=%d en=%d eg=%d', q, data.explains.length, data.enDefs.length, data.examples.length); return data; }
    console.log('[youdao] web  q=%s 无词条，走兜底', q);
  } catch (e) { console.log('[youdao] web  q=%s error=%s', q, e && e.message); }

  // 2) 付费接口兜底（需要凭据）
  function doTranslate(fallbackCode) {
    if (!key || !secret) return Promise.resolve({ ok: false, errorCode: 'NO_KEY', source: 'web' });
    const fields = Object.assign({ q: q, from: 'en', to: 'zh-CHS' }, authParams(key, secret, q));
    return postForm(YD_TRANS_URL, fields).then(function (j2) {
      const code2 = String((j2 && j2.errorCode) || '0');
      console.log('[youdao] trans q=%s errorCode=%s', q, code2);
      if (code2 === '0') {
        const d = normTranslate(j2, q);
        if (d.explains.length || d.zh) return d;
      }
      return { ok: false, errorCode: code2, source: 'trans' };
    }).catch(function (e) {
      console.log('[youdao] trans error:', e && e.message);
      return { ok: false, errorCode: fallbackCode, source: 'trans' };
    });
  }

  if (_dictUnavailable) return doTranslate('110');
  if (!key || !secret) return doTranslate('NO_KEY');

  let j;
  try {
    const fields = Object.assign({ q: q, langType: 'en', dicts: 'ec' }, authParams(key, secret, q));
    j = await postForm(YD_DICT_URL, fields);
  } catch (e) {
    console.log('[youdao] dict error:', e && e.message);
    return doTranslate('UPSTREAM_ERROR');
  }
  const code = String((j && j.errorCode) || '0');
  console.log('[youdao] dict  q=%s errorCode=%s', q, code);

  if (code === '0') {
    // 词典 API 命中：解析 word[0].trs → l.i（结构与网页版 ec 相同）
    const result = (j && j.result) || {};
    const ec = result.ec || {};
    const wordArr = ec.word || [];
    const w0 = wordArr[0] || {};
    const explains = [];
    if (w0.trs) collectI(w0.trs, explains);
    if (explains.length) {
      return {
        ok: true, source: 'dict', word: q,
        phonetic: '', ukPhonetic: w0.ukphone || '', usPhonetic: w0.usphone || '',
        explains: explains.slice(0, 6), enDefs: [],
        zh: explains[0], examples: [], errorCode: '0'
      };
    }
  } else if (!FALLBACK_CODES.has(code)) {
    return { ok: false, errorCode: code, source: 'dict' };
  } else {
    _dictUnavailable = true;
    console.log('[youdao] 词典 API 不可用（%s），本实例后续直接走文本翻译', code);
  }
  return doTranslate(code);
}

/* ---------- Netlify Function 入口 ---------- */
const CORS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'no-store',
  'Access-Control-Allow-Origin': '*'
};
function resp(status, payload) {
  return { statusCode: status, headers: CORS, body: JSON.stringify(payload) };
}

exports.handler = async function (event) {
  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers: CORS, body: '' };

  let q = '';
  if (event.httpMethod === 'POST') {
    try { const b = JSON.parse(event.body || '{}'); q = String((b && b.q) || ''); } catch (e) { q = ''; }
  } else {
    const ps = event.queryStringParameters || {};
    q = String(ps.q || '');
  }
  q = q.trim();
  if (!q || q.length > 64) return resp(400, { ok: false, errorCode: 'BAD_Q' });

  const now = Date.now();
  while (_recent.length && now - _recent[0] > RATE_WINDOW) _recent.shift();
  if (_recent.length >= RATE_MAX) return resp(429, { ok: false, errorCode: 'RATE_LIMITED' });
  _recent.push(now);

  // 凭据：环境变量优先（公开部署的正确方式），请求头兜底（本地调试用）
  const h = event.headers || {};
  const key = process.env.YOUDAO_APP_KEY || h['x-yd-key'] || '';
  const secret = process.env.YOUDAO_APP_SECRET || h['x-yd-secret'] || '';

  try {
    const data = await query(q, key, secret);
    return resp(200, data);
  } catch (e) {
    console.log('[youdao] handler error:', e && e.message);
    return resp(200, { ok: false, errorCode: 'UPSTREAM_ERROR', source: 'web' });
  }
};
