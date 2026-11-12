"""异常体系"""
from app.core.error_code import ErrorCode


class AppException(Exception):
    def __init__(self, error_code: ErrorCode, detail: str = None):
        self.error_code = error_code
        self.code = error_code.code
        self.message = detail or error_code.message
        super().__init__(self.message)


class AuthException(AppException):
    def __init__(self, detail: str = None):
        super().__init__(ErrorCode.UNAUTHORIZED, detail)


class NotFoundException(AppException):
    def __init__(self, detail: str = None):
        super().__init__(ErrorCode.NOT_FOUND, detail)


class FileException(AppException):
    def __init__(self, code: ErrorCode = ErrorCode.FILE_PARSE_ERROR, detail: str = None):
        super().__init__(code, detail)


class LLMException(AppException):
    def __init__(self, detail: str = None):
        super().__init__(ErrorCode.LLM_ERROR, detail)


class VisionException(AppException):
    def __init__(self, detail: str = None):
        super().__init__(ErrorCode.VISION_ERROR, detail)


class SDXLException(AppException):
    def __init__(self, detail: str = None):
        super().__init__(ErrorCode.SDXL_ERROR, detail)


class KnowledgeBaseException(AppException):
    def __init__(self, detail: str = None):
        super().__init__(ErrorCode.KNOWLEDGE_BASE_ERROR, detail)