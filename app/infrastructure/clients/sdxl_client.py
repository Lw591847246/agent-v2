"""SDXL 文生图客户端"""
import base64
import requests
from app.config.loader import config
from app.core.exceptions import SDXLException


class SDXLClient:

    def generate(self, prompt: str) -> dict:
        url = config.get("services.sdxl.base_url")
        try:
            resp = requests.post(
                f"{url}/generate",
                json={"prompt": prompt},
                timeout=config.get("services.sdxl.read_timeout", 600),
            )
            if resp.status_code != 200:
                raise SDXLException(f"SDXL 返回错误: {resp.text}")
            img_base64 = base64.b64encode(resp.content).decode("utf-8")
            return {
                "type": "image",
                "image_base64": img_base64,
                "prompt": prompt,
            }
        except requests.exceptions.ConnectionError:
            raise SDXLException("SDXL 服务未启动")


sdxl_client = SDXLClient()