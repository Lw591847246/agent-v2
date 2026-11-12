import re
from typing import Optional, Tuple

# Qwen 支持的合法尺寸范围
MIN_SIDE = 256
MAX_SIDE = 1536
MULTIPLE = 8

DEFAULT_SIZE = (768, 1024)


def _clamp_side(v: int) -> int:
    """对齐到 8 的倍数，并限制在合法范围"""
    v = max(MIN_SIDE, min(MAX_SIDE, v))
    return (v // MULTIPLE) * MULTIPLE


def extract_size_from_text(text: str) -> Optional[Tuple[int, int]]:
    """从文本提取宽高 (width, height)，返回 None 表示没提到尺寸"""
    if not text:
        return None

    m = re.search(r"长\s*(\d+)\s*[^\d]*宽\s*(\d+)", text)
    if m:
        return (_clamp_side(int(m.group(2))), _clamp_side(int(m.group(1))))

    m = re.search(r"宽\s*(\d+)\s*[^\d]*长\s*(\d+)", text)
    if m:
        return (_clamp_side(int(m.group(1))), _clamp_side(int(m.group(2))))

    # 256*256像素 / 256x256 / 256×256
    m = re.search(r"(\d+)\s*[xX*×]\s*(\d+)", text)
    if m:
        return (_clamp_side(int(m.group(1))), _clamp_side(int(m.group(2))))

    m = re.search(r"(\d+)\s*[:：]\s*(\d+)", text)
    if m:
        rw, rh = int(m.group(1)), int(m.group(2))
        base = 768
        if rw >= rh:
            w, h = base, int(base * rh / rw)
        else:
            h, w = base, int(base * rw / rh)
        return (_clamp_side(w), _clamp_side(h))

    if any(k in text for k in ["长图", "竖图", "竖版"]):
        return (768, 1024)
    if any(k in text for k in ["方图", "正方形"]):
        return (768, 768)
    if any(k in text for k in ["横图", "横版", "宽图"]):
        return (1024, 768)

    return None


# 尺寸描述的清理模式（用于从 prompt 里移除）
_SIZE_PATTERNS = [
    r"\d+\s*[xX*×]\s*\d+\s*像素?",
    r"\d+\s*[xX*×]\s*\d+",
    r"长\s*\d+\s*[^\d]*宽\s*\d+\s*像素?",
    r"宽\s*\d+\s*[^\d]*长\s*\d+\s*像素?",
    r"\d+\s*[:：]\s*\d+\s*像素?",
    r"[，,、]?\s*尺寸\s*[:：]?\s*\d+\s*[xX*×]\s*\d+\s*像素?",
]


def strip_size_phrase(text: str) -> str:
    """把尺寸描述从 prompt 里拿掉，避免干扰 Qwen 生成内容"""
    result = text or ""
    for p in _SIZE_PATTERNS:
        result = re.sub(p, " ", result)
    # 合并空白、去掉首尾标点
    result = re.sub(r"\s+", " ", result).strip(" ，,、。.；;")
    return result