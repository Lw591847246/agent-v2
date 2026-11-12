"""ComfyUI 文生视频客户端：提交工作流 → 轮询 → 下载视频

节点定位策略（双保险）：
  1. 优先从配置 services.text_to_video.nodes 读取节点 ID
  2. 配置缺失时，按 _meta.title 关键词自动探测
  3. 两者都失败时抛异常
"""
from __future__ import annotations

import copy
import json
import os
import time
from typing import Optional

import requests

from app.config.loader import config
from app.core.logger import logger


class TextToVideoException(Exception):
    pass


_NODE_TITLE_HINTS = {
    "positive_prompt": ["positive prompt", "positive", "正面", "正向", "正面提示词"],
    "negative_prompt": ["negative prompt", "negative", "负面", "反向", "负面提示词"],
    "sampler":         ["ksample", "k采样", "采样器", "ksampler"],
    "latent":          ["empty", "空latent", "空latent视频", "hunyuan", "空"],
}

_SAVE_CLASS_TYPES = {"SaveWEBM", "SaveAnimatedWEBP", "VHS_VideoCombine", "SaveVideo"}


class ComfyUITextToVideoClient:

    def __init__(self):
        base = config.get("services.text_to_video", {}) or {}
        self.base_url = (base.get("base_url") or "").rstrip("/")
        self.workflow_path = base.get("workflow_path")
        self.poll_interval = int(base.get("poll_interval", 5))
        self.poll_timeout = int(base.get("poll_timeout", 3600))

        # ⭐ 帧率 + 时长上限（供 Handler/Service 换算）
        self.fps = int(base.get("fps", 16))
        self.max_length = int(base.get("max_length", 160))

        self.default_width = int(base.get("default_width", 640))
        self.default_height = int(base.get("default_height", 360))
        self.default_length = int(base.get("default_length", 48))

        self.nodes: dict = base.get("nodes", {}) or {}

        presets = base.get("presets", {}) or {}
        if not presets:
            presets = {
                "standard": {
                    "width": self.default_width,
                    "height": self.default_height,
                    "length": self.default_length,
                    "steps": 25,
                    "cfg": 5.0,
                }
            }
        self.presets = presets
        self._template: Optional[dict] = None

    def _load_workflow(self) -> dict:
        if self._template is not None:
            return self._template
        if not self.workflow_path:
            raise TextToVideoException("未配置 services.text_to_video.workflow_path")
        if not os.path.isfile(self.workflow_path):
            raise TextToVideoException(f"工作流文件不存在: {self.workflow_path}")
        with open(self.workflow_path, "r", encoding="utf-8") as f:
            self._template = json.load(f)
        return self._template

    def _find_node_by_hint(self, workflow: dict, hints: list) -> Optional[str]:
        matched = []
        for node_id, node in workflow.items():
            title = (node.get("_meta", {}) or {}).get("title", "") or ""
            lower_title = title.lower()
            for h in hints:
                if h.lower() in lower_title:
                    matched.append(node_id)
                    break
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            logger.warning(
                f"[T2VClient] 关键词 {hints} 匹配到多个节点 {matched}，请手动在 YAML 配置"
            )
        return None

    def _resolve_node(self, workflow: dict, key: str) -> str:
        cfg_id = self.nodes.get(key)
        if cfg_id and str(cfg_id) in workflow:
            return str(cfg_id)
        hints = _NODE_TITLE_HINTS.get(key)
        if hints:
            detected = self._find_node_by_hint(workflow, hints)
            if detected:
                logger.info(f"[T2VClient] 自动探测节点 {key} -> {detected}")
                return detected
        raise TextToVideoException(
            f"无法定位节点 '{key}'，请在 application.yml 的 "
            f"services.text_to_video.nodes.{key} 手动指定"
        )

    def _resolve_save_nodes(self, workflow: dict) -> list:
        cfg_saves = self.nodes.get("save_nodes")
        if cfg_saves:
            if isinstance(cfg_saves, str):
                cfg_saves = [cfg_saves]
            valid = [str(x) for x in cfg_saves if str(x) in workflow]
            if valid:
                return valid
        found = []
        for node_id, node in workflow.items():
            if node.get("class_type", "") in _SAVE_CLASS_TYPES:
                found.append(node_id)
        if found:
            logger.info(f"[T2VClient] 自动探测保存节点 -> {found}")
            return found
        raise TextToVideoException(
            "未找到任何保存节点，请在 YAML 的 nodes.save_nodes 手动指定"
        )

    def submit(
        self,
        prompt: str,
        negative_prompt: str = "",
        preset: str = "standard",
        seed: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        length: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        prefix: str = "t2v",
    ) -> str:
        p = self.presets.get(preset)
        if not p:
            logger.warning(f"[T2VClient] 未知档位 {preset}，回退 standard")
            p = self.presets.get("standard") or self.presets[list(self.presets)[0]]

        final_w = width if width is not None else int(p.get("width", self.default_width))
        final_h = height if height is not None else int(p.get("height", self.default_height))
        final_l = length if length is not None else int(p.get("length", self.default_length))
        final_steps = steps if steps is not None else p.get("steps")
        final_cfg = cfg if cfg is not None else p.get("cfg")

        logger.info(
            f"[T2VClient] submit preset={preset} "
            f"{final_w}x{final_h}x{final_l} steps={final_steps} cfg={final_cfg}"
        )

        workflow = copy.deepcopy(self._load_workflow())

        pos_id = self._resolve_node(workflow, "positive_prompt")
        neg_id = self._resolve_node(workflow, "negative_prompt")
        sampler_id = self._resolve_node(workflow, "sampler")
        latent_id = self._resolve_node(workflow, "latent")
        save_ids = self._resolve_save_nodes(workflow)

        try:
            workflow[pos_id]["inputs"]["text"] = prompt
            if negative_prompt:
                workflow[neg_id]["inputs"]["text"] = negative_prompt

            latent_inputs = workflow[latent_id]["inputs"]
            if "width" in latent_inputs:
                latent_inputs["width"] = final_w
            if "height" in latent_inputs:
                latent_inputs["height"] = final_h
            if "length" in latent_inputs:
                latent_inputs["length"] = final_l

            sampler_inputs = workflow[sampler_id]["inputs"]
            if seed is not None and "seed" in sampler_inputs:
                sampler_inputs["seed"] = seed
            if final_steps is not None and "steps" in sampler_inputs:
                sampler_inputs["steps"] = final_steps
            if final_cfg is not None and "cfg" in sampler_inputs:
                sampler_inputs["cfg"] = final_cfg

            for sid in save_ids:
                if "filename_prefix" in workflow[sid].get("inputs", {}):
                    workflow[sid]["inputs"]["filename_prefix"] = prefix

        except KeyError as e:
            raise TextToVideoException(f"工作流参数注入失败: {e}")

        client_id = str(time.time())
        try:
            r = requests.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
        except requests.exceptions.ConnectionError as e:
            raise TextToVideoException(f"ComfyUI 连接失败: {e}")
        if r.status_code != 200:
            raise TextToVideoException(f"提交任务失败 {r.status_code}: {r.text[:300]}")

        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            raise TextToVideoException(f"提交返回无 prompt_id: {r.text[:200]}")
        return prompt_id

    def wait_for(self, prompt_id: str) -> dict:
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=15)
            except Exception as e:
                logger.warning(f"[T2VClient] poll error: {e}")
                time.sleep(self.poll_interval)
                continue

            if r.status_code == 200:
                history = r.json()
                if prompt_id in history:
                    entry = history[prompt_id]
                    status = entry.get("status", {})
                    if status.get("status_str") == "error":
                        raise TextToVideoException(f"ComfyUI 执行错误: {entry}")
                    outputs = entry.get("outputs", {})
                    for _node_id, node_out in outputs.items():
                        for key in ("videos", "gifs", "images"):
                            items = node_out.get(key) or []
                            if items:
                                return items[0]
            time.sleep(self.poll_interval)

        raise TextToVideoException(f"文生视频超时 (>{self.poll_timeout}s)")

    def download_output(self, info: dict) -> bytes:
        filename = info.get("filename")
        subfolder = info.get("subfolder", "")
        ftype = info.get("type", "output")
        if not filename:
            raise TextToVideoException(f"输出信息缺 filename: {info}")

        url = (
            f"{self.base_url}/view"
            f"?filename={filename}"
            f"&subfolder={subfolder}"
            f"&type={ftype}"
        )
        r = requests.get(url, timeout=300)
        if r.status_code != 200:
            raise TextToVideoException(f"下载失败 {r.status_code}")
        return r.content


comfyui_t2v_client = ComfyUITextToVideoClient()