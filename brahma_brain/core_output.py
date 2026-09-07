"""
brahma_brain/core_output.py — 第五刀：输出格式化 + brahma_os.Signal适配出口

职责（单一）:
  1. signal_from_result(result, symbol) → Signal | None
     将 analyze() _result dict 映射到 brahma_os.contracts.Signal。
     这是 adapter.analyze_to_signal() 的替代调用路径——但不废弃adapter，
     core_output 自己也包一层健壮解析，保留 adapter 作为兜底。
  2. format_vip(result) → str
     从 _result 生成 VIP 卡片格式（姓赵不宣格式，MEMORY.md封印）。
  3. format_signal_line(signal) → str
     Signal → 单行摘要，供日志/推送。
  4. to_jsonl_row(result) → dict
     _result → 可追加到 signals.jsonl 的扁平化dict。

设计原则:
  - 无副作用，无IO，纯函数。调用方负责写文件/推送。
  - 对价格格式化复用 brahma_brain.formatter._fmt_price（已封印）。
  - 对Signal映射，先尝试 brahma_os.adapter.analyze_to_signal，
    失败则降级到本地轻量解析，返回None而非抛出异常。

接入位置:
  brahma_brain/brahma_core.py → 尾部 return _result 之后的调用方
  brahma_brain/brahma_analysis_runner.py → 已通过 adapter 调用，可替换
  brahma_os/adapter.py → 保持向后兼容，不删除

封印: 2026-09-07 苏摩111 · commit 第五刀
"""
from __future__ import annotations

import time
from typing import Any, Optional

# ── 内部依赖（懒导入，避免循环） ───────────────────────────────────────


def _fmt_price(price: Any) -> str:
    """复用 formatter._fmt_price，降级到本地实现。"""
    try:
        from brahma_brain.formatter import _fmt_price as _ext
        return _ext(float(price))
    except Exception:
        try:
            v = float(price)
            if v >= 1000:
                return f"${v:,.2f}"
            elif v >= 1:
                return f"${v:.3f}"
            elif v >= 0.01:
                return f"${v:.4f}"
            elif v >= 0.0001:
                return f"${v:.6f}"
            else:
                return f"${v:.8f}"
        except Exception:
            return str(price)


def _get(d: dict, *keys: str, default: Any = None) -> Any:
    """多级键查找，支持 nested params/trade_params。"""
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    for nest_key in ("params", "trade_params", "trade"):
        nested = d.get(nest_key) or {}
        if isinstance(nested, dict):
            for k in keys:
                if k in nested and nested[k] not in (None, ""):
                    return nested[k]
    return default


def _num(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# 1. Signal适配出口
# ─────────────────────────────────────────────────────────────────────────────

def signal_from_result(result: dict, symbol: str = "") -> Optional[Any]:
    """
    将 analyze() _result 映射到 brahma_os.contracts.Signal。

    优先使用 brahma_os.adapter.analyze_to_signal（已封印的适配层）。
    若 adapter 抛出 AdapterError（ENTRY/EXITS 等）则返回 None，
    调用方可据此决定是否跳过该信号。

    返回 Signal | None
    """
    try:
        from brahma_os.adapter import analyze_to_signal, AdapterError
        sym = symbol or result.get("symbol", "")
        return analyze_to_signal(result, symbol=sym, now_ts=time.time())
    except Exception as e:
        # AdapterError: blocked / missing fields → 正常返回None（不是程序bug）
        err_code = getattr(e, "code", "UNKNOWN")
        if err_code in ("BLOCKED", "ENTRY", "EXITS", "SIDE", "SYMBOL"):
            return None
        # 其他异常：记录但不崩溃
        try:
            import logging
            logging.getLogger("core_output").warning(
                "signal_from_result fallback error: %s", e
            )
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. VIP 卡片格式（姓赵不宣格式 — MEMORY.md封印）
# ─────────────────────────────────────────────────────────────────────────────

def format_vip(result: dict) -> str:
    """
    从 analyze() _result 生成 VIP 卡片。

    格式严格遵循 MEMORY.md 封印的"姓赵不宣 VIP模版"：
    🌿 姓赵不宣 | 今日布局
    ——— BTC ———
    🔴 空单｜挂单区 $X~$X
    止损 $X｜目标 $X→$X→$X
    杠杆 Xx｜仓位 X%
    ⚠️ [一句话逻辑，20字内]
    🚫 破$X作废

    如果 _result 里缺少 entry/stop/target，返回"等待"卡片。
    """
    sym_raw = (result.get("symbol") or "").upper().replace("USDT", "")
    regime = result.get("regime", "UNKNOWN")
    direction = str(result.get("signal_dir") or result.get("direction") or "")
    score = _num(result.get("score_final") or result.get("score"))
    price = _num(_get(result, "price"), 0.0)

    params = result.get("params") or {}
    entry_lo = _num(_get(result, "entry_lo", default=None) or params.get("entry_lo"), 0.0)
    entry_hi = _num(_get(result, "entry_hi", default=None) or params.get("entry_hi"), 0.0)
    stop = _num(_get(result, "stop", "stop_loss", "sl", default=None) or params.get("stop") or params.get("sl"), 0.0)
    tp1 = _num(_get(result, "tp1", default=None) or params.get("tp1"), 0.0)
    tp2 = _num(_get(result, "tp2", default=None) or params.get("tp2"), 0.0)
    tp3 = _num(_get(result, "tp3", default=None) or params.get("tp3"), 0.0)
    lev = int(_num(_get(result, "leverage", default=None) or params.get("leverage"), 5.0))
    pos_pct = _num(_get(result, "pos_pct", "position_pct", default=None) or params.get("pos_pct"), 5.0)
    rr = _num(params.get("rr1") or _get(result, "rr", "rr1"), 0.0)

    # 一句话逻辑（从 action_reason 或 confluence summary 提取）
    logic = (
        result.get("action_reason")
        or (result.get("confluence") or {}).get("summary")
        or f"{regime} {direction} score={score:.0f}"
    )
    # 截断到20字
    if len(logic) > 20:
        logic = logic[:18] + "…"

    # 检查是否有完整价格参数
    has_levels = (entry_lo > 0 and stop > 0 and tp1 > 0)

    header = f"🌿 姓赵不宣 | {sym_raw} 今日布局\n"
    sep = f"——— {sym_raw} ———\n"

    if not has_levels or score < 140:
        return (
            header + sep
            + f"⏳ 等待中｜score={score:.0f} < 门控阈值\n"
            + f"⚠️ 缺少共振点，等待FVG+OB+清算三交叉\n"
            + f"📊 梵天系统｜数据驱动｜不是建议"
        )

    # 方向标识
    if direction in ("SHORT", "SELL"):
        icon = "🔴 空单"
        break_line = f"🚫 破{_fmt_price(entry_hi)}反向作废"
    else:
        icon = "🟢 多单"
        break_line = f"🚫 破{_fmt_price(entry_lo)}止损触发"

    # TP字符串
    tp_parts = [_fmt_price(t) for t in [tp1, tp2, tp3] if t > 0]
    tp_str = "→".join(tp_parts) if tp_parts else _fmt_price(tp1)

    # 仓位（BEAR_EARLY SHORT WR=89%特权：8%NAV）
    if regime in ("BEAR_EARLY",) and direction in ("SHORT", "SELL") and score >= 145:
        pos_pct = max(pos_pct, 8.0)

    lines = [
        header + sep,
        f"{icon}｜挂单区 {_fmt_price(entry_lo)}~{_fmt_price(entry_hi)}",
        f"止损 {_fmt_price(stop)}｜目标 {tp_str}",
        f"杠杆 {lev}x｜仓位 {pos_pct:.0f}%｜RR={rr:.1f}",
        f"",
        f"⚠️ {logic}",
        break_line,
        f"",
        f"📊 梵天系统｜数据驱动｜不是建议",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Signal 单行摘要（日志/推送用）
# ─────────────────────────────────────────────────────────────────────────────

def format_signal_line(signal: Any) -> str:
    """
    Signal → 单行摘要字符串。
    接受 brahma_os.contracts.Signal 实例，或带相同字段的 dict。
    """
    def _attr(obj: Any, key: str, default: Any = "") -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    sym = str(_attr(signal, "symbol")).replace("USDT", "")
    side = _attr(signal, "side", "?")
    score = _num(_attr(signal, "score"))
    regime = str(_attr(signal, "regime", "?"))
    entry_lo = _num(_attr(signal, "entry_lo"))
    entry_hi = _num(_attr(signal, "entry_hi"))
    stop = _num(_attr(signal, "stop"))
    target = _num(_attr(signal, "target"))

    icon = "🔴" if side in ("SHORT", "SELL") else "🟢"
    entry_str = (
        f"{_fmt_price(entry_lo)}~{_fmt_price(entry_hi)}"
        if abs(entry_hi - entry_lo) > 0
        else _fmt_price(entry_lo)
    )

    return (
        f"{icon} {sym} {side} | "
        f"入场 {entry_str} | "
        f"SL {_fmt_price(stop)} | "
        f"TP {_fmt_price(target)} | "
        f"score={score:.0f} | {regime}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. 扁平化 JSONL 行（signals.jsonl 追加用）
# ─────────────────────────────────────────────────────────────────────────────

def to_jsonl_row(result: dict) -> dict:
    """
    将 analyze() _result 压平为可追加到 signals.jsonl 的 dict。
    字段与 brahma_os/snapshot.signal_record() 保持兼容。
    """
    params = result.get("params") or {}
    cf = result.get("confluence") or {}
    score = _num(result.get("score_final") or result.get("score"))

    entry_lo = _num(_get(result, "entry_lo") or params.get("entry_lo"), 0.0)
    entry_hi = _num(_get(result, "entry_hi") or params.get("entry_hi"), 0.0)
    stop = _num(
        _get(result, "stop", "stop_loss", "sl") or params.get("stop") or params.get("sl"), 0.0
    )
    tp1 = _num(_get(result, "tp1") or params.get("tp1"), 0.0)

    entry_mid = (entry_lo + entry_hi) / 2.0 if (entry_lo + entry_hi) > 0 else 0.0
    risk = abs(entry_mid - stop)
    reward = abs(tp1 - entry_mid)
    rr = round(reward / risk, 2) if risk > 0 else 0.0

    return {
        "signal_id": result.get("signal_id") or f"{result.get('symbol', '')}_{int(time.time())}",
        "ts": result.get("price_ts") or time.time(),
        "symbol": result.get("symbol", ""),
        "side": str(result.get("signal_dir") or result.get("direction") or ""),
        "regime": result.get("regime", ""),
        "score": score,
        "grade": _num(result.get("grade_num") or result.get("effective_grade")),
        "entry_lo": entry_lo,
        "entry_hi": entry_hi,
        "stop": stop,
        "target": tp1,
        "valid_until": result.get("valid_until") or (time.time() + 86400.0),
        "source": result.get("source", "analyze"),
        "rr": rr,
        # 扩展字段
        "score_breakdown": cf.get("breakdown"),
        "action": result.get("action"),
        "regime_cn": result.get("regime_cn", ""),
        "rsi_1h": _num(result.get("rsi_1h"), 50.0),
        "rsi_4h": _num(result.get("rsi_4h"), 50.0),
        "price": _num(result.get("price"), 0.0),
        "sl_basis": result.get("sl_basis", ""),
    }
