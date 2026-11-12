# app/skills/word/builder.py
"""Word 构建器。"""
import os
import time

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from app.core.logger import logger


class WordBuilder:
    OUTPUT_ROOT = "./data/word_output"

    def build(self, session_id: str, title: str, markdown_text: str) -> str:
        doc = Document()

        style = doc.styles['Normal']
        style.font.name = '微软雅黑'
        style.font.size = Pt(11)

        h = doc.add_heading(title, level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

        lines = markdown_text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith('### '):
                doc.add_heading(line[4:], level=3)
            elif line.startswith('## '):
                doc.add_heading(line[3:], level=2)
            elif line.startswith('# '):
                doc.add_heading(line[2:], level=1)
            elif line.startswith('- ') or line.startswith('* '):
                doc.add_paragraph(line[2:], style='List Bullet')
            elif line.startswith('1. ') or line.startswith('2. '):
                doc.add_paragraph(line, style='List Number')
            else:
                doc.add_paragraph(line)

        out_dir = os.path.join(self.OUTPUT_ROOT, session_id)
        os.makedirs(out_dir, exist_ok=True)
        fname = f"word_{int(time.time())}.docx"
        fpath = os.path.join(out_dir, fname)
        doc.save(fpath)

        logger.info(f"【WordBuilder】文件生成完成: {fpath}")
        return f"{session_id}/{fname}"


word_builder = WordBuilder()