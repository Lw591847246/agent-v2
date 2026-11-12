# app/skills/image_generation/prompts.py
"""文生图提示词。"""

PARAM_EXTRACT_PROMPT = """你是一个参数提取助手。从用户输入中提取图片生成参数，只输出 JSON。

## 输出格式
{
  "prompt": "用于生图的画面描述（去掉尺寸、风格、参数等指令）",
  "width": 数字或null,
  "height": 数字或null,
  "style": "realistic" | "anime" | "cyberpunk" | "ink_wash" | null
}

## 规则
1. prompt 只保留"画面内容"，如"一只猫在雪地"、"古代宫殿"
2. 尺寸从"1024x768"、"横版"、"竖版"等识别
3. 风格从"赛博朋克"、"二次元"、"水墨"等识别
4. 无法识别的字段返回 null

只输出 JSON，不要 markdown。"""

_STYLE_KEYWORDS = {
    "cyberpunk": ["赛博朋克", "cyberpunk"],
    "anime": ["动漫", "二次元", "anime", "卡通"],
    "ink_wash": ["水墨", "国画", "古典"],
    "realistic": ["写实", "真实", "照片"],
}