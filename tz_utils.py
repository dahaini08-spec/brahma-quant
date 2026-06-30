"""
tz_utils.py — 梵天时区统一工具
================================
原则：
  · 内部存储 / 计算 / 比较 → 始终使用 UTC ISO 格式（不改动）
  · 用户可见输出 / 日志 / 报告 → 统一使用北京时间 (CST = UTC+8)

使用方式：
  from tz_utils import now_cst, now_cst_str, utc_to_cst, cst_date

  now_cst()            → datetime 对象（带 +08:00 tzinfo）
  now_cst_str()        → '2026-05-17 08:46:23 CST'
  now_cst_short()      → '05/17 08:46'
  now_cst_hms()        → '08:46:23'
  now_cst_date()       → '2026-05-17'
  utc_to_cst(dt)       → 将任意 UTC datetime 转为 CST datetime
  utc_iso_to_cst_str(s)→ '2026-05-17T00:46:23Z' → '2026-05-17 08:46:23 CST'
"""

from datetime import datetime, timezone, timedelta

# 中国标准时间 UTC+8
CST = timezone(timedelta(hours=8), name='CST')


def now_cst() -> datetime:
    """返回当前北京时间 datetime（带 tzinfo=CST）"""
    return datetime.now(CST)


def now_cst_str(fmt: str = '%Y-%m-%d %H:%M:%S CST') -> str:
    """返回当前北京时间格式化字符串，默认 '2026-05-17 08:46:23 CST'"""
    return datetime.now(CST).strftime(fmt)


def now_cst_short() -> str:
    """返回短格式 '05/17 08:46'"""
    return datetime.now(CST).strftime('%m/%d %H:%M')


def now_cst_hms() -> str:
    """返回时分秒 '08:46:23'"""
    return datetime.now(CST).strftime('%H:%M:%S')


def now_cst_date() -> str:
    """返回北京日期 '2026-05-17'"""
    return datetime.now(CST).strftime('%Y-%m-%d')


def now_cst_ymd_hm() -> str:
    """返回 '2026-05-17 08:46'"""
    return datetime.now(CST).strftime('%Y-%m-%d %H:%M')


def utc_to_cst(dt: datetime) -> datetime:
    """将 UTC datetime 转换为 CST datetime"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST)


def utc_iso_to_cst_str(iso_str: str, fmt: str = '%Y-%m-%d %H:%M:%S CST') -> str:
    """
    将 UTC ISO 字符串转为北京时间字符串
    支持：'2026-05-17T00:46:23Z' / '2026-05-17T00:46:23+00:00' / '2026-05-17T00:46:23'
    """
    try:
        s = iso_str.replace('Z', '+00:00')
        if '+' not in s and 'T' in s:
            s += '+00:00'
        dt = datetime.fromisoformat(s)
        return utc_to_cst(dt).strftime(fmt)
    except Exception:
        return iso_str  # 解析失败原样返回


def now_utc_iso() -> str:
    """返回当前 UTC ISO 字符串（内部存储用，保持不变）"""
    return datetime.now(timezone.utc).isoformat()


# ── 快捷别名 ──────────────────────────────────────────────
def ts_display() -> str:
    """日志头部时间戳：'[08:46:23 CST]'"""
    return f"[{now_cst_hms()} CST]"


def report_header() -> str:
    """报告抬头时间戳：'2026-05-17 08:46 CST (北京时间)'"""
    return datetime.now(CST).strftime('%Y-%m-%d %H:%M CST') + '（北京时间）'
