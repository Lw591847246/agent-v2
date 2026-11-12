# app/skills/image_to_video/prompts.py
"""图生视频提示词和参数。"""

DEFAULT_I2V_PROMPT = (
    "主播面对镜头自然说话，头部轻微摆动，手势自然，表情亲切，口型自然，"
    "写实风格，保持人物身份，画面稳定，面部清晰，不扭曲五官"
)

_PRESET_KEYWORDS = [
    ("ultra", ["超高清", "超清", "极清", "4k", "4K", "2k", "2K"]),
    ("hd", ["高清", "1080", "720p", "720P", "1080p", "hd", "HD"]),
]

_DURATION_MAX = 5
_DEFAULT_DURATION = 5

_VOICE_DEFAULT = "zf_xiaoxiao"
_VOICE_MAP = {
    "小美": "zf_xiaoxiao", "晓晓": "zf_xiaoxiao", "云健": "zf_yunjian",
    "云希": "zf_yunxi", "云扬": "zf_yunyang", "云夏": "zf_yunxia",
}