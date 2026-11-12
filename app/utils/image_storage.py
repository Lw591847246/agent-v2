"""图片存储：原图文件系统 + 缩略图 base64 供 DB"""
import os
import time
import base64
import uuid
from io import BytesIO
from PIL import Image
from app.core.logger import logger

STORAGE_ROOT = "./data/images"
THUMB_SIZE = (200, 200)
THUMB_QUALITY = 60
THUMB_ROOT = "./data/thumbs"

def _thumb_dir(user_id, session_id) -> str:
    return os.path.join(THUMB_ROOT, str(user_id), session_id)

def _detect_format(image_bytes: bytes) -> str:
    if image_bytes[:2] == b'\xff\xd8':
        return "jpg"
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    if image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "gif"
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "webp"
    return "png"


def _session_dir(user_id, session_id) -> str:
    return os.path.join(STORAGE_ROOT, str(user_id), session_id)


def save_image(user_id: int, session_id: str, image_base64: str) -> dict:
    """
    保存原图 + 缩略图，都落盘。
    返回: {
        "filename": "1727178000000_ab12cd34.png",
        "thumb_filename": "1727178000000_ab12cd34_thumb.jpg",
        "image_path": "...",
        "thumb_path": "..."
    }
    """
    result = {
        "filename": None, "thumb_filename": None,
        "image_path": None, "thumb_path": None,
    }

    # 去前缀 + 解码
    if image_base64.startswith("data:"):
        image_base64 = image_base64.split(",", 1)[-1]
    try:
        image_bytes = base64.b64decode(image_base64)
    except Exception as e:
        logger.error(f"【图片存储】base64 解码失败: {e}")
        return result

    if not image_bytes:
        return result

    # ---- 1. 保存原图 ----
    ext = _detect_format(image_bytes)
    filename = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.{ext}"
    img_dir = _session_dir(user_id, session_id)
    os.makedirs(img_dir, exist_ok=True)
    img_path = os.path.join(img_dir, filename)
    with open(img_path, "wb") as f:
        f.write(image_bytes)

    result["filename"] = filename
    result["image_path"] = img_path
    logger.info(f"【图片存储】原图已保存: {img_path} ({len(image_bytes)/1024:.0f}KB)")

    # ---- 2. 保存缩略图 ----
    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)

        # 缩略图文件名 = 原图文件名（去扩展名）+ _thumb.jpg
        base_name = filename.rsplit('.', 1)[0]
        thumb_filename = f"{base_name}_thumb.jpg"
        thumb_dir = _thumb_dir(user_id, session_id)
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, thumb_filename)
        img.save(thumb_path, format="JPEG", quality=THUMB_QUALITY, optimize=True)

        result["thumb_filename"] = thumb_filename
        result["thumb_path"] = thumb_path
        logger.info(f"【图片存储】缩略图已保存: {thumb_path} "
                    f"({os.path.getsize(thumb_path)/1024:.1f}KB)")
    except Exception as e:
        logger.error(f"【图片存储】缩略图生成失败: {e}", exc_info=True)

    return result

def load_original(user_id, session_id, filename) -> bytes | None:
    if not filename:
        return None
    path = os.path.join(_session_dir(user_id, session_id), filename)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def load_thumb(user_id: int, session_id: str, thumb_filename: str) -> bytes | None:
    """读取缩略图字节"""
    if not thumb_filename:
        return None
    path = os.path.join(_thumb_dir(user_id, session_id), thumb_filename)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def get_latest_filename(user_id, session_id) -> str | None:
    """多轮续改：取会话最新原图"""
    dir_path = _session_dir(user_id, session_id)
    if not os.path.isdir(dir_path):
        return None
    files = [f for f in os.listdir(dir_path)
             if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))]
    if not files:
        return None
    files.sort()
    return files[-1]