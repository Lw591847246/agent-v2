"""错误码枚举"""
from enum import Enum


class ErrorCode(Enum):
    SUCCESS = (200, "成功")
    PARAM_ERROR = (400, "参数错误")
    UNAUTHORIZED = (401, "未登录或登录已过期")
    FORBIDDEN = (403, "无权限")
    NOT_FOUND = (404, "资源不存在")
    RATE_LIMITED = (429, "请求过于频繁")
    SERVER_ERROR = (500, "服务器内部错误")

    USER_EXISTS = (1001, "用户名已存在")
    USER_NOT_FOUND = (1002, "用户不存在")
    PASSWORD_ERROR = (1003, "密码错误")
    CONVERSATION_NOT_FOUND = (1004, "会话不存在")

    FILE_TYPE_ERROR = (2001, "文件类型不支持")
    FILE_TOO_LARGE = (2002, "文件过大")
    FILE_PARSE_ERROR = (2003, "文件解析失败")
    FILE_TOKEN_LIMIT = (2004, "文档内容超长")

    LLM_ERROR = (3001, "模型调用失败")
    LLM_TIMEOUT = (3002, "模型响应超时")
    VISION_ERROR = (3003, "图片理解失败")
    EMBEDDING_ERROR = (3004, "向量化失败")

    SDXL_ERROR = (4001, "文生图服务失败")
    SDXL_UNAVAILABLE = (4002, "文生图服务未启动")

    KNOWLEDGE_BASE_ERROR = (5001, "知识库检索失败")

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message