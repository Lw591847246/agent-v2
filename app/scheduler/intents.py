# app/scheduler/intents.py
"""意图定义：双层路由 L1 领域 + L2 意图。"""
from enum import Enum


class Intent(str, Enum):
    CHAT = "chat"
    ENGLISH_CHAT = "english_chat"
    CODE_GENERATION = "code_generation"
    TRANSLATION = "translation"
    KNOWLEDGE_QUERY = "knowledge_query"
    DOCUMENT_QA = "document_qa"
    DOCUMENT_SUMMARIZE = "document_summarize"
    WEB_SEARCH = "web_search"
    UTILITY = "utility"
    TTS = "tts"

    PPT_GENERATION = "ppt_generation"
    WORD_GENERATION = "word_generation"

    IMAGE_GENERATION = "image_generation"
    IMAGE_EDIT = "image_edit"
    IMAGE_UNDERSTANDING = "image_understanding"

    STORYBOARD = "storyboard"
    PORTRAIT_ANIMATION = "portrait_animation"
    FACE_SWAP = "face_swap"
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    CUSTOM_AVATAR = "custom_avatar"

    MINDMAP = "mindmap"
    PROTOTYPE = "prototype"


INTENT_CATEGORIES = {
    "general": [
        Intent.CHAT,
        Intent.ENGLISH_CHAT,
        Intent.CODE_GENERATION,
        Intent.TRANSLATION,
        Intent.WEB_SEARCH,
        Intent.UTILITY,
        Intent.TTS,
    ],
    "content": [
        Intent.DOCUMENT_QA,
        Intent.DOCUMENT_SUMMARIZE,
        Intent.PPT_GENERATION,
        Intent.WORD_GENERATION,
        Intent.KNOWLEDGE_QUERY,
        Intent.MINDMAP,
        Intent.PROTOTYPE,
    ],
    "image": [
        Intent.IMAGE_GENERATION,
        Intent.IMAGE_EDIT,
        Intent.IMAGE_UNDERSTANDING,
    ],
    "video": [
        Intent.STORYBOARD,
        Intent.PORTRAIT_ANIMATION,
        Intent.FACE_SWAP,
        Intent.TEXT_TO_VIDEO,
        Intent.IMAGE_TO_VIDEO,
        Intent.CUSTOM_AVATAR,
    ],
}


DOMAIN_DESCRIPTIONS = {
    "general": "闲聊、通用问答、写代码、翻译、纯文本创作、本地小工具、联网搜索、语音合成",
    "content": "文档/Office 文件相关、知识库、思维导图、产品原型",
    "image": "图片相关",
    "video": "视频相关",
}


def get_domain(intent: Intent) -> str:
    for domain, intents in INTENT_CATEGORIES.items():
        if intent in intents:
            return domain
    return "general"


def get_intents_of_domain(domain: str) -> list:
    return list(INTENT_CATEGORIES.get(domain, []))