#!/usr/bin/env python3
"""
scripts/liq_paper_trader.py — 清算集群TP纸面交易引擎
达摩院 × 梵天 | 设计院自主 | 2026-08-04

功能：
  1. 扫描 live_signal_log.jsonl 中 valid=True 的新信号
  2. 调用 liq_heatmap.get_liq_heatmap() 实时计算清算集群TP
  3. 为每条信号建立两条平行纸仓腿：
       Leg-A: 梵天原始TP (tp1/tp2)
       Leg-B: 清算集群TP  (liq_cluster_tp)
  4. 每次运行：更新持仓状态，结算到期/触达目标的仓位
  5. --report 模式：输出当前 Leg-A vs Leg-B 对比汇总
  6. --push   模式：汇总推送到Jarvis

存储：
  data/liq_paper/
    trades.jsonl       — 所有开仓记录
    settled.jsonl      — 已结算记录  
    state.json         — 汇总状态

运行：
  python3 scripts/liq_paper_trader.py          # 正常扫描+更新
  python3 scripts/liq_paper_trader.py --report # 输出报告
  python3 scripts/liq_paper_trader.py --push   # 推送Jarvis
"""

import sys, os, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

SIG_LOG   = BASE / 'data' / 'live_signal_log.jsonl'
PAPER_DIR = BASE / 'data' / 'liq_paper'
PAPER_DIR.mkdir(parents=True, exist_ok=True)

TRADES_FILE  = PAPER_DIR / 'trades.jsonl'
SETTLED_FILE = PAPER_DIR / 'settled.jsonl'
STATE_FILE   = PAPER_DIR / 'state.json'
SEEN_FILE    = PAPER_DIR / 'seen_signals.json'  # 已处理的signal_id

# ── 参数 ──────────────────────────────────────────────────────
MIN_SCORE      = 100     # 只跟踪score≥100的信号（梵天有效门槛）
MAX_OPEN       = 20      # 最多同时持有的纸仓对数
SETTLE_HOURS   = 24      # 最长持仓时间（H），超时按市价结算
SIG_MAX_AGE_H  = 4       # 信号最大年龄（H），超过则忽略
LIQ_TP_MIN_PCT = 1.0     # 清算集群TP最小距离（%），太近无意义
LIQ_TP_MAX_PCT = 8.0     # 清算集群TP最大距离（%），太远是噪音
FAPI_BASE      = 'https://fapi.binance.com'

# ── Jarvis推送目标（从system_config读取）──────────────────────
try:
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f"{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}"
except Exception:
    JARVIS_TARGET = '73295708:t:019fd9dd-4b0f-71db-87fb-1e192ccb2291'


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _now_ts() -> float:
    return time.time()

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

def _get_price(symbol: str) -> float:
    try:
        import requests
        r = requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price',
                         params={'symbol': symbol}, timeout=5)
        return float(r.json()['price'])
    except Exception:
        return 0.0

def _get_mark_price(symbol: str) -> float:
    try:
        import requests
        r = requests.get(f'{FAPI_BASE}/fapi/v1/premiumIndex',
                         params={'symbol': symbol}, timeout=5)
        return float(r.json()['markPrice'])
    except Exception:
        return _get_price(symbol)

def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out

def _append_jsonl(path: Path, record: dict):
    with open(path, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

def _load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()

def _save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), indent=2))


# ─────────────────────────────────────────────────────────────
# 清算集群TP计算（实时liq_heatmap）
# ─────────────────────────────────────────────────────────────

def _compute_liq_cluster_tp(symbol: str, direction: str, entry_price: float) -> dict:
    """
    调用liq_heatmap获取实时清算集群，返回TP目标
    direction: 'LONG' | 'SHORT'
    """
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from liq_heatmap import get_liq_heatmap
        liq = get_liq_heatmap(symbol)

        if 'error' in liq:
            return {'tp': None, 'source': 'error', 'detail': liq['error']}

        px = liq['price']

        if direction == 'LONG':
            # 目标：上方空头清算密集区（轧空目标）
            # 取最近的空头清算价（3x/5x中最小的 > entry）
            candidates = []
            for lev, liq_px in liq['short_liq_map'].items():
                if liq_px > entry_price:
                    candidates.append((liq_px, lev))
            # 还加入上方Ask密集区
            for ask_px, ask_vol in liq.get('top_ask_clusters', []):
                if ask_px > entry_price:
                    candidates.append((ask_px, 'ask_cluster'))

            if candidates:
                # 取最近的（价格最小）
                tp_px, tp_source = min(candidates, key=lambda x: x[0])
                dist_pct = (tp_px - entry_price) / entry_price * 100
                return {
                    'tp': round(tp_px, 4),
                    'source': f'liq_{tp_source}x' if isinstance(tp_source, int) else str(tp_source),
                    'dist_pct': round(dist_pct, 2),
                    'liq_bull_score': liq.get('liq_bull_score', 0),
                    'liq_bear_score': liq.get('liq_bear_score', 0),
                }
        else:  # SHORT
            # 目标：下方多头清算密集区（洗盘目标）
            candidates = []
            for lev, liq_px in liq['long_liq_map'].items():
                if liq_px < entry_price:
                    candidates.append((liq_px, lev))
            for bid_px, bid_vol in liq.get('top_bid_clusters', []):
                if bid_px < entry_price:
                    candidates.append((bid_px, 'bid_cluster'))

            if candidates:
                # 取最近的（价格最大）
                tp_px, tp_source = max(candidates, key=lambda x: x[0])
                dist_pct = (entry_price - tp_px) / entry_price * 100
                return {
                    'tp': round(tp_px, 4),
                    'source': f'liq_{tp_source}x' if isinstance(tp_source, int) else str(tp_source),
                    'dist_pct': round(dist_pct, 2),
                    'liq_bull_score': liq.get('liq_bull_score', 0),
                    'liq_bear_score': liq.get('liq_bear_score', 0),
                }

        # 回退：无法找到方向上的清算集群 → 用固定4%
        fallback = entry_price * (1.04 if direction == 'LONG' else 0.96)
        return {'tp': round(fallback, 4), 'source': 'fallback_4pct', 'dist_pct': 4.0}

    except Exception as e:
        # 完全失败 → fallback
        fallback = entry_price * (1.04 if direction == 'LONG' else 0.96)
        return {'tp': round(fallback, 4), 'source': f'fallback_err:{e}', 'dist_pct': 4.0}


# ─────────────────────────────────────────────────────────────
# 开仓：为信号建立双腿纸仓
# ─────────────────────────────────────────────────────────────

def open_paper_pair(sig: dict) -> Optional[dict]:
    """
    为一条梵天信号建立双腿纸仓对
    返回记录字典或None
    """
    sym       = sig['symbol']
    direction = sig.get('direction', sig.get('signal_dir', 'LONG')).upper()
    entry     = float(sig.get('entry_hi', sig.get('price', 0)) or sig.get('price', 0))
    sl        = float(sig.get('stop_loss', 0))
    tp1_orig  = float(sig.get('tp1', 0))
    tp2_orig  = float(sig.get('tp2', 0))
    score     = float(sig.get('score_final', sig.get('score', 0)))
    regime    = sig.get('regime', '')
    sig_id    = sig.get('signal_id', '')

    if entry <= 0 or sl <= 0:
        return None

    # 计算清算集群TP
    liq_tp_data = _compute_liq_cluster_tp(sym, direction, entry)
    liq_tp      = liq_tp_data.get('tp')

    if not liq_tp:
        return None

    # 过滤距离不合理的清算集群TP
    liq_dist_pct = liq_tp_data.get('dist_pct', 0)
    if liq_dist_pct < LIQ_TP_MIN_PCT or liq_dist_pct > LIQ_TP_MAX_PCT:
        # 距离太近(<0.5%)或太远(>8%) — 改用梯队回退
        # 直接用固定2.5%
        fallback_pct = 0.025
        if direction == 'LONG':
            liq_tp = round(entry * (1 + fallback_pct), 4)
        else:
            liq_tp = round(entry * (1 - fallback_pct), 4)
        liq_tp_data['tp']       = liq_tp
        liq_tp_data['source']   = f'fallback_2.5pct(orig_dist={liq_dist_pct:.1f}%)'
        liq_tp_data['dist_pct'] = fallback_pct * 100

    now = _now_ts()
    record = {
        'pair_id'    : f"liq_{sig_id[:8]}_{int(now)}",
        'signal_id'  : sig_id,
        'symbol'     : sym,
        'direction'  : direction,
        'entry'      : entry,
        'sl'         : sl,
        'regime'     : regime,
        'score'      : score,
        'opened_at'  : now,
        'opened_iso' : _iso(now),
        'expires_at' : now + SETTLE_HOURS * 3600,

        # Leg-A: 梵天原始TP
        'leg_a_tp1'  : tp1_orig,
        'leg_a_tp2'  : tp2_orig,
        'leg_a_status': 'open',
        'leg_a_exit' : None,
        'leg_a_pnl'  : None,
        'leg_a_exit_reason': None,

        # Leg-B: 清算集群TP
        'leg_b_tp'       : liq_tp,
        'leg_b_tp_source': liq_tp_data.get('source', ''),
        'leg_b_tp_dist_pct': liq_tp_data.get('dist_pct', 0),
        'leg_b_status'   : 'open',
        'leg_b_exit'     : None,
        'leg_b_pnl'      : None,
        'leg_b_exit_reason': None,

        'liq_bull_score' : liq_tp_data.get('liq_bull_score', 0),
        'liq_bear_score' : liq_tp_data.get('liq_bear_score', 0),
    }
    return record


# ─────────────────────────────────────────────────────────────
# 结算逻辑
# ─────────────────────────────────────────────────────────────

def _calc_pnl(entry: float, exit_px: float, direction: str) -> float:
    if direction == 'LONG':
        return (exit_px - entry) / entry * 100
    else:
        return (entry - exit_px) / entry * 100

def settle_trade(trade: dict, current_px: float, force_timeout: bool = False) -> dict:
    """更新单条纸仓状态，返回更新后的trade"""
    direction = trade['direction']
    entry     = trade['entry']
    sl        = trade['sl']
    now       = _now_ts()

    # ── Leg-A 结算 ──
    if trade['leg_a_status'] == 'open':
        tp1 = trade['leg_a_tp1']
        tp2 = trade['leg_a_tp2']
        if direction == 'LONG':
            if current_px <= sl:
                trade['leg_a_exit'] = sl
                trade['leg_a_exit_reason'] = 'sl'
                trade['leg_a_status'] = 'closed'
            elif current_px >= tp2 and tp2 > 0:
                trade['leg_a_exit'] = tp2
                trade['leg_a_exit_reason'] = 'tp2'
                trade['leg_a_status'] = 'closed'
            elif current_px >= tp1 and tp1 > 0:
                trade['leg_a_exit'] = tp1
                trade['leg_a_exit_reason'] = 'tp1'
                trade['leg_a_status'] = 'closed'
        else:  # SHORT
            if current_px >= sl:
                trade['leg_a_exit'] = sl
                trade['leg_a_exit_reason'] = 'sl'
                trade['leg_a_status'] = 'closed'
            elif current_px <= tp2 and tp2 > 0:
                trade['leg_a_exit'] = tp2
                trade['leg_a_exit_reason'] = 'tp2'
                trade['leg_a_status'] = 'closed'
            elif current_px <= tp1 and tp1 > 0:
                trade['leg_a_exit'] = tp1
                trade['leg_a_exit_reason'] = 'tp1'
                trade['leg_a_status'] = 'closed'

        if force_timeout and trade['leg_a_status'] == 'open':
            trade['leg_a_exit'] = current_px
            trade['leg_a_exit_reason'] = 'timeout'
            trade['leg_a_status'] = 'closed'

        if trade['leg_a_status'] == 'closed' and trade['leg_a_pnl'] is None:
            trade['leg_a_pnl'] = round(_calc_pnl(entry, trade['leg_a_exit'], direction), 4)

    # ── Leg-B 结算（清算集群TP）──
    if trade['leg_b_status'] == 'open':
        liq_tp = trade['leg_b_tp']
        if direction == 'LONG':
            if current_px <= sl:
                trade['leg_b_exit'] = sl
                trade['leg_b_exit_reason'] = 'sl'
                trade['leg_b_status'] = 'closed'
            elif liq_tp and current_px >= liq_tp:
                trade['leg_b_exit'] = liq_tp
                trade['leg_b_exit_reason'] = 'liq_tp_hit'
                trade['leg_b_status'] = 'closed'
        else:  # SHORT
            if current_px >= sl:
                trade['leg_b_exit'] = sl
                trade['leg_b_exit_reason'] = 'sl'
                trade['leg_b_status'] = 'closed'
            elif liq_tp and current_px <= liq_tp:
                trade['leg_b_exit'] = liq_tp
                trade['leg_b_exit_reason'] = 'liq_tp_hit'
                trade['leg_b_status'] = 'closed'

        if force_timeout and trade['leg_b_status'] == 'open':
            trade['leg_b_exit'] = current_px
            trade['leg_b_exit_reason'] = 'timeout'
            trade['leg_b_status'] = 'closed'

        if trade['leg_b_status'] == 'closed' and trade['leg_b_pnl'] is None:
            trade['leg_b_pnl'] = round(_calc_pnl(entry, trade['leg_b_exit'], direction), 4)

    return trade


# ─────────────────────────────────────────────────────────────
# 主扫描循环
# ─────────────────────────────────────────────────────────────

def run_scan(verbose: bool = True):
    """扫描新信号 + 更新持仓状态"""
    seen        = _load_seen()
    open_trades = _load_jsonl(TRADES_FILE)
    settled     = _load_jsonl(SETTLED_FILE)
    now         = _now_ts()

    # 1. 过滤出仍open的仓位
    still_open = [t for t in open_trades
                  if t.get('leg_a_status') == 'open' or t.get('leg_b_status') == 'open']

    # 2. 更新持仓价格
    newly_settled = []
    updated_open  = []
    px_cache      = {}

    for t in still_open:
        sym = t['symbol']
        if sym not in px_cache:
            px = _get_mark_price(sym)
            px_cache[sym] = px
        else:
            px = px_cache[sym]

        is_expired = now >= t['expires_at']
        t = settle_trade(t, px, force_timeout=is_expired)

        both_closed = (t['leg_a_status'] == 'closed' and t['leg_b_status'] == 'closed')
        if both_closed:
            t['settled_at']  = now
            t['settled_iso'] = _iso(now)
            newly_settled.append(t)
        else:
            updated_open.append(t)

    # 3. 写回settled
    for t in newly_settled:
        _append_jsonl(SETTLED_FILE, t)
        if verbose:
            la = t.get('leg_a_pnl') or 0
            lb = t.get('leg_b_pnl') or 0
            diff = lb - la
            sign = '✅' if diff > 0 else ('❌' if diff < 0 else '➖')
            print(f"  📋 结算 {t['symbol']} {t['direction']} | "
                  f"Leg-A(原始TP): {la:+.3f}% | "
                  f"Leg-B(清算TP): {lb:+.3f}% | "
                  f"改进: {diff:+.3f}% {sign}")

    # 4. 处理新信号
    sigs = _load_jsonl(SIG_LOG)
    new_count = 0
    for sig in reversed(sigs):  # 从最新开始
        sig_id = sig.get('signal_id', '')
        if sig_id in seen:
            continue
        if not sig.get('valid', False):
            seen.add(sig_id)
            continue
        score = float(sig.get('score_final', sig.get('score', 0)) or 0)
        if score < MIN_SCORE:
            seen.add(sig_id)
            continue
        # 过滤过期信号（信号产生时间超过SIG_MAX_AGE_H小时）
        sig_ts = float(sig.get('ts', sig.get('timestamp', 0)) or 0)
        if sig_ts > 0 and (now - sig_ts) > SIG_MAX_AGE_H * 3600:
            seen.add(sig_id)
            continue
        if len(updated_open) + new_count >= MAX_OPEN:
            break

        pair = open_paper_pair(sig)
        if pair:
            _append_jsonl(TRADES_FILE, pair)
            updated_open.append(pair)
            new_count += 1
            seen.add(sig_id)
            if verbose:
                print(f"  🆕 开仓 {pair['symbol']} {pair['direction']} "
                      f"score={score:.0f} | "
                      f"Leg-A TP1={pair['leg_a_tp1']:.2f} | "
                      f"Leg-B LIQ-TP={pair['leg_b_tp']:.2f} "
                      f"({pair['leg_b_tp_source']}, +{pair['leg_b_tp_dist_pct']:.1f}%)")
        else:
            seen.add(sig_id)

    _save_seen(seen)

    # 5. 更新state
    all_settled = _load_jsonl(SETTLED_FILE)
    _update_state(updated_open, all_settled)

    if verbose:
        print(f"\n  ✅ 扫描完成 | 持仓: {len(updated_open)} 对 | 新开: {new_count} | 本次结算: {len(newly_settled)}")


# ─────────────────────────────────────────────────────────────
# 状态汇总
# ─────────────────────────────────────────────────────────────

def _update_state(open_trades: list, settled: list):
    state = {
        'updated_at'    : _now_ts(),
        'updated_iso'   : _iso(_now_ts()),
        'open_count'    : len(open_trades),
        'settled_count' : len(settled),
    }
    # 统计settled
    if settled:
        import numpy as np
        la_pnls = [t['leg_a_pnl'] for t in settled if t.get('leg_a_pnl') is not None]
        lb_pnls = [t['leg_b_pnl'] for t in settled if t.get('leg_b_pnl') is not None]
        if la_pnls:
            state['leg_a_ev']  = round(float(np.mean(la_pnls)), 4)
            state['leg_a_wr']  = round(sum(1 for x in la_pnls if x > 0) / len(la_pnls) * 100, 1)
            state['leg_a_n']   = len(la_pnls)
        if lb_pnls:
            state['leg_b_ev']  = round(float(np.mean(lb_pnls)), 4)
            state['leg_b_wr']  = round(sum(1 for x in lb_pnls if x > 0) / len(lb_pnls) * 100, 1)
            state['leg_b_n']   = len(lb_pnls)
        if la_pnls and lb_pnls:
            diffs = [lb - la for lb, la in zip(lb_pnls, la_pnls)]
            state['avg_improvement'] = round(float(np.mean(diffs)), 4)
            state['liq_better_pct']  = round(sum(1 for d in diffs if d > 0) / len(diffs) * 100, 1)

    # TP命中率
    if settled:
        liq_hits  = sum(1 for t in settled if t.get('leg_b_exit_reason') == 'liq_tp_hit')
        state['liq_tp_hit_rate'] = round(liq_hits / len(settled) * 100, 1)

    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return state


# ─────────────────────────────────────────────────────────────
# 报告生成
# ─────────────────────────────────────────────────────────────

def build_report(push: bool = False) -> str:
    settled    = _load_jsonl(SETTLED_FILE)
    open_trades = [t for t in _load_jsonl(TRADES_FILE)
                   if t.get('leg_a_status') == 'open' or t.get('leg_b_status') == 'open']
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    lines = [
        "═══════════════════════════════════════════════════════",
        "  🔥 达摩院 × 梵天 · 清算集群TP纸面交易 实时对比报告",
        f"  更新时间: {_iso(_now_ts())}",
        "═══════════════════════════════════════════════════════",
        "",
        f"  📊 已结算: {len(settled)} 对 | 持仓中: {len(open_trades)} 对",
        "",
    ]

    if not settled:
        lines.append("  ⏳ 暂无结算数据，等待首批信号触达TP/SL...")
    else:
        la_ev  = state.get('leg_a_ev', 0)
        lb_ev  = state.get('leg_b_ev', 0)
        la_wr  = state.get('leg_a_wr', 0)
        lb_wr  = state.get('leg_b_wr', 0)
        avg_imp = state.get('avg_improvement', 0)
        liq_better = state.get('liq_better_pct', 0)
        hit_rate = state.get('liq_tp_hit_rate', 0)
        n = state.get('leg_a_n', len(settled))

        lines += [
            f"  ┌─────────────────────────────────────────────────┐",
            f"  │   对比项              Leg-A(梵天TP)  Leg-B(清算集群) │",
            f"  ├─────────────────────────────────────────────────┤",
            f"  │   期望值 EV/笔      {la_ev:>+10.3f}%  {lb_ev:>+10.3f}%     │",
            f"  │   胜率 WR           {la_wr:>10.1f}%  {lb_wr:>10.1f}%     │",
            f"  │   样本数            {n:>10}   {state.get('leg_b_n', n):>10}      │",
            f"  └─────────────────────────────────────────────────┘",
            "",
            f"  📈 清算集群TP平均改进: {avg_imp:+.3f}%/笔",
            f"  🎯 清算集群TP命中率:   {hit_rate:.1f}%",
            f"  ✅ 清算集群优于梵天TP: {liq_better:.1f}% 的交易",
            "",
        ]

        # 评级
        if avg_imp > 0.1:
            verdict = "🏆 清算集群TP显著优于原始TP — 建议升级主系统"
        elif avg_imp > 0.02:
            verdict = "✅ 清算集群TP边际改进 — 继续积累样本验证"
        elif avg_imp > -0.02:
            verdict = "➖ 两者接近 — 需更多样本区分"
        else:
            verdict = "⚠️ 清算集群TP表现弱于原始TP — 检查计算逻辑"
        lines.append(f"  📋 {verdict}")
        lines.append("")

        # 近5笔明细
        if settled:
            lines += ["  ── 近5笔结算明细 ──", ""]
            for t in settled[-5:]:
                la = t.get('leg_a_pnl') or 0
                lb = t.get('leg_b_pnl') or 0
                diff = lb - la
                sign = '✅' if diff > 0.01 else ('❌' if diff < -0.01 else '➖')
                lines.append(
                    f"  {t['symbol']} {t['direction']} score={t.get('score',0):.0f} | "
                    f"Leg-A: {la:+.3f}% ({t.get('leg_a_exit_reason','?')}) | "
                    f"Leg-B: {lb:+.3f}% ({t.get('leg_b_exit_reason','?')}) | "
                    f"改进: {diff:+.3f}% {sign}"
                )

    # 持仓中
    if open_trades:
        lines += ["", "  ── 持仓中 ──", ""]
        for t in open_trades[:5]:
            sym = t['symbol']
            px = _get_price(sym)
            if px > 0:
                direction = t['direction']
                ep = t['entry']
                float_pnl_a = _calc_pnl(ep, px, direction)
                float_pnl_b = _calc_pnl(ep, px, direction)  # same floating, TP hasn't hit
                lines.append(
                    f"  {sym} {direction} score={t.get('score',0):.0f} | "
                    f"入场={ep:.2f} 现价={px:.2f} 浮盈={float_pnl_a:+.2f}% | "
                    f"Leg-A TP1={t['leg_a_tp1']:.2f} | "
                    f"Leg-B TP={t['leg_b_tp']:.2f}({t['leg_b_tp_source']})"
                )

    lines += ["", "═══════════════════════════════════════════════════════"]
    report = "\n".join(lines)

    if push:
        _push_to_jarvis(report)

    return report


def _push_to_jarvis(text: str):
    """推送报告到Jarvis线程"""
    try:
        import subprocess
        cmd = [
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', JARVIS_TARGET,
            '--message', text
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)
        print("  📤 已推送到Jarvis")
    except Exception as e:
        print(f"  ⚠️ 推送失败: {e}")
        # fallback: use push_hub if available
        try:
            sys.path.insert(0, str(BASE))
            from push_hub import _jarvis
            _jarvis(text)
            print("  📤 push_hub推送成功")
        except Exception as e2:
            print(f"  ⚠️ push_hub也失败: {e2}")


# ─────────────────────────────────────────────────────────────
# CLI入口
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='清算集群TP纸面交易引擎')
    parser.add_argument('--report', action='store_true', help='输出当前报告')
    parser.add_argument('--push',   action='store_true', help='推送报告到Jarvis')
    parser.add_argument('--quiet',  action='store_true', help='静默模式（仅错误输出）')
    args = parser.parse_args()

    verbose = not args.quiet

    if verbose:
        print(f"\n{'='*60}")
        print(f"  清算集群TP纸面交易引擎 | {_iso(_now_ts())}")
        print(f"{'='*60}")

    # 总是先扫描更新
    run_scan(verbose=verbose)

    if args.report or args.push:
        print()
        report = build_report(push=args.push)
        print(report)
    elif verbose:
        # 简短状态
        if STATE_FILE.exists():
            state = json.loads(STATE_FILE.read_text())
            n = state.get('settled_count', 0)
            if n > 0:
                la_ev = state.get('leg_a_ev', 0)
                lb_ev = state.get('leg_b_ev', 0)
                imp   = state.get('avg_improvement', 0)
                print(f"\n  📊 累计: {n}笔 | Leg-A EV={la_ev:+.3f}% | Leg-B EV={lb_ev:+.3f}% | 改进={imp:+.3f}%")
            else:
                print(f"\n  ⏳ 已建立追踪，等待信号触达TP/SL...")


if __name__ == '__main__':
    main()
