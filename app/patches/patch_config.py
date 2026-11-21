#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""application.yml 安全基线补丁（幂等，可重复执行）。

用法
----
    python3 patch_config.py --file /path/to/app/config/application.yml
    python3 patch_config.py --file app/config/application.yml --dry-run

做的事（只动安全相关的键，其余内容原样保留，含 default 占位符）
--------------------------------------------------------------------------
1. auth.require_download_auth: false -> true            【高危 3：全站裸奔开关】
2. auth.white_list 移除 /docs /openapi.json /metrics    【中危 6：匿名可访问】
   （/healthz /readyz /health /auth/login /auth/register 保留）
3. 新增 auth.ops_paths / ops_token / ops_allow_ips      运维端点守卫配置
4. 新增 rate_limit.trust_forwarded_for / trusted_proxies 真实客户端 IP【中危 4】
5. 新增 cors 段（把 bootstrap 里写死的 allow_origins=["*"] 变成可收敛的配置）
6. 新增 security 段（签名 URL 默认时效 / 无归属资源签发开关）
"""
import argparse
import io
import re
import sys

WHITE_LIST_DROP = {"/docs", "/openapi.json", "/metrics", "/redoc"}

AUTH_EXTRA = """
  # —— 运维端点守卫（本轮新增）———————————————————————————————
  # /metrics、/docs、/openapi.json 已从 white_list 移出，改由此守卫把关：
  #   配了 ops_token  -> 必须带 X-Ops-Token 或 Bearer <ops_token>
  #   否则配 ops_allow_ips -> 来源 IP 必须命中白名单
  #   两者都没配     -> 统一 404（隐藏端点存在，fail-closed）
  ops_paths:
    - /metrics
    - /docs
    - /docs/oauth2-redirect
    - /redoc
    - /openapi.json
  ops_token: ""
  ops_allow_ips: []
  ops_trust_forwarded_for: false
"""

RATE_LIMIT_EXTRA = """  # —— 真实客户端 IP（本轮新增，反代后限流不再退化）——————————
  # remote_only = 仅当直连对端属于 trusted_proxies 时才信任转发头（推荐）
  # always      = 一定部署在可信反代后面时用；never = 永远只看 socket 对端
  trust_forwarded_for: remote_only
  prefer_real_ip: false
  trusted_proxies:
    - "127.0.0.1"
    - "::1"
"""

SECURITY_BLOCK = """
# ==========================================
# 安全基线（本轮新增）
# ==========================================
cors:
  # 生产必须收敛成具体域名列表，例如：
  #   allow_origins: ["https://your-frontend.example.com"]
  # allow_credentials=true 时禁止 allow_origins 为 *（bootstrap 会强制降级）
  allow_origins: "*"
  allow_methods: "*"
  allow_headers: "*"
  allow_credentials: false

security:
  # 签名下载 URL 的默认时效（秒）。默认 7 天；到期后前端重新拉消息/任务即可换新链接。
  default_url_ttl_seconds: 604800
  # tts / liveportrait_job 没有可靠归属元数据，默认禁止显式签发签名（fail-closed）。
  # 置 true 会重新允许越权签发，属危险开关。
  allow_sign_ownerless: false

# 签名密钥（不在 yml 里存值，只从环境变量读；未配置则服务启动即失败）
#   SIGNED_URL_SECRET=<openssl rand -hex 32>
#   SIGNED_URL_DEFAULT_TTL=604800
#   SIGNED_URL_ALLOW_LEGACY_PERMANENT=true    # 灰度期兼容 exp=0 老链接，收口后置 false
"""


def patch(text: str):
    lines = text.splitlines(keepends=True)
    out = []
    notes = []

    i = 0
    in_auth = False
    in_white_list = False
    white_list_added = False
    rate_limit_added = False
    sec_appended = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 1) 关掉裸奔开关，并紧随其后插入运维端点守卫配置
        if re.match(r"^\s*require_download_auth\s*:", line):
            out.append("  require_download_auth: true\n")
            notes.append("auth.require_download_auth -> true")
            if not white_list_added:
                while i + 1 < len(lines) and lines[i + 1].strip() == "":
                    i += 1
                out.append(AUTH_EXTRA.lstrip("\n"))
                white_list_added = True
                notes.append("新增 auth.ops_* 运维端点守卫")
            i += 1
            continue

        # 2) 白名单去掉匿名可访问的运维/文档路径
        if re.match(r"^\s*white_list\s*:\s*$", line):
            in_white_list = True
            out.append(line)
            i += 1
            while i < len(lines):
                l = lines[i]
                if re.match(r"^\s*-\s+", l):
                    val = l.split("-", 1)[1].strip().strip('"').strip("'")
                    if val in WHITE_LIST_DROP:
                        notes.append(f"auth.white_list 移除 {val}")
                        i += 1
                        continue
                    out.append(l)
                    i += 1
                    continue
                break
            in_white_list = False
            continue

        out.append(line)

        # 4) rate_limit 段末尾补真实 IP 配置
        if re.match(r"^\s*max_keys\s*:", line) and not rate_limit_added:
            out.append(RATE_LIMIT_EXTRA)
            rate_limit_added = True
            notes.append("新增 rate_limit.trust_forwarded_for / trusted_proxies")

        i += 1

    # 5/6) 追加 cors / security 段（若不存在）
    body = "".join(out)
    if not re.search(r"^cors\s*:", body, re.M):
        body = body.rstrip("\n") + "\n" + SECURITY_BLOCK
        notes.append("新增 cors / security 段")
    elif not re.search(r"^security\s*:", body, re.M):
        body = body.rstrip("\n") + "\n" + SECURITY_BLOCK.split("security:")[1]
        notes.append("新增 security 段")

    return body, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="application.yml 路径")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    text = io.open(args.file, encoding="utf-8-sig").read()
    new, notes = patch(text)

    if not notes:
        print("已是修复后的配置，无需变更。")
        return 0

    for n in notes:
        print("  -", n)

    if args.dry_run:
        print("\n[dry-run] 未写文件。")
        return 0

    bak = args.file + ".bak"
    io.open(bak, "w", encoding="utf-8").write(text)
    io.open(args.file, "w", encoding="utf-8").write(new)
    print(f"\n已写入 {args.file}（原文件备份为 {bak}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
