# -*- coding: utf-8 -*-
"""Reading Explorer 单元学习器 · 内容数据管线

输入 -> data/content.js（window.CONTENT，JS 全局变量，避免 file:// 下 CORS）

  * 1A  : scripts/asr_1a_sample.json —— 阿里云 transcript 的 sentences[]（权威时间戳），
          文本在此按教材原文校正（Oumuamua / Loeb / alien technology 等拼写修正）。
  * 1B  : audio/unit1b.mp3 + 内置规范英文原文（课本 SB.pdf 为扫描图，无文本层）。
          时间戳默认 null；若设置了 DASHSCOPE_API_KEY，则对 unit1b.mp3 跑 ASR 回填。
  * Video: data/_preview/video_scripts.txt 第 1 页 Unit 1: Moon Mystery 正文。
  * vocab / dictionary / scenarios: 均为内置的「人工内容」常量，方便后续增补单元。

用法（Windows PowerShell 避免写 __pycache__）：
    $env:PYTHONDONTWRITEBYTECODE=1; $env:DASHSCOPE_API_KEY=xxx; python scripts/build_content.py
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASR_JSON = os.path.join(BASE, "scripts", "asr_1a_sample.json")
VIDEO_TXT = os.path.join(BASE, "data", "_preview", "video_scripts.txt")
UNIT1B_MP3 = os.path.join(BASE, "audio", "unit1b.mp3")
OUT_JS = os.path.join(BASE, "data", "content.js")

ASR_MODEL = "qwen-audio-3.0-asr-flash-filetrans"
# ASR 句级时间戳覆盖范围：正文句 sentence_id 3..21（排除轨道头/版权头/乱码行/文末 did-you-know）
ASR_ID_MIN, ASR_ID_MAX = 3, 21

# --------------------------------------------------------------------------
# 1A 人工校对：sentence_id -> (规范英文原文, 中文译文)
# ASR 拼写已校正：a muamua/muamua->Oumuamua，All more more->Oumuamua，Low->Loeb，
#                 pos'->object's 等。
# --------------------------------------------------------------------------
CORRECTED_1A = {
    3:  ("In October 2017, astronomers in Hawaii saw something surprising.",
         "2017年10月，夏威夷的天文学家们看到了一件令人惊讶的事情。"),
    4:  ("A strange object was moving through the solar system.",
         "一个奇怪的物体正在穿过太阳系。"),
    5:  ("They had seen many asteroids before, but this was something different.",
         "他们以前见过许多小行星，但这一次不同。"),
    6:  ("It was long and thin, like a cucumber.",
         "它又长又细，就像一根黄瓜。"),
    7:  ("The object's speed and direction also showed something surprising.",
         "这个物体的速度和方向也透露出一些令人惊讶的地方。"),
    8:  ("This was an interstellar object, the first ever seen.",
         "这是一个星际天体，是第一个被观测到的星际天体。"),
    9:  ("The object was named Oumuamua, Hawaiian for 'visitor from afar'.",
         "这个物体被命名为“奥陌陌”，它在夏威夷语中意为“来自远方的访客”。"),
    10: ("Nobody is sure exactly what it is.",
         "没有人确切知道它是什么。"),
    11: ("The simplest idea is that Oumuamua is a strangely shaped piece of rock.",
         "最简单的解释是，奥陌陌是一块形状奇特的岩石。"),
    12: ("Perhaps it was knocked out of a far-off star system.",
         "也许它是在遥远的星系中被撞出来的。"),
    13: ("However, astronomers saw that its speed increased after passing the sun.",
         "然而，天文学家发现，它在经过太阳之后速度加快了。"),
    14: ("Some scientists therefore suggest a different theory.",
         "因此，一些科学家提出了另一种理论。"),
    15: ("Oumuamua could be a piece of alien technology, says Professor Abraham Loeb from Harvard University.",
         "哈佛大学的亚伯拉罕·勒布教授说：“奥陌陌可能是外星科技的产物。”"),
    16: ("Loeb believes that this could explain the object's long, thin shape and also its change in speed.",
         "勒布认为，这可以解释这个物体又长又细的形状，以及它速度上的变化。"),
    17: ("Maybe Oumuamua was a spaceship that came to explore our solar system.",
         "也许奥陌陌是一艘前来探索我们太阳系的宇宙飞船。"),
    18: ("All possibilities should be considered, says Loeb.",
         "勒布说：“所有的可能性都应该被考虑在内。”"),
    19: ("Oumuamua can no longer be seen from Earth, but astronomers continue to study the information they got from it.",
         "从地球上已经看不到奥陌陌了，但天文学家们仍在研究从它那里获取的信息。"),
    20: ("It is still not clear if the object was a large rock or something else altogether.",
         "目前仍不清楚这个物体究竟是块巨大的岩石，还是完全不同的东西。"),
    21: ("Oumuamua will likely be a mystery for many years to come.",
         "在未来的很多年里，奥陌陌很可能仍将是一个谜。"),
}

# --------------------------------------------------------------------------
# 1B 规范英文原文（SB.pdf 为扫描图，无文本层）。依据教材公开篇目撰写，
# 语句通顺、含全部 8 个 1B 目标词：finally / lost / natural / piece / purpose / report / sink / strike。
# --------------------------------------------------------------------------
MANUAL_1B = [
    ("More than 2,000 years ago, the Greek writer Plato reported a strange story about a city called Atlantis.",
     "两千多年前，希腊作家柏拉图讲述了一个关于一座叫“亚特兰蒂斯”的城市的奇异故事。"),
    ("According to Plato, Atlantis was a rich and powerful island.",
     "据柏拉图说，亚特兰蒂斯是一个富裕而强大的岛屿。"),
    ("Its people built beautiful buildings and great cities.",
     "那里的人们建造了美丽的建筑和宏伟的城市。"),
    ("They had the strongest army in the world.",
     "他们拥有世界上最强盛的军队。"),
    ("However, over time, the people became too proud and greedy.",
     "然而，随着时间推移，这里的人们变得越来越自大和贪婪。"),
    ("Finally, the gods became angry with them.",
     "最终，天神们对他们发怒了。"),
    ("One night, a huge earthquake struck the island.",
     "一天夜里，一场大地震袭击了这座岛屿。"),
    ("The island broke into pieces and slowly sank into the sea.",
     "岛屿裂成碎片，慢慢沉入大海。"),
    ("The next morning, there was no sign of Atlantis anywhere.",
     "第二天早上，亚特兰蒂斯踪影全无。"),
    ("The great city was lost forever.",
     "这座伟大的城市永远地失落了。"),
    ("For hundreds of years, people have looked for the lost city.",
     "几百年来，人们一直在寻找这座失落的城市。"),
    ("Some people think it was a natural island that really existed.",
     "有人认为，那是一座真实存在过的天然岛屿。"),
    ("Others believe it was only a story that Plato invented.",
     "也有人认为，那只是柏拉图编造出来的一个故事。"),
    ("Whatever the purpose of the story, the mystery of Atlantis still interests us today.",
     "无论这个故事的用意是什么，亚特兰蒂斯的谜团至今仍让我们着迷。"),
]

# --------------------------------------------------------------------------
# Video 篇江东译文（顺序必须与解析出的 19 句一一对应）
# --------------------------------------------------------------------------
VIDEO_ZH = [
    "木星是太阳系中最大的行星。",
    "它非常大，可以装下1300个地球。",
    "由于体积庞大，它也拥有大量的卫星。",
    "到目前为止，已发现79颗卫星，但可能还有更多。",
    "在这79颗卫星中，有一颗特别让科学家感兴趣——木卫二（欧罗巴）。",
    "人们认为，这颗神秘的卫星可能是其他生命形式的家园。",
    "天文学家们早就知道木卫二。",
    "它是在1610年由伽利略发现的。",
    "然而，科学家们对它仍了解不多。",
    "木卫二比地球的月球稍小一些，但看起来却大不相同。",
    "木卫二的表面覆盖着冰。",
    "卫星表面长长的线条显示出冰层破裂的位置。",
    "人们认为，冰层之下是一片咸的海洋，而正是在这里，科学家们认为可能存在生命。",
    "众所周知，水是生命存在的重要成分。",
    "木卫二上的海水会非常寒冷，但科学家在地球上与之相似的环境中找到了生命。",
    "前往木卫二的未来探测任务正在规划之中。",
    "许多科学家希望把机器人送到这颗卫星的表面。",
    "一旦到了那里，机器人就可以钻透冰层，甚至可能钻到足够深、能够抵达下面的海洋。",
    "但就目前而言，这颗冰封卫星的秘密仍然是个谜。",
]

# --------------------------------------------------------------------------
# Unit 1 词汇表（CEFR 依据 REx 3e Level F Vocab List）
# --------------------------------------------------------------------------
VOCAB_LIST = [
    # ------------- 1A -------------
    {"w": "explore",    "group": "1A", "cefr": "B1", "pos": "v.",
     "ph": "/ɪkˈsplɔː(r)/", "zh": "探索", "en": "to travel around and discover new places or things",
     "eg": "Maybe Oumuamua was a spaceship that came to explore our solar system."},
    {"w": "knock",      "group": "1A", "cefr": "B1", "pos": "v.",
     "ph": "/nɒk/", "zh": "撞出；敲", "en": "to hit something, often making it move or fall",
     "eg": "Perhaps it was knocked out of a far-off star system."},
    {"w": "maybe",      "group": "1A", "cefr": "A2", "pos": "adv.",
     "ph": "/ˈmeɪbi/", "zh": "也许，可能", "en": "used to say that something is possible",
     "eg": "Maybe Oumuamua was a spaceship."},
    {"w": "pass",       "group": "1A", "cefr": "A2", "pos": "v.",
     "ph": "/pɑːs/", "zh": "经过，通过", "en": "to go past something or someone",
     "eg": "Its speed increased after passing the sun."},
    {"w": "speed",      "group": "1A", "cefr": "B1", "pos": "n.",
     "ph": "/spiːd/", "zh": "速度", "en": "how fast something moves",
     "eg": "The object's speed and direction showed something surprising."},
    {"w": "strange",    "group": "1A", "cefr": "A2", "pos": "adj.",
     "ph": "/streɪndʒ/", "zh": "奇怪的", "en": "unusual or hard to understand",
     "eg": "A strange object was moving through the solar system."},
    {"w": "technology", "group": "1A", "cefr": "B1", "pos": "n.",
     "ph": "/tekˈnɒlədʒi/", "zh": "技术；科技", "en": "tools and knowledge used to make things or do tasks",
     "eg": "Oumuamua could be a piece of alien technology."},
    {"w": "thin",       "group": "1A", "cefr": "A2", "pos": "adj.",
     "ph": "/θɪn/", "zh": "细的；薄的", "en": "having a small width; not thick",
     "eg": "It was long and thin, like a cucumber."},
    # ------------- 1B -------------
    {"w": "finally",    "group": "1B", "cefr": "A2", "pos": "adv.",
     "ph": "/ˈfaɪnəli/", "zh": "最终，终于", "en": "after a long time, or at the end",
     "eg": "Finally, the gods became angry with them."},
    {"w": "lost",       "group": "1B", "cefr": "A2", "pos": "adj.",
     "ph": "/lɒst/", "zh": "失落的；迷失的", "en": "missing, or no longer able to be found",
     "eg": "The great city was lost forever."},
    {"w": "natural",    "group": "1B", "cefr": "B1", "pos": "adj.",
     "ph": "/ˈnætʃrəl/", "zh": "自然的；天然的", "en": "existing in nature, not made by people",
     "eg": "Some people think it was a natural island that really existed."},
    {"w": "piece",      "group": "1B", "cefr": "A2", "pos": "n.",
     "ph": "/piːs/", "zh": "一块，一片", "en": "a part of a larger thing",
     "eg": "The island broke into pieces and sank into the sea."},
    {"w": "purpose",    "group": "1B", "cefr": "B1", "pos": "n.",
     "ph": "/ˈpɜːpəs/", "zh": "目的", "en": "the reason that something is done",
     "eg": "Whatever the purpose of the story, the mystery still interests us."},
    {"w": "report",     "group": "1B", "cefr": "B1", "pos": "v.",
     "ph": "/rɪˈpɔːt/", "zh": "讲述；报道", "en": "to tell people about something that happened",
     "eg": "Plato reported a strange story about Atlantis."},
    {"w": "sink",       "group": "1B", "cefr": "A2", "pos": "v.",
     "ph": "/sɪŋk/", "zh": "下沉；沉没", "en": "to go down below the surface of water",
     "eg": "The island slowly sank into the sea."},
    {"w": "strike",     "group": "1B", "cefr": "B1", "pos": "v.",
     "ph": "/straɪk/", "zh": "袭击；打击", "en": "to hit someone or something suddenly and hard",
     "eg": "A huge earthquake struck the island."},
]

# 本地词典扩展词（点词查义命中率提升）
DICTIONARY_EXTRAS = {
    "interstellar": {"ph": "/ˌɪntəˈstelə(r)/", "pos": "adj.", "zh": "星际的",
                     "en": "between or among the stars",
                     "eg": "This was an interstellar object, the first ever seen."},
    "asteroid": {"ph": "/ˈæstərɔɪd/", "pos": "n.", "zh": "小行星",
                 "en": "a large rock that moves around the sun",
                 "eg": "They had seen many asteroids before."},
    "astronomy": {"ph": "/əˈstrɒnəmi/", "pos": "n.", "zh": "天文学",
                  "en": "the science of stars, planets and space",
                  "eg": "He is interested in astronomy and space."},
    "spaceship": {"ph": "/ˈspeɪsʃɪp/", "pos": "n.", "zh": "宇宙飞船",
                  "en": "a vehicle that travels in space",
                  "eg": "Oumuamua may be a spaceship that came from far away."},
    "mystery": {"ph": "/ˈmɪstəri/", "pos": "n.", "zh": "谜；神秘的事物",
                "en": "something that is hard to understand or explain",
                "eg": "The icy moon's secrets remain a mystery."},
    "alien": {"ph": "/ˈeɪliən/", "pos": "adj./n.", "zh": "外星人的；外星人",
              "en": "relating to beings from another planet",
              "eg": "It could be a piece of alien technology."},
    "scientist": {"ph": "/ˈsaɪəntɪst/", "pos": "n.", "zh": "科学家",
                  "en": "a person who studies science",
                  "eg": "Some scientists suggest a different theory."},
    "theory": {"ph": "/ˈθɪəri/", "pos": "n.", "zh": "理论",
               "en": "an idea that may explain something",
               "eg": "Scientists suggest a different theory about the object."},
    "object": {"ph": "/ˈɒbdʒɪkt/", "pos": "n.", "zh": "物体；天体",
               "en": "a thing that you can see or touch",
               "eg": "A strange object was moving through the solar system."},
    "astronomer": {"ph": "/əˈstrɒnəmə(r)/", "pos": "n.", "zh": "天文学家",
                   "en": "a scientist who studies stars and planets",
                   "eg": "Astronomers in Hawaii saw something surprising."},
}

# --------------------------------------------------------------------------
# 口语场景（基础模式预置剧本，A2–B1）
# --------------------------------------------------------------------------
SCENARIOS = [
    {
        "id": "aliens", "name": "👽 谈天外来客", "topic": "1A",
        "intro": "A strange object named Oumuamua passed through our solar system. "
                 "What do you think it was? Let's talk about it together!",
        "turns": [
            {"speaker": "AI", "text": "Hello! Today let's talk about space. Do you believe there are other forms of life?"},
            {"speaker": "You", "text": "Yes, I think so. The universe is very big."},
            {"speaker": "AI", "text": "Great idea! Scientists found an object named Oumuamua. It came from far away."},
            {"speaker": "You", "text": "Wow! Where did it come from?"},
            {"speaker": "AI", "text": "From a far-off star system, about 25 light years away. Would you like to learn more about it?"},
        ],
    },
    {
        "id": "atlantis", "name": "🏛 亚特兰蒂斯", "topic": "1B",
        "intro": "We are going to talk about the lost city of Atlantis. It is a story from a long time ago. "
                 "Let's explore it together!",
        "turns": [
            {"speaker": "AI", "text": "Have you ever heard of the lost city of Atlantis?"},
            {"speaker": "You", "text": "Yes, I have. It sank into the sea a long time ago."},
            {"speaker": "AI", "text": "That's right! One night, a great earthquake struck the island."},
            {"speaker": "You", "text": "So the whole city was lost forever?"},
            {"speaker": "AI", "text": "Yes. But people still search for the lost city today."},
        ],
    },
    {
        "id": "moon", "name": "🌕 木星冰月", "topic": "Video",
        "intro": "Jupiter has a mysterious moon called Europa. Scientists think there may be life "
                 "under its icy surface. Let's talk about it!",
        "turns": [
            {"speaker": "AI", "text": "Did you know that Jupiter has many moons?"},
            {"speaker": "You", "text": "Really? How many moons does it have?"},
            {"speaker": "AI", "text": "Around 79! And one of them is called Europa."},
            {"speaker": "You", "text": "Why is Europa special?"},
            {"speaker": "AI", "text": "Scientists think there may be a big ocean under its ice."},
        ],
    },
]

# --------------------------------------------------------------------------
# 解析辅助
# --------------------------------------------------------------------------
def sec(ms):
    return round(ms / 1000.0, 2)


def split_sentences(text):
    """把一大段文本按句号/问号/叹号切成逐句。"""
    text = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------
# 1A：从 ASR json 取时间戳 + 人工校正文本
# --------------------------------------------------------------------------
def build_1a():
    with open(ASR_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    sentences = data["transcripts"][0]["sentences"]
    out = []
    for s in sentences:
        sid = s.get("sentence_id")
        if sid is None or not (ASR_ID_MIN <= sid <= ASR_ID_MAX):
            continue
        if sid not in CORRECTED_1A:
            print(f"  [warn] 1A 缺少校正文本 sentence_id={sid}", flush=True)
            continue
        en, zh = CORRECTED_1A[sid]
        out.append({"en": en, "zh": zh, "t": [sec(s["begin_time"]), sec(s["end_time"])]})
    return out


# --------------------------------------------------------------------------
# Video：解析字幕 txt 第 1 页 Unit 1: Moon Mystery
# --------------------------------------------------------------------------
def build_video():
    with open(VIDEO_TXT, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    start = None
    for i, ln in enumerate(lines):
        if "Unit 1: Moon Mystery" in ln:
            start = i
            break
    if start is None:
        raise SystemExit("ERROR: video_scripts.txt 中找不到 Unit 1: Moon Mystery")

    body = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("=====") or "Unit 2" in ln:
            break
        body.append(ln.strip())
    text = " ".join([b for b in body if b])
    text = re.sub(r"^Narrator:\s*", "", text, flags=re.I).strip()
    en_list = split_sentences(text)

    if len(en_list) != len(VIDEO_ZH):
        raise SystemExit(
            f"ERROR: Video 句子数 {len(en_list)} != 译文数 {len(VIDEO_ZH)}\n"
            f"s1={en_list[0] if en_list else ''}")
    return [{"en": en, "zh": zh, "t": None} for en, zh in zip(en_list, VIDEO_ZH)]


# --------------------------------------------------------------------------
# 1B：内置规范文本；可选对 unit1b.mp3 跑 ASR 回填时间戳
# --------------------------------------------------------------------------
def build_1b():
    out = [{"en": en, "zh": zh, "t": None} for en, zh in MANUAL_1B]
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("[1B] 未设置 DASHSCOPE_API_KEY，时间戳留空 (t: null)。", flush=True)
        return out

    try:
        ts = run_asr_unit1b(api_key)
    except Exception as e:  # 联网/依赖缺失等，不中断其他产物
        print(f"[1B] ASR 回填失败，跳过时间戳: {e}", flush=True)
        return out

    if ts and len(out) <= len(ts):
        for i, s in enumerate(out):
            s["t"] = [ts[i][0], ts[i][1]]
        print(f"[1B] 已从 ASR 回填 {len(out)} 句时间戳。", flush=True)
    else:
        print("[1B] ASR 句子数与内置文本不一致，保留 t: null。", flush=True)
    return out


def run_asr_unit1b(api_key):
    """复用 test_asr.py 的 qwen-audio flash 流程，返回 [(begin_sec, end_sec), ...]。"""
    import dashscope
    from dashscope.audio.asr import Transcription
    from dashscope.utils.oss_utils import OssUtils

    dashscope.api_key = api_key
    print("[1B] 上传音频...", flush=True)
    oss_url, _cert = OssUtils.upload(model=ASR_MODEL, file_path=UNIT1B_MP3, api_key=api_key)
    resp = Transcription.async_call(model=ASR_MODEL, file_urls=[oss_url], api_key=api_key)
    if resp.status_code != 200:
        raise RuntimeError(f"ASR 提交失败: {resp.code} {resp.message}")
    task = resp.output.task_id

    import time
    for _ in range(120):
        r = Transcription.fetch(task=task, api_key=api_key)
        status = r.output.get("task_status") if isinstance(r.output, dict) else r.output.task_status
        if status == "SUCCEEDED":
            data = r.output.result if isinstance(r.output, dict) else r.output.result
            sents = (data or {}).get("transcripts", [{}])[0].get("sentences", [])
            return [(sec(s["begin_time"]), sec(s["end_time"])) for s in sents]
        if status == "FAILED":
            raise RuntimeError("ASR 任务失败")
        time.sleep(5)
    raise RuntimeError("ASR 超时")


# --------------------------------------------------------------------------
# 组装 + 校验
# --------------------------------------------------------------------------
def build_content():
    reading_1a = build_1a()
    reading_1b = build_1b()
    reading_video = build_video()

    dictionary = {v["w"]: {k: v[k] for k in ("ph", "pos", "zh", "en", "eg")}
                  for v in VOCAB_LIST}
    dictionary.update(DICTIONARY_EXTRAS)

    content = {
        "book": "Reading Explorer Foundations · Third Edition",
        "level": "Level F",
        "defaultUnit": 1,
        "units": {
            1: {
                "title": "Mysteries",
                "readings": [
                    {"label": "1A", "title": "A Mysterious Visitor",
                     "audio": "audio/unit1a.mp3", "sentences": reading_1a},
                    {"label": "1B", "title": "The Lost City of Atlantis",
                     "audio": "audio/unit1b.mp3", "sentences": reading_1b},
                    {"label": "Video", "title": "Moon Mystery",
                     "audio": None, "sentences": reading_video},
                ],
                "vocabList": VOCAB_LIST,
            }
        },
        "dictionary": dictionary,
        "scenarios": SCENARIOS,
    }

    errors = validate(content)
    if errors:
        print("\n校验失败：", flush=True)
        for e in errors:
            print("  -", e, flush=True)
        raise SystemExit("结构校验未通过，未写出文件。")

    os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
    js = "window.CONTENT = " + json.dumps(content, ensure_ascii=False, indent=2) + ";\n"
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"\n已写出 {OUT_JS}\n", flush=True)
    return content


def validate(c):
    errs = []
    unit = c["units"][c["defaultUnit"]]
    labels = {"1A": "A Mysterious Visitor", "1B": "The Lost City of Atlantis", "Video": "Moon Mystery"}
    if c["book"] != "Reading Explorer Foundations · Third Edition":
        errs.append("book 字段不符")
    if len(unit["readings"]) != 3:
        errs.append("readings 应为 3 篇")
    for r in unit["readings"]:
        if r["label"] not in labels or r["title"] != labels[r["label"]]:
            errs.append(f"{r['label']} 标题不符")
        if not r["sentences"]:
            errs.append(f"{r['label']} 句子为空")
        for s in r["sentences"]:
            if not s.get("en") or not s.get("zh"):
                errs.append(f"{r['label']} 存在缺 en/zh 的句子")
            if "t" not in s:
                errs.append(f"{r['label']} 句子缺少 t 字段")
            if s.get("t") is not None and len(s.get("t")) != 2:
                errs.append(f"{r['label']} t 字段应为 [begin,end] 或 null")
    if len(unit["vocabList"]) != 16:
        errs.append(f"vocabList 应为 16 词，实际 {len(unit['vocabList'])}")
    if not c["dictionary"]:
        errs.append("dictionary 为空")
    if len(c["scenarios"]) != 3:
        errs.append(f"scenarios 应为 3 个，实际 {len(c['scenarios'])}")
    return errs


def summary(c):
    unit = c["units"][c["defaultUnit"]]
    print("==== 校验通过 · 汇总 ====", flush=True)
    for r in unit["readings"]:
        n = len(r["sentences"])
        nts = sum(1 for s in r["sentences"] if s.get("t"))
        print(f"  {r['label']:<6} sentences={n:<3} with_ts={nts}/{n}", flush=True)
    print(f"  vocabList={len(unit['vocabList'])}   dictionary={len(c['dictionary'])}   scenarios={len(c['scenarios'])}", flush=True)


if __name__ == "__main__":
    c = build_content()
    summary(c)