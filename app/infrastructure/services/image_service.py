import base64
from typing import Optional
from app.infrastructure.clients.qwen_Image_client import qwen_image_client


class ImageService:

    def generate_text_to_image(
        self,
        prompt: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> dict:
        # None 时让 client 决定用默认尺寸，避免双份硬编码
        return qwen_image_client.generate(prompt, width=width, height=height)

    def generate_image_to_image(
        self,
        prompt: str,
        image_base64: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        denoise: float = 1,
    ) -> dict:
        try:
            image_bytes = base64.b64decode(image_base64)
        except Exception as e:
            raise ValueError(f"图片Base64解码失败: {e}")

        return qwen_image_client.generate_img2img(
            prompt=prompt,
            image_bytes=image_bytes,
            width=width,
            height=height,
            denoise=denoise,
        )


image_service = ImageService()