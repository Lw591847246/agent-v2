"""文本处理工具"""
import json
import re
from typing import List


def extract_json_array(content: str) -> list:
    """从模型输出中提取 JSON 数组"""
    start = content.find("[")
    end = content.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(content[start:end + 1])
    except json.JSONDecodeError:
        return []


def split_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """将长文本切分为带重叠的片段"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def clean_think(content: str) -> str:
    """去除 DeepSeek-R1 的 think 标签"""
    if "<think>" in content:
        content = content.split("</think>")[-1].strip()
    return content