"""本地小工具集（无需联网）：日期、计算、换算、日期差、随机选择。"""
import ast
import operator
import random
from datetime import datetime, timedelta

from app.core.logger import logger


# ============================================================
# 安全计算
# ============================================================
_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}


def _safe_eval(expr: str) -> float:
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    node = ast.parse(expr, mode="eval")
    return float(_eval_node(node.body))


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        return _SAFE_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        return _SAFE_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"不支持的表达式类型: {type(node).__name__}")


# ============================================================
# 工具 1：日期时间
# ============================================================
def tool_datetime_now(format: str = "full") -> dict:
    try:
        now = datetime.now()
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        if format == "date":
            content = f"今天是 {now.strftime('%Y年%m月%d日')}"
        elif format == "time":
            content = f"现在是 {now.strftime('%H:%M')}"
        elif format == "weekday":
            content = f"今天是星期{weekdays[now.weekday()]}"
        else:
            content = (
                f"今天是 {now.strftime('%Y年%m月%d日')} "
                f"星期{weekdays[now.weekday()]} {now.strftime('%H:%M')}"
            )
        return {"type": "text", "content": content}
    except Exception as e:
        logger.error(f"[Utility-datetime] 失败: {e}")
        return {"type": "text", "content": f"日期查询失败: {e}"}


# ============================================================
# 工具 2：计算器
# ============================================================
def tool_calculator(expression: str) -> dict:
    try:
        result = _safe_eval(expression)
        if result == int(result):
            display = str(int(result))
        else:
            display = f"{result:.6f}".rstrip("0").rstrip(".")
        return {"type": "text", "content": f"{expression} = {display}"}
    except Exception as e:
        logger.warning(f"[Utility-calc] 计算失败 '{expression}': {e}")
        return {"type": "text", "content": f"计算失败：{e}"}


# ============================================================
# 工具 3：百分比
# ============================================================
def tool_percentage(value: float, total: float) -> dict:
    try:
        result = total * value / 100
        if result == int(result):
            display = str(int(result))
        else:
            display = f"{result:.4f}".rstrip("0").rstrip(".")
        return {"type": "text", "content": f"{total} 的 {value}% = {display}"}
    except Exception as e:
        return {"type": "text", "content": f"百分比计算失败: {e}"}


# ============================================================
# 工具 4：单位换算
# ============================================================
_UNIT_CONVERSIONS = {
    "m": 1, "米": 1, "km": 1000, "公里": 1000, "千米": 1000,
    "cm": 0.01, "厘米": 0.01, "mm": 0.001, "毫米": 0.001,
    "mile": 1609.34, "英里": 1609.34,
    "ft": 0.3048, "英尺": 0.3048,
    "inch": 0.0254, "英寸": 0.0254,
    "kg": 1, "公斤": 1, "千克": 1,
    "g": 0.001, "克": 0.001,
    "斤": 0.5,
    "lb": 0.453592, "磅": 0.453592,
    "ton": 1000, "吨": 1000,
}


def tool_unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    try:
        f = from_unit.strip().lower()
        t = to_unit.strip().lower()
        if f not in _UNIT_CONVERSIONS or t not in _UNIT_CONVERSIONS:
            return {"type": "text", "content": f"不支持的单位：{from_unit} → {to_unit}"}
        length_units = {"m", "米", "km", "公里", "千米", "cm", "厘米", "mm", "毫米", "mile", "英里", "ft", "英尺", "inch", "英寸"}
        weight_units = {"kg", "公斤", "千克", "g", "克", "斤", "lb", "磅", "ton", "吨"}
        if (f in length_units and t not in length_units) or (f in weight_units and t not in weight_units):
            return {"type": "text", "content": f"不能跨量纲换算：{from_unit} → {to_unit}"}
        base_value = value * _UNIT_CONVERSIONS[f]
        result = base_value / _UNIT_CONVERSIONS[t]
        if result == int(result):
            display = str(int(result))
        else:
            display = f"{result:.4f}".rstrip("0").rstrip(".")
        return {"type": "text", "content": f"{value} {from_unit} = {display} {to_unit}"}
    except Exception as e:
        return {"type": "text", "content": f"换算失败: {e}"}


# ============================================================
# 工具 5：温度换算
# ============================================================
def tool_temperature_convert(value: float, from_unit: str, to_unit: str) -> dict:
    try:
        f = "c" if from_unit in ("celsius", "摄氏度", "C", "°C") else "f"
        t = "c" if to_unit in ("celsius", "摄氏度", "C", "°C") else "f"
        if f == t:
            result = value
        elif f == "c" and t == "f":
            result = value * 9 / 5 + 32
        else:
            result = (value - 32) * 5 / 9
        display = f"{result:.2f}".rstrip("0").rstrip(".")
        return {"type": "text", "content": f"{value}°{from_unit.upper()} = {display}°{to_unit.upper()}"}
    except Exception as e:
        return {"type": "text", "content": f"温度换算失败: {e}"}


# ============================================================
# 工具 6：日期差
# ============================================================
def tool_date_diff(date1: str, date2: str) -> dict:
    try:
        def _parse(s: str) -> datetime:
            s = s.strip()
            if s in ("今天", "今日", "现在"):
                return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if s in ("明天", "明日"):
                return (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            if s in ("昨天", "昨日"):
                return (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            raise ValueError(f"无法解析日期: {s}")

        d1 = _parse(date1)
        d2 = _parse(date2)
        delta = (d2 - d1).days
        if delta == 0:
            return {"type": "text", "content": f"{date1} 和 {date2} 是同一天"}
        direction = "后" if delta > 0 else "前"
        return {"type": "text", "content": f"从 {date1} 到 {date2} 相差 {abs(delta)} 天（{date2} 在 {date1} 的{direction}）"}
    except Exception as e:
        return {"type": "text", "content": f"日期差计算失败: {e}"}


# ============================================================
# 工具 7：随机选择
# ============================================================
def tool_random_pick(options: list) -> dict:
    try:
        if not options:
            return {"type": "text", "content": "没有可选的选项"}
        chosen = random.choice(options)
        return {"type": "text", "content": f"从 {options} 里随机选了：{chosen}"}
    except Exception as e:
        return {"type": "text", "content": f"随机选择失败: {e}"}


# ============================================================
# 工具元数据（供 LLM 选择器用）
# ============================================================
TOOLS_SCHEMA = {
    "datetime_now": {
        "description": "查询当前日期、时间、星期几。用户问'今天几号'、'现在几点'、'今天星期几'时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["date", "time", "weekday", "full"]},
            },
            "required": ["format"],
        },
        "func": tool_datetime_now,
    },
    "calculator": {
        "description": "计算数学表达式。支持 + - * / ** % ( )。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
            },
            "required": ["expression"],
        },
        "func": tool_calculator,
    },
    "percentage": {
        "description": "计算百分比。如'100的20%是多少'。",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "total": {"type": "number"},
            },
            "required": ["value", "total"],
        },
        "func": tool_percentage,
    },
    "unit_convert": {
        "description": "长度/重量单位换算。如'100公斤等于多少斤'、'5公里等于多少米'。",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
        "func": tool_unit_convert,
    },
    "temperature_convert": {
        "description": "温度换算（摄氏度 ↔ 华氏度）。",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            },
            "required": ["value", "from_unit", "to_unit"],
        },
        "func": tool_temperature_convert,
    },
    "date_diff": {
        "description": "计算两个日期相差多少天。日期格式 YYYY-MM-DD。",
        "parameters": {
            "type": "object",
            "properties": {
                "date1": {"type": "string"},
                "date2": {"type": "string"},
            },
            "required": ["date1", "date2"],
        },
        "func": tool_date_diff,
    },
    "random_pick": {
        "description": "从多个选项里随机选一个。",
        "parameters": {
            "type": "object",
            "properties": {
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["options"],
        },
        "func": tool_random_pick,
    },
}