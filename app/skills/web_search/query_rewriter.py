# app/application/skills/search_skill/query_rewriter.py
"""
查询词重写：中文口语 → 搜索引擎友好。
"""
import re
from datetime import datetime, timedelta
from typing import Optional


# ============================================================
# 时间表达式（长的优先，命中第一个 break）
# ============================================================
_TIME_PATTERNS = [
    # 未来
    (r"大后天",                 3,   "day"),
    (r"后天",                   2,   "day"),
    (r"明天|明日",              1,   "day"),
    (r"下周[一二三四五六日天]?", 7,   "day"),
    (r"下个?月",                30,  "month"),
    (r"明年",                   365, "year"),
    # 过去
    (r"(?:最近|近|过去)\s*半年",   -180, "month"),
    (r"(?:最近|近|过去)\s*一年",   -365, "year"),
    (r"(?:最近|近|过去)\s*三个月", -90,  "month"),
    (r"(?:最近|近|过去)\s*一个月|上个月", -30, "month"),
    (r"(?:最近|近|过去)\s*两周",   -14,  "day"),
    (r"(?:最近|近|过去)\s*一周|这周|本周", -7, "day"),
    (r"昨天",                    -1,   "day"),
    # 今天
    (r"今天|今日",                0,   "day"),
    # 兜底
    (r"最近|近期",                -7,   "day"),
]


_FILLER_WORDS = (
    "有没有", "是否有", "什么", "怎么样", "如何", "请问", "帮我",
    "查一下", "查查", "关于", "相关的", "的新闻", "吗", "呢", "啊",
)


# ============================================================
# 按场景分类：是否加时间标签
# ============================================================
# 这些场景**不加日期**（Bing 对纯关键词最准）
_NO_DATE_SCENES = (
    "天气", "气温", "温度", "下雨", "下雪", "刮风", "晴天", "阴天", "多云",
    "空气质量", "PM2.5", "雾霾",
)

# 这些场景**加日期**（新闻类需要时间限定）
_NEED_DATE_SCENES = (
    "新闻", "事件", "动态", "发布会", "比赛", "股价", "汇率",
    "空难", "事故", "会议", "政策", "融资", "财报",
)


def _should_add_date(text: str) -> bool:
    """判断查询是否应该加日期标签"""
    # 天气类：不加日期（Bing 加日期反而不准）
    if any(kw in text for kw in _NO_DATE_SCENES):
        return False
    # 新闻/事件类：加日期
    if any(kw in text for kw in _NEED_DATE_SCENES):
        return True
    # 其它：默认不加日期（避免日期成为主 token 污染结果）
    return False


# ============================================================
# 时间标签构造（⭐ 已修 days=1 的 bug）
# ============================================================
def _build_time_label(days: int, granularity: str) -> str:
    """
    按天数偏移生成时间标签。
    ⭐ 关键：明天(days=1) 返回 target（明天日期），不是 today。
    """
    today = datetime.now()
    target = today + timedelta(days=days)

    # 未来/过去 1-3 天的单日：明天、后天、大后天、昨天、前天、大前天
    if granularity == "day" and 0 < abs(days) <= 3:
        return target.strftime("%Y年%m月%d日")

    # 今天
    if days == 0:
        return today.strftime("%Y年%m月%d日")

    # 年粒度
    if granularity == "year":
        if target.year == today.year:
            return f"{target.year}年"
        if days > 0:
            return f"{today.year}年至{target.year}年"
        return f"{target.year}年至{today.year}年"

    # 月粒度
    if granularity == "month":
        if target.year == today.year and target.month == today.month:
            return f"{today.year}年{today.month}月"
        if days > 0:
            return f"{today.strftime('%Y年%m月')}至{target.strftime('%Y年%m月')}"
        return f"{target.strftime('%Y年%m月')}至{today.strftime('%Y年%m月')}"

    # 多日区间（如"最近一周"=7 天，返回区间）
    if days > 0:
        return f"{today.strftime('%Y年%m月%d日')}至{target.strftime('%Y年%m月%d日')}"
    return f"{target.strftime('%Y年%m月%d日')}至{today.strftime('%Y年%m月%d日')}"


# ============================================================
# 主入口
# ============================================================
def rewrite(text: str, synonyms: Optional[dict] = None) -> str:
    """
    口语查询 → 搜索引擎友好。

    ⭐ 关键逻辑：
      - 天气类 → 不加日期（只剥离时间词）
      - 新闻类 → 加日期标签
      - 其它 → 不加日期
    """
    original = text
    q = text.strip()

    # 1. 是否加日期
    add_date = _should_add_date(original)

    # 2. 时间词剥离（不论加不加日期都要剥离）
    time_label = ""
    for pattern, days, granularity in _TIME_PATTERNS:
        m = re.search(pattern, q)
        if m:
            if add_date:
                time_label = _build_time_label(days, granularity)
            q = re.sub(pattern, " ", q)
            break

    # 3. 虚词去除
    for filler in _FILLER_WORDS:
        q = q.replace(filler, " ")

    # 4. 标点清理
    q = re.sub(r"[？?。！!，,、；;：:]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()

    # 5. 可选同义词
    if synonyms:
        for kw, enhanced in synonyms.items():
            if kw in q:
                q = q.replace(kw, enhanced)
                break

    # 6. 拼装
    if time_label:
        return f"{time_label} {q}".strip()
    return q or original