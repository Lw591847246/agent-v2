# app/api/v1/image_controller.py
"""图片 / 缩略图访问（HMAC 签名防枚举）。

修复背景：原实现只靠 message_id（agent_message 主键，自增）定位图片，
         从 1 遍历即可拉全站图片 —— 属垂直越权。现改为：
1) get_all_messages 出参里 image_url / thumb_url 自动附 ?sig=<hmac>；
2) 本接口强制校验 sig，缺失或错误一律 403。
兼容性：前端拿到 messages 接口返回的 image_url 原样渲染即可；
        历史消息需重新拉一次 messages（旧 URL 无 sig，会 403）。
"""
from fastapi import APIRouter, HTTPException, Query, Response

from app.core.logger import logger
from app.infrastructure.repositories.conversation_repo import conversation_repo
from app.utils.image_storage import load_original, load_thumb
from app.utils.signed_url import verify as verify_sig

router = APIRouter(tags=["图片"])


def _fetch_row(message_id: int, filename_col: str, thumb_col: str = None):
    cols = f"m.{filename_col}, m.session_id, c.user_id"
    if thumb_col:
        cols = f"m.{thumb_col}, " + cols
    sql = (
        f"SELECT {cols} "
        f"FROM agent_message m "
        f"JOIN agent_conversation c ON m.session_id = c.session_id "
        f"WHERE m.id = %s"
    )
    return conversation_repo.query_one(sql, (message_id,))


@router.get("/agent/v1/image/{message_id}")
def get_image(
    message_id: int,
    sig: str = Query("", description="HMAC 签名（messages 接口里 image_url 自带）"),
):
    if not verify_sig(message_id, sig):
        raise HTTPException(status_code=403, detail="invalid signature")

    row = _fetch_row(message_id, "image_filename")
    if not row or not row.get("image_filename"):
        raise HTTPException(status_code=404, detail="图片不存在")

    image_bytes = load_original(row["user_id"], row["session_id"], row["image_filename"])
    if not image_bytes:
        raise HTTPException(status_code=404, detail="图片文件丢失")

    ext = row["image_filename"].rsplit(".", 1)[-1].lower()
    media_type = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif",
    }.get(ext, "image/png")

    return Response(content=image_bytes, media_type=media_type,
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/agent/v1/thumb/{message_id}")
def get_thumb(
    message_id: int,
    sig: str = Query("", description="HMAC 签名（与 image 同一 sig）"),
):
    if not verify_sig(message_id, sig):
        raise HTTPException(status_code=403, detail="invalid signature")

    row = _fetch_row(message_id, "thumb_filename", thumb_col="thumb_filename")
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