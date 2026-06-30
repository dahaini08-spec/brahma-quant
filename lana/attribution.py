"""
attribution.py — 梵天归因分析官 v1.0
每笔交易平仓后，记录「为什么开、为什么赢/亏」
输出结构化归因，喂给 evolve_kelly 做参数自适应

归因维度:
  - 体制匹配度（regime 是否符合方向）
  - 入场质量（score 分布、共振指标）
  - 出场原因（SL/TP1/TP2/手动/超时）
  - 时段特征（UTC 小时）
  - 品种特征（symbol 历史胜率）
"""
import json, time
from pathlib import Path
from typing import Dict, List, Any, Optional

ROOT       = Path(__file__).parent.parent
DATA_DIR   = ROOT / "data"
TRADES_F   = DATA_DIR / "hunter_v2_trades.json"
ATTR_F     = DATA_DIR / "attribution_log.jsonl"   # 每笔追加
SUMMARY_F  = DATA_DIR / "attribution_summary.json" # 汇总统计


# ── 归因字段枚举 ──────────────────────────────────────────────
EXIT_QUALITY = {
    "TP2":         "优秀",   # 达到 TP2，满足预期
    "TP1":         "良好",   # 达到 TP1
    "SL":          "止损",   # 止损出场
    "TRAIL_STOP":  "移止",   # 移动止盈触发
    "TIMEOUT":     "超时",   # 持仓超时
    "MANUAL":      "手动",
    "CIRCUIT":     "熔断",
    "UNKNOWN":     "未知",
}

SCORE_TIER = lambda s: "高(≥90)" if s >= 90 else "中(75-89)" if s >= 75 else "低(60-74)" if s >= 60 else "差(<60)"


# ── 核心归因函数 ──────────────────────────────────────────────
def attribute_trade(trade: dict) -> dict:
    """
    对单笔已平仓交易生成归因记录
    trade 字段参考 hunter_v2_trades.json schema
    """
    sym       = trade.get("symbol", "")
    direction = trade.get("direction", "")
    entry     = float(trade.get("entry_price") or 0)
    exit_p    = float(trade.get("exit_price") or trade.get("close_price") or 0)
    pnl       = float(trade.get("pnl_usdt") or trade.get("realized_pnl") or trade.get("pnl") or 0)  # [修复P0-1] pnl_usdt优先
    score     = float(trade.get("score") or trade.get("filter_score") or 0)
    regime    = trade.get("regime", trade.get("market_regime", "UNKNOWN"))
    exit_r    = trade.get("close_reason") or trade.get("exit_reason") or "UNKNOWN"
    open_ts   = trade.get("open_ts") or trade.get("open_time") or 0
    close_ts  = trade.get("close_ts") or trade.get("close_time") or 0
    confluence= (trade.get("confluence_total") or trade.get("brahma_score") or
                  trade.get("brahma",{}).get("confluence") if isinstance(trade.get("brahma"),dict) else 0 or 0)  # [修复P0-1]

    # 方向与体制是否匹配
    regime_match = _regime_direction_match(regime, direction)

    # 持仓时长（小时）
    hold_hours = 0
    try:
        if open_ts and close_ts:
            o = open_ts / 1000 if open_ts > 1e10 else open_ts
            c = close_ts / 1000 if close_ts > 1e10 else close_ts
            hold_hours = round((c - o) / 3600, 1)
    except Exception:
        _ = None  # 非致命，不阻断

    # 盈亏比（实际）
    rr_actual = 0
    if entry and exit_p:
        move = abs(exit_p - entry) / entry * 100
        sl   = trade.get("stop_loss")
        if sl and entry:
            risk_pct = abs(entry - float(sl)) / entry * 100
            rr_actual = round(move / risk_pct, 2) if risk_pct else 0

    # 入场质量评级
    entry_quality = SCORE_TIER(score)

    # UTC 交易时段
    utc_hour = 0
    try:
        utc_hour = int(time.gmtime(open_ts / 1000 if open_ts > 1e10 else open_ts).tm_hour)
    except Exception:
        _ = None  # 非致命，不阻断

    session = _hour_to_session(utc_hour)

    # [Phase A-2] 贝叶斯后验更新：每笔出场更新胜率分布
    try:
        import sys as _bs, os as _bo
        _bd = _bo.path.join(_bo.path.dirname(_bo.path.abspath(__file__)), '..', 'brahma_brain')
        if _bd not in _bs.path: _bs.path.insert(0, _bd)
        from bayesian_updater import update as _bayes_update
        _bayes_update(sym, direction, win=(pnl > 0), regime=regime, score=float(score))
        # [Phase B-2] 在线贝叶斯多维更新
        from online_bayes import update as _ob_update
        _tier = 'S1' if float(score) >= 120 else ('S2' if float(score) >= 90 else 'S3')
        import time as _t_ob
        _ob_update(sym, direction, win=(pnl > 0), regime=regime,
                   score=float(score), score_tier=_tier,
                   hour=_t_ob.gmtime().tm_hour)
        # [Phase C-2] RL Q-table 出场更新
        from rl_position import close_trade as _rl_close
        _pnl_pct_v = float(trade.get('pnl_pct', 0))
        _rl_close(str(trade.get('signal_id', '')), _pnl_pct_v, bool(pnl > 0))
    except Exception:
        pass  # 非致命

    attr = {
        "ts":            int(time.time()),
        "symbol":        sym,
        "direction":     direction,
        "regime":        regime,
        "regime_match":  regime_match,
        "score":         score,
        "entry_quality": entry_quality,
        "confluence":    confluence,
        "exit_reason":   exit_r,
        "exit_quality":  EXIT_QUALITY.get(exit_r.upper(), "未知"),
        "pnl":           round(pnl, 4),
        "win":           pnl > 0,
        "rr_actual":     rr_actual,
        "hold_hours":    hold_hours,
        "utc_hour":      utc_hour,
        "session":       session,
        # 一句话归因
        "verdict":       _verdict(pnl, regime_match, exit_r, score),
    }
    return attr


def _regime_direction_match(regime: str, direction: str) -> bool:
    """
    体制与方向是否匹配
    [修复4] 对齐系统实际体制名（BULL_EARLY/BEAR_EARLY等）
    来源: state_engine.detect_state() 实际输出
    """
    regime = regime.upper()
    # 系统实际体制名（state_engine.py 输出）
    long_regimes  = {
        "BULL", "BULL_TREND", "BULL_EARLY", "BULL_PEAK", "BULL_ETF",
        "BULL_TRANS", "RECOVERY", "BEAR_RECOVERY",
    }
    short_regimes = {
        "BEAR", "BEAR_TREND", "BEAR_EARLY", "BEAR_CRASH",
        "BEAR_TRANS", "BEAR_TRANSITION", "CORRECTION", "BULL_CORRECTION",
    }
    # neutral: CHOP_HIGH / CHOP_LOW / CHOP / RANGING / UNKNOWN → 双向均视为不匹配
    if "多" in direction or "LONG" in direction.upper():
        return regime in long_regimes
    if "空" in direction or "SHORT" in direction.upper():
        return regime in short_regimes
    return False


def _hour_to_session(h: int) -> str:
    if 0 <= h < 8:   return "亚洲夜盘"
    if 8 <= h < 14:  return "亚洲主力"
    if 14 <= h < 20: return "欧美重叠"
    return "美盘收尾"


def _verdict(pnl: float, regime_match: bool, exit_r: str, score: float) -> str:
    """一句话归因摘要"""
    if pnl > 0:
        if regime_match and score >= 80:
            return "体制匹配+高评分，正常盈利"
        if regime_match:
            return "体制匹配，顺势盈利"
        return "逆势侥幸盈利，注意可重复性"
    else:
        if not regime_match:
            return "体制不匹配，逆势亏损"
        if "SL" in exit_r.upper():
            return "体制匹配但止损出场，市场噪音或参数过紧"
        if score < 70:
            return "低评分强入，质量亏损"
        return "体制/评分正常，市场不利"


# ── 批量归因（对 hunter_v2_trades 历史补录）────────────────────
def run_attribution(force: bool = False) -> dict:
    """
    扫描 trade_records.jsonl（优先）+ hunter_v2_trades.json（兜底），对未归因的已平仓交易补充归因
    force=True 重新归因所有历史
    D8修复(2026-05-18): 优先读 trade_records.jsonl，其中含真实 pnl_usdt/close_price
    """
    # ── 优先从 trade_records.jsonl 读取（D8完整记录）
    TR_F = DATA_DIR / "trade_records.jsonl"
    trades = []
    if TR_F.exists():
        for line in TR_F.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                # 标准化字段名：trade_records 用 pnl_usdt，attribution 期望 pnl
                if r.get('pnl_usdt') is not None and r.get('pnl') is None:
                    r['pnl'] = r['pnl_usdt']
                if r.get('pnl_pct') is not None and r.get('realized_pnl') is None:
                    r['realized_pnl'] = r['pnl_pct']
                if r.get('close_price') and not r.get('exit_price'):
                    r['exit_price'] = r['close_price']
                r.setdefault('status', 'CLOSED')
                trades.append(r)
            except Exception:
                _ = None  # 非致命，不阻断

    # 兜底：hunter_v2_trades.json（对已有 pnl 的记录做补充）
    if TRADES_F.exists():
        try:
            old_trades = json.loads(TRADES_F.read_text())
            if isinstance(old_trades, dict):
                old_trades = old_trades.get("trades", old_trades.get("records", []))
            tr_ids = {t.get('signal_id') for t in trades}
            for t in old_trades:
                if t.get('signal_id') not in tr_ids:
                    trades.append(t)
        except Exception:
            _ = None  # 非致命

    if not trades:
        return {"attributed": 0, "skipped": 0}

    # 已归因的 signal_id 集合
    attributed_ids = set()
    if not force and ATTR_F.exists():
        for line in ATTR_F.read_text().splitlines():
            try:
                r = json.loads(line)
                if r.get("signal_id"):
                    attributed_ids.add(r["signal_id"])
            except Exception:
                _ = None  # 非致命

    attributed = 0
    skipped = 0
    new_records = []

    for t in trades:
        sid = t.get("signal_id", "")
        status = t.get("status", "")

        # 只处理已平仓
        if status not in ("CLOSED", "closed", "SETTLED"):
            skipped += 1
            continue

        if sid in attributed_ids and not force:
            skipped += 1
            continue

        attr = attribute_trade(t)
        attr["signal_id"] = sid
        new_records.append(attr)
        attributed += 1

    # 追加写入 attribution_log.jsonl
    if new_records:
        with open(ATTR_F, "a") as f:
            for r in new_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 更新汇总统计
    _update_summary()

    return {"attributed": attributed, "skipped": skipped}


# ── 汇总统计（喂给 evolve_kelly）──────────────────────────────
def _update_summary() -> dict:
    if not ATTR_F.exists():
        return {}

    records = []
    for line in ATTR_F.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except Exception:
            _ = None  # 非致命

    if not records:
        return {}

    total = len(records)
    wins  = [r for r in records if r.get("win")]
    losses = [r for r in records if not r.get("win")]

    def avg(lst, key):
        vals = [x.get(key, 0) for x in lst if x.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0

    summary = {
        "total":        total,
        "win_count":    len(wins),
        "loss_count":   len(losses),
        "win_rate":     round(len(wins) / total, 4) if total else 0,
        "avg_win_pnl":  avg(wins, "pnl"),
        "avg_loss_pnl": avg(losses, "pnl"),
        "avg_rr":       avg(records, "rr_actual"),
        "avg_hold_h":   avg(records, "hold_hours"),
        # 按体制
        "regime_match_wr":  round(len([r for r in records if r.get("regime_match") and r.get("win")]) /
                                  max(1, len([r for r in records if r.get("regime_match")])), 4),
        "regime_mismatch_wr": round(len([r for r in records if not r.get("regime_match") and r.get("win")]) /
                                    max(1, len([r for r in records if not r.get("regime_match")])), 4),
        # 按时段
        "by_session":   _group_wr(records, "session"),
        # 按出场原因
        "by_exit":      _group_wr(records, "exit_reason"),
        # 按评分段（旧）
        "by_score_tier": _group_wr(records, "entry_quality"),
        # P0修复: 按信号等级 S1/S2/S3 分组统计胜率
        "by_signal_tier": {
            tier: {
                "count": len([r for r in records if r.get("signal_tier_label","S3")==tier]),
                "win_rate": round(
                    len([r for r in records if r.get("signal_tier_label","S3")==tier and r.get("win")]) /
                    max(1, len([r for r in records if r.get("signal_tier_label","S3")==tier])), 4
                )
            }
            for tier in ["S1","S2","S3"]
        },
        # P0修复: 按通道分组（排除TEST通道污染）
        "by_channel": _group_wr(records, "channel"),
        "updated_at":   int(time.time()),
    }

    SUMMARY_F.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _group_wr(records: list, key: str) -> dict:
    groups: Dict[str, List] = {}
    for r in records:
        k = str(r.get(key, "unknown"))
        groups.setdefault(k, []).append(r)
    return {
        k: {"count": len(v),
            "win_rate": round(sum(1 for x in v if x.get("win")) / len(v), 4)}
        for k, v in groups.items()
    }


def get_summary() -> dict:
    """供 evolve_kelly 读取的汇总统计"""
    if SUMMARY_F.exists():
        return json.loads(SUMMARY_F.read_text())
    return _update_summary()


# ── CLI ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = run_attribution(force=force)
    print(f"✅ 归因完成: 新增 {result['attributed']} 笔，跳过 {result['skipped']} 笔")
    s = get_summary()
    if s:
        print(f"   胜率: {s.get('win_rate',0)*100:.1f}%  "
              f"平均盈: {s.get('avg_win_pnl',0):.3f}  "
              f"平均亏: {s.get('avg_loss_pnl',0):.3f}")
        print(f"   体制匹配胜率: {s.get('regime_match_wr',0)*100:.1f}%  "
              f"体制不匹配胜率: {s.get('regime_mismatch_wr',0)*100:.1f}%")
