import os
import json
import copy
import time
import base64
import requests
import logging
from typing import Optional, Tuple   # ⭐ 必须补上，否则 Optional 未定义

from app.config.loader import config
from app.core.logger import logger   # ⭐ 关键：用项目的统一 logger


class QwenImageException(Exception):
    """自定义异常，您也可以替换成项目里统一的异常类"""
    pass


class QwenImageClient:
    def __init__(self):
        # ComfyUI 服务的地址
        self.base_url = config.get("services.qwen_image.base_url")

        # 从配置中读取两个工作流的路径
        self.text_2_image_path = config.get("services.qwen_image.text_2_image_path")
        self.img_2_img_path = config.get("services.qwen_image.image_2_image_path")

        # 优雅地预加载两个工作流模板
        self.workflow_template = self._load_workflow(self.text_2_image_path)
        self.img2img_workflow_template = self._load_workflow(self.img_2_img_path)

    def _load_workflow(self, workflow_path: str) -> dict | None:
        """公共方法：加载工作流 JSON 文件"""
        if not workflow_path:
            logger.info(f"[Qwen-Image] 工作流路径未配置")
            return None

        # ★ 相对路径基于项目根解析（避免 cwd 不对）
        if not os.path.isabs(workflow_path):
            from pathlib import Path
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            workflow_path = str(project_root / workflow_path)

        try:
            with open(workflow_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.info(f"[Qwen-Image] 加载工作流失败 {workflow_path}: {e}")
            return None

    def generate(
            self,
            prompt: str,
            width: Optional[int] = None,
            height: Optional[int] = None,
    ) -> dict:
        if not self.workflow_template:
            raise QwenImageException("ComfyUI 工作流模板未加载")

        # ⭐ None → 默认值
        if width is None:
            width = 768
        if height is None:
            height = 1024

        workflow = copy.deepcopy(self.workflow_template)
        # 尺寸安全限制（8G 显存保护）
        width, height = self._clamp_size(width, height)

        # ⭐ 小尺寸警告（Qwen 在 <384 时效果会明显下降）
        if min(width, height) < 384:
            logger.warning(f"【文生图】尺寸 {width}x{height} 偏小，Qwen 效果可能不佳")

        try:
            workflow["459:452"]["inputs"]["prompt"] = prompt
            workflow["459:452"]["inputs"]["resolution"] = 0
            workflow["459:456"]["inputs"]["width"] = width
            workflow["459:456"]["inputs"]["height"] = height
            workflow["459:458"]["inputs"]["denoise"] = 1.0
            workflow["459:458"]["inputs"]["seed"] = int(time.time() * 1000) % 2147483647

            if "459:468" in workflow:
                workflow["459:468"]["inputs"]["switch"] = True
        except KeyError as e:
            raise QwenImageException(f"文生图节点 ID 匹配失败: {e}")

        logger.info(f"【文生图】尺寸={width}x{height}, prompt={prompt[:40]}")
        return self._submit_and_poll(workflow, prompt, mode="文生图")


    # 新增：上传图片到 ComfyUI
    def upload_image(self, image_bytes: bytes, filename: str = None) -> str:
        """上传图片到 ComfyUI，自动识别真实格式"""
        # 动态检测真实图片格式
        actual_ext = "png"
        mime_type = "image/png"
        if image_bytes[:2] == b'\xff\xd8':
            actual_ext, mime_type = "jpg", "image/jpeg"
        elif image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            actual_ext, mime_type = "png", "image/png"
        elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
            actual_ext, mime_type = "gif", "image/gif"
        elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            actual_ext, mime_type = "webp", "image/webp"
        else:
            raise QwenImageException(f"无法识别的图片格式")

        # 生成唯一文件名
        if not filename:
            filename = f"input_{int(time.time() * 1000)}.{actual_ext}"
        else:
            base_name = filename.rsplit('.', 1)[0] if '.' in filename else filename
            filename = f"{base_name}_{int(time.time() * 1000)}.{actual_ext}"

        # 上传到 ComfyUI
        url = f"{self.base_url.rstrip('/')}/upload/image"
        files = {'image': (filename, image_bytes, mime_type)}
        data = {'type': 'input', 'overwrite': 'true'}
        resp = requests.post(url, files=files, data=data, timeout=30)
        if resp.status_code != 200:
            raise QwenImageException(f"图片上传失败: {resp.text}")

        return resp.json().get("name")

    # 新增：图生图方法
    def generate_img2img(self, prompt: str, image_bytes: bytes, width=None, height=None, denoise=1.0):
        if not self.img2img_workflow_template:
            raise QwenImageException("图生图工作流模板未加载")

        # 缩放到合理尺寸
        from PIL import Image
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes))
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        orig_w, orig_h = img.size

        if width and height:
            new_w, new_h = width, height
        else:
            MAX_SIDE = 768
            if max(orig_w, orig_h) > MAX_SIDE:
                if orig_w >= orig_h:
                    new_w, new_h = MAX_SIDE, int(orig_h * MAX_SIDE / orig_w)
                else:
                    new_h, new_w = MAX_SIDE, int(orig_w * MAX_SIDE / orig_h)
            else:
                new_w, new_h = orig_w, orig_h

        new_w = max((new_w // 8) * 8, 8)
        new_h = max((new_h // 8) * 8, 8)

        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buffer = BytesIO()
        resized.save(buffer, format="JPEG", quality=92)
        resized_bytes = buffer.getvalue()

        # 上传
        uploaded_name = self.upload_image(resized_bytes)

        # 注入参数
        workflow = copy.deepcopy(self.img2img_workflow_template)
        try:
            workflow["470"]["inputs"]["image"] = uploaded_name
            workflow["459:474"]["inputs"]["prompt"] = prompt
            workflow["459:474"]["inputs"]["resolution"] = 0
            workflow["459:458"]["inputs"]["denoise"] = denoise
            workflow["459:458"]["inputs"]["seed"] = int(time.time() * 1000) % 2147483647
            workflow["459:468"]["inputs"]["switch"] = False
            workflow["459:456"]["inputs"]["width"] = new_w
            workflow["459:456"]["inputs"]["height"] = new_h
        except KeyError as e:
            raise QwenImageException(f"图生图节点 ID 匹配失败: {e}")

        # ⭐ 唯一保留的 INFO 日志：一行概览
        logger.info(f"【图生图】原图{orig_w}x{orig_h}→{new_w}x{new_h}, denoise={denoise}, prompt={prompt[:30]}...")
        return self._submit_and_poll(workflow, prompt, mode="图生图")

    def _clamp_size(self, width: int, height: int) -> Tuple[int, int]:
        """尺寸安全限制，保护 8G 显存"""
        MAX_SIDE = 1024
        MAX_PIXELS = 768 * 1024
        MIN_SIDE = 256  # ⭐ Qwen-Image 2.1 最低支持 256

        # ⭐ 最小值保护
        width = max(width, MIN_SIDE)
        height = max(height, MIN_SIDE)

        # 长边限制
        if max(width, height) > MAX_SIDE:
            scale = MAX_SIDE / max(width, height)
            width = int(width * scale)
            height = int(height * scale)

        # 总像素限制
        if width * height > MAX_PIXELS:
            scale = (MAX_PIXELS / (width * height)) ** 0.5
            width = int(width * scale)
            height = int(height * scale)

        # 8 的倍数
        return (max(width // 8, 8) * 8, max(height // 8, 8) * 8)


    # 将原来的提交和轮询逻辑提取成公共方法
    def _submit_and_poll(self, workflow: dict, prompt: str, mode: str = "图生图") -> dict:
        try:
            client_id = str(time.time())
            resp = requests.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                timeout=30,
            )
            if resp.status_code != 200:
                raise QwenImageException(f"ComfyUI 接收任务失败: {resp.text}")
            prompt_id = resp.json()["prompt_id"]
        except requests.exceptions.ConnectionError:
            raise QwenImageException("ComfyUI 服务未启动")

        logger.info(f"【{mode}】任务已提交, ID={prompt_id}")
        start_time = time.time()
        while time.time() - start_time < 1200:
            try:
                history_resp = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=10)
                if history_resp.status_code == 200:
                    history = history_resp.json()
                    if prompt_id in history:
                        outputs = history[prompt_id]["outputs"]
                        for node_id in outputs:
                            if "images" in outputs[node_id]:
                                img_info = outputs[node_id]["images"][0]
                                filename = img_info["filename"]
                                subfolder = img_info.get("subfolder", "")
                                img_type = img_info.get("type", "output")
                                img_url = f"{self.base_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}"
                                img_resp = requests.get(img_url, timeout=30)
                                if img_resp.status_code == 200:
                                    img_base64 = base64.b64encode(img_resp.content).decode("utf-8")
                                    elapsed = time.time() - start_time
                                    logger.info(
                                        f"【{mode}】✅ 完成, 耗时={elapsed:.1f}s, 图片={len(img_resp.content) / 1024:.0f}KB"
                                    )
                                    return {
                                        "type": "image",
                                        "image_base64": img_base64,
                                        "prompt": prompt,
                                    }
            except Exception:
                pass
            time.sleep(2)

        raise QwenImageException("生成超时 (超过 20 分钟)")

# 单例模式暴露
qwen_image_client = QwenImageClient()
