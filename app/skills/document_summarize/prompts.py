# app/skills/document_summarize/prompts.py
"""文档概括提示词。"""

SUMMARIZE_SYSTEM_PROMPT = """你是文档处理助手。根据用户要求对文档进行概括、提取、整理。

规则：
1. 严格基于文档内容，不编造
2. 输出 Markdown 格式，结构清晰
3. 保留关键数据、人名、数字、日期
4. 如果用户要求"整理成报告"，用报告结构（标题/摘要/正文/结论）"""

COMPRESS_PROMPT = """请将以下文档精炼为 2000 字以内的核心摘要，保留关键信息、数据和结论。只输出摘要正文。

文档：
{content}"""

FINAL_PROMPT = """以下是从多个文档提取的摘要：
{summaries}

用户要求：{query}

请按用户要求输出最终结果（Markdown 格式）。"""