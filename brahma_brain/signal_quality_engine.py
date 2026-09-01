"""
signal_quality_engine.py — 梵天信号质量引擎（唯一真相）
设计院自主决策 2026-08-07 | 苏摩「梵天 自主决策」授权

架构原则：
  信号质量判断在产生时完成，不在消费时打补丁。
  所有门控规则集中在此，auto_executor/其他下游不再做质量判断。

铁证来源：
  Gate 1 — sl_pct > 2.0%: WR=0% EV=-5.07% (BrahmaOptimizer n=15, 2026-08-07)
  Gate 2 — 体制方向死穴: BULL做空/BEAR做多 EV<0 (方仓v8 n=3000+)

待铁证化（Step B，数据积累后加入）：
  Gate 3 — timing=READY + 下行4H: WR=17.6% (n=17, 置信度不足，需n≥50)
  Gate 4 — RSI_4H 60-70 + score<148: EV=+0.055% (方仓铁证，但需实盘验证)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── 常量（铁证封印，不得随意修改）────────────────────────────────────────
SL_PCT_GATE = 2.0          # Gate1铁证: sl>2.0% WR=0% EV=-5.07% (n=15)
# SL_PCT_EXEMPT_SCORE 已废弃：铁证验证score≥155+sl>2% WR=0% EV=-5.87%，豁免无效，永久删除 (2026-08-22)

# 死穴体制方向组合（MEMORY.md宪法）
DEAD_ZONE_COMBOS = {
    ('BULL_TREND', 'SHORT'),
    ('BEAR_TREND', 'LONG'),
}


@dataclass
class GateResult:
    status: str          # 'PASS' | 'REJECT'
    gate_name: str = ''  # 触发的门控名称
    reason: str = ''     # 拒绝原因（供rejected_signal_log记录）
    score_penalty: float = 0.0  # 可选：降分而非拒绝

    @property
    def rejected(self) -> bool:
        return self.status == 'REJECT'

    @property
    def passed(self) -> bool:
        return self.status == 'PASS'

    def __repr__(self):
        if self.passed:
            return 'GateResult(PASS)'
        return f'GateResult(REJECT gate={self.gate_name} reason={self.reason!r})'


class SignalQualityEngine:
    """
    梵天信号质量引擎 — 唯一真相门控层

    用法：
        sqe = SignalQualityEngine()
        result = sqe.evaluate(signal_dict)
        if result.passed:
            log_signal(signal_dict)
        else:
            log_rejected(signal_dict, result)

    所有铁证门控规则集中在此。auto_executor 看到已写入的信号直接执行，
    不再做任何质量判断（它只做执行层：size/leverage/order routing）。
    """

    def evaluate(self, signal: dict) -> GateResult:
        """
        依次过所有 Gate，第一个 REJECT 立即返回。
        通过所有 Gate 返回 PASS。
        """
        for gate_fn in [
            self._gate1_sl_pct,
            self._gate2_regime_direction,
            self._gate3_timing_ready_downtrend,  # [封印 2026-08-07] READY×mid陷阱区
            self._gate4_rsi4h_low_eff,  # [P2 2026-08-26 启用]
        ]:
            result = gate_fn(signal)
            if result.rejected:
                return result
        return GateResult(status='PASS')

    # ── Gate 1: sl_pct 硬门控 ────────────────────────────────────────────
    def _gate1_sl_pct(self, signal: dict) -> GateResult:
        """
        铁证: sl_pct > 2.0% → WR=0% EV=-5.07% (BrahmaOptimizer n=15)
        豁免: score≥155 + BULL_TREND + LONG（小币高波动通道，sl≤15%）
        """
        sl_pct = float(signal.get('sl_pct', 0) or 0)
        if sl_pct <= 0:
            return GateResult(status='PASS')  # 无sl_pct信息不拦截

        score = float(signal.get('score', 0) or signal.get('score_final', 0) or 0)
        regime = str(signal.get('regime', '') or '')
        direction = str(signal.get('direction', '') or signal.get('signal_dir', '') or '')

        # [铁证修正 2026-08-07] 原豁免通道（score≥155+宽止损）验证 WR=0% EV=-5.87% → 取消豁免
        # 数据：豁免的8条 score≥155+sl>2% 全部止损，与原假设相反
        # 结论：sl_pct门控对所有信号一视同仁，无例外
        # [P0 2026-08-22 苏摩111] BULL_TREND:LONG 死亡区专项封禁
        # 铁证: simfactory n=14 WR=0% avg_pnl=-4.7% (score≥140且SL≥3%)
        if (regime == 'BULL_TREND' and direction == 'LONG' and
                score >= 140 and sl_pct >= 3.0):
            return GateResult(
                status='REJECT',
                gate_name='bull_long_death_zone',
                reason=f'BULL_TREND:LONG:score={score:.0f}≥140+sl={sl_pct:.1f}%≥3% 死亡区 WR=0% n=14 铁证封禁',
            )
        if sl_pct > SL_PCT_GATE:
            return GateResult(
                status='REJECT',
                gate_name='sl_pct_gate',
                reason=f'sl_pct={sl_pct:.2f}%>{SL_PCT_GATE}% wide_sl死亡区 WR=0% EV=-5.07%',
            )
        return GateResult(status='PASS')

    # ── Gate 2: 体制方向死穴 ─────────────────────────────────────────────
    def _gate2_regime_direction(self, signal: dict) -> GateResult:
        """
        体制宪法死穴：
          BULL_TREND做空: EV=-0.266% (方仓v8 n=3655)
          BEAR_TREND做多: EV<0 (体制宪法，BEAR_TREND_LONG WR=45%)
        """
        regime = str(signal.get('regime', '') or '')
        direction = str(signal.get('direction', '') or signal.get('signal_dir', '') or '')

        if not regime or not direction:
            return GateResult(status='PASS')  # 字段缺失不拦截

        combo = (regime, direction)
        if combo in DEAD_ZONE_COMBOS:
            return GateResult(
                status='REJECT',
                gate_name='regime_direction_gate',
                reason=f'{regime}+{direction} 体制死穴 EV<0',
            )
        return GateResult(status='PASS')

    # ── Gate 3: READY × mid_sl 陷阱区 ──────────────────────────────────
    def _gate3_timing_ready_downtrend(self, signal: dict) -> GateResult:
        """
        铁证封印 2026-08-07 设计院自主决策：
          READY × sl(1.0~1.5%): WR=8.3% EV=-0.991% n=12
          二项分布验证：WR=8.3%远低于50%阈值，n=12置信充足
          
        根因：timing=READY在震荡下行期误判为上行反弹入场
              1.0~1.5%止损区间在震荡期被频繁触碰
              组合效果：追反弹+止损位太近 = 系统性亏损

        保留场景（不拦截）：
          timing=empty × sl(1-1.5%): WR=75%（早期上行趋势）
          timing=STANDBY × sl(1-1.5%): WR=50%（合理观望信号）
        """
        timing = str(signal.get('timing_status', '') or '')
        sl_pct = float(signal.get('sl_pct', 0) or 0)
        
        if timing == 'READY' and 1.0 < sl_pct <= 1.5:
            return GateResult(
                status='REJECT',
                gate_name='ready_mid_sl_trap',
                reason=f'READY×mid_sl={sl_pct:.2f}% 陷阱区 WR=8.3% EV=-0.991% n=12',
            )
        return GateResult(status='PASS')

    # ── Gate 4（Step B，待铁证化）────────────────────────────────────────
    def _gate4_rsi4h_low_eff(self, signal: dict) -> GateResult:
        """
        [P2修复 2026-08-26] RSI_4H 60-70区间降仓验证通道
        逻辑：RSI_4H在60-70"抬头区"，方向不明确，注入0.5%NAV试仓标志
        不拦截信号，仅降仓让下游执行
        """
        direction = str(signal.get('direction', '') or signal.get('signal_dir', '') or '')
        score     = float(signal.get('score', 0) or signal.get('score_final', 0) or 0)
        rsi_4h    = float(signal.get('rsi_4h', 0) or signal.get('rsi4h', 0) or
                          signal.get('indicators', {}).get('rsi_4h', 0) or 0)

        if (direction == 'LONG' and 60 <= rsi_4h <= 70 and 0 < score < 148 and rsi_4h > 0):
            # 不拦截，注入降仓标志：0.5%NAV试仓验证通道
            signal['_gate4_lite'] = True
            signal.setdefault('_pos_override_pct', 0.5)
        return GateResult(status='PASS')


# ── 单例（模块级）────────────────────────────────────────────────────────
_sqe_instance: Optional[SignalQualityEngine] = None


def get_sqe() -> SignalQualityEngine:
    """获取 SQE 单例"""
    global _sqe_instance
    if _sqe_instance is None:
        _sqe_instance = SignalQualityEngine()
    return _sqe_instance


def evaluate_signal(signal: dict) -> GateResult:
    """模块级便捷函数，供 brahma_engine 直接调用"""
    return get_sqe().evaluate(signal)


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/signal_queue.py ══
# ponytail: signal_queue 322行，有意为之，重构前先 grep 所有调用方
"""

# STATUS: ACTIVE
# 信号队列管理，异步处理
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
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

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/signal_lifecycle.py ══
"""
P3 信号生命周期管理 + P4 TP动态计算 + P5 评分数据实时性
梵天设计院封印 2026-07-26 · 苏摩授权自主执行
"""
import json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

_SIGNAL_LOG = Path(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
_LIFECYCLE_STATE = Path(__file__).parent.parent / 'data' / '_signal_lifecycle.json'


# ── P3: 信号生命周期管理 ────────────────────────────────

SIGNAL_TTL_BARS = 8        # 默认8根1H K线后过期
SIGNAL_TTL_SECS = 8 * 3600

def tick_signal_lifecycle(symbol: str, current_price: float) -> list:
    """
    检查所有OPEN信号的生命周期状态
    返回需要推送的告警列表
    """
    alerts = []
    if not _SIGNAL_LOG.exists():
        return alerts

    lines = _SIGNAL_LOG.read_text().strip().split('\n')
    updated_lines = []
    now_ts = time.time()

    for line in lines:
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
        except Exception:
            updated_lines.append(line)
            continue

        # 只处理当前标的的OPEN信号
        if sig.get('symbol', '').upper() != symbol.upper():
            updated_lines.append(line)
            continue
        if sig.get('status') != 'OPEN':
            updated_lines.append(line)
            continue

        direction = sig.get('direction', 'LONG')
        entry_price = float(sig.get('entry_price', 0) or 0)
        sl_price = float(sig.get('sl_price', 0) or 0)
        tp1_price = float(sig.get('tp1_price', 0) or 0)
        sig_ts = float(sig.get('ts', now_ts) or now_ts)
        sig_id = sig.get('id', sig.get('sha8', '?'))

        # TTL检查
        age_secs = now_ts - sig_ts
        if age_secs > SIGNAL_TTL_SECS:
            sig['status'] = 'EXPIRED'
            sig['result'] = 'EXPIRED'
            sig['settled_price'] = current_price
            sig['settled_ts'] = now_ts
            pnl = ((current_price - entry_price) / entry_price * 100
                   if direction == 'LONG'
                   else (entry_price - current_price) / entry_price * 100)
            sig['pnl_pct'] = round(pnl, 3)
            alerts.append({
                'level': 'INFO',
                'msg': f'⏰ [{symbol}] 信号{sig_id}已超过{SIGNAL_TTL_BARS}H TTL → EXPIRED，PnL={pnl:+.2f}%'
            })
            updated_lines.append(json.dumps(sig, ensure_ascii=False))
            continue

        # SL触发检查
        if sl_price > 0 and entry_price > 0:
            sl_hit = (direction == 'LONG' and current_price <= sl_price) or \
                     (direction == 'SHORT' and current_price >= sl_price)
            if sl_hit:
                sig['status'] = 'STOP_LOSS'
                sig['result'] = 'STOP_LOSS'
                sig['settled_price'] = current_price
                sig['settled_ts'] = now_ts
                pnl = ((current_price - entry_price) / entry_price * 100
                       if direction == 'LONG'
                       else (entry_price - current_price) / entry_price * 100)
                sig['pnl_pct'] = round(pnl, 3)
                alerts.append({
                    'level': 'CRITICAL',
                    'msg': (f'🚨 [{symbol}] 信号{sig_id} STOP_LOSS触发！\n'
                            f'  入场: ${entry_price:.4f} → 现价: ${current_price:.4f}\n'
                            f'  SL: ${sl_price:.4f}  PnL: {pnl:+.2f}%')
                })
                updated_lines.append(json.dumps(sig, ensure_ascii=False))
                continue

        # TP1触发检查
        if tp1_price > 0 and entry_price > 0:
            tp1_hit = (direction == 'LONG' and current_price >= tp1_price) or \
                      (direction == 'SHORT' and current_price <= tp1_price)
            if tp1_hit and sig.get('tp1_hit') != True:
                sig['tp1_hit'] = True
                sig['tp1_ts'] = now_ts
                sig['result'] = 'TP1'          # [fix 2026-07-27 闭环TP1写入]
                sig['settled_price'] = current_price
                sig['settled_ts'] = now_ts
                pnl = ((current_price - entry_price) / entry_price * 100
                       if direction == 'LONG'
                       else (entry_price - current_price) / entry_price * 100)
                sig['pnl_pct'] = round(pnl, 3)
                alerts.append({
                    'level': 'SUCCESS',
                    'msg': (f'✅ [{symbol}] 信号{sig_id} TP1触达！\n'
                            f'  入场: ${entry_price:.4f} → TP1: ${tp1_price:.4f}\n'
                            f'  PnL: {pnl:+.2f}%  建议：移动止损至保本位')
                })

        # TP2触发检查（TP1已触达后才检查TP2）[fix 2026-07-28 TP2闭环]
        tp2_price = float(sig.get('tp2') or sig.get('tp2_price') or 0)
        if tp2_price > 0 and sig.get('tp1_hit') and not sig.get('tp2_hit'):
            tp2_hit = (direction == 'LONG' and current_price >= tp2_price) or \
                      (direction == 'SHORT' and current_price <= tp2_price)
            if tp2_hit:
                sig['tp2_hit'] = True
                sig['tp2_ts'] = now_ts
                sig['result'] = 'TP2'
                sig['settled_price'] = current_price
                sig['settled_ts'] = now_ts
                pnl2 = ((current_price - entry_price) / entry_price * 100
                        if direction == 'LONG'
                        else (entry_price - current_price) / entry_price * 100)
                sig['pnl_pct'] = round(pnl2, 3)
                alerts.append({
                    'level': 'SUCCESS',
                    'msg': (f'🎯 [{symbol}] 信号{sig_id} TP2触达！\n'
                            f'  入场: ${entry_price:.4f} → TP2: ${tp2_price:.4f}\n'
                            f'  PnL: {pnl2:+.2f}%  满仓出场')
                })

        updated_lines.append(json.dumps(sig, ensure_ascii=False))

    # 写回
    _SIGNAL_LOG.write_text('\n'.join(updated_lines) + '\n')
    return alerts


# ── P4: TP基于清算集群密度动态计算 ────────────────────────

def calc_dynamic_tp(
    direction: str,
    entry_price: float,
    liq_heatmap: dict,
    equal_highs: list,
    equal_lows: list,
    fallback_rr: float = 2.0
) -> tuple:
    """
    P4: 基于清算集群密度动态计算TP1/TP2
    Returns (tp1, tp2, method)
    """
    candidates = []

    if direction == 'LONG':
        # TP候选：上方等高止损池（空头踩踏区）
        for pool in equal_highs[:5]:
            price = float(pool.get('level', 0) or 0)
            count = int(pool.get('count', 1) or 1)
            if price > entry_price:
                candidates.append((price, count))

        # 也考虑清算热力图上方密集区
        liq_clusters = liq_heatmap.get('long_clusters', []) if isinstance(liq_heatmap, dict) else []
        for c in liq_clusters[:3]:
            price = float(c.get('price', 0) or 0)
            density = float(c.get('density', 1) or 1)
            if price > entry_price:
                candidates.append((price, int(density * 2)))

    else:  # SHORT
        for pool in equal_lows[:5]:
            price = float(pool.get('level', 0) or 0)
            count = int(pool.get('count', 1) or 1)
            if price < entry_price:
                candidates.append((price, count))

    if not candidates:
        # fallback: RR倍数
        sl_dist = entry_price * 0.02
        if direction == 'LONG':
            tp1 = round(entry_price + sl_dist * fallback_rr, 4)
            tp2 = round(entry_price + sl_dist * fallback_rr * 2, 4)
        else:
            tp1 = round(entry_price - sl_dist * fallback_rr, 4)
            tp2 = round(entry_price - sl_dist * fallback_rr * 2, 4)
        return tp1, tp2, 'FALLBACK_RR'

    # 按密度排序，取前两个
    candidates.sort(key=lambda x: x[1], reverse=True)
    prices_sorted = sorted([c[0] for c in candidates],
                           key=lambda p: abs(p - entry_price))
    tp1 = prices_sorted[0] if len(prices_sorted) >= 1 else None
    tp2 = prices_sorted[1] if len(prices_sorted) >= 2 else None

    if tp1 is None:
        return None, None, 'NO_TARGET'

    return round(tp1, 4), round(tp2, 4) if tp2 else None, 'LIQUIDITY_CLUSTER'


# ── P5: 评分数据实时性审计 ─────────────────────────────────

def audit_score_with_realtime(symbol: str, score_breakdown: dict) -> dict:
    """
    P5: 对关键评分维度附上实时原始数据
    返回增强的breakdown字典，每个维度附上2~3个原始指标值
    """
    enhanced = dict(score_breakdown)

    try:
        # 拉取1H K线
        try:
            from brahma_brain.data_cache import get_klines as _dc
            klines = _dc(symbol, '1h', 20) or []
        except Exception:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=20'
            klines = json.loads(urllib.request.urlopen(url, timeout=6).read())
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        lows = [float(k[3]) for k in klines]

        cur_vol = volumes[-1]
        ma5_vol = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else cur_vol
        vol_decay_pct = round((cur_vol - ma5_vol) / ma5_vol * 100, 1) if ma5_vol > 0 else 0

        # OBV
        obv = 0
        for i in range(1, len(klines)):
            c, pc = float(klines[i][4]), float(klines[i-1][4])
            v = float(klines[i][5])
            obv += v if c > pc else (-v if c < pc else 0)
        obv_prev = obv - (volumes[-1] if closes[-1] > closes[-2] else -volumes[-1])
        obv_dir = 'UP' if obv > obv_prev else 'DOWN'

        # RSI
        gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        rsi_cur = round(100 - 100 / (1 + ag/al), 1) if al > 0 else 100

        # 价格低点比较（底背离检测）
        price_low_cur = min(lows[-6:]) if len(lows) >= 6 else lows[-1]
        price_low_prev = min(lows[-14:-6]) if len(lows) >= 14 else price_low_cur
        rsi_arr = []
        for i in range(14, len(closes)):
            g = [max(closes[j]-closes[j-1],0) for j in range(i-13,i+1)]
            l = [max(closes[j-1]-closes[j],0) for j in range(i-13,i+1)]
            ag2 = sum(g)/14; al2 = sum(l)/14
            rsi_arr.append(round(100-100/(1+(ag2/al2 if al2 else 999)),1))
        rsi_low_cur = min(rsi_arr[-6:]) if len(rsi_arr) >= 6 else rsi_cur
        rsi_low_prev = min(rsi_arr[-14:-6]) if len(rsi_arr) >= 14 else rsi_low_cur
        div_valid = price_low_cur < price_low_prev and rsi_low_cur > rsi_low_prev

        ts_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
        enhanced['_P5_realtime'] = {
            'ts': ts_str,
            '量能衰竭_实测': {
                '当前1H量': f'{cur_vol:,.0f}张',
                'MA5均量': f'{ma5_vol:,.0f}张',
                '衰减率': f'{vol_decay_pct:+.1f}%(相对MA5)',
                'OBV方向': obv_dir,
                '评分是否合理': '⚠️存疑' if (vol_decay_pct > -30 and '衰竭' in str(score_breakdown.get('vol_exhaustion',''))) else '✅基本符合'
            },
            '底背离_实测': {
                '当前RSI_1H': rsi_cur,
                '价格低点_当前': round(price_low_cur, 4),
                '价格低点_前期': round(price_low_prev, 4),
                '底背离_是否成立': '✅成立(价低RSI高)' if div_valid else '❌不成立(实测数据)',
            }
        }

    except Exception as e:
        enhanced['_P5_realtime'] = {'error': str(e)}

    return enhanced

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/signal_trace.py ══
"""
signal_trace.py — 信号执行轨迹审计日志
brahma_brain · 设计院封印 2026-07-02

# ╔══ INTERFACE CONTRACT ══════════════════════════════════════╗
# 入口: log_signal_trace(result, action, outcome=None) -> None
# 入口: get_trace_history(symbol=None, limit=50) -> list[dict]
# 入口: format_audit_report(traces) -> str
# 输出: JSONL → logs/signal_trace.jsonl
# 设计目标: 完整记录 信号生成→LLM审查→执行→实际PnL 的完整链路
# ╚═══════════════════════════════════════════════════════════╝

每条trace记录格式（JSONL）:
{
  "ts":         "2026-07-02T04:00:00Z",
  "signal_id":  "BRAHMA:P1:RUNNER:BTCUSDT:152:SHORT:BEAR_TREND:...:a3f7c2d1",
  "symbol":     "BTCUSDT",
  "score":      152,
  "direction":  "SHORT",
  "regime":     "BEAR_TREND",
  "grade":      88,
  "valid":      true,
  "action":     "SIGNAL_GENERATED | SIGNAL_SKIPPED | EXECUTED | CLOSED",
  "entry":      60094.0,
  "sl":         61200.0,
  "tp1":        58600.0,
  "timing":     "READY",
  "kronos_p_up": 0.383,
  "llm_council": "APPROVED | SKIPPED | N/A",
  "outcome":    {"exit_price": 58603, "pnl_pct": 2.48, "duration_h": 14},
  "sha8":       "a3f7c2d1"
}
"""
import json
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

_TRACE_LOG = os.path.join(os.path.dirname(__file__), '..', 'logs', 'signal_trace.jsonl')
_TRACE_LOG = os.path.normpath(_TRACE_LOG)


def _sha8(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:8]


def _parse_tag(tag: str) -> dict:
    """从BRAHMA标签解析元数据"""
    try:
        parts = tag.strip('[]').split(':')
        if len(parts) >= 9 and parts[0] == 'BRAHMA':
            return {
                'level':     parts[1],
                'source':    parts[2],
                'symbol':    parts[3],
                'score':     int(float(parts[4])),  # fix: '72.0' → 72
                'direction': parts[5],
                'regime':    parts[6],
                'ts_tag':    parts[7],
                'sha8':      parts[8],
            }
    except Exception:
        pass
    return {}


def log_signal_trace(
    result:     dict,
    action:     str,            # SIGNAL_GENERATED | SIGNAL_SKIPPED | EXECUTED | CLOSED
    outcome:    Optional[dict] = None,  # {'exit_price': ..., 'pnl_pct': ..., 'duration_h': ...}
    llm_council: str = 'N/A',  # APPROVED | REJECTED | SKIPPED
) -> None:
    """记录一条信号轨迹到 logs/signal_trace.jsonl"""
    try:
        meta   = result.get('_runner_meta', {})
        tag    = meta.get('output_tag', '')
        fields = result.get('_fields', result)
        timing = result.get('_timing', {})
        tag_d  = _parse_tag(tag)

        # 优先使用runner注入的字段（更准确）
        symbol    = result.get('_direction_for_trace') and tag_d.get('symbol') or tag_d.get('symbol') or fields.get('symbol', '?')
        score     = result.get('_score_for_trace') or tag_d.get('score') or fields.get('score', 0)
        direction = result.get('_direction_for_trace') or tag_d.get('direction') or fields.get('direction', '?')
        regime    = tag_d.get('regime') or fields.get('regime', '?')
        sha8      = tag_d.get('sha8', _sha8(f'{symbol}{score}{direction}'))

        record = {
            'ts':           datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'signal_id':    tag or f'BRAHMA:?:{symbol}:{score}:{direction}:{sha8}',
            'symbol':       symbol,
            'score':        score,
            'direction':    direction,
            'regime':       regime,
            'grade':        fields.get('structure_grade') or fields.get('grade'),
            'valid':        fields.get('valid', False),
            'action':       action,
            'entry_lo':     fields.get('entry_lo'),
            'entry_hi':     fields.get('entry_hi'),
            'sl':           fields.get('sl'),
            'tp1':          fields.get('tp1'),
            'timing':       timing.get('state') if isinstance(timing, dict) else timing,
            'kronos_p_up':  result.get('s23_p_up') or result.get('kronos_p_up'),
            'llm_council':  llm_council,
            'outcome':      outcome,
            'sha8':         sha8,
        }

        os.makedirs(os.path.dirname(_TRACE_LOG), exist_ok=True)
        with open(_TRACE_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    except Exception as e:
        pass  # 审计日志不应影响主流程


def get_trace_history(symbol: Optional[str] = None, limit: int = 50) -> list:
    """读取最近的信号轨迹记录"""
    if not os.path.exists(_TRACE_LOG):
        return []
    try:
        lines = open(_TRACE_LOG, encoding='utf-8').readlines()
        records = []
        for line in reversed(lines):
            try:
                r = json.loads(line.strip())
                if symbol and r.get('symbol') != symbol:
                    continue
                records.append(r)
                if len(records) >= limit:
                    break
            except Exception:
                continue
        return records
    except Exception:
        return []


def format_audit_report(traces: Optional[list] = None, limit: int = 20) -> str:
    """格式化审计报告（用于llm_council或健康检查）"""
    if traces is None:
        traces = get_trace_history(limit=limit)
    if not traces:
        return '  [signal_trace] 暂无记录'

    lines = ['  📋 信号轨迹审计（最近{}条）'.format(len(traces))]
    gen     = [t for t in traces if t.get('action') == 'SIGNAL_GENERATED']
    skip    = [t for t in traces if t.get('action') == 'SIGNAL_SKIPPED']
    exec_   = [t for t in traces if t.get('action') == 'EXECUTED']
    closed  = [t for t in traces if t.get('action') == 'CLOSED' and t.get('outcome')]

    lines.append(f'  生成: {len(gen)} | 跳过: {len(skip)} | 执行: {len(exec_)} | 平仓: {len(closed)}')

    if closed:
        pnls = [c['outcome']['pnl_pct'] for c in closed if isinstance(c.get('outcome'), dict)]
        if pnls:
            lines.append(f'  平仓均收益: {sum(pnls)/len(pnls):+.2f}% | 盈利: {sum(1 for p in pnls if p>0)}/{len(pnls)}')

    for t in traces[:5]:
        score = t.get('score', 0)
        action = t.get('action', '?')[:8]
        timing = t.get('timing', '-')
        llm = t.get('llm_council', '-')
        ts = t.get('ts', '')[-8:-1]  # HH:MM:SS
        lines.append(f'  [{ts}] {t.get("symbol","?"):10} {score:3}分 {t.get("direction","?")} {action} timing={timing} llm={llm}')

    return '\n'.join(lines)


# ── 便捷函数：注入brahma_analysis_runner ─────────────────────────────────
def trace_generated(result: dict, llm_council: str = 'N/A') -> None:
    """信号已生成（valid=True）"""
    log_signal_trace(result, 'SIGNAL_GENERATED', llm_council=llm_council)


def trace_skipped(result: dict) -> None:
    """信号被跳过（valid=False）"""
    log_signal_trace(result, 'SIGNAL_SKIPPED')


def trace_executed(result: dict, entry_price: float) -> None:
    """信号已执行（下单完成）"""
    log_signal_trace(result, 'EXECUTED', outcome={'entry_price': entry_price})


def trace_closed(result: dict, exit_price: float, pnl_pct: float, duration_h: float) -> None:
    """仓位平仓"""
    log_signal_trace(result, 'CLOSED', outcome={
        'exit_price': exit_price,
        'pnl_pct':    pnl_pct,
        'duration_h': duration_h,
    })

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/signal_weight_updater.py ══
"""
signal_weight_updater.py — 结算闭环权重更新器 v1.0
设计院封印 2026-08-09 苏摩111

职责：
  每次 signal_settler 结算新信号后调用此模块
  → 按 regime:direction:score_tier 分组统计滚动WR
  → 动态更新 data/signal_weights.json 对应 multiplier
  → brahma_core 下次 analyze() 自动读取更新后的权重

闭环路径：
  信号产生 → auto_executor执行 → signal_settler结算
  → signal_weight_updater更新weights → brahma_core读取 → 下次信号

设计原则（梵天宪法）：
  - 最简实现：纯stdlib，零新依赖
  - 保守更新：n<20 不更新（样本不足）
  - 静态规则优先：手工铁证 (min_n_override) 不被覆盖
  - fail-safe：任何异常静默，不影响主结算流程
"""

import json
import time
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent.parent
SF_PATH  = BASE / 'data' / 'simfactory_trades.jsonl'
SW_PATH  = BASE / 'data' / 'signal_weights.json'
LOG_PATH = BASE / 'data' / 'weight_update_log.jsonl'

# ── 分组窗口 & 阈值 ───────────────────────────────────────────────────────
ROLLING_N   = 30    # 滚动窗口：最近30笔同类信号
MIN_N       = 15    # 至少15笔才允许动态更新（避免噪音）
WR_HIGH     = 0.62  # WR >= 62% → multiplier 向 1.2 靠拢
WR_LOW      = 0.42  # WR <= 42% → multiplier 向 0.3 靠拢
WR_DEAD     = 0.30  # WR <= 30% → 考虑降为 0.0（需连续3次确认）

# ── 不允许动态覆盖的静态铁证规则（手工封印优先）──────────────────────────
STATIC_LOCK = {
    'CHOP_MID:LONG',           # 死穴永久封禁
    'CHOP_MID:LONG:155+',      # 死穴
    'BEAR_TREND:LONG:155+',    # 逆势死亡区
    'BEAR_TREND:LONG:140-154', # 逆势极危
}


def _score_tier(score: float) -> str:
    """将score转换为分段标签"""
    if score >= 165: return '165+'
    if score >= 155: return '155+'
    if score >= 140: return '140-154'
    if score >= 120: return '120-139'
    return '<120'


def _load_trades() -> list:
    """加载simfactory_trades.jsonl，返回已结算记录"""
    if not SF_PATH.exists():
        return []
    trades = []
    for line in SF_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
            # 只取有明确结果的（TP1/SL，排除EXPIRE超时）
            result = t.get('result', t.get('outcome', ''))
            if result in ('TP1', 'TP2', 'SL'):
                trades.append(t)
        except Exception:
            continue
    return trades


def _load_signal_weights() -> dict:
    """加载signal_weights.json，返回整个结构"""
    if not SW_PATH.exists():
        return {'version': '1.0', 'weights': {}}
    try:
        return json.loads(SW_PATH.read_text())
    except Exception:
        return {'version': '1.0', 'weights': {}}


def _calc_rolling_wr(trades: list, key_regime: str, key_dir: str,
                     key_tier: str) -> tuple:
    """
    计算 regime:direction:score_tier 最近 ROLLING_N 笔的 WR。
    返回 (wr, n_used) 或 (None, 0) 表示样本不足
    """
    # 按时间倒序，找匹配的最近 ROLLING_N 笔
    matched = []
    for t in reversed(trades):
        t_regime = t.get('regime', '')
        t_dir    = t.get('direction', '')
        t_score  = float(t.get('score', 0) or 0)
        t_tier   = _score_tier(t_score)
        if t_regime == key_regime and t_dir == key_dir and t_tier == key_tier:
            result = t.get('result', t.get('outcome', ''))
            matched.append(result in ('TP1', 'TP2'))
        if len(matched) >= ROLLING_N:
            break

    n = len(matched)
    if n < MIN_N:
        return None, n

    wr = sum(matched) / n
    return round(wr, 4), n


def _new_multiplier(current_mult: float, wr: float, n: int) -> float:
    """
    根据实盘滚动WR平滑调整 multiplier。
    保守更新：每次最多调整 ±0.1，避免剧烈波动。
    """
    # 目标 multiplier
    if wr >= WR_HIGH:
        target = min(1.5, current_mult + 0.1)   # 高WR → 轻微加权
    elif wr <= WR_LOW:
        target = max(0.1, current_mult - 0.1)   # 低WR → 轻微降权
    else:
        return current_mult  # 中性区间不变

    # 平滑：向目标靠拢 50%
    new_mult = current_mult + 0.5 * (target - current_mult)
    return round(new_mult, 3)


def update_weights(dry_run: bool = False) -> dict:
    """
    主入口：扫描实盘结算数据，动态更新 signal_weights.json。

    返回：
      {
        'updated': int,   # 更新的key数量
        'skipped': int,   # 跳过的key数量（样本不足 or 静态锁定）
        'changes': list,  # 每个变化的详情
      }
    """
    trades  = _load_trades()
    sw_data = _load_signal_weights()
    weights = sw_data.get('weights', {})

    if not trades:
        return {'updated': 0, 'skipped': 0, 'changes': [],
                'reason': 'no_trades'}

    updated  = 0
    skipped  = 0
    changes  = []

    # 遍历所有现有 key
    for key, entry in weights.items():
        # 静态锁定检查
        if key in STATIC_LOCK:
            skipped += 1
            continue

        # 解析 key：REGIME:DIRECTION[:TIER]
        parts = key.split(':')
        if len(parts) < 2:
            skipped += 1
            continue
        regime = parts[0]
        direc  = parts[1]
        tier   = parts[2] if len(parts) >= 3 else None

        # 如果没有 tier，跳过（聚合key，由各tier子key覆盖）
        if tier is None:
            skipped += 1
            continue

        current_mult = float(entry.get('multiplier', 1.0) if isinstance(entry, dict) else entry)

        wr, n = _calc_rolling_wr(trades, regime, direc, tier)

        if wr is None:
            # 样本不足
            skipped += 1
            continue

        new_mult = _new_multiplier(current_mult, wr, n)

        if abs(new_mult - current_mult) < 0.005:
            # 变化太小，不更新
            skipped += 1
            continue

        # 记录变化
        change = {
            'key':          key,
            'old_mult':     current_mult,
            'new_mult':     new_mult,
            'wr':           wr,
            'n':            n,
            'ts':           int(time.time()),
        }
        changes.append(change)

        if not dry_run:
            if isinstance(entry, dict):
                entry['multiplier']      = new_mult
                entry['live_wr']         = wr
                entry['live_n']          = n
                entry['last_updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                entry['note']            = '%s (auto-updated live_wr=%.0f%% n=%d)' % (
                    entry.get('note', ''), wr * 100, n)
            else:
                weights[key] = {
                    'multiplier': new_mult,
                    'live_wr':    wr,
                    'live_n':     n,
                }

        updated += 1

    if not dry_run and changes:
        sw_data['weights']            = weights
        sw_data['last_auto_update']   = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        sw_data['auto_update_trades'] = len(trades)
        SW_PATH.write_text(json.dumps(sw_data, indent=2, ensure_ascii=False))

        # 写更新日志
        log_entry = {
            'ts':      int(time.time()),
            'updated': updated,
            'skipped': skipped,
            'changes': changes,
        }
        with open(LOG_PATH, 'a') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    return {
        'updated':  updated,
        'skipped':  skipped,
        'changes':  changes,
        'n_trades': len(trades),
    }


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    dry = '--dry-run' in sys.argv
    result = update_weights(dry_run=dry)
    prefix = '[DRY-RUN] ' if dry else ''
    print('%ssignal_weight_updater: updated=%d skipped=%d n_trades=%d' % (
        prefix, result['updated'], result['skipped'], result['n_trades']))
    for c in result['changes']:
        print('  %s  %.2f→%.2f  live_wr=%.0f%% n=%d' % (
            c['key'], c['old_mult'], c['new_mult'], 100*c['wr'], c['n']))
    if not result['changes']:
        print('  (无变化：样本不足或已是最优)')

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/signal_integrity_gate.py ══
"""
SignalIntegrityGate — P0~P2 信号完整性门控
梵天设计院封印 2026-07-26 · 苏摩授权自主执行

P0: consensus + timing 互锁门控
P1: SL距离硬封禁（>5% → REJECT）
P2: 体制动态衰减（从峰值-3%开始）
"""
import time
from pathlib import Path
import json

# ── P0: 门控常量 ───────────────────────────────────────
# consensus包含BEAR且方向LONG → 拒绝
CONSENSUS_LONG_BLOCK  = {'BEAR', 'FULL_BEAR', 'STRONG_BEAR'}
CONSENSUS_SHORT_BLOCK = {'BULL', 'FULL_BULL', 'STRONG_BULL'}

# timing_status不允许ENTER的状态
TIMING_BLOCK_ACTIONS = {'ENTER', 'ENTER_FULL'}
TIMING_BLOCK_STATUS  = {'WAIT', 'STANDBY'}

# ── P1: SL距离上限 ─────────────────────────────────────
SL_PCT_HARD_CAP = 5.0  # 超过5%直接拒绝

# ── P2: 体制动态衰减 ────────────────────────────────────
REGIME_DECAY_THRESHOLD = -3.0   # 从峰值下跌超过3%开始衰减
REGIME_DECAY_FACTOR    = 3.0    # 衰减系数
REGIME_DECAY_FLOOR     = 0.40   # 最低保留40%权重
CONSECUTIVE_RED_PENALTY = 0.70  # 连续3根4H阴线额外×0.70

# 峰值追踪文件
_PEAK_STATE_FILE = Path(__file__).parent.parent / 'data' / '_regime_peak_state.json'


class SignalIntegrityGate:
    """
    P0~P2 信号完整性门控
    在brahma_engine最终 _valid 计算前调用
    返回 (pass: bool, reason: str)
    """

    @staticmethod
    def validate(
        direction: str,
        action: str,
        consensus: str,
        timing_status: str,
        sl_pct: float,
        regime: str,
        score: float,
        symbol: str = '',  # [2026-07-28] RWA代币SL放宽需要symbol
    ) -> tuple:
        """
        Returns (True, '') if signal passes all gates
        Returns (False, reason) if rejected
        """
        consensus_upper = (consensus or '').upper().replace(' ', '_')
        timing_upper    = (timing_status or '').upper()
        direction_upper = (direction or '').upper()
        action_upper    = (action or '').upper()

        # ── P0-A: consensus与方向冲突 ──────────────────────
        if direction_upper == 'LONG' and consensus_upper in CONSENSUS_LONG_BLOCK:
            return False, f'[P0-A] consensus={consensus} 与 LONG 方向冲突 → REJECT'

        if direction_upper == 'SHORT' and consensus_upper in CONSENSUS_SHORT_BLOCK:
            return False, f'[P0-A] consensus={consensus} 与 SHORT 方向冲突 → REJECT'

        # ── P0-B: timing=WAIT/STANDBY 时禁止ENTER ──────────
        if timing_upper in TIMING_BLOCK_STATUS and action_upper in TIMING_BLOCK_ACTIONS:
            return False, f'[P0-B] timing_status={timing_status} 时禁止 {action} → REJECT'

        # ── P1: SL距离硬封禁 ────────────────────────────────
        # [2026-07-28 设计院封印] RWA/TradFi代币波动更大，放宽至6%
        from brahma_brain.tradfi_dump_detector import is_tradfi_token as _is_tf
        _sl_cap = 6.0 if _is_tf(symbol) else SL_PCT_HARD_CAP
        if sl_pct > _sl_cap:
            return False, (
                f'[P1] SL距离={sl_pct:.2f}% 超过硬上限{_sl_cap}% → REJECT'
                f'（SL距离铁律：BEAR/BULL体制≤2.5%，绝对上限≤{_sl_cap}%）'
            )

        return True, ''


def get_dynamic_regime_mult(
    regime: str,
    base_mult: float,
    symbol: str,
    current_price: float,
) -> tuple:
    """
    P2: 体制动态衰减
    Returns (adjusted_mult: float, note: str)
    """
    try:
        # 读取峰值状态
        state = {}
        if _PEAK_STATE_FILE.exists():
            state = json.loads(_PEAK_STATE_FILE.read_text())

        sym_state = state.get(symbol, {})
        peak_price = sym_state.get('peak_price', current_price)
        peak_ts    = sym_state.get('peak_ts', time.time())
        peak_regime = sym_state.get('peak_regime', regime)

        # 更新峰值（只在BULL体制下追踪峰值）
        if regime in ('BULL_TREND', 'BULL_EARLY') and current_price > peak_price:
            peak_price = current_price
            peak_ts    = time.time()
            peak_regime = regime
            state[symbol] = {
                'peak_price': peak_price,
                'peak_ts':    peak_ts,
                'peak_regime': peak_regime,
            }
            _PEAK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PEAK_STATE_FILE.write_text(json.dumps(state, indent=2))

        # 计算从峰值的跌幅
        if peak_price > 0:
            drop_pct = (current_price - peak_price) / peak_price * 100
        else:
            drop_pct = 0.0

        # 仅在BULL体制下应用衰减（BEAR/CHOP体制有自己的乘数）
        if regime not in ('BULL_TREND', 'BULL_EARLY'):
            return base_mult, ''

        if drop_pct >= REGIME_DECAY_THRESHOLD:
            # 价格未偏离峰值或在上涨，不衰减
            return base_mult, ''

        # 计算衰减
        decay_depth = abs(drop_pct - REGIME_DECAY_THRESHOLD) / 100
        decay_factor = 1.0 - decay_depth * REGIME_DECAY_FACTOR
        adjusted_mult = max(REGIME_DECAY_FLOOR, base_mult * decay_factor)

        note = (
            f'[P2] 体制动态衰减: {regime} 从峰值${peak_price:.2f}跌{drop_pct:.1f}% '
            f'→ mult {base_mult:.2f}→{adjusted_mult:.2f}'
        )
        return adjusted_mult, note

    except Exception as e:
        return base_mult, f'[P2] 衰减计算异常: {e}'


# ── 便捷函数供brahma_engine直接调用 ──────────────────────

def gate_check(cf: dict, params: dict, ms: dict) -> tuple:
    """
    主入口：从brahma_engine的cf/params/ms中提取字段，执行P0+P1检查
    Returns (pass: bool, reason: str)
    """
    direction     = ms.get('signal_dir', cf.get('signal_dir', ''))
    action        = cf.get('action', '')
    consensus     = cf.get('consensus', ms.get('consensus', ''))
    timing_status = cf.get('timing_status', ms.get('timing_status', ''))
    sl_pct        = float(params.get('sl_pct', 0) or 0)
    regime        = str(ms.get('regime', '') or '')
    score         = float(cf.get('score_final', 0) or 0)

    symbol        = ms.get('symbol', cf.get('symbol', ''))

    return SignalIntegrityGate.validate(
        direction, action, consensus, timing_status,
        sl_pct, regime, score, symbol=symbol
    )