"""
capital_allocator.py — I5 资金分配规划器 (Brahma v12.9)
═══════════════════════════════════════════════════════
功能:
  1. 全局风险预算管理 (NAV×2% 总风险上限)
  2. 多仓并发资金分配
  3. 动态调整：NAV回撤 → 自动缩减预算
  4. 品种权重：主流币 > 山寨
  5. 输出: 本次可用资金 + 仓位建议 + 剩余预算

风险预算模型:
  total_risk_budget  = NAV × RISK_PCT_MAX   (默认2%)
  used_risk          = sum(active_positions × sl_pct)
  available_risk     = total_risk_budget - used_risk
  position_usdt      = available_risk / sl_pct_estimate
"""
import json, statistics, time, subprocess
from pathlib import Path
from datetime import datetime, timezone

# ─── 文件读取缓存（TTL=10s）避免同一次analyze多次全量读取 ──────────
_FILE_CACHE: dict = {}
_FILE_CACHE_TTL = 10  # 秒

def _read_tail(path: Path, n: int = 600) -> list:
    """只读文件最后n行，避免全量加载大文件（101k行→600行）"""
    cache_key = f'{path}:tail:{n}'
    now = time.time()
    if cache_key in _FILE_CACHE and now - _FILE_CACHE[cache_key]['ts'] < _FILE_CACHE_TTL:
        return _FILE_CACHE[cache_key]['data']
    try:
        r = subprocess.run(['tail', '-n', str(n), str(path)],
                           capture_output=True, text=True, timeout=3)
        lines = r.stdout.split('\n') if r.returncode == 0 else []
    except Exception:
        # fallback: 全量读（降级）
        lines = path.read_text(errors='ignore').split('\n')[-n:] if path.exists() else []
    _FILE_CACHE[cache_key] = {'ts': now, 'data': lines}
    return lines

DATA_DIR  = Path(__file__).parent.parent / 'data'
TRADE_F   = DATA_DIR / 'trade_records.jsonl'
NAV_F     = DATA_DIR / 'nav_history.json'
ALLOC_LOG = DATA_DIR / 'capital_alloc.jsonl'

# 全局参数
RISK_PCT_MAX    = 0.02    # 总仓风险上限 NAV×2%
SINGLE_RISK_MAX = 0.008   # 单仓风险上限 NAV×0.8%
SL_DEFAULT_PCT  = 0.015   # 默认止损幅度1.5%（估算）
MAX_CONCURRENT  = 3

# 品种权重
# [UP-CAPITAL-v2 2026-05-31] 实盘铁证驱动仓位权重
# BTC SHORT: WR=92% n=23 → 最强Alpha，权重×1.5
# DOGE:      PF=3.234(M02最高) WR=67%→修复后90%+ → 权重×1.3
# 其余维持原权重
TIER_WEIGHTS = {
    'ALPHA': {'BTCUSDT': 1.5},                                           # WR=92% n=23 铁证
    'S1':    {'ETHUSDT':0.9,'BNBUSDT':0.8,'SOLUSDT':0.8},
    'S1+':   {'DOGEUSDT': 1.3},                                          # PF=3.234 M02最高
    'S2':    {'XRPUSDT':0.7,'ADAUSDT':0.7,'DOTUSDT':0.7,'AVAXUSDT':0.7},
    'DEFAULT': 0.5,
}


def _get_nav() -> float:
    # [FIX-C v6.0] 优先读 brahma_state.json 的实时 NAV
    try:
        _bs_path = DATA_DIR / 'brahma_state.json'
        if _bs_path.exists():
            _bs = json.loads(_bs_path.read_text())
            _nav = _bs.get('nav') or _bs.get('nav_verified')
            if _nav and float(_nav) > 50:
                return float(_nav)
    except: pass
    try:
        if NAV_F.exists():
            d = json.loads(NAV_F.read_text())
            if isinstance(d, list) and d: return float(d[-1].get('nav', 127.62))
            if isinstance(d, dict): return float(d.get('latest_nav', 127.62))
    except: pass
    try:
        lines = list(reversed(_read_tail(TRADE_F, 400)))
        candidates = []
        for l in lines[:200]:  # 只看最近200条
            if not l.strip(): continue
            r = json.loads(l)
            nav = r.get('nav_at_open') or r.get('nav_verified')
            if nav and float(nav) > 50:  # 排除异常小值（>50防止历史早期小值）
                candidates.append(float(nav))
        if candidates:
            return max(candidates)  # 取最大值（最近真实NAV）
    except: pass
    return 127.62


def _get_active_exposure() -> tuple:
    """返回 (n_active, total_risk_used)"""
    trade_f = DATA_DIR / 'trade_records.jsonl'
    if not trade_f.exists(): return (0, 0.0)
    active = []
    for l in reversed(_read_tail(trade_f, 200)):
        if not l.strip(): continue
        try:
            r = json.loads(l)
            if not r.get('_is_simulation') and r.get('result') in (None,'','OPEN'):
                active.append(r)
        except: pass
    # 估算每个持仓占用的风险
    total_risk = 0.0
    for pos in active:
        nav_open = float(pos.get('nav_at_open') or pos.get('nav_verified') or 127.62)
        qty = float(pos.get('qty') or 0)
        entry = float(pos.get('entry_price') or 0)
        sl    = float(pos.get('stop_loss') or 0)
        if entry > 0 and sl > 0 and qty > 0:
            sl_pct = abs(entry - sl) / entry
            risk_usdt = qty * entry * sl_pct
            total_risk += risk_usdt
        else:
            total_risk += nav_open * SL_DEFAULT_PCT  # 估算
    return (len(active), total_risk)


def _symbol_weight(symbol: str) -> float:
    for tier, syms in TIER_WEIGHTS.items():
        if tier == 'DEFAULT': continue
        if symbol in syms: return syms[symbol]
    return TIER_WEIGHTS['DEFAULT']


def _recent_drawdown() -> float:
    """最近20笔累计回撤"""
    if not TRADE_F.exists(): return 0.0
    pnls = []
    for l in reversed(_read_tail(TRADE_F, 100)):
        if not l.strip(): continue
        try:
            r = json.loads(l)
            if not r.get('_is_simulation') and r.get('pnl_pct'):
                pnls.append(float(r['pnl_pct']))
        except: pass
        if len(pnls) >= 20: break
    if not pnls: return 0.0
    cum, peak, max_dd = 0, 0, 0
    for p in reversed(pnls):
        cum += p
        if cum > peak: peak = cum
        dd = peak - cum
        if dd > max_dd: max_dd = dd
    return max_dd


def compute(
    symbol: str,
    sl_pct: float = None,
    signal_score: float = 100,
    nav_override: float = None,
) -> dict:
    """
    计算本次可分配资金

    Returns:
        {
          'position_usdt':    建议开仓USDT
          'risk_usdt':        本次风险敞口
          'budget_remaining': 风险预算剩余
          'budget_used_pct':  已用预算%
          'allowed':          bool
          'reason':           str
          'adjustments':      dict
        }
    """
    nav = nav_override or _get_nav()
    n_active, used_risk = _get_active_exposure()
    drawdown = _recent_drawdown()
    sl_est = sl_pct or SL_DEFAULT_PCT

    total_budget  = nav * RISK_PCT_MAX
    avail_budget  = max(0, total_budget - used_risk)
    single_budget = nav * SINGLE_RISK_MAX

    adjustments = {}

    # ── 并发上限检查 ───────────────────────────────────────
    if n_active >= MAX_CONCURRENT:
        return {
            'position_usdt': 0, 'risk_usdt': 0,
            'budget_remaining': avail_budget, 'budget_used_pct': used_risk/total_budget,
            'allowed': False,
            'reason': f'Max concurrent {MAX_CONCURRENT} reached ({n_active} active)',
            'adjustments': {}
        }

    # ── 预算检查 ───────────────────────────────────────────
    if avail_budget <= 0:
        return {
            'position_usdt': 0, 'risk_usdt': 0,
            'budget_remaining': 0, 'budget_used_pct': 1.0,
            'allowed': False,
            'reason': f'Risk budget exhausted ({used_risk:.2f}u/{total_budget:.2f}u)',
            'adjustments': {}
        }

    # ── 品种权重调整 ────────────────────────────────────────
    sym_w = _symbol_weight(symbol)
    adjustments['symbol_weight'] = sym_w

    # ── 回撤调整 ────────────────────────────────────────────
    dd_mult = 1.0
    if drawdown >= 0.08:   dd_mult = 0.6
    elif drawdown >= 0.05: dd_mult = 0.8
    adjustments['drawdown_mult'] = dd_mult
    adjustments['drawdown'] = round(drawdown, 4)

    # ── 信号评分调整 ────────────────────────────────────────
    score_mult = 0.5 + min(signal_score / 200, 0.5)   # 0.5~1.0
    adjustments['score_mult'] = round(score_mult, 3)

    # ── 计算本次风险额度 ────────────────────────────────────
    this_budget = min(avail_budget, single_budget) * sym_w * dd_mult * score_mult
    this_budget = max(0, this_budget)

    # 从风险额度反算仓位
    position_usdt = this_budget / max(sl_est, 0.005)
    position_usdt = max(5.0, min(position_usdt, nav * 0.12))  # 5u~12%NAV

    risk_usdt = position_usdt * sl_est
    used_after = used_risk + risk_usdt
    budget_used_pct = used_after / total_budget if total_budget > 0 else 1.0

    reason = (f"NAV={nav:.1f} budget={total_budget:.2f}u "
              f"avail={avail_budget:.2f}u "
              f"sym_w={sym_w:.1f} dd_m={dd_mult:.1f} "
              f"pos={position_usdt:.1f}u risk={risk_usdt:.2f}u")

    result = {
        'position_usdt': round(position_usdt, 2),
        'risk_usdt':     round(risk_usdt, 3),
        'budget_total':  round(total_budget, 3),
        'budget_used':   round(used_risk, 3),
        'budget_remaining': round(avail_budget, 3),
        'budget_used_pct': round(budget_used_pct, 3),
        'n_active': n_active,
        'allowed': True,
        'reason': reason,
        'adjustments': adjustments,
        'ts': datetime.now(timezone.utc).isoformat(),
    }

    try:
        with open(ALLOC_LOG, 'a') as f:
            f.write(json.dumps({'symbol': symbol, **result}) + '\n')
        # 自动截断：超过3000行时保留最新2000行（设计院 2026-06-29 防膜胀）
        try:
            lines = ALLOC_LOG.read_text().splitlines()
            if len(lines) > 3000:
                ALLOC_LOG.write_text('\n'.join(lines[-2000:]) + '\n')
        except Exception:
            pass
    except: pass

    return result


def get_budget_summary() -> dict:
    """预算概览"""
    nav = _get_nav()
    n_active, used_risk = _get_active_exposure()
    total_budget = nav * RISK_PCT_MAX
    avail = max(0, total_budget - used_risk)
    return {
        'nav': nav,
        'total_budget_usdt': round(total_budget, 2),
        'used_risk_usdt':    round(used_risk, 2),
        'available_usdt':    round(avail, 2),
        'used_pct':          round(used_risk / total_budget if total_budget > 0 else 0, 3),
        'n_active':          n_active,
        'slots_left':        MAX_CONCURRENT - n_active,
    }


if __name__ == '__main__':
    summary = get_budget_summary()
    print(f"Budget: {summary['used_risk_usdt']:.2f}/{summary['total_budget_usdt']:.2f}u "
          f"({summary['used_pct']:.0%}) NAV={summary['nav']:.1f}")
    print(f"Active: {summary['n_active']}/{MAX_CONCURRENT}")
    for sym in ['BTCUSDT','ETHUSDT','SOLUSDT']:
        r = compute(sym, signal_score=120)
        print(f"  {sym}: pos={r['position_usdt']:.1f}u risk={r['risk_usdt']:.2f}u "
              f"allowed={r['allowed']}")
