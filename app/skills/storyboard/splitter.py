# app/skills/storyboard/splitter.py
"""LLM 拆场景器。"""
from __future__ import annotations

import json
import re
from typing import Dict

from json_repair import repair_json

from app.core.logger import logger
from app.infrastructure.clients.model_manager import model_manager


STYLE_PROFILES = {
    "realistic_cinema": {
        "name": "写实电影",
        "description": "真实电影质感、自然光影、人物写实",
        "prompt_prefix": "cinematic photography, photorealistic, natural lighting, film grain, 8k ultra detailed",
        "tts": {"ref_audio": "", "speed": 1.0},
    },
    "anime_drama": {
        "name": "漫剧",
        "description": "二次元、多角色对白、快节奏",
        "prompt_prefix": "anime style, cel shading, vivid colors, high quality, dramatic composition",
        "tts": {"ref_audio": "", "speed": 1.0},
    },
    "ink_wash": {
        "name": "水墨国风",
        "description": "古典水墨、留白意境",
        "prompt_prefix": "traditional chinese ink painting, watercolor, minimalist, elegant brush strokes",
        "tts": {"ref_audio": "", "speed": 1.0},
    },
}


_SCENE_PROMPT = """你是专业的影视导演，需要将一段故事改编成"有声绘本/漫剧"的分镜脚本。

风格：{style_name}（{style_desc}）

## 任务
1. 提取故事的核心情节，拆解为 {min_scenes}-{max_scenes} 个场景。
2. 每个场景包含：
   - text: 该场景的旁白文本（100-200 字，保留原文精华，可以是连续段落）
   - visual_hint: 画面描述（20-40 字，用于 AI 生图，描述环境/人物/氛围/光影，**不要出现"说话"、"台词"等词**）

3. **额外生成 1 个 overview_visual_hint**：
   - 一段能概括全文的"总览式"画面描述（30-50 字）
   - 适合作为封面/单图模式的画面

## 故事原文
{story}

## 输出格式（严格 JSON，不要 markdown 代码块）
{{
  "title": "故事标题（从原文提取或总结）",
  "overview_visual_hint": "一张能概括全文的画面描述",
  "scenes": [
    {{"index": 1, "text": "场景 1 的旁白文本...", "visual_hint": "场景 1 的画面描述..."}}
  ]
}}

## 规则
1. 场景数量 {min_scenes}-{max_scenes} 个，必须覆盖故事全文
2. 每个 visual_hint 都必须是具体、可视觉化的画面描述
3. 只输出 JSON，不要任何解释
"""


def split_story_to_scenes(
    story: str, style: str = "realistic_cinema", max_scenes: int = 6,
) -> Dict:
    if not story or not story.strip():
        raise ValueError("故事为空")

    story_to_use = story
    if len(story) > 8000:
        logger.info(f"[Splitter] 故事过长 ({len(story)} 字)，先精简到 5000 字以内")
        compress_prompt = (
            f"请将以下故事精简到 5000 字以内，保留所有关键情节、人物、对话精华，"
            f"删减重复描述，保持故事完整性和可读性。直接输出精简后的故事，不要解释。\n\n{story}"
        )
        story_to_use = model_manager.chat(
            task_type="chat",
            messages=[{"role": "user", "content": compress_prompt}],
        ).strip()

    style_cfg = STYLE_PROFILES.get(style, STYLE_PROFILES["realistic_cinema"])
    min_scenes = 3
    prompt = _SCENE_PROMPT.format(
        style_name=style_cfg["name"],
        style_desc=style_cfg["description"],
        min_scenes=min_scenes,
        max_scenes=max_scenes,
        story=story_to_use,
    )

    raw = model_manager.chat(
        task_type="chat",
        messages=[{"role": "user", "content": prompt}],
        format="json",
    )

    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:-3]
    elif raw.startswith("```"):
        raw = raw[3:-3]
    raw = raw.strip()

    l = raw.find("{")
    if l >= 0:
        raw = raw[l:]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"[Splitter] JSON 解析失败，启用 repair_json")
        data = json.loads(repair_json(raw))

    if not data.get("overview_visual_hint"):
        scenes_tmp = data.get("scenes") or []
        if scenes_tmp and scenes_tmp[0].get("visual_hint"):
            data["overview_visual_hint"] = scenes_tmp[0]["visual_hint"]
        else:
            data["overview_visual_hint"] = "一个富有故事氛围的画面"

    if not data.get("scenes"):
        raise ValueError("LLM 未返回有效 scenes")

    for i, s in enumerate(data["scenes"]):
        s["index"] = i + 1

    logger.info(
        f"[Splitter] 拆场景完成: title={data.get('title')}, "
        f"scenes={len(data['scenes'])}, overview={data['overview_visual_hint'][:30]}"
    )
    return data