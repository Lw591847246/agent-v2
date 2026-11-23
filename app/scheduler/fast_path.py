# app/scheduler/fast_path.py
"""L0 硬规则快速通道。

两种输出：
- 含 "intent"：直接命中，短路 L1+L2（原逻辑，短文本强关键词）
- 含 "hint"  ：不短路，把 hint 透传给 L1（方案 C，长文本强指令）

语义边界：
- 短文本（<=15 字）+ 强关键词 → 直接命中（无 LLM 误判风险）
- 长文本 + 开头强指令 → 产 hint 给 L1，L1 的 LLM 仍有最终决策权
"""
from typing import Optional

from app.scheduler.intents import Intent


# ============ 短文本强关键词（<=15 字，直接短路） ============
_STRONG_KEYWORDS = {
    Intent.PPT_GENERATION: [
        "生成ppt", "做个ppt", "做一份ppt", "帮我做ppt", "生成演示文稿",
    ],
    Intent.WORD_GENERATION: [
        "生成word", "做个word", "导出word", "导出成word", "保存成word",
    ],
    Intent.IMAGE_GENERATION: [
        "画一张", "画一个", "生成图片", "生成一张图", "帮我画",
    ],
    Intent.TRANSLATION: [
        "翻译成", "翻译为", "translate to",
    ],
    Intent.ENGLISH_CHAT: [
        "练英语", "practice english",
    ],
    Intent.TTS: [
        "朗读", "配音", "生成语音", "转成语音",
    ],
}

# ============ 长文本强指令（只产 hint，交给 L1 决策） ============
# key 是 hint 名（语义化标识），value 是关键词列表
_STRONG_INSTRUCTION_KEYWORDS = {
    "storyboard": [
        "做成漫剧", "做成短剧", "做成有声绘本", "做成广播剧", "做成动漫",
        "变成漫剧", "变成短剧", "变成有声绘本", "变成广播剧", "变成动漫",
        "转成漫剧", "转成短剧", "转为漫剧", "转为短剧",
        "制作成漫剧", "制作成短剧", "制作成有声绘本",
        "生成漫剧", "生成短剧", "生成有声绘本",
        "做成分镜", "做成视频漫剧",
    ],
    "text_to_video": [
        "生成一段视频", "生成一个视频", "生成视频", "做一段视频", "做一个视频",
    ],
    "face_swap": [
        "换脸", "替换人脸", "人脸替换",
    ],
    "portrait_animation": [
        "数字人口播", "数字人讲课", "孔子讲课",
    ],
}

# hint 名 → Intent 枚举（短文本直接命中时用，长文本产 hint 时不用）
_HINT_TO_INTENT = {
    "storyboard": Intent.STORYBOARD,
    "text_to_video": Intent.TEXT_TO_VIDEO,
    "face_swap": Intent.FACE_SWAP,
    "portrait_animation": Intent.PORTRAIT_ANIMATION,
}

_MAX_FAST_TEXT_LEN = 15
_HINT_HEAD_CHARS = 300   # 长文本只扫描开头 300 字，避免长故事正文稀释指令信号


def try_fast_path(text: str, has_att: bool, att_types: list) -> Optional[dict]:
    """
    返回：
    - None                                             未命中，走 L1
    - {"intent": Intent, "confidence": float, "source": str}  直接命中，短路 L1+L2
    - {"hint": str, "confidence": float, "source": str}       产 hint，走 L1 但携带提示
    """
    if not text:
        return {"intent": Intent.CHAT, "confidence": 1.0, "source": "fast_empty"}

    t = text.strip()
    if not t:
        return {"intent": Intent.CHAT, "confidence": 1.0, "source": "fast_blank"}

    # 极短文本（1-2 字）且无附件 → CHAT
    if len(t) <= 2 and not has_att:
        return {"intent": Intent.CHAT, "confidence": 1.0, "source": "fast_short"}

    lower = t.lower()

    # ① 短文本：直接短路（原逻辑 + 短文本强指令）
    if len(t) <= _MAX_FAST_TEXT_LEN:
        for intent, kws in _STRONG_KEYWORDS.items():
            for kw in kws:
                if kw.lower() in lower:
                    return {
                        "intent": intent,
                        "confidence": 0.95,
                        "source": f"fast_kw:{kw}",
                    }
        # 短文本的强指令也直接命中（如"做成漫剧"这种 6 字指令）
        for hint, kws in _STRONG_INSTRUCTION_KEYWORDS.items():
            for kw in kws:
                if kw in lower:
                    return {
                        "intent": _HINT_TO_INTENT[hint],
                        "confidence": 0.95,
                        "source": f"fast_instr:{kw}",
                    }
        return None

    # ② 长文本：只扫描开头 _HINT_HEAD_CHARS，命中则产 hint（不短路）
    head = lower[:_HINT_HEAD_CHARS]
    for hint, kws in _STRONG_INSTRUCTION_KEYWORDS.items():
        for kw in kws:
            if kw in head:
                return {
                    "hint": hint,
                    "confidence": 0.9,
                    "source": f"fast_instr_head:{kw}",
                }

    return None