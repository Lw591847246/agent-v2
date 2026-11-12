# app/skills/text_to_video/prompts.py
"""文生视频提示词和默认参数。"""

DEFAULT_PROMPT_SUFFIX = "，高清画质，画面稳定，无闪烁，无变形"

_PRESET_KEYWORDS = [
    ("hd", ["高清", "1080", "720p", "720P", "1080p", "1080P", "hd", "HD", "清晰", "清晰度"]),
]