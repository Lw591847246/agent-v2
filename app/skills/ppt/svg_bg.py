# app/skills/ppt/svg_bg.py
"""PPT 背景生成（Pillow）。"""
import os
import uuid

from PIL import Image, ImageDraw, ImageFilter

from app.core.logger import logger


class PillowBackgroundBuilder:

    def __init__(self, output_dir="./data/ppt_assets"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _hex_to_rgb(self, hex_str: str, default: str = "#4D6BFE"):
        h = hex_str.lstrip('#') if hex_str else default.lstrip('#')
        if len(h) != 6:
            h = default.lstrip('#')
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return (77, 107, 254)

    def generate(self, prompt: str, theme_colors: dict, bg_style: str = "minimal") -> str:
        primary = self._hex_to_rgb(theme_colors.get("primary", "#4D6BFE"))
        bg = self._hex_to_rgb(theme_colors.get("bg", "#FFFFFF"))

        width, height = 1920, 1080
        filename = f"bg_{uuid.uuid4().hex[:8]}.png"
        filepath = os.path.join(self.output_dir, filename)

        try:
            img = Image.new("RGB", (width, height), bg)

            glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            glow_draw = ImageDraw.Draw(glow)
            glow_draw.ellipse((-300, -300, 800, 800), fill=(primary[0], primary[1], primary[2], 80))
            glow_draw.ellipse((width - 500, height - 500, width + 200, height + 200),
                              fill=(primary[0], primary[1], primary[2], 60))
            glow = glow.filter(ImageFilter.GaussianBlur(150))
            img.paste(glow, (0, 0), glow)

            if bg_style == "tech":
                draw = ImageDraw.Draw(img, 'RGBA')
                grid_color = (primary[0], primary[1], primary[2], 15)
                for x in range(0, width, 80):
                    draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
                for y in range(0, height, 80):
                    draw.line([(0, y), (width, y)], fill=grid_color, width=1)
            elif bg_style == "geometric":
                draw = ImageDraw.Draw(img, 'RGBA')
                draw.ellipse((width - 800, -200, width + 200, 800),
                             fill=(primary[0], primary[1], primary[2], 40))
                draw.ellipse((-200, height - 600, 400, height + 200),
                             fill=(primary[0], primary[1], primary[2], 30))

            img.save(filepath, "PNG")
            logger.info(f"【PPT背景】生成完毕: {filepath} (风格: {bg_style})")
            return filepath
        except Exception as e:
            logger.error(f"【PPT背景】失败: {e}")
            return ""


svg_bg_builder = PillowBackgroundBuilder()