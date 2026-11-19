"""ComfyUI 文生视频客户端：提交工作流 → 轮询 → 下载视频"""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Optional

import requests

from app.config.loader import config
from app.core.logger import logger


# ⭐ 项目根绝对路径，不依赖 cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
logger.info(f"[T2VClient] 项目根 = {_PROJECT_ROOT}")


def _resolve_path(p: str) -> str:
    """把相对路径统一解析到项目根下。"""
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    return str(_PROJECT_ROOT / p.lstrip("./"))


class TextToVideoException(Exception):
    pass


_NODE_TITLE_HINTS = {
    "positive_prompt": ["positive prompt", "positive", "正面", "正向"],
    "negative_prompt": ["negative prompt", "negative", "负面", "反向"],
    "sampler": ["ksample", "k采样", "采样器", "ksampler"],
    "latent": ["empty", "空latent", "空latent视频", "hunyuan", "空"],
}

_SAVE_CLASS_TYPES = {"SaveWEBM", "SaveAnimatedWEBP", "VHS_VideoCombine", "SaveVideo"}


class ComfyUITextToVideoClient:

    def __init__(self):
        base = config.get("services.text_to_video", {}) or {}
        self.base_url = (base.get("base_url") or "").rstrip("/")

        # ⭐ 关键：把 workflow_path 解析成绝对路径
        raw_path = base.get("workflow_path", "")
        self.workflow_path = _resolve_path(raw_path)
        logger.info(f"[T2VClient] workflow_path 原始值 = {raw_path!r}")
        logger.info(f"[T2VClient] workflow_path 解析后 = {self.workflow_path!r}")
        logger.info(f"[T2VClient] 文件存在 = {os.path.isfile(self.workflow_path)}")

        self.poll_interval = int(base.get("poll_interval", 5))
        self.poll_timeout = int(base.get("poll_timeout", 3600))
        self.fps = int(base.get("fps", 16))
        self.max_length = int(base.get("max_length", 160))

        self.default_width = int(base.get("default_width", 640))
        self.default_height = int(base.get("default_height", 360))
        self.default_length = int(base.get("default_length", 48))

        # ⭐ 分层超时：connect 秒级失败、read 维持原值；均可在 application.yml 覆盖
        self.connect_timeout = float(base.get("connect_timeout", 5))
        self.submit_timeout = float(base.get("submit_timeout", 30))
        self.poll_read_timeout = float(base.get("poll_read_timeout", 15))
        self.download_connect_timeout = float(base.get("download_connect_timeout", 10))
        self.download_read_timeout = float(base.get("download_read_timeout", 300))
        # ⭐ 轮询连续连接/读失败达到该次数即提前判定任务失败，不再空转到 poll_timeout
        self.poll_max_connect_failures = int(base.get("poll_max_connect_failures", 5))
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
            logger.error(
                f"[T2VClient] ❌ 工作流文件不存在: {self.workflow_path!r}\n"
                f"  cwd = {os.getcwd()!r}\n"
                f"  目录 = {os.path.dirname(self.workflow_path)!r}"
            )
            raise TextToVideoException(f"工作流文件不存在: {self.workflow_path}")
        with open(self.workflow_path, "r", encoding="utf-8") as f:
            self._template = json.load(f)
        logger.info(f"[T2VClient] ✅ 工作流加载成功: {self.workflow_path}")
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
            logger.warning(f"[T2VClient] 关键字 {hints} 匹配到多个节点 {matched}，请手动配置")
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
        raise TextToVideoException("未找到任何保存节点，请在 YAML 中手动指定 nodes.save_nodes")

    def submit(
        self, prompt: str, negative_prompt: str = "",
        preset: str = "standard", seed: Optional[int] = None,
        width: Optional[int] = None, height: Optional[int] = None,
        length: Optional[int] = None, steps: Optional[int] = None,
        cfg: Optional[float] = None, prefix: str = "t2v",
    ) -> str:
        p = self.presets.get(preset)
        if not p:
            p = self.presets.get("standard") or list(self.presets.values())[0]

        final_w = width if width is not None else int(p.get("width", self.default_width))
        final_h = height if height is not None else int(p.get("height", self.default_height))
        final_l = length if length is not None else int(p.get("length", self.default_length))
        final_steps = steps if steps is not None else p.get("steps")
        final_cfg = cfg if cfg is not None else p.get("cfg")

        logger.info(f"[T2VClient] submit preset={preset} {final_w}x{final_h}x{final_l}")

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
                timeout=(self.connect_timeout, self.submit_timeout),
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
        # ⭐ 连续(连接/读)失败计数：每收到一次 HTTP 响应(含非 200)即清零；
        #    连续达到 poll_max_connect_failures 次 -> 判定 ComfyUI 已挂，提前抛错，
        #    不再像旧逻辑吞掉异常 sleep 重试，一路空转到 poll_timeout(默认 3600s)。
        consec_fail = 0
        max_fail = max(1, self.poll_max_connect_failures)
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/history/{prompt_id}",
                                 timeout=(self.connect_timeout, self.poll_read_timeout))
                consec_fail = 0  # 收到响应 = 服务仍存活，清零
                if r.status_code == 200:
                    history = r.json()
                    if prompt_id in history:
                        entry = history[prompt_id]
                        status = entry.get("status", {})
                        if status.get("status_str") == "error":
                            raise TextToVideoException(f"执行错误: {entry}")
                        for _node_id, node_out in entry.get("outputs", {}).items():
                            for key in ("videos", "gifs", "images"):
                                items = node_out.get(key) or []
                                if items:
                                    return items[0]
            except TextToVideoException:
                raise
            except Exception as e:
                consec_fail += 1
                logger.warning(f"[T2VClient] poll error {consec_fail}/{max_fail}: {e}")
                if consec_fail >= max_fail:
                    raise TextToVideoException(
                        f"ComfyUI 连续 {consec_fail} 次轮询失败，提前判定任务失败: {e}")
            time.sleep(self.poll_interval)
        raise TextToVideoException(f"轮询超时 (>{self.poll_timeout}s)")

    def download_output(self, info: dict) -> bytes:
        filename = info.get("filename")
        subfolder = info.get("subfolder", "")
        ftype = info.get("type", "output")
        if not filename:
            raise TextToVideoException(f"输出信息缺 filename: {info}")
        url = (
            f"{self.base_url}/view"
            f"?filename={filename}&subfolder={subfolder}&type={ftype}"
        )
        r = requests.get(url, timeout=(self.download_connect_timeout, self.download_read_timeout))
        if r.status_code != 200:
            raise TextToVideoException(f"下载失败 {r.status_code}")
        return r.content


comfyui_t2v_client = ComfyUITextToVideoClient()