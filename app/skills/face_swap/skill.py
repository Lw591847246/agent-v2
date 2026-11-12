# app/skills/face_swap/skill.py
"""FaceSwap Skill：换脸多轮 Flow。"""
from __future__ import annotations

import json

from app.context.flow_state_store import flow_state_store
from app.contracts.action import ActionRequest
from app.contracts.result import Result, card_part, text_part
from app.contracts.skill import BaseSkill
from app.core.logger import logger
from app.skills.face_swap import flow as fs_flow


def _flow_result_to_skill_result(flow_result: dict) -> Result:
    """把 flow 返回的 {type, card_json} / {type, content} 转成 V2 Result。"""
    if not isinstance(flow_result, dict):
        return Result(parts=[text_part(str(flow_result))])

    rtype = flow_result.get("type", "")
    if rtype == "card":
        try:
            card_data = json.loads(flow_result.get("card_json", "{}"))
        except Exception:
            return Result(parts=[text_part("[卡片解析失败]")])
        card_type = card_data.get("card_type", "unknown")
        status = "processing" if card_type in ("face_swap_task",) else "done"
        return Result(parts=[card_part(card_type, card_data, status=status)])
    if rtype == "text":
        return Result(parts=[text_part(flow_result.get("content", ""))])
    return Result(parts=[text_part(str(flow_result))])


class FaceSwapSkill(BaseSkill):
    name = "face_swap"
    description = (
        "视频换脸（Flow 多轮交互：上传素材 → 确认 → 生成）。"
        "适用于：'换脸/替换人脸/把XX换成YY'（有视频或图片附件）。"
        "不适用于：图生视频（用 image_to_video）、数字人（用 custom_avatar）。"
    )
    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "换脸提示词，可空"},
            "user_text": {"type": "string", "description": "用户原始输入，可空"},
            "history": {"type": "array", "description": "历史对话，可空"},
        },
        "required": [],
    }
    intent = "face_swap"
    category = "video"
    sync = True
    returns = ["card", "text"]
    requires_attachments = []
    produces_outputs = False
    visible_to_l2 = True
    visible_to_planner = True

    def execute(
        self, prompt: str = "", user_text: str = "",
        history: list = None, session_id: str = "",
        user_id: int = 0, **kwargs,
    ) -> Result:
        # 优先从 flow_state_store 拿 active state
        active = flow_state_store.get_active(fs_flow.FLOW_NAME, session_id)
        if active:
            logger.info(f"【FaceSwap】继续已有 flow session={session_id}")
            flow_result = fs_flow.continue_from_chat(
                active, text=prompt or user_text or "",
                attachments=kwargs.get("attachments_obj") or [],
            )
            return _flow_result_to_skill_result(flow_result)

        # 构建一个轻量 ctx 给 flow.start
        class _Ctx:
            pass
        ctx = _Ctx()
        ctx.session_id = session_id
        ctx.user_id = user_id
        ctx.text = prompt or user_text or ""
        ctx.attachments = kwargs.get("attachments_obj") or []

        logger.info(f"【FaceSwap】启动新 flow session={session_id} atts={len(ctx.attachments)}")
        flow_result = fs_flow.start(ctx)
        return _flow_result_to_skill_result(flow_result)

    def on_action(self, action: ActionRequest) -> Result:
        state = flow_state_store.load(action.session_id, action.card_id)
        if not state:
            logger.warning(f"【FaceSwap】action state not found {action.session_id}/{action.card_id}")
            return Result(parts=[text_part("该操作已过期，请重新发起")])

        card_type = action.card_type or ""
        option_key = action.option_key or ""
        text = action.text or ""
        file_id = action.file_id or ""

        logger.info(
            f"【FaceSwap】action flow={state.flow_name} step={state.current_step} "
            f"card_type={card_type} option_key={option_key}"
        )

        try:
            if option_key == "cancel":
                flow_result = fs_flow.on_cancel(state)
            elif option_key == "confirm":
                flow_result = fs_flow.on_confirm(state)
            elif option_key == "update_prompt":
                flow_result = fs_flow.on_update_prompt(state, text=text)
            elif option_key == "remove_asset":
                flow_result = fs_flow.on_remove_asset(state, file_id=file_id)
            elif option_key == "clear_assets":
                flow_result = fs_flow.on_clear_assets(state)
            elif option_key == "add_video":
                flow_result = fs_flow.on_add_video(state)
            elif option_key == "replace_face":
                flow_result = fs_flow.on_replace_face(state)
            elif option_key == "attach_source_image":
                flow_result = fs_flow.on_attach_source_image(state, file_id=file_id)
            elif option_key == "attach_source_images":
                flow_result = fs_flow.on_attach_source_images(state, file_ids=file_id)
            elif option_key == "attach_target_video":
                flow_result = fs_flow.on_attach_target_video(state, file_id=file_id)
            elif option_key == "attach_target_videos":
                flow_result = fs_flow.on_attach_target_videos(state, file_ids=file_id)
            elif option_key == "pick_source_image":
                flow_result = fs_flow.on_pick_source_image(state, file_id=file_id)
            elif option_key in ("upload_source_image", "upload_target_video"):
                flow_result = fs_flow._rebuild_card(state)
            else:
                logger.warning(f"【FaceSwap】未处理 option_key={option_key}")
                return Result(parts=[text_part(f"无法识别操作：{option_key}")])
        except Exception as e:
            logger.error(f"【FaceSwap】action 执行失败: {e}", exc_info=True)
            return Result(parts=[text_part(f"操作失败：{e}")])

        return _flow_result_to_skill_result(flow_result)


skill = FaceSwapSkill()