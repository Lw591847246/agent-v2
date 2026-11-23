"""Storyboard Skill：故事 → 漫剧/短剧/有声绘本。"""
from __future__ import annotations

from typing import List

from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.storyboard.extractor import detect_style, extract_story, extract_pure_story
from app.skills.storyboard.service import storyboard_service


# 指代残句关键词：用户用"上一轮/刚才..."来指代历史，而非真的粘贴了正文
_REFERENT_KEYWORDS = (
    "上一轮", "上一个", "上一句", "上一段", "上一篇", "刚才", "刚刚",
    "之前", "前面", "上面的", "上述", "那段", "这段", "这篇", "那个",
)


def _looks_like_pointer(story: str) -> bool:
    """判断 story 是不是"指针/指令残句"而非真正的正文。

    - 空：算指针（该走 history 提取）
    - 长度 >= 200：当作真实正文，不动它
    - 含指代关键词：算指针
    - 剥掉指令前后缀后所剩无几（<30 字）：是纯指令句，算指针
    """
    if not story or not story.strip():
        return True
    s = story.strip()
    if len(s) >= 200:
        return False
    if any(k in s for k in _REFERENT_KEYWORDS):
        return True
    if len(extract_pure_story(s)) < 30:
        return True
    return False


class StoryboardSkill(BaseSkill):
    name = "generate_storyboard"
    description = (
        "把一段故事文本改编成漫剧/有声绘本/广播剧（多场景视频拼接 + 旁白配音）。"
        "适用于：'做成漫剧/短剧/有声绘本/广播剧/分镜视频'、'把上一轮的故事做成视频'。"
        "不适用于：纯文本生视频（用 text_to_video）、图片动起来（用 image_to_video）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "story": {
                "type": "string",
                "description": "故事文本，可空；为空或仅为指代残句时从 user_text + history 提取",
            },
            "style": {
                "type": "string",
                "description": "视觉风格：realistic_cinema（写实电影，默认）/ anime_drama（漫剧）/ ink_wash（水墨）",
                "enum": ["realistic_cinema", "anime_drama", "ink_wash"],
            },
            "user_text": {
                "type": "string",
                "description": "用户原始输入（用于从 history 提取故事）",
            },
            "history": {
                "type": "array",
                "description": "历史对话（用于提取故事）",
            },
        },
        "required": [],
    }
    intent = "storyboard"
    category = "video"
    sync = False
    returns = ["card", "video"]
    requires_attachments = []
    produces_outputs = True
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, story: str = "", style: str = "realistic_cinema",
        user_text: str = "", history: List[dict] = None,
        session_id: str = "", user_id: int = 0, **kwargs,
    ) -> Result:
        history = history or []

        # 没传 story，或传进来的只是"上一轮..."这种指代残句时：丢弃脏值，走 user_text + history 提取
        # 防御点：上游 L2/RouterProvider 可能把 prompt 里的指代短语误填进 story，
        # 若不在此拦截，会跳过历史提取并直接命中"故事太短"误判。
        if _looks_like_pointer(story):
            if story and story.strip():
                logger.info(f"[Storyboard] 丢弃疑似指代残句 story={story[:30]!r}，转历史提取")
            user_text = user_text or kwargs.get("text") or ""
            story, source_type = extract_story(user_text, history)
            logger.info(f"[Storyboard] 提取结果: source={source_type}, len={len(story)}")
            if not story:
                return Result(parts=[text_part(
                    "未找到故事内容。\n你可以：\n1. 直接粘贴完整故事\n2. 或上一轮已生成故事后说'把上一轮的故事做成漫剧'"
                )])
            if style == "realistic_cinema":
                style = detect_style(user_text)

        if len(story) < 100:
            return Result(parts=[text_part(f"故事太短（{len(story)} 字），建议至少 100 字。")])

        task_id = storyboard_service.submit(
            session_id=session_id, story=story,
            style=style, user_id=user_id,
        )

        card = card_part(
            "skill_progress",
            {
                "task_id": task_id,
                "title": "正在制作漫剧",
                "description": "拆解故事 → 生成画面 → 拼接成片...",
                "progress": 5,
                "steps": [
                    {"label": "拆解故事场景", "status": "running"},
                    {"label": "合成旁白音频", "status": "pending"},
                    {"label": "生成画面", "status": "pending"},
                    {"label": "合成视频", "status": "pending"},
                ],
            },
            status="processing",
        )
        return Result(parts=[card], task_id=task_id)


skill = StoryboardSkill()
