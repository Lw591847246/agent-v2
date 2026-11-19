"""
failure_message_service.py
==========================
后端"任务失败落库"统一处理模块（供 update_message_content / 历史消息序列化复用）。

设计目标（对齐前端已封装好的 MessageItem + CardRenderer）：
1. 任务失败时，历史消息里只把【任务进度卡 skill_progress】收口成失败卡：
   - part.status / data.status = 'failed'
   - 失败节点 -> 'failed'（红叉），其后未跑节点 -> 'pending'（灰），前置 -> 'done'（绿）
   - error 用【友好短句】，原始异常全文绝不进入用户可见内容（只进日志 / task.error）
2. 其它消息 part（text 对话正文 / 代码片段 / markdown / image / video / audio）
   全部【原样保留】，绝不把整条消息退化成一段原始 JSON 文本。
3. message 级 status 置终态 'done'，让前端 displayDuration 正常显示耗时，且不再被当成进行中。

接入方式见 build_failed_message_payload 文档字符串与交付说明。
"""
from __future__ import annotations
import re
from typing import Optional

# ---- 友好文案映射：原始异常 -> 用户可见短句（全文只进日志）----
# 命中顺序即优先级，先匹配更具体的。
_FRIENDLY_RULES = [
    (re.compile('WinError\s*10061|ConnectionRefused|拒绝(连接|访问)|Connection refused', re.I),
     'ComfyUI 服务不可用（连接被拒绝），任务已终止，请稍后重试。'),
    (re.compile('Max retries exceeded|Failed to establish|NewConnectionError|Name or service not known|getaddrinfo', re.I),
     'ComfyUI 服务不可用（无法连接），任务已终止，请稍后重试。'),
    (re.compile('连续\s*\d+\s*次轮询失败|轮询失败|poll.*fail', re.I),
     '生成服务响应中断，任务已终止，请稍后重试。'),
    (re.compile('进程重启|启动时自动回收|任务中断', re.I),
     '任务因服务重启而中断，请重新发起。'),
    (re.compile('timeout|超时', re.I),
     '任务执行超时，已自动终止，请稍后重试。'),
    (re.compile('显存不足|out of memory|CUDA error|OOM', re.I),
     '算力资源不足，任务已终止，请稍后重试或调低参数。'),
]
_FALLBACK_FRIENDLY = '任务未能完成，已终止，请稍后重试。'

# ---- 业务（按 task_id 前缀）-> 默认 steps 模板（仅在任务未落 steps 时兜底）----
_STEP_TEMPLATES = [
    (('image_generation', '文生图'), ['解析参数', '生成图片', '保存图片']),
    (('image_to_video', '图生视频', 'i2v'), ['解析素材', '生成画面', '合成语音']),
    (('text_to_video', '文生视频', 't2v'), ['解析文案', '生成画面', '合成语音']),
]

_PROGRESS_CARD_TYPES = {'skill_progress'}


def friendly_error(raw: Optional[str]) -> str:
    """把原始异常全文转成用户可见友好短句；绝不返回 None/空。"""
    if not raw:
        return _FALLBACK_FRIENDLY
    for pat, msg in _FRIENDLY_RULES:
        if pat.search(raw):
            return msg
    return _FALLBACK_FRIENDLY


def _infer_template(task_id: str):
    tid = (task_id or '').lower()
    for keys, labels in _STEP_TEMPLATES:
        if any(k in tid for k in keys):
            return labels
    return None


def _reconcile_steps(steps: list, *, failed_node_index: Optional[int] = None) -> list:
    """收口为失败态：遇第一个 running（或指定 index）置 failed，其后全 pending，前置补 done。"""
    out = [dict(s) if isinstance(s, dict) else s for s in steps]
    if not out:
        return out
    run_idx = None
    for i, s in enumerate(out):
        if isinstance(s, dict) and s.get('status') == 'running':
            run_idx = i
            break
    fail_idx = failed_node_index if failed_node_index is not None else run_idx
    if fail_idx is None:
        for i in range(len(out) - 1, -1, -1):
            s = out[i]
            if isinstance(s, dict) and s.get('status') != 'done':
                fail_idx = i
                break
    if fail_idx is None:
        fail_idx = len(out) - 1
    for i, s in enumerate(out):
        if not isinstance(s, dict):
            continue
        if i < fail_idx:
            if s.get('status') != 'done':
                s['status'] = 'done'
        elif i == fail_idx:
            s['status'] = 'failed'
        else:
            s['status'] = 'pending'
    return out


def build_failure_card_data(task_id: str, *, raw_error: Optional[str],
                            progress: int = 0,
                            task_steps: Optional[list] = None,
                            title: str = '任务已终止') -> dict:
    """产出前端 CardRenderer 可直接渲染的失败卡 data（友好文案，无原始异常）。"""
    if task_steps:
        steps = _reconcile_steps(task_steps)
    else:
        tmpl = _infer_template(task_id)
        if tmpl:
            steps = []
            for i, label in enumerate(tmpl):
                st = 'done' if i == 0 else ('failed' if i == 1 else 'pending')
                steps.append({'label': label, 'status': st})
        else:
            steps = []
    return {
        'card_type': 'skill_progress',
        'task_id': task_id,
        'title': title,
        'status': 'failed',
        'progress': int(progress or 0),
        'steps': steps,
        'error': friendly_error(raw_error),
    }


def _is_progress_card(part: dict) -> bool:
    if not isinstance(part, dict) or part.get('type') != 'card':
        return False
    ct = part.get('card_type') or (part.get('data') or {}).get('card_type')
    return ct in _PROGRESS_CARD_TYPES


def reconcile_parts_for_failure(parts: list, *, task_id: str,
                               raw_error: Optional[str], progress: int = 0,
                               task_steps: Optional[list] = None,
                               title: str = '任务已终止') -> list:
    """失败落库核心：只收口任务进度卡，其它 part 原样保留，返回新列表。"""
    new_parts = []
    card_data = build_failure_card_data(
        task_id, raw_error=raw_error, progress=progress,
        task_steps=task_steps, title=title)
    replaced = False
    for p in (parts or []):
        if _is_progress_card(p):
            old_data = dict(p.get('data') or {})
            old_data.update(card_data)
            new_parts.append({**p, 'type': 'card', 'status': 'failed', 'data': old_data})
            replaced = True
        else:
            new_parts.append(p)
    if not replaced:
        new_parts.append({'type': 'card', 'status': 'failed', 'data': card_data})
    return new_parts


def build_failed_message_payload(parts: list, *, task_id: str,
                                 raw_error: Optional[str], progress: int = 0,
                                 task_steps: Optional[list] = None,
                                 duration_ms: int = 0,
                                 title: str = '任务已终止') -> dict:
    """给 update_message_content 的一站式返回值。
    message.status 固定置 'done'（终态，触发耗时显示），parts 已按失败收口。
    raw_error 不进入返回的任何用户可见字段（原始全文应写日志 / task_repo.fail）。"""
    return {
        'status': 'done',
        'duration_ms': int(duration_ms or 0),
        'parts': reconcile_parts_for_failure(
            parts, task_id=task_id, raw_error=raw_error,
            progress=progress, task_steps=task_steps, title=title),
    }
