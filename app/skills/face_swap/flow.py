# app/skills/face_swap/flow.py
"""换脸多轮 flow：素材收集 → 选图 → 确认 → 提交。"""
from __future__ import annotations

import json
import time
import uuid
from typing import List

from app.context.flow_state_store import flow_state_store
from app.core.logger import logger
from app.domain.models.flow_state import FlowState
from app.models.attachment import Attachment


FLOW_NAME = "face_swap"


# ==================== helpers ====================

def _gen_card_id() -> str:
    return "c_" + uuid.uuid4().hex[:10]


def _new_card_id(state: FlowState) -> str:
    cid = _gen_card_id()
    state.message_id = cid
    state.updated_at = time.time()
    return cid


def _as_card_event(card: dict) -> dict:
    return {"type": "card", "card_json": json.dumps(card, ensure_ascii=False)}


def _att_to_item(att: Attachment) -> dict:
    return att.to_card_item()


def _list_images(atts: List[Attachment]) -> List[Attachment]:
    return [a for a in atts if a.is_image()]


def _list_videos(atts: List[Attachment]) -> List[Attachment]:
    return [a for a in atts if a.is_video()]


def _att_from_file_id(file_id: str, default_type: str = "image") -> Attachment | None:
    if not file_id:
        return None
    from app.utils.file_registry import file_registry
    meta = file_registry.get(file_id)
    if not meta:
        logger.warning(f"【FaceSwap】file_id not in registry: {file_id}")
        return None
    return Attachment(
        file_id=file_id, type=meta.get("type", default_type),
        name=meta.get("name", ""), path=meta.get("path", ""),
        thumb_url=(f"/agent/v1/upload/thumb/{file_id}" if meta.get("thumb_path") else ""),
        size=meta.get("size", 0), duration=meta.get("duration") or 0.0,
    )


# ==================== card builders ====================

def _build_pick_image_card(state: FlowState) -> dict:
    pending = state.slots.get("pending_images", []) or []
    items = []
    for p in pending:
        items.append({
            "key": p.get("file_id", ""),
            "label": p.get("name", "image"),
            "description": "", "thumbnail": p.get("thumb_url") or "",
            "preview": None, "tags": [], "default": False, "extra": {},
        })
    return {
        "card_id": state.message_id, "card_type": "face_swap_pick_image",
        "title": "选择人脸图",
        "description": f"检测到 {len(items)} 张图片，请选择一张作为人脸图",
        "items": items, "allow_text_input": False,
        "text_input_placeholder": None, "submit_action": None,
    }


def _build_card(state: FlowState) -> dict:
    slots = state.slots
    src = slots.get("source_image")
    vids = slots.get("target_videos", []) or []
    prompt = slots.get("prompt", "") or ""

    has_image = bool(src)
    has_video = len(vids) > 0
    summary = {"source_image": src, "target_videos": vids, "prompt": prompt}

    if has_image and has_video:
        card_type = "face_swap_confirm"
        title = "确认换脸素材"
        description = "请确认后开始生成"
        buttons = [
            {"key": "confirm", "label": "确认生成"},
            {"key": "add_video", "label": "补充视频"},
            {"key": "replace_face", "label": "更换人脸图"},
            {"key": "clear_assets", "label": "清空素材"},
            {"key": "cancel", "label": "取消"},
        ]
        missing = []
    else:
        card_type = "face_swap_asset_input"
        missing = []
        if not has_image:
            missing.append("source_image")
        if not has_video:
            missing.append("target_video")
        title = "补充换脸素材"
        description = "还差：" + "、".join(
            "人脸图片" if m == "source_image" else "目标视频" for m in missing
        )
        buttons = [
            {"key": "upload_source_image", "label": "上传人脸图"},
            {"key": "upload_target_video", "label": "上传目标视频"},
            {"key": "cancel", "label": "取消"},
        ]

    return {
        "card_id": state.message_id, "card_type": card_type,
        "title": title, "description": description, "summary": summary,
        "missing": missing, "allow_text_input": True,
        "text_input_default": prompt,
        "text_input_placeholder": "可以修改提示词，例如：换成周杰伦",
        "submit_text_action": {"key": "update_prompt", "label": "更新"},
        "buttons": buttons,
    }


def _rebuild_card(state: FlowState) -> dict:
    pending = state.slots.get("pending_images", []) or []
    if state.current_step == "pick_source_image" and len(pending) > 0:
        _new_card_id(state)
        flow_state_store.save(state)
        flow_state_store.set_active(FLOW_NAME, state.session_id, state.message_id)
        return _as_card_event(_build_pick_image_card(state))

    slots = state.slots
    has_image = bool(slots.get("source_image"))
    has_video = len(slots.get("target_videos", []) or []) > 0
    state.current_step = "confirming" if (has_image and has_video) else "collecting"
    if state.current_step != "pick_source_image":
        state.slots["pending_images"] = []

    _new_card_id(state)
    flow_state_store.save(state)
    flow_state_store.set_active(FLOW_NAME, state.session_id, state.message_id)
    return _as_card_event(_build_card(state))


# ==================== entry points ====================

def start(ctx) -> dict:
    logger.info(f"【FaceSwap】start attachments={len(ctx.attachments)}")
    images = _list_images(ctx.attachments)
    videos = _list_videos(ctx.attachments)
    vids = [_att_to_item(v) for v in videos]

    if len(images) > 1:
        card_id = _gen_card_id()
        state = FlowState(
            session_id=ctx.session_id or "", message_id=card_id,
            user_id=int(ctx.user_id or 0), intent=FLOW_NAME, flow_name=FLOW_NAME,
            current_step="pick_source_image",
            slots={
                "source_image": None, "target_videos": vids,
                "prompt": ctx.text or "",
                "pending_images": [_att_to_item(i) for i in images],
                "group_id": None, "task_ids": [],
            },
            created_at=time.time(), updated_at=time.time(),
        )
        flow_state_store.save(state)
        flow_state_store.set_active(FLOW_NAME, state.session_id, card_id)
        return _as_card_event(_build_pick_image_card(state))

    src_meta = _att_to_item(images[0]) if images else None
    card_id = _gen_card_id()
    state = FlowState(
        session_id=ctx.session_id or "", message_id=card_id,
        user_id=int(ctx.user_id or 0), intent=FLOW_NAME, flow_name=FLOW_NAME,
        current_step="collecting" if not (src_meta and vids) else "confirming",
        slots={
            "source_image": src_meta, "target_videos": vids,
            "prompt": ctx.text or "", "pending_images": [],
            "group_id": None, "task_ids": [],
        },
        created_at=time.time(), updated_at=time.time(),
    )
    flow_state_store.save(state)
    flow_state_store.set_active(FLOW_NAME, state.session_id, card_id)
    return _as_card_event(_build_card(state))


def continue_from_chat(
    state: FlowState, text: str = "", attachments: List[Attachment] = None,
) -> dict:
    if text:
        state.slots["prompt"] = text

    if attachments:
        images = _list_images(attachments)
        videos = _list_videos(attachments)

        if videos:
            state.slots["target_videos"] = [_att_to_item(a) for a in videos]

        if images:
            if len(images) > 1:
                state.slots["pending_images"] = [_att_to_item(a) for a in images]
                state.slots["source_image"] = None
                state.current_step = "pick_source_image"
            else:
                state.slots["source_image"] = _att_to_item(images[0])
                state.slots["pending_images"] = []
                if state.current_step == "pick_source_image":
                    state.current_step = "collecting"

    return _rebuild_card(state)


# ==================== action handlers ====================

def on_pick_source_image(state: FlowState, file_id: str) -> dict:
    if not file_id:
        return _rebuild_card(state)

    picked = None
    pending = state.slots.get("pending_images", []) or []
    for p in pending:
        if p.get("file_id") == file_id:
            picked = p
            break

    if picked is None:
        att = _att_from_file_id(file_id, default_type="image")
        if att is None:
            if pending:
                picked = pending[0]
            else:
                return _rebuild_card(state)
        else:
            picked = att.to_card_item()

    state.slots["source_image"] = picked
    state.slots["pending_images"] = []
    state.current_step = "collecting"
    return _rebuild_card(state)


def on_confirm(state: FlowState) -> dict:
    slots = state.slots
    src = slots.get("source_image")
    vids = slots.get("target_videos", []) or []

    if not src or not vids:
        return _as_card_event(_build_card(state))

    from app.skills.face_swap.service import face_swap_service

    try:
        result = face_swap_service.submit_group(
            user_id=state.user_id, session_id=state.session_id,
            source_image_file_id=src["file_id"],
            target_video_file_ids=[v["file_id"] for v in vids],
            prompt=slots.get("prompt", ""),
        )
    except Exception as e:
        logger.error(f"【FaceSwap】submit failed: {e}", exc_info=True)
        return _as_card_event({
            "card_id": state.message_id, "card_type": "error",
            "title": "提交失败", "description": str(e),
            "buttons": [{"key": "cancel", "label": "关闭"}],
        })

    slots["group_id"] = result["group_id"]
    slots["task_ids"] = result["task_ids"]
    state.current_step = "waiting"
    _new_card_id(state)
    flow_state_store.save(state)
    flow_state_store.set_active(FLOW_NAME, state.session_id, state.message_id)

    tasks = []
    for i, tid in enumerate(result["task_ids"]):
        tasks.append({
            "task_id": tid,
            "video_name": vids[i]["name"] if i < len(vids) else "",
            "status": "pending", "progress": 0,
        })

    return _as_card_event({
        "card_id": state.message_id, "card_type": "face_swap_task",
        "title": "换脸任务已提交",
        "description": f"共 {len(tasks)} 个视频，处理中…",
        "tasks": tasks,
        "poll_url_template": "/agent/v1/job/{task_id}",
        "video_url_template": "/agent/v1/job/{task_id}/video",
        "buttons": [{"key": "cancel", "label": "关闭"}],
    })


def on_cancel(state: FlowState) -> dict:
    flow_state_store.clear_active(FLOW_NAME, state.session_id)
    flow_state_store.delete(state.session_id, state.message_id)
    return {"type": "text", "content": "已取消换脸流程。"}


def on_update_prompt(state: FlowState, text: str) -> dict:
    state.slots["prompt"] = (text or "").strip()
    return _rebuild_card(state)


def on_remove_asset(state: FlowState, file_id: str) -> dict:
    if not file_id:
        return _as_card_event(_build_card(state))
    src = state.slots.get("source_image")
    if src and src.get("file_id") == file_id:
        state.slots["source_image"] = None
    else:
        state.slots["target_videos"] = [
            v for v in state.slots.get("target_videos", []) if v["file_id"] != file_id
        ]
    return _rebuild_card(state)


def on_clear_assets(state: FlowState) -> dict:
    state.slots["source_image"] = None
    state.slots["target_videos"] = []
    return _rebuild_card(state)


def on_add_video(state: FlowState) -> dict:
    return _rebuild_card(state)


def on_replace_face(state: FlowState) -> dict:
    state.slots["source_image"] = None
    return _rebuild_card(state)


def on_attach_source_image(state: FlowState, file_id: str) -> dict:
    att = _att_from_file_id(file_id, default_type="image")
    if att is None:
        return _rebuild_card(state)
    state.slots["source_image"] = att.to_card_item()
    return _rebuild_card(state)


def on_attach_target_video(state: FlowState, file_id: str) -> dict:
    att = _att_from_file_id(file_id, default_type="video")
    if att is None:
        return _rebuild_card(state)
    existing_ids = {v["file_id"] for v in state.slots.get("target_videos", [])}
    if file_id not in existing_ids:
        state.slots.setdefault("target_videos", []).append(att.to_card_item())
    return _rebuild_card(state)


def on_attach_source_images(state: FlowState, file_ids: str) -> dict:
    if not file_ids:
        return _rebuild_card(state)
    ids = [x.strip() for x in file_ids.split(",") if x.strip()]
    if not ids:
        return _rebuild_card(state)
    if len(ids) == 1:
        return on_attach_source_image(state, ids[0])

    items = []
    for fid in ids:
        att = _att_from_file_id(fid, default_type="image")
        if att:
            items.append(_att_to_item(att))

    if not items:
        return _rebuild_card(state)
    if len(items) == 1:
        state.slots["source_image"] = items[0]
        return _rebuild_card(state)

    state.slots["pending_images"] = items
    state.current_step = "pick_source_image"
    return _rebuild_card(state)


def on_attach_target_videos(state: FlowState, file_ids: str) -> dict:
    if not file_ids:
        return _rebuild_card(state)
    ids = [x.strip() for x in file_ids.split(",") if x.strip()]
    existing_ids = {v["file_id"] for v in state.slots.get("target_videos", [])}
    for fid in ids:
        if fid in existing_ids:
            continue
        att = _att_from_file_id(fid, default_type="video")
        if att:
            state.slots.setdefault("target_videos", []).append(_att_to_item(att))
            existing_ids.add(fid)
    return _rebuild_card(state)