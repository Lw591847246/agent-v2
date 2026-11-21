#!/usr/bin/env python
# tests/acceptance/run_acceptance.py
"""P0 验收脚本（独立运行，不依赖 pytest / 不需要 Java 登录）。

它做什么
--------
对着**正在运行**的 agent 服务（默认 http://127.0.0.1:9000）跑 14 组验收，
用两个"假用户"的 token 直连，覆盖：越权写入、未鉴权访问、目录穿越、
上传穿越、缩略图鉴权、出口签名、SSE 断连落库、日志判据。

为什么不走 Java 登录
--------------------
agent 的 auth_interceptor 只做 `EXISTS token` + `GET token -> JSON(userId)`，
所以本脚本直接往 Redis 写两个测试 token（默认 userId=990001/990002，跑完删掉），
完全不碰你的 Java 服务，也不污染真实用户。

用法
----
    # 最简（自动往 Redis 造 token）
    python tests/acceptance/run_acceptance.py

    # 严格模式 + 项目根（用于检查 data/ 外是否被写入）+ 日志文件
    python tests/acceptance/run_acceptance.py --strict ^
        --project-root D:\\agent ^
        --log-file logs\\app.log

    # 已经有一个真实产物（视频任务）时，顺带验证 job 归属与签名播放
    python tests/acceptance/run_acceptance.py --strict --task-id t_xxxxxxxxxx

    # 复用你现有的两个真实 token（不想动 Redis 时）
    python tests/acceptance/run_acceptance.py --token-a <A> --token-b <B>

退出码：0=全部通过（SKIP 不算失败），1=有 FAIL。
报告：tests/acceptance/acceptance-report.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid

try:
    import requests
except ImportError:
    print("需要 requests：pip install requests")
    sys.exit(2)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAATklEQVR4nGP0zf7HQEvARFPTGUYt"
    "IAKMxgFBwIJLYtMURgYSgV/Of0zB0TggCEaDiCAYDSKCYDSICILRIBr4IGIcbXgRAqPJlCCgeRAB"
    "AIUpBq1rbDv6AAAAAElFTkSuQmCC"
)  # 32x32 PNG：确保能生成缩略图

# 视为"被拒绝"的业务码
DENY_CODES = {400, 401, 403, 404}


# ============================================================
# 基础设施
# ============================================================

class Report:
    def __init__(self):
        self.rows = []
        self.t0 = time.time()

    def add(self, tid, name, status, detail=""):
        row = {"id": tid, "name": name, "status": status, "detail": str(detail)[:500]}
        self.rows.append(row)
        mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "INFO": "INFO"}[status]
        print(f"[{mark}] {tid:5} {name}" + (f"  |  {detail}" if detail else ""))

    def ok(self, tid, name, detail=""):
        self.add(tid, name, "PASS", detail)

    def fail(self, tid, name, detail=""):
        self.add(tid, name, "FAIL", detail)

    def skip(self, tid, name, detail=""):
        self.add(tid, name, "SKIP", detail)

    def info(self, tid, name, detail=""):
        self.add(tid, name, "INFO", detail)

    @property
    def fails(self):
        return [r for r in self.rows if r["status"] == "FAIL"]

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"base": None, "started": self.t0, "rows": self.rows,
                 "pass": sum(1 for r in self.rows if r["status"] == "PASS"),
                 "fail": len(self.fails),
                 "skip": sum(1 for r in self.rows if r["status"] == "SKIP")},
                f, ensure_ascii=False, indent=2,
            )


class Api:
    """HTTP 客户端。

    关于 429：服务端按 `ip:path` 限流（chat 5 QPS、upload/asset 与 process-file 2 QPS、
    其余 10 QPS）。验收脚本会连续打请求，**正常的业务接口可能真的被限流**。
    这里的处理原则是"不掩盖"：
      - 默认每请求间隔 0.25s，尽量不触发；
      - 真被限流时打一行 INFO、退避 1.2s 重试**一次**；
      - 重试后仍 429 → 原样返回，让用例判 FAIL（而不是被吞掉）。
    """

    def __init__(self, base, throttle=0.25, retries=1):
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.throttle = throttle
        self.retries = retries
        self.rate_limited = []   # [(path, attempts)]，跑完汇总进报告

    @staticmethod
    def _is_429(resp):
        if resp.status_code == 429:
            return True
        try:
            body = resp.json()
            return isinstance(body, dict) and int(body.get("code") or 0) == 429
        except Exception:
            return False

    def call(self, method, path, token=None, stream=False, timeout=120, retry=True, **kw):
        headers = kw.pop("headers", {}) or {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        attempt = 0
        while True:
            if self.throttle:
                time.sleep(self.throttle)
            resp = self.s.request(
                method, self.base + path, headers=headers,
                stream=stream, timeout=timeout, **kw,
            )
            if retry and self._is_429(resp):
                if attempt < self.retries:
                    attempt += 1
                    self.rate_limited.append((path, attempt))
                    print(f"[INFO] 被限流(429)，退避 {1.2 * attempt:.1f}s 重试：{path}")
                    try:
                        resp.close()
                    except Exception:
                        pass
                    time.sleep(1.2 * attempt)
                    continue
                # 重试次数用尽：不吞，交回用例判 FAIL
                self.rate_limited.append((path, 0))
                return resp
            return resp

    @staticmethod
    def code(resp):
        try:
            body = resp.json()
            if isinstance(body, dict) and "code" in body:
                return int(body["code"])
        except Exception:
            pass
        return resp.status_code

    @staticmethod
    def text(resp):
        try:
            return resp.text[:2000]
        except Exception:
            return ""

    @staticmethod
    def is_sse(resp):
        return "text/event-stream" in (resp.headers.get("content-type") or "")

    @staticmethod
    def drain_sse(resp, seconds=60):
        """读完 SSE，返回事件列表（遇到 [DONE] 或超时截止）。"""
        events, t0 = [], time.time()
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if time.time() - t0 > seconds:
                    break
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    events.append(json.loads(payload))
                except Exception:
                    pass
        finally:
            resp.close()
        return events


def seed_tokens(args):
    """往 Redis 里写两个测试 token（agent 只做 EXISTS/GET，所以能直接用）。"""
    import redis  # agent 依赖里已有

    kwargs = dict(host=args.redis_host, port=args.redis_port, db=args.redis_db,
                  decode_responses=True)
    if args.redis_password:
        kwargs["password"] = args.redis_password
    conn = redis.Redis(**kwargs)

    ta, tb = f"acc-a-{uuid.uuid4().hex}", f"acc-b-{uuid.uuid4().hex}"
    conn.setex(ta, 7200, json.dumps({"userId": 990001, "username": "acc-A"}))
    conn.setex(tb, 7200, json.dumps({"userId": 990002, "username": "acc-B"}))
    return ta, tb, conn


# ============================================================
# 用例
# ============================================================

def t01_auth_matrix(api, rep, ctx, args):
    """T01 未鉴权访问：管理类必须 401；下载类在 strict 下必须非 200。"""
    admin_paths = [
        "/agent/v1/conversations",
        "/agent/v1/knowledge/count",
        "/agent/v1/knowledge/query?question=x",
    ]
    for p in admin_paths:
        r = api.call("GET", p)
        c = api.code(r)
        if c == 401:
            rep.ok("T01", f"未登录访问 {p} -> 401")
        else:
            rep.fail("T01", f"未登录访问 {p} 期望 401，实际 {c}", api.text(r))

    dl_paths = [
        "/agent/v1/job/acc-none/video",
        "/agent/v1/ppt/download/aaaaaaaa/bb.pptx",
        "/agent/v1/word/download/aaaaaaaa/bb.docx",
        "/agent/v1/storyboard/download?task_id=aaaaaaaa",
        "/agent/v1/skill/download?path=./data/ppt_output/a/b.pptx",
        "/agent/v1/custom_avatar/download?path=D:/data/custom_avatar/x_final.mp4",
        "/agent/v1/liveportrait/download?path=D:/data/x/y.mp4",
        "/agent/v1/tts/1_a.mp3",
    ]
    for p in dl_paths:
        r = api.call("GET", p)
        c = api.code(r)
        if args.strict:
            if c in DENY_CODES:
                rep.ok("T01", f"strict 未鉴权下载被拒 {p} -> {c}")
            else:
                rep.fail("T01", f"strict 下 {p} 未被拒（{c}）", api.text(r))
        else:
            rep.info("T01", f"灰度模式 {p} -> {c}（strict 时应为拒绝码）")


def t02_session_ownership(api, rep, ctx, args):
    """T02 越权写入：A 用 B 的 session_id 调 /chat、/chat/action、/process-file。"""
    ra = api.call("POST", "/agent/v1/conversation/new", token=ctx["ta"])
    rb = api.call("POST", "/agent/v1/conversation/new", token=ctx["tb"])
    sid_a, sid_b = ra.json().get("session_id"), rb.json().get("session_id")
    if not sid_a or not sid_b:
        rep.fail("T02", "无法创建测试会话", f"{api.text(ra)} / {api.text(rb)}")
        return
    ctx["sid_a"], ctx["sid_b"] = sid_a, sid_b

    # /chat
    r = api.call("POST", "/agent/v1/chat", token=ctx["ta"],
                 json={"text": "hi", "session_id": sid_b}, stream=False)
    c = api.code(r)
    if c == 404:
        rep.ok("T02", "A 用 B 的 session 调 /chat -> 404")
    else:
        rep.fail("T02", f"A 用 B 的 session 调 /chat 未被拒（{c}）", api.text(r))

    # /chat/action
    r = api.call("POST", "/agent/v1/chat/action", token=ctx["ta"],
                 json={"session_id": sid_b, "card_id": "x"})
    c = api.code(r)
    if c == 404:
        rep.ok("T02", "A 用 B 的 session 调 /chat/action -> 404")
    else:
        rep.fail("T02", f"A 用 B 的 session 调 /chat/action 未被拒（{c}）", api.text(r))

    # /process-file（multipart）
    r = api.call(
        "POST", "/agent/v1/process-file", token=ctx["ta"],
        files={"files": ("a.png", PNG_1X1, "image/png")},
        data={"text": "看图", "session_id": sid_b},
        stream=False,
    )
    c = api.code(r)
    if c == 404:
        rep.ok("T02", "A 用 B 的 session 调 /process-file -> 404")
    else:
        rep.fail("T02", f"A 用 B 的 session 调 /process-file 未被拒（{c}）", api.text(r))

    # 对照：自己的 session 可写（详细回归在 T08，这里只验证不被误拒）


def t03_illegal_session(api, rep, ctx, args):
    """T03 非法 session_id：含 .. / 反斜杠，必须被拒且不产生目录。"""
    for bad in ["..\\..\\pwned", "../../pwned", "a/b", "中文会话", "x" * 80]:
        r = api.call("POST", "/agent/v1/chat", token=ctx["ta"],
                     json={"text": "hi", "session_id": bad}, stream=False)
        c = api.code(r)
        if c in DENY_CODES:
            rep.ok("T03", f"非法 session_id 被拒 {bad!r} -> {c}")
        else:
            rep.fail("T03", f"非法 session_id 未被拒 {bad!r} -> {c}", api.text(r))

    if args.project_root:
        escaped = os.path.join(args.project_root, "pwned")
        if os.path.exists(escaped):
            rep.fail("T03", f"发现越权写入产物：{escaped}")
        else:
            rep.ok("T03", "项目根下未出现 pwned 目录")


def t04_upload_ownership(api, rep, ctx, args):
    """T04 上传：session_id 属于他人时必须拒；自己的可以。"""
    r = api.call("POST", "/agent/v1/upload/asset", token=ctx["ta"],
                 files={"file": ("a.png", PNG_1X1, "image/png")},
                 data={"session_id": ctx["sid_b"]})
    c = api.code(r)
    if c in (403, 404):
        rep.ok("T04", "A 用 B 的 session 上传 -> 拒绝")
    else:
        rep.fail("T04", f"A 用 B 的 session 上传未被拒（{c}）", api.text(r))

    r = api.call("POST", "/agent/v1/upload/asset", token=ctx["ta"],
                 files={"file": ("a.png", PNG_1X1, "image/png")},
                 data={"session_id": ctx["sid_a"]})
    if r.status_code == 200 and api.code(r) in (0, 200):
        body = r.json()
        ctx["file_id"] = body.get("file_id")
        ctx["thumb_url"] = body.get("thumb_url") or ""
        rep.ok("T04", "A 用自己 session 上传成功", ctx.get("file_id", ""))
    else:
        rep.fail("T04", f"A 用自己 session 上传失败（{api.code(r)}）", api.text(r))

    # 穿越型 session_id 直接 400（service 层白名单）
    r = api.call("POST", "/agent/v1/upload/asset", token=ctx["ta"],
                 files={"file": ("a.png", PNG_1X1, "image/png")},
                 data={"session_id": "..\\..\\..\\pwned"})
    c = api.code(r)
    if c in DENY_CODES:
        rep.ok("T04", "上传 session_id 穿越被拒")
    else:
        rep.fail("T04", f"上传 session_id 穿越未被拒（{c}）", api.text(r))


def t05_thumb_auth(api, rep, ctx, args):
    """T05 缩略图鉴权：签名可匿名；无签名需登录+归属；越权 404；篡改 403。"""
    fid, turl = ctx.get("file_id"), ctx.get("thumb_url")
    if not fid:
        rep.skip("T05", "没有 file_id，跳过缩略图鉴权")
        return
    if not turl:
        rep.skip("T05", "上传未生成 thumb（1x1 png 可能过小），跳过")
        return

    r = api.call("GET", turl)  # 不带 token
    if r.status_code == 200:
        rep.ok("T05", "带签名缩略图可匿名访问（<img src> 场景）")
    else:
        rep.fail("T05", f"带签名缩略图匿名访问失败（{api.code(r)}）", api.text(r))

    plain = turl.split("?")[0]

    # 灰度模式下"无凭证"会被放行（这是过渡开关的预期行为），strict 下必须拒
    no_cred_ok = not args.strict
    forged_ok = not args.strict

    checks = [
        ("无签名+无token", plain, None, no_cred_ok),
        ("无签名+B的token", plain, ctx["tb"], False),
        ("无签名+A的token", plain, ctx["ta"], True),
    ]
    for label, path, token, want_ok in checks:
        r = api.call("GET", path, token=token)
        ok = r.status_code == 200
        if ok == want_ok:
            rep.ok("T05", f"缩略图 {label} -> {api.code(r)}（期望 ok={want_ok}）")
        else:
            rep.fail("T05", f"缩略图 {label} 期望 ok={want_ok}，实际 {api.code(r)}", api.text(r))

    r = api.call("GET", plain + "?exp=0&sig=deadbeefdeadbeef")
    ok = r.status_code == 200
    if ok == forged_ok:
        rep.ok("T05", f"伪造签名 -> {api.code(r)}（期望 ok={forged_ok}）")
    else:
        rep.fail("T05", f"伪造签名期望 ok={forged_ok}，实际 {api.code(r)}", api.text(r))


def t06_path_traversal(api, rep, ctx, args):
    """T06 下载类路径穿越：session_id / task_id / path 各种编码。"""
    payloads = [
        "/agent/v1/ppt/download/..%5C..%5Cconfig/application.yml",
        "/agent/v1/ppt/download/..%2F..%2Fconfig/application.yml",
        "/agent/v1/ppt/download/..%5C..%5C..%5CWindows%5Cwin.ini",
        "/agent/v1/word/download/..%5C..%5Cconfig/application.yml",
        "/agent/v1/storyboard/download?task_id=..",
        "/agent/v1/storyboard/download?task_id=..%5C..%5Cconfig",
        "/agent/v1/skill/download?path=../../app/config/application.yml",
        "/agent/v1/skill/download?path=./data/../app/config/application.yml",
        "/agent/v1/custom_avatar/download?path=../../app/config/application.yml",
        "/agent/v1/liveportrait/download?path=../../app/config/application.yml",
    ]
    for p in payloads:
        r = api.call("GET", p, token=ctx["ta"])
        c = api.code(r)
        leaked = ("mysql:" in api.text(r)) or ("password" in api.text(r).lower())
        if leaked:
            rep.fail("T06", f"疑似泄漏配置文件内容：{p}")
        elif c in DENY_CODES:
            rep.ok("T06", f"穿越被拒 {p} -> {c}")
        else:
            rep.fail("T06", f"穿越返回了非拒绝码 {p} -> {c}", api.text(r))


def t07_knowledge_auth(api, rep, ctx, args):
    """T07 知识库：无 token 拒绝，有 token 可用。"""
    for p in ["/agent/v1/knowledge/count", "/agent/v1/knowledge/query?question=test"]:
        r = api.call("GET", p)
        if api.code(r) in (401, 403):
            rep.ok("T07", f"未登录 {p} -> {api.code(r)}")
        else:
            rep.fail("T07", f"未登录 {p} 未拒（{api.code(r)}）", api.text(r))

    r = api.call("GET", "/agent/v1/knowledge/count", token=ctx["ta"])
    if r.status_code == 200 and api.code(r) in (0, 200):
        rep.ok("T07", "登录后知识库可用")
    else:
        rep.fail("T07", f"登录后知识库不可用（{api.code(r)}）", api.text(r))


def t08_regression_chat(api, rep, ctx, args):
    """T08 正向回归：SSE 正常、消息落库、产物 URL 已签名。"""
    r = api.call("POST", "/agent/v1/chat", token=ctx["ta"],
                 json={"text": "请只回复：OK", "session_id": ctx["sid_a"]},
                 stream=True, timeout=180)
    if not api.is_sse(r):
        rep.fail("T08", "chat 未返回 SSE", api.text(r))
        return
    events = api.drain_sse(r, seconds=120)
    types = {e.get("type") for e in events if isinstance(e, dict)}
    done = next((e for e in events if isinstance(e, dict) and e.get("type") == "done"), None)
    if "done" in types and done and done.get("message_id"):
        rep.ok("T08", f"SSE 正常收尾 message_id={done.get('message_id')}")
    elif "error" in types:
        rep.fail("T08", "SSE 返回 error 事件", str(events[:2]))
    else:
        rep.fail("T08", "未收到 done 事件", str(events[-2:]))

    r = api.call("GET", f"/agent/v1/conversation/{ctx['sid_a']}/messages", token=ctx["ta"])
    body = r.json()
    msgs = (body.get("data") or body or {}).get("messages") or []
    ctx["messages"] = msgs
    if len(msgs) >= 2:
        rep.ok("T08", f"历史消息落库 {len(msgs)} 条")
    else:
        rep.fail("T08", f"历史消息数量异常：{len(msgs)}", api.text(r))

    # 产物 URL 必须已签名（有产物时）
    urls = [o.get("url") for m in msgs for o in (m.get("outputs") or []) if o.get("url")]
    unsigned = [u for u in urls if u.startswith("/agent/v1/") and "sig=" not in u]
    if not urls:
        rep.skip("T08", "本条消息无产物，跳过出口签名检查")
    elif not unsigned:
        rep.ok("T08", f"产物 URL 均已签名（{len(urls)} 个）")
    else:
        rep.fail("T08", "存在未签名产物 URL", str(unsigned[:3]))


def t09_sse_disconnect(api, rep, ctx, args):
    """T09 断连：中途 close 后助手消息仍落库、服务仍可用。"""
    r = api.call("POST", "/agent/v1/conversation/new", token=ctx["ta"])
    sid = r.json().get("session_id")
    ctx["sid_c"] = sid

    r = api.call("POST", "/agent/v1/chat", token=ctx["ta"],
                 json={"text": "请写一段 500 字的风景散文", "session_id": sid},
                 stream=True, timeout=180)
    if not api.is_sse(r):
        rep.fail("T09", "断连用例：chat 未返回 SSE", api.text(r))
        return

    got = 0
    t0 = time.time()
    try:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                got += 1
            if got >= 2 or time.time() - t0 > 20:
                break
    finally:
        r.close()  # 模拟浏览器断开

    time.sleep(8)  # 给服务端 finalize 留时间

    r = api.call("GET", f"/agent/v1/conversation/{sid}/messages", token=ctx["ta"])
    body = r.json()
    msgs = (body.get("data") or body or {}).get("messages") or []
    if any(m.get("role") == "assistant" for m in msgs):
        rep.ok("T09", "断连后助手消息仍落库（finalize 正常）")
    else:
        rep.fail("T09", f"断连后未落库：{len(msgs)} 条", api.text(r))

    r = api.call("GET", "/agent/v1/conversations", token=ctx["ta"])
    if r.status_code == 200:
        rep.ok("T09", "断连后服务仍可正常响应（副本：会话列表）")
    else:
        rep.fail("T09", "断连后服务异常", api.text(r))


def t10_job_and_signed_url(api, rep, ctx, args):
    """T10 任务归属 + 签名播放（需要 --task-id，或从消息产物里自动挑一个）。"""
    task_id = args.task_id
    if not task_id:
        for m in ctx.get("messages", []):
            for o in (m.get("outputs") or []):
                if o.get("task_id"):
                    task_id = o["task_id"]
                    break
            if task_id:
                break
    if not task_id:
        rep.skip("T10", "没有可用 task_id（可传 --task-id），跳过任务归属用例")
        return

    r = api.call("GET", f"/agent/v1/job/{task_id}", token=ctx["ta"])
    if r.status_code == 200:
        rep.ok("T10", "A 查自己的任务 -> 200")
        data = r.json().get("data") or r.json()
        vurl = data.get("video_url") if isinstance(data, dict) else None
        ctx["video_url"] = vurl
    else:
        rep.fail("T10", f"A 查自己的任务失败（{api.code(r)}）", api.text(r))

    r = api.call("GET", f"/agent/v1/job/{task_id}", token=ctx["tb"])
    if api.code(r) in DENY_CODES:
        rep.ok("T10", "B 查 A 的任务 -> 拒绝")
    else:
        rep.fail("T10", f"B 查 A 的任务未被拒（{api.code(r)}）", api.text(r))

    vurl = ctx.get("video_url")
    if not vurl:
        rep.skip("T10", "任务未完成，暂无 video_url，跳过签名播放检查")
        return
    if "sig=" not in vurl:
        rep.fail("T10", "job 状态里的 video_url 未签名", vurl)
        return
    r = api.call("GET", vurl)  # vurl 形如 /agent/v1/job/<id>/video?exp=..&sig=..
    if r.status_code in (200, 206):
        rep.ok("T10", "签名视频地址可匿名播放（<video src> 场景）")
    else:
        rep.fail("T10", f"签名视频地址匿名播放失败（{api.code(r)}）", api.text(r))


def t11_log_scan(api, rep, ctx, args):
    """T11 日志判据：无 GeneratorExit、无未签名放行、有归属拒绝记录。"""
    if not args.log_file or not os.path.isfile(args.log_file):
        rep.skip("T11", "未提供 --log-file，跳过日志扫描")
        return
    with open(args.log_file, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(ctx.get("log_offset", 0))
        log = f.read()

    bad = {
        "generator ignored GeneratorExit": "SSE 断连仍触发 GeneratorExit",
        "generator ignored": "生成器忽略退出",
        "Traceback (most recent call last)": "存在未捕获异常栈",
    }
    for needle, desc in bad.items():
        n = log.count(needle)
        if n:
            rep.fail("T11", f"日志出现 {desc} ×{n}", needle)
        else:
            rep.ok("T11", f"日志无「{desc}」")

    gray = log.count("未签名且未登录的下载被放行")
    if args.strict:
        if gray:
            rep.fail("T11", f"strict 模式仍有未签名放行 ×{gray}")
        else:
            rep.ok("T11", "strict 模式无未签名放行记录")
    else:
        rep.info("T11", f"灰度模式未签名放行 ×{gray}（切 strict 前应为 0）")

    denied = log.count("越权写入被拒绝") + log.count("归属校验拒绝") + log.count("归属拒绝")
    if denied:
        rep.ok("T11", f"归属拒绝日志 ×{denied}（说明校验真的生效）")
    else:
        rep.info("T11", "未看到归属拒绝日志（可能日志级别或文件不对）")


# ============================================================
# main
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="agent P0 验收脚本")
    ap.add_argument("--base", default="http://127.0.0.1:9000")
    ap.add_argument("--token-a", help="用户A的token（不传则自动往Redis造）")
    ap.add_argument("--token-b", help="用户B的token")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--redis-db", type=int, default=0)
    ap.add_argument("--redis-password", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="require_download_auth=true 时加这个；灰度模式不传")
    ap.add_argument("--project-root", help="项目根目录（检查越权落盘）")
    ap.add_argument("--log-file", help="服务日志文件（用于 T11 判据）")
    ap.add_argument("--task-id", help="一个已完成的视频任务ID（用于 T10）")
    ap.add_argument("--throttle", type=float, default=0.25,
                    help="每个请求之间的最小间隔秒数（默认 0.25，用于避开 chat 5QPS 桶）")
    ap.add_argument("--no-retry", action="store_true",
                    help="关闭 429 退避重试（想看限流原样出现时用）")
    args = ap.parse_args()

    api = Api(args.base, throttle=args.throttle,
              retries=0 if args.no_retry else 1)
    rep = Report()
    ctx = {}

    # 探活
    try:
        api.call("GET", "/agent/v1/conversations", timeout=5)
    except Exception as e:
        print(f"服务不可达：{args.base} -> {e}")
        return 2

    if args.log_file and os.path.isfile(args.log_file):
        ctx["log_offset"] = os.path.getsize(args.log_file)

    seeded = None
    if args.token_a and args.token_b:
        ctx["ta"], ctx["tb"] = args.token_a, args.token_b
        print(f"使用传入 token（A/B）")
    else:
        ta, tb, conn = seed_tokens(args)
        seeded = (conn, ta, tb)
        ctx["ta"], ctx["tb"] = ta, tb
        print(f"已注入测试 token：A userId=990001 / B userId=990002")

    print(f"目标服务：{args.base}   模式：{'strict' if args.strict else 'gray'}\n" + "-" * 78)

    try:
        for fn in (t01_auth_matrix, t02_session_ownership, t03_illegal_session,
                   t04_upload_ownership, t05_thumb_auth, t06_path_traversal,
                   t07_knowledge_auth, t08_regression_chat, t09_sse_disconnect,
                   t10_job_and_signed_url, t11_log_scan):
            try:
                fn(api, rep, ctx, args)
            except Exception as e:
                rep.fail(fn.__name__, f"用例执行异常: {e}")
    finally:
        if seeded:
            conn, ta, tb = seeded
            try:
                conn.delete(ta, tb)
                print("已清理测试 token")
            except Exception:
                pass

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acceptance-report.json")

    # 限流情况如实汇总（不隐藏：重试成功记 INFO，重试失败已在用例里 FAIL）
    if api.rate_limited:
        hit = len(api.rate_limited)
        exhausted = sum(1 for _, a in api.rate_limited if a == 0)
        rep.info("T00", f"本次运行遭遇 429 共 {hit} 次，其中重试后仍失败 {exhausted} 次",
                 str(sorted({p for p, _ in api.rate_limited})[:3]))
    else:
        rep.ok("T00", "全程未触发限流")

    rep.dump(out)

    print("-" * 78)
    print(f"PASS={sum(1 for r in rep.rows if r['status']=='PASS')}  "
          f"FAIL={len(rep.fails)}  "
          f"SKIP={sum(1 for r in rep.rows if r['status']=='SKIP')}  "
          f"耗时={time.time()-rep.t0:.1f}s")
    print(f"报告：{out}")
    if rep.fails:
        print("\n未通过用例：")
        for r in rep.fails:
            print(f"  - [{r['id']}] {r['name']} | {r['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())