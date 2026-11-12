"""ComfyUI 图生视频客户端：上传图片 → 提交工作流 → 轮询 → 下载"""
from __future__ import annotations

import copy
import io
import json
import os
import time
from typing import Optional, Tuple

import requests
from PIL import Image

from app.config.loader import config
from app.core.logger import logger


class ImageToVideoException(Exception):
    pass


class ComfyUIImageToVideoClient:

    def __init__(self):
        base = config.get("services.image_to_video", {}) or {}
        self.base_url = (base.get("base_url") or "").rstrip("/")
        self.workflow_path = base.get("workflow_path")
        self.poll_interval = int(base.get("poll_interval", 5))
        self.poll_timeout = int(base.get("poll_timeout", 3600))
        self.fps = int(base.get("fps", 16))
        self.max_length = int(base.get("max_length", 97))
        self.default_length = int(base.get("default_length", 49))
        self.nodes = base.get("nodes", {}) or {}
        self.presets = base.get("presets", {}) or {}

        self._template: Optional[dict] = None

    # ---------- workflow ----------

    def _load_workflow(self) -> dict:
        if self._template is not None:
            return self._template
        if not self.workflow_path or not os.path.isfile(self.workflow_path):
            raise ImageToVideoException(f"工作流不存在: {self.workflow_path}")
        with open(self.workflow_path, "r", encoding="utf-8") as f:
            self._template = json.load(f)
        return self._template

    # ---------- 尺寸计算（核心） ----------

    @staticmethod
    def _compute_video_size(img_w: int, img_h: int, max_side: int) -> Tuple[int, int]:
        """
        按原图比例等比缩放到 max_side 以内，结果必须是 32 的倍数。
        原图小 → 保持原尺寸（但仍 32 对齐）。
        """
        if img_w <= 0 or img_h <= 0:
            return 512, 512

        if max(img_w, img_h) <= max_side:
            target_w, target_h = img_w, img_h
        elif img_w >= img_h:
            target_w = max_side
            target_h = int(img_h * max_side / img_w)
        else:
            target_h = max_side
            target_w = int(img_w * max_side / img_h)

        # 对齐 32 倍数（Wan 要求 32 的倍数）
        target_w = max((target_w // 32) * 32, 256)
        target_h = max((target_h // 32) * 32, 256)
        return target_w, target_h

    # ---------- 上传图片 ----------

    def upload_image(self, image_bytes: bytes, filename: str = None) -> str:
        # 检测格式
        ext, mime = "png", "image/png"
        if image_bytes[:2] == b"\xff\xd8":
            ext, mime = "jpg", "image/jpeg"
        elif image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            ext, mime = "png", "image/png"
        elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            ext, mime = "webp", "image/webp"

        if not filename:
            filename = f"i2v_{int(time.time() * 1000)}.{ext}"
        else:
            base = filename.rsplit(".", 1)[0] if "." in filename else filename
            filename = f"{base}_{int(time.time() * 1000)}.{ext}"

        url = f"{self.base_url}/upload/image"
        files = {"image": (filename, image_bytes, mime)}
        data = {"type": "input", "overwrite": "true"}
        r = requests.post(url, files=files, data=data, timeout=60)
        if r.status_code != 200:
            raise ImageToVideoException(f"图片上传失败: {r.text[:200]}")
        name = r.json().get("name")
        if not name:
            raise ImageToVideoException("上传返回无 name")
        return name

    # ---------- 提交 ----------

    def submit(
        self,
        prompt: str,
        image_bytes: bytes,
        preset: str = "standard",
        length: Optional[int] = None,
        negative_prompt: str = "",
        seed: Optional[int] = None,
    ) -> str:
        logger.info(f"[I2VClient] 开始准备提交任务, preset={preset}, length={length}")
        # 1) 读原图尺寸
        try:
            img = Image.open(io.BytesIO(image_bytes))
            orig_w, orig_h = img.size
        except Exception as e:
            raise ImageToVideoException(f"读取图片失败: {e}")

        # 2) 取 preset
        p = self.presets.get(preset) or self.presets.get("standard") \
            or list(self.presets.values())[0]
        max_side = int(p.get("max_side", 512))
        steps = int(p.get("steps", 20))
        cfg = float(p.get("cfg", 7.5))

        # 3) 算目标尺寸
        target_w, target_h = self._compute_video_size(orig_w, orig_h, max_side)

        # 4) 算帧数
        final_length = length if length else self.default_length
        # Wan 要求 length = 4n + 1
        final_length = int((final_length - 1) // 4) * 4 + 1
        final_length = max(33, min(final_length, self.max_length))

        # 5) 上传图片
        comfy_name = self.upload_image(image_bytes)
        logger.info(f"[I2VClient] 图片上传成功, comfy_name={comfy_name}")
        # 6) 注入工作流
        workflow = copy.deepcopy(self._load_workflow())
        n = self.nodes
        try:
            # 图片节点
            workflow[n["start_image"]]["inputs"]["image"] = comfy_name

            # 提示词
            workflow[n["positive_prompt"]]["inputs"]["text"] = prompt
            if negative_prompt:
                workflow[n["negative_prompt"]]["inputs"]["text"] = negative_prompt

            # WanImageToVideo 尺寸
            latent_inputs = workflow[n["latent"]]["inputs"]
            latent_inputs["width"] = target_w
            latent_inputs["height"] = target_h
            latent_inputs["length"] = final_length
            latent_inputs["batch_size"] = 1

            # 采样器
            sampler_inputs = workflow[n["sampler"]]["inputs"]
            sampler_inputs["steps"] = steps
            sampler_inputs["cfg"] = cfg
            sampler_inputs["seed"] = seed if seed is not None else \
                int(time.time() * 1000) % 2147483647

            # 保存节点前缀
            for sid in n.get("save_nodes", []):
                if "filename_prefix" in workflow[sid].get("inputs", {}):
                    workflow[sid]["inputs"]["filename_prefix"] = "i2v"

        except KeyError as e:
            raise ImageToVideoException(f"节点注入失败: {e}")

        logger.info(
            f"[I2VClient] submit preset={preset} "
            f"原图 {orig_w}x{orig_h} → 目标 {target_w}x{target_h} "
            f"length={final_length} steps={steps} cfg={cfg}"
        )
        logger.info(f"[I2VClient] 工作流准备完毕，开始 POST /prompt 提交...")
        # 7) 提交
        client_id = str(time.time())
        r = requests.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=30,
        )
        logger.info(f"[I2VClient] 提交接口返回, HTTP状态码={r.status_code}")
        if r.status_code != 200:
            raise ImageToVideoException(f"提交失败 {r.status_code}: {r.text[:300]}")
        pid = r.json().get("prompt_id")
        if not pid:
            raise ImageToVideoException(f"无 prompt_id: {r.text[:200]}")
        logger.info(f"[I2VClient] 提交成功，拿到 prompt_id={pid}")
        return pid

    # ---------- 轮询 / 下载 ----------

    def wait_for(self, prompt_id: str) -> dict:
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=15)
                if r.status_code == 200:
                    history = r.json()
                    if prompt_id in history:
                        entry = history[prompt_id]
                        status = entry.get("status", {})
                        if status.get("status_str") == "error":
                            raise ImageToVideoException(f"执行错误: {entry}")
                        for _nid, out in entry.get("outputs", {}).items():
                            for key in ("videos", "gifs", "images"):
                                items = out.get(key) or []
                                if items:
                                    return items[0]
            except ImageToVideoException:
                raise
            except Exception as e:
                logger.warning(f"[I2VClient] poll error: {e}")
            time.sleep(self.poll_interval)
        raise ImageToVideoException(f"超时 (>{self.poll_timeout}s)")

    def download_output(self, info: dict) -> bytes:
        filename = info.get("filename")
        subfolder = info.get("subfolder", "")
        ftype = info.get("type", "output")
        if not filename:
            raise ImageToVideoException(f"缺 filename: {info}")
        url = (f"{self.base_url}/view?filename={filename}"
               f"&subfolder={subfolder}&type={ftype}")
        r = requests.get(url, timeout=300)
        if r.status_code != 200:
            raise ImageToVideoException(f"下载失败 {r.status_code}")
        return r.content


comfyui_i2v_client = ComfyUIImageToVideoClient()