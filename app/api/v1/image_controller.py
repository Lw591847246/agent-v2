"""图片访问接口"""
from fastapi import APIRouter, HTTPException, Response
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.utils.image_storage import load_original, load_thumb
from app.core.logger import logger

router = APIRouter(tags=["图片"])


@router.get("/agent/v1/image/{message_id}")
def get_image(message_id: int):
    """返回原图（无需登录态，URL 里的 message_id 本身即随机标识）"""
    sql = """
        SELECT m.image_filename, m.session_id, c.user_id
        FROM agent_message m
        JOIN agent_conversation c ON m.session_id = c.session_id
        WHERE m.id = %s
    """
    row = conversation_repo.query_one(sql, (message_id,))
    if not row or not row.get("image_filename"):
        raise HTTPException(status_code=404, detail="图片不存在")

    image_bytes = load_original(row["user_id"], row["session_id"], row["image_filename"])
    if not image_bytes:
        raise HTTPException(status_code=404, detail="图片文件丢失")

    ext = row["image_filename"].rsplit(".", 1)[-1].lower()
    media_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")

    return Response(content=image_bytes, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/agent/v1/thumb/{message_id}")
def get_thumb(message_id: int):
    """返回缩略图（从本地文件读取）"""
    sql = """
        SELECT m.thumb_filename, m.session_id, c.user_id
        FROM agent_message m
        JOIN agent_conversation c ON m.session_id = c.session_id
        WHERE m.id = %s
    """
    row = conversation_repo.query_one(sql, (message_id,))
    if not row or not row.get("thumb_filename"):
        raise HTTPException(status_code=404, detail="缩略图不存在")

    img_bytes = load_thumb(row["user_id"], row["session_id"], row["thumb_filename"])
    if not img_bytes:
        logger.warning(f"【缩略图】文件不存在 message_id={message_id}")
        raise HTTPException(status_code=404, detail="缩略图文件丢失")

    return Response(
        content=img_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )