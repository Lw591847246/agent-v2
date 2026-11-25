"""敏感信息脱敏工具。

定位:上传入库前的兜底脱敏,非强安全保证。
- 只打"配置项的值",保留 key 名与代码结构(不删行、不改语法);
- 占位符统一为 ****;
- 内部任何异常一律吞掉、原样返回,绝不因脱敏本身中断调用方任务;
- 后续接 nacos 后明文配置消失,本模块可整体下线。
"""
import re
from typing import Final, List, Tuple

MASK: Final[str] = "****"

# 首批 key 清单(按报错增量补充;key 名保留,只替换其后的值)
_SENSITIVE_KEYS: Final[List[str]] = [
    # 凭据类
    "token", "password", "passwd", "pwd", "secret",
    "api_key", "apikey", "access_key", "access_token",
    "refresh_token", "private_key", "auth", "authorization", "credential",
    # 连接类
    "redis_url", "database_url", "db_password", "dsn",
    "connection_string", "mongo_uri", "amqp", "rabbitmq_url",
    # 云服务类
    "aws_secret", "oss_access_key", "cos_secret", "sk", "ak",
]

# 命中形态:key 后跟 = : ： 或空格+引号,值部分整体替换为 ****
# 例:token=abc -> token=**** | "password": "x" -> "password": "****" | redis_url: redis://... -> redis_url: ****
_KEY_PATTERN: Final[str] = "|".join(_SENSITIVE_KEYS)
_VALUE_RE: Final[re.Pattern] = re.compile(
    r"""
    (?P<prefix>
        ["']?(?:{_keys})["']?          # key(允许带引号)
        \s*[=:：]\s*                    # 分隔符
        ["']?                           # 值的开引号(可选)
    )
    (?P<value>[^\s"',;#)\]]+)          # 值:到空白/引号/逗号/分号/#/括号前为止
    """.format(_keys=_KEY_PATTERN),
    re.IGNORECASE | re.VERBOSE,
)

# 文本类扩展名(仅这些做脱敏;其余按二进制原样保存)
TEXT_EXTENSIONS: Final[frozenset] = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".conf", ".cfg",
    ".env", ".md", ".txt", ".sh", ".bash", ".sql", ".java", ".go",
    ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb",
    ".properties", ".xml", ".html", ".css", ".scss", ".less",
})


def is_text_file(filename: str) -> bool:
    """按扩展名判断是否为可脱敏的文本文件。"""
    try:
        suffix = filename[filename.rfind("."):].lower() if "." in filename else ""
        return suffix in TEXT_EXTENSIONS
    except Exception:
        return False


def mask_text(content: str) -> Tuple[str, bool]:
    """对文本内容做脱敏。

    Returns:
        (脱敏后内容, 是否发生替换)。任何异常都原样返回,不抛出。
    """
    try:
        masked, count = _VALUE_RE.subn(
            lambda m: m.group("prefix") + MASK, content
        )
        return masked, count > 0
    except Exception:
        # 脱敏失败绝不中断上传任务,原样返回
        return content, False


def mask_bytes(filename: str, raw: bytes) -> tuple[bytes, bool]:
    """对 bytes 内容做脱敏。非文本/解码失败一律原样返回，绝不抛。
    Returns: (脱敏后bytes, 是否发生替换)
    """
    try:
        if not is_text_file(filename):
            return raw, False
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, AttributeError, ValueError):
        return raw, False
    masked_text, changed = mask_text(text)
    if not changed:
        return raw, False
    return masked_text.encode("utf-8"), True