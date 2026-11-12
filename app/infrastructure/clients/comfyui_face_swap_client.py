"""ComfyUI 换脸客户端：上传素材 → 提交工作流 → 轮询 → 下载视频"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import time
from typing import Optional

import requests

from app.config.loader import config
from app.core.logger import logger


class FaceSwapException(Exception):
    pass


class ComfyUIFaceSwapClient:

    def __init__(self):
        self.base_url = (config.get("services.face_swap.base_url") or "").rstrip("/")
        self.workflow_path = config.get("services.face_swap.workflow_path")
        self.poll_interval = int(config.get("services.face_swap.poll_interval", 3))
        self.poll_timeout = int(config.get("services.face_swap.poll_timeout", 1800))
        self.frame_load_cap = int(config.get("services.face_swap.frame_load_cap", 60))
        self.custom_width = int(config.get("services.face_swap.custom_width", 512))
        self.custom_height = int(config.get("services.face_swap.custom_height", 512))
        self._template: Optional[dict] = None

    # ---------- workflow ----------

    def _load_workflow(self) -> dict:
        if self._template is not None:
            return self._template
        if not self.workflow_path:
            raise FaceSwapException("未配置 services.face_swap.workflow_path")
        if not os.path.isfile(self.workflow_path):
            raise FaceSwapException(f"工作流文件不存在: {self.workflow_path}")
        with open(self.workflow_path, "r", encoding="utf-8") as f:
            self._template = json.load(f)
        return self._template

    # ---------- video fix ----------

    @staticmethod
    def _has_audio(video_path: str) -> bool:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                 video_path],
                capture_output=True, text=True, timeout=15,
            )
            return bool(r.stdout.strip())
        except Exception as e:
            logger.warning(f"[FaceSwapClient] ffprobe 检测失败: {e}")
            return True     # 检测失败就当有，别阻断流程

    @staticmethod
    def _ensure_audio_bytes(video_bytes: bytes, filename: str) -> bytes:
        """视频没音频轨时，用 ffmpeg 补一条静音轨"""
        # 先落盘
        suffix = "." + (filename.rsplit(".", 1)[-1] if "." in filename else "mp4")
        tmp_in = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_in.write(video_bytes)
        tmp_in.close()

        try:
            if ComfyUIFaceSwapClient._has_audio(tmp_in.name):
                logger.info(f"[FaceSwapClient] video has audio, keep as-is")
                return video_bytes

            logger.info(f"[FaceSwapClient] video has NO audio, adding silent track ...")
            tmp_out = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name

            r = subprocess.run(
                ["ffmpeg", "-y",
                 "-i", tmp_in.name,
                 "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-shortest",
                 "-c:v", "copy",
                 "-c:a", "aac",
                 "-b:a", "64k",
                 tmp_out],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode != 0:
                logger.error(f"[FaceSwapClient] ffmpeg add audio failed: {r.stderr[:500]}")
                return video_bytes

            with open(tmp_out, "rb") as f:
                fixed = f.read()
            os.remove(tmp_out)
            logger.info(f"[FaceSwapClient] silent track added, {len(video_bytes)} -> {len(fixed)} bytes")
            return fixed

        finally:
            try:
                os.remove(tmp_in.name)
            except Exception:
                pass

    # ---------- upload ----------

    def upload_file(self, file_bytes: bytes, filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"

        # ⭐ 视频入口统一走一次音频预处理
        if ext in ("mp4", "mov", "avi", "mkv", "webm"):
            try:
                file_bytes = self._ensure_audio_bytes(file_bytes, filename)
            except Exception as e:
                logger.error(f"[FaceSwapClient] ensure audio failed: {e}")

        mime = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp", "bmp": "image/bmp",
            "mp4": "video/mp4", "mov": "video/quicktime",
            "avi": "video/x-msvideo", "mkv": "video/x-matroska",
            "webm": "video/webm",
        }.get(ext, "application/octet-stream")

        url = f"{self.base_url}/upload/image"
        files = {"image": (filename, file_bytes, mime)}
        data = {"type": "input", "overwrite": "true"}

        try:
            r = requests.post(url, files=files, data=data, timeout=120)
        except requests.exceptions.ConnectionError as e:
            raise FaceSwapException(f"ComfyUI 连接失败: {e}")
        if r.status_code != 200:
            raise FaceSwapException(f"上传失败 {r.status_code}: {r.text[:200]}")
        name = r.json().get("name")
        if not name:
            raise FaceSwapException(f"上传返回无 name: {r.text[:200]}")
        return name

    # ---------- submit ----------

    def submit(self, source_image_name: str, video_name: str, prefix: str) -> str:
        workflow = copy.deepcopy(self._load_workflow())

        try:
            workflow["1"]["inputs"]["image"] = source_image_name
            workflow["9"]["inputs"]["video"] = f"input/{video_name}"
            workflow["9"]["inputs"]["frame_load_cap"] = self.frame_load_cap
            if self.custom_width > 0:
                workflow["9"]["inputs"]["custom_width"] = self.custom_width
            if self.custom_height > 0:
                workflow["9"]["inputs"]["custom_height"] = self.custom_height
            workflow["4"]["inputs"]["filename_prefix"] = prefix
        except KeyError as e:
            raise FaceSwapException(f"工作流节点注入失败: {e}")

        client_id = str(time.time())
        try:
            r = requests.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
        except requests.exceptions.ConnectionError as e:
            raise FaceSwapException(f"ComfyUI 连接失败: {e}")
        if r.status_code != 200:
            raise FaceSwapException(f"提交任务失败 {r.status_code}: {r.text[:300]}")

        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            raise FaceSwapException(f"提交返回无 prompt_id: {r.text[:200]}")
        return prompt_id

    # ---------- wait ----------

    def wait_for(self, prompt_id: str) -> dict:
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=15)
            except Exception as e:
                logger.warning(f"[FaceSwapClient] poll error: {e}")
                time.sleep(self.poll_interval)
                continue

            if r.status_code == 200:
                history = r.json()
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})
                    if status.get("status_str") == "error":
                        raise FaceSwapException(f"ComfyUI 执行错误: {entry}")
                    outputs = entry.get("outputs", {})
                    for _node_id, node_out in outputs.items():
                        for key in ("gifs", "videos", "images"):
                            items = node_out.get(key) or []
                            if items:
                                return items[0]
            time.sleep(self.poll_interval)

        raise FaceSwapException(f"换脸超时 (>{self.poll_timeout}s)")

    # ---------- download ----------

    def download_output(self, info: dict) -> bytes:
        filename = info.get("filename")
        subfolder = info.get("subfolder", "")
        ftype = info.get("type", "output")
        if not filename:
            raise FaceSwapException(f"输出信息缺 filename: {info}")

        url = (
            f"{self.base_url}/view"
            f"?filename={filename}"
            f"&subfolder={subfolder}"
            f"&type={ftype}"
        )
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            raise FaceSwapException(f"下载失败 {r.status_code}")
        return r.content


comfyui_face_swap_client = ComfyUIFaceSwapClient()