"""
signal_queue.py — I5 多品种信号优先级调度队列 (Brahma v12.9)
═══════════════════════════════════════════════════════════════
功能:
  1. 多品种并发分析队列，自动调度优先级
  2. 信号去重（同品种/方向/体制 冷却期）
  3. 容量管理（最大并发持仓 / 总敞口上限）
  4. 品种评分：流动性×波动率×近期胜率 综合排序
  5. 跨品种相关性过滤（相关>0.85 只保留最高分）

优先级算法:
  priority = score×0.4 + liquidity_rank×0.3 + regime_bonus×0.2 + session_bonus×0.1
"""
import json, time, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

DATA_DIR  = Path(__file__).parent.parent / 'data'
QUEUE_LOG = DATA_DIR / 'signal_queue.jsonl'
STATE_F   = DATA_DIR / 'queue_state.json'

# 全局配置
MAX_CONCURRENT    = 3      # 最大并发持仓
MAX_TOTAL_RISK    = 0.06   # 总仓位不超过NAV 6%
COOLDOWN_MIN      = 120    # 默认冷却120分钟（CHOP体制）
CORR_THRESHOLD    = 0.85   # 相关系数阈值

# ── 设计院 v2.0：结构质量分级冷却（2026-06-04）──────────────────
# 核心原则：用结构质量替代时间冷却，市场不管你上次什么时候开仓
# S/A级(grade≥70)：完全豁免冷却——结构清晰，错过=损失Alpha
# B级(grade 50-69)：v24.2已全系统封堵，不再进入此逻辑
# C级(grade 25-49)：维持原冷却——边缘信号，谨慎
# X级(grade<25)：永久拒绝——不是冷却问题，是结构噪音
# Paper模式：grade≥50完全豁免——积累样本比模拟资金管理更优先
GRADE_COOLDOWN_EXEMPT   = 70   # grade≥70 完全豁免冷却（S/A级）
GRADE_COOLDOWN_FAST     = 70   # [v24.2] grade≥70 与全局门槛对齐（B级已封堵）
GRADE_COOLDOWN_FAST_MIN = 30   # B级快速冷却时长
GRADE_REJECT_BELOW      = 70   # [v24.2] grade<70 永久拒绝（与全系统门槛对齐）
PAPER_MODE_GRADE_EXEMPT = 70   # [v24.2] Paper模式grade≥70豁免冷却（B级已全系统封堵）
PAPER_MODE              = True # 武曲Paper积累阶段，实盘改False

# [UP-018 P2] 动态冷却：体制相关冷却时长（分钟）
# BEAR体制信号可靠性更高 → 90min；CHOP噪音多 → 120min；BULL趋势连贯 → 60min
REGIME_COOLDOWN = {
    'BEAR_EARLY':     90,
    'BEAR_TREND':     90,
    'BEAR_CRASH':     60,   # 崩跌快，需要快速响应
    'BEAR_RECOVERY':  60,   # [v24.3-fix] 90→60min
    'BULL_TREND':     60,
    'BULL_EARLY':     75,
    'BULL_PEAK':      90,
    'BULL_CORRECTION':90,
    'CHOP_LOW':      120,
    'CHOP_MID':      120,
    'CHOP_HIGH':     150,   # 高波震荡最难判断，加长冷却
    'RECOVERY':      120,
}  # 未匹配体制默认 COOLDOWN_MIN=120min

# 品种流动性排名（越小越好）
LIQUIDITY_RANK = {
    'BTCUSDT':1,'ETHUSDT':2,'BNBUSDT':3,'SOLUSDT':4,'XRPUSDT':5,
    'ADAUSDT':6,'DOTUSDT':7,'AVAXUSDT':8,'LINKUSDT':9,'LTCUSDT':10,
}

# 高度相关品种组（任意两个>0.85认为相关）
CORR_GROUPS = [
    {'BTCUSDT','ETHUSDT','SOLUSDT','AVAXUSDT'},   # L1 生态
    {'BNBUSDT'},                                   # 交易所币独立
    {'XRPUSDT','ADAUSDT','DOTUSDT'},               # 支付/基础设施
]


def _load_state() -> dict:
    if STATE_F.exists():
        try: return json.loads(STATE_F.read_text())
        except: pass
    return {'queue': [], 'cooldowns': {}, 'active_positions': [], 'last_updated': ''}


def _save_state(state: dict):
    state['last_updated'] = datetime.now(timezone.utc).isoformat()
    STATE_F.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _get_cooldown_min(regime: str = '') -> int:
    """根据体制返回冷却时长（分钟），未匹配返回默认值"""
    return REGIME_COOLDOWN.get(regime.upper() if regime else '', COOLDOWN_MIN)


def _is_in_cooldown(symbol: str, state: dict, regime: str = '', grade: int = 0) -> bool:
    """
    设计院 v2.0：结构质量分级冷却
    - Paper模式 + grade≥70 → 完全豁免 [v24.2]
    - S/A级 grade≥70 → 完全豁免
    - B级(已封堵) grade≥50 → N/A [v24.2 全系统封堵]
    - C级 grade 25-49 → 维持原冷却
    - X级 grade<25 → Bridge-Gate已拦，此处豁免（不是冷却问题）
    """
    # Paper模式：grade≥70完全豁免冷却 [v24.2 B级已封堵]
    if PAPER_MODE and grade >= PAPER_MODE_GRADE_EXEMPT:
        return False

    # S/A级：完全豁免
    if grade >= GRADE_COOLDOWN_EXEMPT:
        return False

    cd = state.get('cooldowns', {})
    if symbol not in cd:
        return False

    last_rec = cd[symbol]
    if isinstance(last_rec, dict):
        last_ts  = last_rec.get('ts', '')
        last_reg = last_rec.get('regime', '')
    else:
        last_ts  = last_rec
        last_reg = ''

    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_ts)).total_seconds() / 60

    # B级：冷却缩短至 GRADE_COOLDOWN_FAST_MIN
    if grade >= GRADE_COOLDOWN_FAST:
        return elapsed < GRADE_COOLDOWN_FAST_MIN

    # C级及其他：原始体制冷却时长
    cooldown_min = _get_cooldown_min(last_reg or regime)
    return elapsed < cooldown_min


def _corr_group(symbol: str) -> int:
    for i, grp in enumerate(CORR_GROUPS):
        if symbol in grp: return i
    return -1  # 独立品种


def _regime_bonus(regime: str, signal_dir: str) -> float:
    """体制与方向匹配加分"""
    r = regime.upper()
    d = signal_dir.upper()
    if 'BULL' in r and d in ('LONG','做多'): return 1.0
    if 'BEAR' in r and d in ('SHORT','做空'): return 1.0
    if 'CHOP' in r: return 0.3
    return 0.0


def _session_bonus() -> float:
    """US时段最高"""
    h = datetime.now(timezone.utc).hour
    if 13 <= h < 21: return 1.0   # US session
    if 8  <= h < 16: return 0.7   # EU session
    return 0.4                     # ASIA


def _load_recent_wr(symbol: str) -> float:
    """从trade_records读取近20笔该品种胜率"""
    trade_f = DATA_DIR / 'trade_records.jsonl'
    if not trade_f.exists(): return 0.35
    records = []
    for l in reversed(trade_f.read_text(errors='ignore').strip().split('\n')):
        if not l.strip(): continue
        try:
            r = json.loads(l)
            if r.get('symbol','') == symbol and r.get('result') in ('WIN','WIN_T1','WIN_T2','LOSS'):
                records.append(r)
        except: pass
        if len(records) >= 20: break
    if not records: return 0.35
    wins = sum(1 for r in records if r['result'].startswith('WIN'))
    return wins / len(records)


def add_signal(symbol: str, signal_dir: str, score: float, regime: str,
               extra: dict = None, grade: int = 0) -> dict:
    """
    尝试将信号加入队列
    grade: 结构质量分(0-100)，用于分级冷却豁免（设计院 v2.0）
    Returns:
        {'accepted': bool, 'reason': str, 'priority': float, 'rank': int}
    """
    state = _load_state()
    now = datetime.now(timezone.utc)

    # ── 检查1: 冷却期（结构分级豁免）──────────────────────
    if _is_in_cooldown(symbol, state, regime, grade):
        cd_min = GRADE_COOLDOWN_FAST_MIN if grade >= GRADE_COOLDOWN_FAST else _get_cooldown_min(regime)
        return {'accepted': False, 'reason': f'{symbol} in cooldown ({cd_min}min, grade={grade})', 'priority': 0}

    # ── 检查2: 并发上限 ────────────────────────────────────
    active = [p for p in state.get('active_positions', [])
              if p.get('status') == 'OPEN']
    if len(active) >= MAX_CONCURRENT:
        return {'accepted': False, 'reason': f'Max concurrent {MAX_CONCURRENT} reached', 'priority': 0}

    # ── 检查3: 相关性过滤 ──────────────────────────────────
    my_group = _corr_group(symbol)
    if my_group >= 0:
        active_syms = {p['symbol'] for p in active}
        for asym in active_syms:
            if _corr_group(asym) == my_group:
                return {'accepted': False, 'reason': f'Correlated with active {asym}', 'priority': 0}

    # ── 计算优先级 ─────────────────────────────────────────
    liq_rank = LIQUIDITY_RANK.get(symbol, 20)
    liq_score = max(0, 1.0 - (liq_rank - 1) * 0.05)   # rank1=1.0, rank20=0.05
    regime_b  = _regime_bonus(regime, signal_dir)
    session_b = _session_bonus()
    recent_wr = _load_recent_wr(symbol)
    wr_score  = (recent_wr - 0.25) / 0.25  # 0→0, 0.35→0.4, 0.5→1.0

    score_norm = min(score / 150.0, 1.0)
    priority = (score_norm*0.40 + liq_score*0.25 + regime_b*0.15
               + session_b*0.10 + wr_score*0.10)

    # ── 加入队列 ───────────────────────────────────────────
    entry = {
        'symbol': symbol,
        'signal_dir': signal_dir,
        'score': score,
        'regime': regime,
        'priority': round(priority, 4),
        'recent_wr': round(recent_wr, 3),
        'regime_bonus': regime_b,
        'session_bonus': session_b,
        'ts': now.isoformat(),
        'extra': extra or {},
    }

    queue = state.get('queue', [])
    queue.append(entry)
    # 按优先级排序
    queue.sort(key=lambda x: -x['priority'])
    state['queue'] = queue[:10]  # 最多保留10个候选

    # 设置冷却（存储体制，用于动态冷却时长计算）
    state['cooldowns'][symbol] = {'ts': now.isoformat(), 'regime': regime}

    _save_state(state)

    # 日志
    try:
        with open(QUEUE_LOG, 'a') as f:
            f.write(json.dumps({'action':'ADD', **entry}) + '\n')
    except: pass

    rank = next((i+1 for i,e in enumerate(queue) if e['symbol']==symbol), 99)
    return {
        'accepted': True,
        'reason': f'Added to queue rank #{rank}',
        'priority': round(priority, 4),
        'rank': rank,
        'queue_depth': len(queue),
        'regime_match': regime_b > 0.5,
    }


def get_next() -> dict | None:
    """取出优先级最高的信号"""
    state = _load_state()
    q = state.get('queue', [])
    if not q: return None
    top = q.pop(0)
    state['queue'] = q
    _save_state(state)
    return top


def get_status() -> dict:
    """队列状态摘要"""
    state = _load_state()
    q = state.get('queue', [])
    active = [p for p in state.get('active_positions', []) if p.get('status')=='OPEN']
    cds = state.get('cooldowns', {})
    now = datetime.now(timezone.utc)
    active_cds = {}
    for sym, rec in cds.items():
        if isinstance(rec, dict):
            ts_str = rec.get('ts', '')
            reg = rec.get('regime', '')
        else:
            ts_str = rec
            reg = ''
        try:
            cd_min = _get_cooldown_min(reg)
            elapsed = (now - datetime.fromisoformat(ts_str)).total_seconds() / 60
            remain = cd_min - elapsed
            if remain > 0:
                active_cds[sym] = f"{remain:.0f}min"
        except:
            pass
    return {
        'queue_depth': len(q),
        'active_positions': len(active),
        'max_concurrent': MAX_CONCURRENT,
        'slots_available': MAX_CONCURRENT - len(active),
        'active_cooldowns': active_cds,
        'top_signals': [{'symbol':e['symbol'],'dir':e['signal_dir'],'priority':e['priority']}
                        for e in q[:3]],
    }


if __name__ == '__main__':
    # 测试
    for sym, d, sc, reg in [
        ('BTCUSDT','SHORT',134,'CHOP_MID'),
        ('ETHUSDT','SHORT',154,'CHOP_MID'),
        ('SOLUSDT','SHORT',138,'CHOP_LOW'),
        ('BNBUSDT','LONG', 115,'BEAR_RECOVERY'),
    ]:
        r = add_signal(sym, d, sc, reg)
        print(f"{'✓' if r['accepted'] else '✗'} {sym} {d} score={sc}  priority={r['priority']:.3f}  {r['reason']}")
    print()
    st = get_status()
    print(f"Queue: {st['queue_depth']} signals  Active: {st['active_positions']}/{st['max_concurrent']}")
    for sig in st['top_signals']:
        print(f"  #{st['top_signals'].index(sig)+1} {sig['symbol']} {sig['dir']} p={sig['priority']:.3f}")
