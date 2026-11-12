# app/scheduler/fast_path.py
"""L0 硬规则快速通道：极短文本/强关键词直接命中，省去 L1+L2 两次 LLM 调用。"""
from typing import Optional

from app.scheduler.intents import Intent


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

_MAX_FAST_TEXT_LEN = 15


def try_fast_path(text: str, has_att: bool, att_types: list) -> Optional[dict]:
    """
    返回 None 表示未命中，需要走 LLM 路由。
    返回 dict 表示命中：{"intent": Intent, "confidence": float, "source": str}
    """
    if not text:
        return {"intent": Intent.CHAT, "confidence": 1.0, "source": "fast_empty"}

    t = text.strip()
    if not t:
        return {"intent": Intent.CHAT, "confidence": 1.0, "source": "fast_blank"}

    # 极短文本（1-2 字）且无附件 → CHAT
    if len(t) <= 2 and not has_att:
        return {"intent": Intent.CHAT, "confidence": 1.0, "source": "fast_short"}

    # 短文本强关键词命中
    if len(t) <= _MAX_FAST_TEXT_LEN:
        lower = t.lower()
        for intent, kws in _STRONG_KEYWORDS.items():
            for kw in kws:
                if kw.lower() in lower:
                    return {
                        "intent": intent,
                        "confidence": 0.95,
                        "source": f"fast_kw:{kw}",
                    }

    return None