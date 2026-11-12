# app/skills/ppt/builder.py
"""PPT 构建器。"""
import os
import time
from typing import Dict

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

from app.core.logger import logger
from app.skills.ppt.hooks import get_bg_generator
from app.skills.ppt.svg_bg import svg_bg_builder


class DynamicPptBuilder:
    SLIDE_WIDTH = Inches(13.33)
    SLIDE_HEIGHT = Inches(7.5)
    OUTPUT_ROOT = "./data/ppt_output"

    def _hex_to_rgb(self, hex_str: str, default: str = "#4D6BFE") -> RGBColor:
        h = hex_str.lstrip('#') if hex_str else default.lstrip('#')
        if len(h) != 6:
            h = default.lstrip('#')
        try:
            return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return RGBColor(0x4D, 0x6B, 0xFE)

    def _extract_text(self, obj) -> str:
        if isinstance(obj, str):
            return obj.strip()
        if isinstance(obj, list):
            return " ".join([self._extract_text(x) for x in obj if self._extract_text(x)])
        if isinstance(obj, dict):
            texts = []
            for k, v in obj.items():
                if k in ['type', 'key', 'id', 'layout']:
                    continue
                t = self._extract_text(v)
                if t:
                    texts.append(t)
            return "，".join(texts) if len(texts) > 1 else (texts[0] if texts else "")
        return str(obj).strip()

    def build(self, session_id: str, outline: Dict, on_progress=None) -> str:
        colors = outline.get("theme_colors", {})
        primary = self._hex_to_rgb(colors.get("primary", "#4D6BFE"))
        bg_color = self._hex_to_rgb(colors.get("bg", "#FFFFFF"))
        text_color = self._hex_to_rgb(colors.get("text", "#333333"))
        font_name = colors.get("font", "微软雅黑")

        bg_prompt = outline.get("bg_prompt", "")
        bg_style = outline.get("bg_style", "tech")
        if not bg_prompt:
            bg_prompt = "科技感蓝色渐变背景，带有微光粒子"
            bg_style = "tech"

        prs = Presentation()
        prs.slide_width = self.SLIDE_WIDTH
        prs.slide_height = self.SLIDE_HEIGHT

        slides = outline.get("slides", [])

        bg_image_path = ""
        bg_hook = get_bg_generator()
        if bg_hook and bg_prompt:
            if on_progress:
                on_progress("渲染 AI 背景", "running")
            bg_image_path = bg_hook(bg_prompt, session_id)
        elif bg_prompt:
            if on_progress:
                on_progress("渲染几何背景", "running")
            bg_image_path = svg_bg_builder.generate(bg_prompt, colors, bg_style)

        for i, s in enumerate(slides):
            if on_progress:
                on_progress(f"排版第 {i + 1}/{len(slides)} 页", "running")

            t = s.get("type", "content")
            layout = s.get("layout", "list")

            clean_bullets = []
            if t not in ("cover", "end"):
                raw_bullets = s.get("bullets") or s.get("cards") or []
                if isinstance(raw_bullets, dict) and layout == "two_col":
                    left_list = raw_bullets.get("left") or []
                    right_list = raw_bullets.get("right") or []
                    max_len = max(len(left_list), len(right_list))
                    for j in range(max_len):
                        left_text = self._extract_text(left_list[j]) if j < len(left_list) else ""
                        right_text = self._extract_text(right_list[j]) if j < len(right_list) else ""
                        if left_text or right_text:
                            clean_bullets.append(f"{left_text} → {right_text}")
                else:
                    if isinstance(raw_bullets, list):
                        for b in raw_bullets:
                            txt = self._extract_text(b)
                            if txt and len(txt) > 1:
                                clean_bullets.append(txt)

                if not clean_bullets:
                    logger.warning(f"【PPT】跳过空白页: {s.get('title')}")
                    continue

            slide = prs.slides.add_slide(prs.slide_layouts[6])

            if bg_image_path and os.path.isfile(bg_image_path):
                slide.shapes.add_picture(bg_image_path, 0, 0,
                                         width=self.SLIDE_WIDTH, height=self.SLIDE_HEIGHT)
            else:
                fill = slide.background.fill
                fill.solid()
                fill.fore_color.rgb = bg_color

            if t in ("cover", "end"):
                self._render_cover(slide, s, primary, text_color, font_name)
            else:
                self._add_page_header(slide, s.get("title", ""), primary, font_name)
                bullets = clean_bullets
                if isinstance(bullets, str):
                    bullets = [bullets]

                if layout == "two_col":
                    self._render_two_col(slide, bullets, primary, text_color, font_name)
                elif layout == "cards":
                    self._render_cards(slide, bullets, primary, text_color, font_name, bg_color)
                elif layout == "quote":
                    self._render_quote(slide, bullets, primary, text_color, font_name)
                else:
                    self._render_list(slide, bullets, primary, text_color, font_name)

        out_dir = os.path.join(self.OUTPUT_ROOT, session_id)
        os.makedirs(out_dir, exist_ok=True)
        fname = f"ppt_{int(time.time())}.pptx"
        fpath = os.path.join(out_dir, fname)
        prs.save(fpath)
        logger.info(f"【PPT】生成完成: {fpath}")
        return f"{session_id}/{fname}"

    def _add_page_header(self, slide, title, primary, font_name):
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(0.8),
                                     Pt(6), Inches(0.8))
        bar.fill.solid()
        bar.fill.fore_color.rgb = primary
        bar.line.fill.background()
        tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.65), Inches(11), Inches(1))
        p = tb.text_frame.paragraphs[0]
        p.text = title
        p.font.name = font_name
        p.font.size = Pt(32)
        p.font.bold = True
        p.font.color.rgb = primary

    def _render_list(self, slide, bullets, primary, text_color, font_name):
        if not bullets:
            return
        body = slide.shapes.add_textbox(Inches(1.0), Inches(2.2), Inches(11.5), Inches(4.5))
        tf = body.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.1)
        total_len = sum(len(str(b)) for b in bullets)
        base_size = 22 if total_len < 100 else (18 if total_len < 200 else 16)
        for i, b in enumerate(bullets):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = f"• {str(b)}"
            para.font.name = font_name
            para.font.size = Pt(base_size)
            para.font.color.rgb = text_color
            para.space_after = Pt(16)

    def _render_two_col(self, slide, bullets, primary, text_color, font_name):
        if not bullets:
            return
        mid = len(bullets) // 2
        left = bullets[:mid] if mid > 0 else bullets
        right = bullets[mid:] if mid > 0 else []
        total_len = sum(len(str(b)) for b in bullets)
        base_size = 20 if total_len < 80 else (16 if total_len < 160 else 14)

        left_box = slide.shapes.add_textbox(Inches(1.0), Inches(2.2), Inches(4.8), Inches(4.5))
        left_box.text_frame.word_wrap = True
        for i, b in enumerate(left):
            p = left_box.text_frame.paragraphs[0] if i == 0 else left_box.text_frame.add_paragraph()
            p.text = f"• {str(b)}"
            p.font.name = font_name
            p.font.size = Pt(base_size)
            p.font.color.rgb = text_color
            p.space_after = Pt(15)

        divider = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(6.5), Inches(2.2),
                                         Pt(2), Inches(4.5))
        divider.fill.solid()
        divider.fill.fore_color.rgb = primary
        divider.line.fill.background()

        right_box = slide.shapes.add_textbox(Inches(7.0), Inches(2.2), Inches(4.8), Inches(4.5))
        right_box.text_frame.word_wrap = True
        for i, b in enumerate(right):
            p = right_box.text_frame.paragraphs[0] if i == 0 else right_box.text_frame.add_paragraph()
            p.text = f"• {str(b)}"
            p.font.name = font_name
            p.font.size = Pt(base_size)
            p.font.color.rgb = text_color
            p.space_after = Pt(15)

    def _render_cards(self, slide, bullets, primary, text_color, font_name, bg_color):
        if not bullets:
            return
        count = min(len(bullets), 3)
        card_width = Inches(3.6)
        spacing = Inches(0.4)
        start_x = Inches(0.8)
        bg_avg = (bg_color[0] + bg_color[1] + bg_color[2]) / 3
        is_light_theme = bg_avg > 128
        card_bg = RGBColor(0xFF, 0xFF, 0xFF) if is_light_theme else RGBColor(0x2A, 0x2A, 0x2A)
        for i in range(count):
            text = str(bullets[i])
            card_shape = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE,
                start_x + i * (card_width + spacing), Inches(2.2),
                card_width, Inches(3.5),
            )
            card_shape.fill.solid()
            card_shape.fill.fore_color.rgb = card_bg
            card_shape.line.color.rgb = primary
            card_shape.line.width = Pt(1.5)
            tb = slide.shapes.add_textbox(
                start_x + i * (card_width + spacing), Inches(2.2),
                card_width, Inches(3.5),
            )
            tf = tb.text_frame
            tf.word_wrap = True
            tf.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf.margin_left = Inches(0.2)
            tf.margin_right = Inches(0.2)
            p = tf.paragraphs[0]
            p.text = text
            p.font.name = font_name
            p.font.color.rgb = text_color
            p.alignment = PP_ALIGN.CENTER
            if len(text) > 60:
                p.font.size = Pt(14)
            elif len(text) > 30:
                p.font.size = Pt(16)
            else:
                p.font.size = Pt(18)

    def _render_quote(self, slide, bullets, primary, text_color, font_name):
        text = str(bullets[0]) if bullets else "核心理念"
        tb = slide.shapes.add_textbox(Inches(1.5), Inches(2.5), Inches(10.33), Inches(2))
        p = tb.text_frame.paragraphs[0]
        p.text = f"「 {text} 」"
        p.alignment = PP_ALIGN.CENTER
        p.font.name = font_name
        p.font.size = Pt(40)
        p.font.italic = True
        p.font.color.rgb = primary

    def _render_cover(self, slide, d, primary, text_color, font_name):
        tb = slide.shapes.add_textbox(Inches(1), Inches(2.8), Inches(11.33), Inches(1.5))
        p = tb.text_frame.paragraphs[0]
        p.text = d.get("title", "")
        p.alignment = PP_ALIGN.CENTER
        p.font.name = font_name
        p.font.size = Pt(54)
        p.font.bold = True
        p.font.color.rgb = primary
        subtitle = d.get("subtitle", "")
        if subtitle:
            sub_tb = slide.shapes.add_textbox(Inches(1), Inches(4.5), Inches(11.33), Inches(1))
            sp = sub_tb.text_frame.paragraphs[0]
            sp.text = subtitle
            sp.alignment = PP_ALIGN.CENTER
            sp.font.name = font_name
            sp.font.size = Pt(24)
            sp.font.color.rgb = text_color


dynamic_ppt_builder = DynamicPptBuilder()