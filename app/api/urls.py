# app/api/urls.py
"""URL 常量。"""


class UrlRegistry:
    AUTH_LOGIN = "/front/v1/login"
    AUTH_REGISTER = "/front/v1/user/add"

    CONVERSATION_LIST = "/agent/v1/conversations"
    CONVERSATION_NEW = "/agent/v1/conversation/new"
    CONVERSATION_MESSAGES = "/agent/v1/conversation/{session_id}/messages"

    CHAT = "/agent/v1/chat"
    CHAT_ACTION = "/agent/v1/chat/action"

    JOB = "/agent/v1/job/{task_id}"
    JOB_VIDEO = "/agent/v1/job/{task_id}/video"

    FILE_PROCESS = "/agent/v1/process-file"
    UPLOAD_ASSET = "/agent/v1/upload/asset"

    KNOWLEDGE_ADD = "/agent/v1/knowledge/add"
    KNOWLEDGE_QUERY = "/agent/v1/knowledge/query"


urls = UrlRegistry()