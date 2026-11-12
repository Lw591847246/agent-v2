"""文件解析工具"""
import io

from PyPDF2 import PdfReader
from docx import Document
from pptx import Presentation
import pandas as pd

from app.core.logger import logger


# ============ PDF ============
def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text.strip()


# ============ DOCX ============
def extract_text_from_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    # 读表格
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                paragraphs.append(" | ".join(cells))
    return "\n".join(paragraphs)


# ============ PPTX ============
def extract_text_from_pptx(file_bytes: bytes) -> str:
    """提取 .pptx：每页的文本框 + 表格 + 备注"""
    try:
        prs = Presentation(io.BytesIO(file_bytes))
    except Exception as e:
        return f"[PPTX 解析失败: {e}]"

    parts = []
    for idx, slide in enumerate(prs.slides, start=1):
        slide_texts = []

        for shape in slide.shapes:
            # 文本框
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        slide_texts.append(t)

            # 表格
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        slide_texts.append(" | ".join(cells))

        # 演讲者备注
        try:
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    slide_texts.append(f"[备注] {notes}")
        except Exception:
            pass

        if slide_texts:
            parts.append(f"【Slide {idx}】\n" + "\n".join(slide_texts))

    return "\n\n".join(parts)


# ============ EXCEL ============
def extract_text_from_excel(file_bytes: bytes, filename: str) -> str:
    """
    .xlsx → openpyxl 引擎
    .xls  → xlrd 引擎（需 pip install xlrd>=2.0.1）
    pandas 会自动按扩展名选引擎
    """
    try:
        excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
        text_parts = []
        for sheet_name in excel_file.sheet_names:
            df = excel_file.parse(sheet_name).head(400)
            text_parts.append(f"【Sheet: {sheet_name}】")
            text_parts.append(df.to_string(index=False))
        return "\n\n".join(text_parts)
    except ImportError as e:
        logger.error(f"[Excel] 缺依赖: {e}")
        return f"[Excel 解析失败: 缺少依赖，请安装 xlrd（.xls）或 openpyxl（.xlsx）。详情: {e}]"
    except Exception as e:
        logger.error(f"[Excel] 解析失败 {filename}: {e}")
        return f"[Excel 解析失败: {e}]"


# ============ 总入口 ============
def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext == "pdf":
        return extract_text_from_pdf(file_bytes)
    if ext == "docx":
        return extract_text_from_docx(file_bytes)
    if ext == "pptx":
        return extract_text_from_pptx(file_bytes)
    if ext in ("xls", "xlsx"):
        return extract_text_from_excel(file_bytes, filename)

    # 纯文本
    if ext in ("txt", "md"):
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("gbk", errors="ignore")

    # .doc / .ppt 暂不支持
    if ext in ("doc", "ppt"):
        return f"[暂不支持 .{ext} 格式，请另存为 .{'docx' if ext == 'doc' else 'pptx'} 后重试]"

    return ""