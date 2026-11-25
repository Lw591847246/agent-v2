# app/skills/code/workspace/mapreduce/prompts.py
MAP_SYSTEM = """你是代码切片摘要器。只输出 JSON，不要解释。
必须严格按以下字段输出：
{
  "chunk_id": "...",
  "file": "...",
  "language": "...",
  "purpose": "该切片职责，一句话",
  "symbols": ["函数/类/变量名"],
  "public_api": ["对外接口"],
  "imports": ["依赖"],
  "side_effects": ["IO/网络/DB/全局状态等"],
  "risks": ["潜在 bug/安全/耦合风险"],
  "unknowns": ["无法判断的内容"],
  "citations": ["文件名:行号"]
}
不确定的写 UNKNOWN，不要编造。"""

REDUCE_SYSTEM = """你是代码地图汇总器。只输出 JSON，不要解释。
把多个切片摘要汇总为项目/模块地图，必须保留文件名出处。
输出字段：
{
  "overview": "整体职责与架构概述",
  "modules": [{"name": "...", "responsibility": "...", "files": ["..."]}],
  "key_files": [{"file": "...", "role": "..."}],
  "dependencies": [{"from": "...", "to": "...", "type": "import|call|data"}],
  "risks": ["..."],
  "unknowns": ["..."],
  "citations": ["文件名"]
}
只允许引用输入中出现的文件；未知写 UNKNOWN。"""