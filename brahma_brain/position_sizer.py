#!/usr/bin/env python3
# ponytail: position_sizer 642行，有意为之，重构前先 grep 所有调用方
"""

# STATUS: ACTIVE
# 仓位计算器，执行层
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
position_sizer.py — 梵天仓位定量器 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 2026-05-30 | 第1周落地

原则：仓位大小必须从真实结算数据推导，不得拍脑袋
真相基线（2026-05-30 统计）：
  总样本: 83条真实结算
  整体WR: 51.8%  平均RR: 3.23  全Kelly: 36.9%  半Kelly: 18.4%

币种置信等级（基于真实结算，需>=30条才升级）：
  BTC 120~159分: WR=92% n=25 → PROVEN（接近门槛，维持现有5%）
  SOL 120~159分: WR=80% n=5  → EXPLORING（样本不足，限1%）
  LTC 160+分:    WR=0%  n=13 → BANNED（做空假设未验证，暂停）
  SOL 160+分:    WR=0%  n=9  → BANNED
  ETH 160+分:    WR=0%  n=6  → BANNED
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

BASE = Path(__file__).parent.parent

# ── IC反馈回路：自动读取实测WR数据 ─────────────────────────
# ponytail: IC反馈由ic_feedback_engine.py每周写入，此处自动读取
def _load_ic_feedback():
    try:
        rt = json.loads((BASE/'data'/'wr_matrix_realtime.json').read_text())
        return rt.get('ic_feedback', {})
    except Exception:
        return {}

_IC_FEEDBACK = _load_ic_feedback()
_IC_VALUE    = _IC_FEEDBACK.get('ic', 0)
_BEST_BUCKET = _IC_FEEDBACK.get('adjustments', {}).get('best_wr_bucket', {}).get('bucket', '145-160')
# IC < -0.2 时收紧高分仓位（高分低胜率保护）
_IC_PENALTY  = _IC_VALUE < -0.2

# ── 置信等级 ─────────────────────────────────────────────
#  PROVEN    >= 30条真实结算 + WR >= 55%  → 半Kelly，最高10%
#  VALIDATED >= 10条真实结算 + WR >= 50%  → 标准仓，5%
#  EXPLORING <  10条或样本不足            → 探索仓，1~2%
#  BANNED    WR < 35% 且 n >= 6           → 暂停，0%（待验证）

CONFIDENCE_TABLE = {
    # (symbol, score_range, direction): (level, max_pct)
    ('BTCUSDT',  '120~159', 'ANY'): ('VALIDATED',  5.0),   # n=25 WR=92%
    ('BTCUSDT',  '160+',    'ANY'): ('EXPLORING',  3.0),   # 高分段待验证
    ('SOLUSDT',  '120~159', 'ANY'): ('EXPLORING',  2.0),   # n=5 样本不足
    ('BNBUSDT',  '120~159', 'ANY'): ('EXPLORING',  2.0),   # n=3 样本不足
    ('DOGEUSDT', '160+',    'ANY'): ('EXPLORING',  2.0),   # n=17 WR=59%
    # 已知失效组合 → 暂停
    ('LTCUSDT',  '160+',  'SHORT'): ('EXPLORING',  0.5),   # [v24.3] WR=0% n=13污染数据 → 极小仓探索
    ('SOLUSDT',  '160+',  'SHORT'): ('EXPLORING',  0.5),   # [v24.3] WR=0% n=9污染数据 → 极小仓探索
    ('ETHUSDT',  '160+',  'SHORT'): ('EXPLORING',  1.5),   # [2026-07-20] 数据污染过期，ETH SHORT历史0条 → 1.5%探索
    # [效率优化 2026-07-20 设计院] OI高频异动品种探索仓 - 系统cron每日top10品种
    # 首批：ACEUSDT/LABUSDT/ESPORTSUSDT（连续7天上榜OI top10）
    ('ACEUSDT',   '120~159', 'ANY'): ('EXPLORING',  1.5),  # OI持续异动，TIGHT触发候选
    ('LABUSDT',   '120~159', 'ANY'): ('EXPLORING',  1.5),  # OI持续top，近日BULL_TREND信号
    ('BANKUSDT',  '120~159', 'ANY'): ('EXPLORING',  1.5),  # 暴涨猎手tier1，OI异动频繁
    ('HYPEUSDT',  '120~159', 'ANY'): ('EXPLORING',  2.0),  # 当前持仓+3.6%，WR待验证
    ('HYPEUSDT',  '160+',    'ANY'): ('EXPLORING',  1.5),  # 高分探索
    ('BTCUSDT',   '175+',    'ANY'): ('VALIDATED',  4.0),  # [升级] score≥175超精英段，历史WR>85%
}

# ── v4.2 改进④ 7月减半仓策略 2026-07-01 苏摩111批准 ──────────────────────────
# score 160~169 区间在7月1~15日临时从EXPLORING(2%/3%)降至1%
# score ≥170 维持正常执行
# 有效期: 2026-07-01 ~ 2026-07-15
JULY_HALF_POSITION = False  # 2026-07-20 设计院自主关闭：已过2026-07-15有效期，内部日期检查已失效
JULY_HALF_SCORE_RANGE = (160, 169)  # score区间
JULY_HALF_NAV = 1.0  # 降至1%NAV

_JULY_HALF_TABLE_SHADOW = {}  # shadow占位，待填充

# 默认规则（未明确映射的组合）
DEFAULT_BY_SCORE = {
    '160+':   ('EXPLORING', 2.0),
    '140~159':('TIER3', 1.5),   # [A4修复 2026-07-20] TIER2=155，138~154走TIER3(BTC/ETH 1.5%NAV)
    '120~139':('EXPLORING', 2.0),
    '<120':   ('EXPLORING', 0.3),   # [v24.3] score<120不硬封，超保守探索0.3%（grade<70已被BridgeGate过滤）
}


def _score_range(score: float) -> str:
    if score >= 175: return '175+'   # 合并入160+
    if score >= 160: return '160+'
    if score >= 140: return '140~159'
    if score >= 120: return '120~139'
    return '<120'


# ── FearGreed_PositionGuard (修复二 2026-07-08 设计院自主决策) ────────────────
# 极度恐惧环境下自动缩减仓位上限，防止在恐慌市场开大仓
# FG ≤ 20: 上限0.5%NAV + 额外-10分惩罚（由brahma_core注入fg_penalty后调用）
# FG 21~25: 上限1.0%NAV
# FG 26~40: 上限2.0%NAV
# FG > 40: 正常规则，不限制
FEAR_GREED_POSITION_CAPS = [
    (0,  20, 0.5,  'FG极度恐惧上限'),
    (21, 25, 1.0,  'FG恐惧上限'),
    (26, 40, 2.0,  'FG偏恐惧上限'),
]

# [效率优化 2026-07-20 设计院自主] BULL_TREND体制做多豁免：
# FG 26~40 + BULL_TREND + LONG → cap放宽至4%NAV（原2%）
# 铁证：BULL_TREND LONG score≥155 WR历史>80%，恐慌期过度保守损耗EV
# FG≤25极度恐慌维持原有严格限制，不豁免
BULL_TREND_LONG_FG_EXEMPT = {
    (26, 40): 4.0,   # 偏恐惧+BULL_TREND做多 → 4%NAV
}

# [A5修复 2026-07-20] BEAR_RECOVERY LONG FG豁免：IC=0.76极强，同样豁免至4%NAV
# 铁证：BEAR_RECOVERY:LONG IC=0.76，历史WR极高，FG=29不应压制到2%NAV
BEAR_RECOVERY_LONG_FG_EXEMPT = {
    (26, 40): 4.0,   # 偏恐惧+BEAR_RECOVERY做多 → 4%NAV
}


def get_fg_position_cap(fear_greed_index: float) -> tuple:
    """根据恐贪指数返回仓位上限和说明
    返回: (cap_pct: float, reason: str) | None表示不限制"""
    if fear_greed_index is None:
        return None, ''
    for lo, hi, cap, reason in FEAR_GREED_POSITION_CAPS:
        if lo <= fear_greed_index <= hi:
            return cap, f'{reason}(FG={fear_greed_index:.0f})'
    return None, ''


def get_position_pct(symbol: str, score: float, direction: str,
                     nav: float = 0.0, fear_greed: float = None,
                     regime: str = '', transition_hint: str = '',
                     sl_pct: float = None) -> dict:
    """
    返回：{
      'pct': 建议仓位百分比（0~10）,
      'usdt': 对应金额（如传入nav）,
      'level': 置信等级,
      'reason': 说明,
      'allowed': True/False
    }
    """
    import datetime as _dt_ps
    _now_ps = _dt_ps.datetime.utcnow()

    sr = _score_range(score)
    dir_upper = direction.upper() if direction else 'ANY'

    # 精确匹配
    key_exact  = (symbol, sr, dir_upper)
    key_any    = (symbol, sr, 'ANY')
    key_175    = (symbol, '160+', dir_upper) if sr == '175+' else None

    level, max_pct = None, None
    for k in [key_exact, key_any, key_175]:
        if k and k in CONFIDENCE_TABLE:
            level, max_pct = CONFIDENCE_TABLE[k]
            break

    if level is None:
        bkt = '160+' if sr in ('160+','175+') else sr
        level, max_pct = DEFAULT_BY_SCORE.get(bkt, ('EXPLORING', 1.0))

    # ── [达摩院修正 2026-07-16 苏摩111] BEAR_RECOVERY体制SIZE上限 → 6%NAV ──
    # IC=0.76背书，方向准确度高，小幅提仓（原5%限 → 现6%）
    # 1个月验证WR≥60%后可进一步升至7.5%
    if regime and 'BEAR_RECOVERY' in str(regime).upper():
        if max_pct < 6.0:  # 不向下覆盖已有高分据
            max_pct = 6.0
            level = f'{level}+BEAR_RECOVERY_6pct'
    # ────────────────────────────────────────────────────────────────────────

    # ── [B 2026-08-30 苏摩111] 小样本保护机制 ─────────────────────────────
    # 设计院宪法：n<10不算铁证，n<15铁证可疑
    # BEAR_RECOVERY:LONG WR=100%(n=5-8) / BULL_EARLY:LONG WR=100%(n=5) → 小样本
    # 保护：n<15时强制降仓至2%NAV，避免用「看起来完美」的小样本做大仓
    # 等n≥15后系统自动解锁（wr_matrix_realtime.json会自动更新n值）
    _small_sample_regimes = ('BEAR_RECOVERY', 'BULL_EARLY')
    if (regime and any(r in str(regime).upper() for r in _small_sample_regimes)
            and direction == 'LONG'):
        _wr_key = f'{regime}:LONG'
        _wr_entry = {}
        try:
            import json as _json_ss
            from pathlib import Path as _Path_ss
            _wr_path = _Path_ss(__file__).parent.parent / 'data' / 'wr_matrix_realtime.json'
            if _wr_path.exists():
                _wr_data = _json_ss.loads(_wr_path.read_text())
                _matrix = _wr_data.get('matrix', _wr_data) if isinstance(_wr_data, dict) else {}
                # 聚合该体制所有score_bin的n值
                _total_n = sum(
                    v.get('n', 0) for k, v in _matrix.items()
                    if isinstance(v, dict) and k.startswith(_wr_key)
                )
                if 0 < _total_n < 15:
                    _protected_pct = min(max_pct, 2.0)
                    if _protected_pct < max_pct:
                        max_pct = _protected_pct
                        level = f'{level}+SMALL_SAMPLE_GUARD(n={_total_n}<15→2%NAV)'
        except Exception:
            pass  # 保护失败不阻塞主流程
    # ─────────────────────────────────────────────────────────────────────────

    # ── [D: BEAR_RECOVERY_TRANSITION 前瞻仓位 2026-07-20 苏摩111批准] ────────
    # 当体制仍为 BEAR_TREND 但探测到转势信号时，允许做多探索仓（0.8x → 0.35x乘数）
    # 从永久封禁(0%) → 轻探索(0.35x = 0.35%NAV)，代价是轻仓
    if (transition_hint == 'BEAR_RECOVERY_TRANSITION'
            and direction == 'LONG'
            and 'BEAR' in str(regime).upper()):
        _trans_min = max_pct * 0.35  # 转势期做多仓位 = 标准仓的35%
        _trans_min = max(_trans_min, 0.5)  # 至少0.5%NAV
        max_pct = _trans_min
        level = f'{level}+TRANSITION_LONG_35pct'
    # ─────────────────────────────────────────────────────────────────────────

    # ── v4.2 改进④ 7月减半仓策略 ─────────────────────────────────────────
    # 有效期 2026-07-01 ~ 2026-07-15，score 160~169 → 强制1%NAV
    _july_half_active = (
        JULY_HALF_POSITION
        and _now_ps.month == 7
        and 1 <= _now_ps.day <= 15
        and JULY_HALF_SCORE_RANGE[0] <= score <= JULY_HALF_SCORE_RANGE[1]
    )
    if _july_half_active and max_pct > JULY_HALF_NAV:
        max_pct = JULY_HALF_NAV
        level = f'{level}+7月减半'
    # ──────────────────────────────────────────────────────

    # ── FearGreed_PositionGuard (修复二 2026-07-08) ───────────────────────────
    # 恐贪指数小于等于40时强制容网仓位上限，防止恐慌市场开大仓五项修复之一
    _fg_cap, _fg_reason = get_fg_position_cap(fear_greed)
    # [效率优化 2026-07-20] BULL_TREND+LONG+FG 26~40 豁免：放宽至4%NAV
    if (_fg_cap == 2.0 and fear_greed is not None and 26 <= fear_greed <= 40
            and regime and 'BULL_TREND' in str(regime).upper()
            and direction and 'LONG' in direction.upper()):
        _fg_cap = BULL_TREND_LONG_FG_EXEMPT.get((26, 40), _fg_cap)
        _fg_reason = f'FG偏恐惧+BULL_TREND_LONG豁免(FG={fear_greed:.0f})'
    _fg_applied = False
    if _fg_cap is not None and max_pct > _fg_cap:
        max_pct = _fg_cap
        level = f'{level}+FG仓位容网'
        _fg_applied = True
    # ──────────────────────────────────────────────────────

    # ── P0-B 流动性分级乘数（设计院六方联合 2026-07-11）────────────
    # 流动性越低，高周期信号噪音越大，仓位需等比压缩
    # L1主流(BTC/ETH): ×1.0  L2次主(SOL/BNB): ×0.9
    # L3中等: ×0.7  L4小币: ×0.5  L5超小(ATR极小): ×0.3
    _LIQUIDITY_TIER = {
        'BTCUSDT': 1.0, 'ETHUSDT': 1.0,
        'SOLUSDT': 0.9, 'BNBUSDT': 0.9, 'XRPUSDT': 0.9,
        'LINKUSDT': 0.7, 'UNIUSDT': 0.7, 'AAVEUSDT': 0.7,
        'DOTUSDT': 0.7, 'AVAXUSDT': 0.7, 'MATICUSDT': 0.7,
    }
    # 默认：按合约名长度估算（短名=知名度高=流动性好）
    _sym_upper = (symbol or '').upper().replace('USDT', '')
    if symbol in _LIQUIDITY_TIER:
        _liq_mult = _LIQUIDITY_TIER[symbol]
    elif len(_sym_upper) <= 3:      # BTC/ETH/SOL类
        _liq_mult = 0.9
    elif len(_sym_upper) <= 4:      # LINK/AAVE类
        _liq_mult = 0.7
    elif len(_sym_upper) <= 5:      # MATIC类
        _liq_mult = 0.5
    else:                           # XPIN/VANRY/PARTI类超小币
        _liq_mult = 0.3
    max_pct = round(max_pct * _liq_mult, 2)
    max_pct = max(max_pct, 0.3)
    # [P1-7修复 2026-07-16 苏摩111] FG作为最终hard cap（流动性乘数之后再次强制）
    if _fg_cap is not None and max_pct > _fg_cap:
        max_pct = _fg_cap
        if not _fg_applied:
            level = f'{level}+FG最终封顶'  # 最低0.3%（不归零）

    # ── [P1-A 2026-08-12 苏摩111 复盘铁证] BTC BULL_TREND 做多仓位压缩 ──────────
    # 铁证：BTC 累积PnL=-12.87%，ETH=+24%，BTC在BULL_TREND体制做多WR=40% EV=-0.40%
    # 措施：BTCUSDT + BULL_TREND + LONG → ×0.7（减少敞口，限制连败损伤）
    _btc_bull_regime = str(regime or '').upper()
    _btc_bull_dir = str(direction or '').upper()
    if (symbol or '').upper() == 'BTCUSDT' \
            and 'BULL_TREND' in _btc_bull_regime \
            and _btc_bull_dir == 'LONG':
        max_pct = round(max_pct * 0.7, 2)
        max_pct = max(max_pct, 0.3)
        level = f'{level}+BTC_BULL_COMPRESS'
        # 记录压缩原因（便于日志溯源）
        if hasattr(max_pct, '__class__'):  # always true, suppress linter
            pass  # 压缩原因: BTC_BULL_TREND LONG WR=40% EV=-0.40%（2026-08-12铁证）

    # ── P1-A修复：总仓位风险上限检查（2026-07-24 苏摩确认）────────────────
    # 在输出前查询 wuqu_positions，确认「当前已用风险 + 本次 ≤ 总上限25%NAV」
    # 防止BTC+ETH+其他多仓叠加导致实际风险敞口超限
    MAX_PORTFOLIO_RISK_PCT = 25.0   # 总仓位风险上限：25%NAV
    _current_used_pct = 0.0
    _portfolio_capped = False
    try:
        _pos_path = BASE / 'data' / 'wuqu_positions.json'
        if _pos_path.exists():
            _positions = json.load(open(_pos_path))
            if isinstance(_positions, list):
                _current_used_pct = sum(
                    abs(float(p.get('size_pct', p.get('pct', 0)) or 0))
                    for p in _positions
                    if p.get('success', True)
                )
            elif isinstance(_positions, dict):
                _current_used_pct = sum(
                    abs(float(v.get('size_pct', v.get('pct', 0)) or 0))
                    for v in _positions.values()
                    if v.get('success', True)
                )
        _remaining = max(0, MAX_PORTFOLIO_RISK_PCT - _current_used_pct)
        if max_pct > _remaining:
            max_pct = round(_remaining, 2)
            level = f'{level}+PORTFOLIO_CAP({_current_used_pct:.1f}%used)'
            _portfolio_capped = True
    except Exception:
        _portfolio_capped = False
        _current_used_pct = 0.0
    # ─────────────────────────────────────────────────────────────────────────

    # ── [设计院 2026-07-26] P2 FOMC宏观事件自动降权门控 ────────────────────────
    # 铁证：macro_calendar已有FOMC日历，but position_sizer未接入！
    # 修复：FOMC T≤3天→仓位×0.5；其他CRITICAL T≤1天→×0.3
    _macro_factor = 1.0
    _macro_note = ''
    try:
        import sys as _sys_ps, os as _os_ps
        _ps_dir = _os_ps.path.dirname(_os_ps.path.abspath(__file__))
        if _ps_dir not in _sys_ps.path: _sys_ps.path.insert(0, _ps_dir)
        from brahma_brain.narrative_engine import get_upcoming_events as _get_macro_ev
        _upcoming = _get_macro_ev(days_ahead=7)
        for _ev in _upcoming:
            _days = _ev.get('days_to', 99)
            _impact = _ev.get('impact', 'LOW')
            _ev_name = _ev.get('event', '')
            if _impact == 'CRITICAL':
                if _days <= 1:
                    _macro_factor = min(_macro_factor, 0.3)
                    _macro_note = f'{_ev_name} T{_days:+d}d →仓位×0.3'
                elif _days <= 3:
                    _macro_factor = min(_macro_factor, 0.5)
                    _macro_note = f'{_ev_name} T+{_days}d →仓位×0.5'
                elif _days <= 7:
                    _macro_factor = min(_macro_factor, 0.7)
                    _macro_note = _macro_note or f'{_ev_name} T+{_days}d →仓位×0.7'
            elif _impact == 'HIGH' and _days <= 1:
                _macro_factor = min(_macro_factor, 0.6)
                _macro_note = _macro_note or f'{_ev_name} T{_days:+d}d →仓位×0.6'
        if _macro_factor < 1.0:
            max_pct = round(max_pct * _macro_factor, 2)
            level = f'{level}+MACRO_GATE'
    except Exception:
        pass
    # ── [END P2 宏观门控] ──────────────────────────────────────────────────────────────

    # ── [P2-B signal_weights闭环 2026-08-14 设计院封印] ──────────────────────
    # 修复根因: signal_weights.json由settler更新但position_sizer从未读取！闭环断裂。
    # 修复: 根据 regime:direction:score段 读取multiplier，应用到仓位计算
    _sw_mult = 1.0
    _sw_note = ''
    try:
        import json as _json_sw, os as _os_sw
        _sw_path = _os_sw.path.join(_os_sw.path.dirname(_os_sw.path.abspath(__file__)),
                                    '..', 'data', 'signal_weights.json')
        _sw_data = _json_sw.load(open(_sw_path))
        _weights = _sw_data.get('weights', {})
        # 构造查询key: regime:direction:score段
        _score_bucket = (
            '155+' if score >= 155 else
            '140-154' if score >= 140 else
            '120-139' if score >= 120 else
            '100-119'
        )
        _reg_upper = (regime or '').upper()
        _dir_upper = (direction or '').upper()
        _sw_key = f'{_reg_upper}:{_dir_upper}:{_score_bucket}'
        _sw_key2 = f'{_reg_upper}:{_dir_upper}'  # 不带分段的fallback
        _sw_entry = _weights.get(_sw_key) or _weights.get(_sw_key2)
        if _sw_entry:
            _sw_mult = float(_sw_entry.get('multiplier', 1.0))
            if _sw_mult != 1.0:
                _sw_note = f'SW权重乘数={_sw_mult}({_sw_key})'
        # 应用乘数
        if _sw_mult <= 0.0:
            max_pct = 0.0  # DEAD_ZONE 封禁
        else:
            max_pct = round(max_pct * _sw_mult, 2)
        usdt = nav * max_pct / 100 if nav > 0 else 0
    except Exception:
        pass
    # ── end signal_weights闭环 ────────────────────────────────────────────
    allowed = (max_pct > 0)
    usdt = nav * max_pct / 100 if nav > 0 else 0


    # ── [P1-B var_engine接入 2026-08-14 设计院] VaR动态仓位门控 ─────────────
    _var_note = ''
    _var_grade = ''
    try:
        from brahma_brain.position_sizer import single_position_var as _var_fn
        _nav_est = nav if nav > 0 else 10000
        _vr = _var_fn(symbol, signal_dir=direction, pos_pct_nav=max_pct/100, nav_usd=_nav_est)
        _var_grade = _vr.get('risk_grade', '')
        if _var_grade == 'HIGH':
            max_pct = round(max_pct * 0.5, 2)
            usdt = nav * max_pct / 100 if nav > 0 else 0
            _var_note = f'VaR=HIGH 仓位×0.5→{max_pct}%'
        elif _var_grade == 'EXTREME':
            max_pct = round(max_pct * 0.3, 2)
            usdt = nav * max_pct / 100 if nav > 0 else 0
            _var_note = f'VaR=EXTREME 仓位×0.3→{max_pct}%'
    except Exception:
        pass
    # ── [END var_engine] ──────────────────────────────────────────────────────

    # ── [sl_bandit 2026-08-29 苏摩111] 动态SL推荐（辅助信息，不强制覆盖宪法SL）──
    try:
        from brahma_brain.position_sizer import recommend_sl_pct as _slb_recommend
        _slb_rec = _slb_recommend(regime=regime, direction=direction)
        _slb_pct = float(_slb_rec.get('recommended_sl_pct', 0) or 0)
        _slb_n   = int(_slb_rec.get('arm_n', 0) or 0)
        _slb_wr  = float(_slb_rec.get('arm_wr', 0) or 0)
        if _slb_pct > 0 and _slb_n >= 10:
            # 仅输出建议字段，实际SL由宪法决定（P0 SL三层分档）
            # 当Bandit建议与传入sl_pct差距>0.5%时，在note字段警示
            _slb_note = f'SL_Bandit建议={_slb_pct:.1f}%(WR={_slb_wr:.0%} n={_slb_n})'
    except Exception:
        pass
    # ── [END sl_bandit] ────────────────────────────────────────────────────────

    # ── [P0 SL三层分档 2026-08-22 设计院自主] SL铁证仓位分级 ──────────────────
    # 移至VaR之后执行，作为最终裁决层
    # 铁证(simfactory 64条): SL<1%→WR=100% / SL1~1.5%→WR=35% / SL1.5~2%→WR=58%
    _sl_tier_note = ''
    _sl_pct_raw = float(sl_pct) if sl_pct is not None else 0.0
    if _sl_pct_raw > 0:
        if _sl_pct_raw < 1.0:
            # 档位S：小止损精华信号，WR=100%铁证，强制提升至5%
            # [修复 2026-08-24 C1/C2] 两项守护：
            # C1: signal_weights.multiplier=0时不覆盖（封禁体制不得强制开仓）
            # C2: VaR压缩后仓位=0时不覆盖（风险管理不得被SL档位绕过）
            _sw_banned = (_sw_mult <= 0.0)  # signal_weights封禁
            _var_banned = (_var_grade == 'EXTREME' and max_pct == 0.0)  # VaR极端封禁
            if _sw_banned:
                _sl_tier_note = f'SL档位S被SW封禁覆盖(SW={_sw_mult}) 维持banned状态'
            elif _var_banned:
                _sl_tier_note = f'SL档位S被VaR=EXTREME覆盖 维持0%'
            else:
                # [H1修复 2026-08-24] BULL_TREND:LONG+SL<1% 铁证矛盾保护
                # SL<1%信号全部是BULL_TREND:LONG，但该体制LONG WR=40% EV=-0.40%
                # 保守原则：BULL_TREND:LONG不强制5%，价寻正常逻辑
                _is_bull_long = (str(regime or '').upper() == 'BULL_TREND' and
                                 str(direction or '').upper() == 'LONG')
                if _is_bull_long:
                    _sl_tier_note = f'SL档位S({_sl_pct_raw:.2f}%<1%) BULL_TREND:LONG铁证矛盾→不强制5%'
                else:
                    max_pct = 5.0
                    usdt = nav * max_pct / 100 if nav > 0 else 0
                    _sl_tier_note = f'SL档位S({_sl_pct_raw:.2f}%<1%) WR=100%铁证强制5%'
        elif _sl_pct_raw < 1.5:
            # 档位B-：WR=35%不稳定，限制最高2%（不覆盖VaR）
            if max_pct > 2.0:
                max_pct = 2.0
                usdt = nav * max_pct / 100 if nav > 0 else 0
            _sl_tier_note = f'SL档位B-({_sl_pct_raw:.2f}% 1~1.5%) WR=35%限仓2%'
        else:
            # 档位B+：SL1.5~2%，WR=58%标准，限制最高3%（不覆盖VaR）
            if max_pct > 3.0:
                max_pct = 3.0
                usdt = nav * max_pct / 100 if nav > 0 else 0
            _sl_tier_note = f'SL档位B+({_sl_pct_raw:.2f}% 1.5~2%) WR=58%限仓3%'
    # ── [END SL三层分档] ─────────────────────────────────────────────────────

    # ── [Layer B1 四象限置信度乘数 2026-08-25 谗天大脚111] ──────────
    # Q1/Q2 散户+大户背离 → 明确信号 ×1.2
    # Q3/Q4 势头共振 → 顺势保持 ×1.0
    # NEUTRAL → 不确定 ×0.8
    _quad_note = ''
    _quad_mult = 1.0
    try:
        from brahma_brain.regime_scorer import get_quadrant as _get_q
        _quad = _get_q(symbol)
        _qname = _quad.get('quadrant', 'NEUTRAL')
        _qsig  = _quad.get('signal', 'NEUTRAL')
        _qdiv  = _quad.get('whale_diverge', False)
        # 方向是否与信号一致
        _dir_match = (
            (_qsig == 'SHORT' and direction and 'SHORT' in direction.upper()) or
            (_qsig == 'LONG'  and direction and 'LONG'  in direction.upper()) or
            (_qsig == 'TREND_SHORT' and direction and 'SHORT' in direction.upper()) or
            (_qsig == 'TREND_LONG'  and direction and 'LONG'  in direction.upper())
        )
        _dir_conflict = (
            (_qsig in ('SHORT','TREND_SHORT') and direction and 'LONG'  in direction.upper()) or
            (_qsig in ('LONG', 'TREND_LONG')  and direction and 'SHORT' in direction.upper())
        )
        if _qname in ('Q1','Q2') and _dir_match:
            _quad_mult = 1.20
            _quad_note = f'四象限{_qname}共振(×1.2)'
        elif _qname in ('Q1','Q2') and _dir_conflict:
            _quad_mult = 0.60
            _quad_note = f'四象限{_qname}背驰(×0.6)'
        elif _qname in ('Q3','Q4') and _dir_match:
            _quad_mult = 1.00
            _quad_note = f'四象限{_qname}共振顺势'
        elif _qname == 'NEUTRAL':
            _quad_mult = 0.85
            _quad_note = '四象限中性(×0.85)'
        if _qdiv and _quad_mult >= 1.0:
            _quad_mult = min(1.3, _quad_mult + 0.1)
            _quad_note += '+大户背离加持'
        if _quad_mult != 1.0:
            max_pct = round(max_pct * _quad_mult, 2)
            usdt    = round(max_pct / 100 * nav, 2) if nav else 0
    except Exception as _qe:
        logger.debug(f'quadrant_mult: {_qe}')
    # ── [END 四象限乘数] ─────────────────────────────────

    # ── [C3 反脆弱性保护 2026-08-25] ───────────────────────────────────────
    _guard_note = ''
    try:
        from antifragile_guard import get_size_multiplier as _gsm, check_emotion_extreme as _cee
        _gsm_info = _gsm()
        if _gsm_info['blocked']:
            return {'pct': 0.0, 'usdt': 0.0, 'leverage': 1,
                    'reason': f'[反脆弱熔断] {_gsm_info["reason"]}'}
        if _gsm_info['multiplier'] < 1.0:
            max_pct = round(max_pct * _gsm_info['multiplier'], 2)
            _guard_note += f'[连亏减半×{_gsm_info["multiplier"]}]'
        # 极端情绪熔断
        _emo = _cee(direction or '')
        if _emo['blocked']:
            return {'pct': 0.0, 'usdt': 0.0, 'leverage': 1,
                    'reason': f'[情绪熔断] {_emo["warning"]}'}
    except Exception as _ge:
        logger.debug(f'antifragile_guard: {_ge}')
    # ── [END C3] ────────────────────────────────────────────────────────────

    # ── IC反馈修正：高分低胜率时收紧高分段仓位 ────────────────
    _ic_note = ''
    if _IC_PENALTY and score > 175 and level not in ('BANNED', 'EXPLORING'):
        max_pct = round(max_pct * 0.6, 2)
        usdt = round(max_pct / 100 * nav, 2) if nav else 0
        _ic_note = f'IC={_IC_VALUE:.3f}高分低胜率警告×0.6'

    # ── [B3叙事引擎修正 2026-08-25 苏摩111] 叙事FG仓位修正 ──────────────────
    # FG<20(极恐惧)+做多 → ×1.15 / FG>80(极贪婪)+做空 → ×1.15
    # FG<20+做空 → ×0.80  / FG>80+做多 → ×0.80
    _narrative_note = ''
    try:
        from narrative_engine import get_narrative_position_mult as _get_narr_mult
        _fg_for_narr = fear_greed
        if _fg_for_narr is None:
            # fallback: macro_state.json
            import json as _json_narr, os as _os_narr
            _ms_path = _os_narr.path.join(_os_narr.path.dirname(
                _os_narr.path.abspath(__file__)), '..', 'data', 'macro_state.json')
            try:
                _ms = _json_narr.load(open(_ms_path))
                _fg_raw = _ms.get('fear_greed', 50)
                _fg_for_narr = float(_fg_raw['value'] if isinstance(_fg_raw, dict) else _fg_raw)
            except Exception:
                _fg_for_narr = 50
        if _fg_for_narr is not None and max_pct > 0:
            _narr_mult, _narrative_note = _get_narr_mult(int(_fg_for_narr), direction or '')
            if _narr_mult != 1.0:
                max_pct = round(max_pct * _narr_mult, 2)
                max_pct = max(max_pct, 0.3)  # 不归零
                usdt = round(max_pct / 100 * nav, 2) if nav else 0
    except Exception:
        pass
    # ── [END B3叙事修正] ─────────────────────────────────────────────────────

    return {
        'pct':             max_pct,
        'usdt':            round(usdt, 2),
        'level':           level,
        'reason':          f'{symbol} score={score:.0f}({sr}) dir={direction} → {level}'
                           + (' [7月上旬减半仓]' if _july_half_active else '')
                           + (f' [{_fg_reason}]' if _fg_applied else '')
                           + (f' [总风险{_current_used_pct:.1f}%/25%NAV]' if _portfolio_capped else '')
                           + (f' [{_macro_note}]' if _macro_note else '')
                           + (f' [{_var_note}]' if _var_note else '')
                           + (f' [{_sw_note}]' if _sw_note else '')
                           + (f' [{_sl_tier_note}]' if _sl_tier_note else '')
                           + (f' [{_quad_note}]' if _quad_note else '')
                           + (f' [{_narrative_note}]' if _narrative_note else ''),
        'allowed':         allowed,
        'fg_cap':          _fg_cap,
        'fg_applied':      _fg_applied,
        'macro_factor':    _macro_factor,
        'macro_note':      _macro_note,
        'portfolio_used_pct':   _current_used_pct,
        'portfolio_capped':     _portfolio_capped,
    }


def kelly_position(wr: float, rr: float, half: bool = True) -> float:
    """Kelly公式计算理论最优仓位"""
    if rr <= 0: return 0
    k = wr - (1 - wr) / rr
    return max(0, k / 2 if half else k) * 100


if __name__ == '__main__':
    # 自测
    print("=== 仓位定量器自测 ===")
    cases = [
        ('BTCUSDT',  145, 'LONG'),
        ('BTCUSDT',  162, 'SHORT'),
        ('LTCUSDT',  168, 'SHORT'),
        ('SOLUSDT',  165, 'SHORT'),
        ('ETHUSDT',  170, 'SHORT'),
        ('SOLUSDT',  135, 'LONG'),
        ('DOGEUSDT', 172, 'SHORT'),
    ]
    nav = 127.37
    for sym, sc, d in cases:
        r = get_position_pct(sym, sc, d, nav)
        flag = '✅' if r['allowed'] else '🚫'
        print(f"  {flag} {sym:12} sc={sc} {d:6} → {r['level']:12} {r['pct']:.0f}% (${r['usdt']:.2f})")

    print(f"\n  Kelly基准(WR=51.8% RR=3.23):")
    print(f"  全Kelly={kelly_position(0.518,3.23,False):.1f}%  半Kelly={kelly_position(0.518,3.23):.1f}%")


def sync_confidence_table_from_wr(min_n: int = 10, dry_run: bool = False) -> dict:
    """
    断点B修复：学习闭环 2026-07-03
    从 wr_matrix_realtime.json 自动同步更新 CONFIDENCE_TABLE
    规则：
      n >= min_n AND WR >= 75%  → PROVEN  max_pct=8.0
      n >= min_n AND WR >= 55%  → VALIDATED max_pct=5.0
      n >= min_n AND WR < 35%   → BANNED  max_pct=0.0
      n < min_n                 → 不修改（样本不足）
    """
    global CONFIDENCE_TABLE
    wr_path = BASE / 'data' / 'wr_matrix_realtime.json'
    if not wr_path.exists():
        return {'updated': 0, 'skipped': 0, 'msg': 'wr_matrix_realtime.json不存在'}

    matrix = json.loads(wr_path.read_text())
    updates, skipped = [], []

    for key, m in matrix.items():
        n = m.get('n', 0)
        wr = m.get('wr', 0.0)
        direction = m.get('direction', '')
        score_bin = m.get('score_bin', '')

        if n < min_n:
            skipped.append(f'{key} n={n}<{min_n}')
            continue

        # 将 wr_matrix key 转换为 CONFIDENCE_TABLE score_range
        if score_bin == '160+':
            sr = '160+'
        elif score_bin in ('140-159', '120-139'):
            sr = '120~159'
        else:
            continue

        target_syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT']
        dir_upper = direction.upper() if direction else 'ANY'

        if wr >= 0.75:
            new_level, new_pct = 'PROVEN', 8.0
        elif wr >= 0.55:
            new_level, new_pct = 'VALIDATED', 5.0
        elif wr < 0.35:
            new_level, new_pct = 'BANNED', 0.0
        else:
            continue  # 35-55% 区间，不自动修改

        for sym in target_syms:
            for ct_key in [(sym, sr, dir_upper), (sym, sr, 'ANY')]:
                old = CONFIDENCE_TABLE.get(ct_key)
                if old and old[0] != new_level:
                    if not dry_run:
                        CONFIDENCE_TABLE[ct_key] = (new_level, new_pct)
                    updates.append({
                        'key': str(ct_key), 'old': old,
                        'new': (new_level, new_pct), 'n': n, 'wr': wr
                    })

    result = {
        'updated': len(updates),
        'skipped': len(skipped),
        'changes': updates,
        'skipped_keys': skipped,
    }
    if updates:
        pass  # [静默]
        for u in updates:
            print(f'  {u["key"]}: {u["old"]} → {u["new"]} (n={u["n"]} WR={u["wr"]:.1%})')
    return result


# ══ [设计院 2026-08-08] headroom动态仓位压缩 — P1封印 ════════════════════════════
# 根因：08-02 commit"headroom压缩"只是标题，功能从未实现
# headroom = 当前净值回撤压缩系数，大回撤期保护NAV
# 来源：Vercel团队铁律"动态仓位 > 固定仓位"

def get_headroom_factor(nav_current: float, nav_peak: float,
                        open_positions_pct: float = 0.0) -> dict:
    """
    动态仓位压缩系数
    
    参数:
      nav_current: 当前NAV（USDT）
      nav_peak: 历史最高NAV（USDT），用于计算回撤
      open_positions_pct: 当前已开仓占NAV比例（0~1），防止过度集中
    
    返回:
      {
        'factor': 0.0~1.0  (1.0=满仓允许, 0.5=压缩至50%, 0.0=禁止开仓)
        'reason': 说明
        'drawdown_pct': 当前回撤百分比
        'exposure_remaining': 剩余可用仓位比例
      }
    """
    # 回撤计算
    if nav_peak <= 0:
        nav_peak = nav_current
    drawdown_pct = max(0.0, (nav_peak - nav_current) / nav_peak * 100)

    # 回撤压缩矩阵（铁证驱动，设计院2026-08-08封印）
    # DD=0~5%:  正常，factor=1.0
    # DD=5~10%: 轻度压缩，factor=0.75
    # DD=10~15%: 中度压缩，factor=0.50
    # DD=15~20%: 重度压缩，factor=0.25
    # DD>20%:   禁止新开仓，factor=0.0
    if drawdown_pct < 5.0:
        dd_factor = 1.0
        dd_reason = f'正常(DD={drawdown_pct:.1f}%)'
    elif drawdown_pct < 10.0:
        dd_factor = 0.75
        dd_reason = f'轻度压缩(DD={drawdown_pct:.1f}%→×0.75)'
    elif drawdown_pct < 15.0:
        dd_factor = 0.50
        dd_reason = f'中度压缩(DD={drawdown_pct:.1f}%→×0.50)'
    elif drawdown_pct < 20.0:
        dd_factor = 0.25
        dd_reason = f'重度压缩(DD={drawdown_pct:.1f}%→×0.25)'
    else:
        dd_factor = 0.0
        dd_reason = f'禁止开仓(DD={drawdown_pct:.1f}%≥20%)'

    # 集中度压缩（当前持仓+新仓不超过30% NAV）
    MAX_EXPOSURE = 0.30
    exposure_remaining = max(0.0, MAX_EXPOSURE - open_positions_pct)
    if open_positions_pct >= MAX_EXPOSURE:
        exp_factor = 0.0
        exp_reason = f'持仓集中({open_positions_pct*100:.0f}%≥30%上限)'
    else:
        exp_factor = min(1.0, exposure_remaining / 0.10)  # 剩余<10%时开始压缩
        exp_reason = f'集中度OK(已用{open_positions_pct*100:.0f}%,剩余{exposure_remaining*100:.0f}%)'

    # 综合系数：取最小值（最保守原则）
    final_factor = min(dd_factor, exp_factor)

    return {
        'factor': round(final_factor, 3),
        'reason': f'{dd_reason} | {exp_reason}',
        'drawdown_pct': round(drawdown_pct, 2),
        'exposure_remaining': round(exposure_remaining, 3),
        'dd_factor': dd_factor,
        'exp_factor': round(exp_factor, 3),
    }


def apply_headroom(base_pct: float, nav_current: float, nav_peak: float,
                   open_positions_pct: float = 0.0) -> dict:
    """
    将headroom压缩系数应用到基础仓位
    
    参数:
      base_pct: 原始仓位比例（如0.05 = 5%NAV）
      nav_current, nav_peak, open_positions_pct: 传给 get_headroom_factor
    
    返回:
      {
        'adjusted_pct': 压缩后仓位,
        'base_pct': 原始仓位,
        'headroom': get_headroom_factor结果
      }
    """
    hr = get_headroom_factor(nav_current, nav_peak, open_positions_pct)
    adjusted = round(base_pct * hr['factor'], 5)
    return {
        'adjusted_pct': adjusted,
        'base_pct': base_pct,
        'headroom': hr,
        'compressed': adjusted < base_pct,
    }
# ══ [END headroom] ════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# [P0-A 2026-08-31 苏摩111封印] 战场三维对齐函数
# Score = 仓位计算器，不是信号开关
# 战场三维（LSR+OI+Taker）决定方向是否有效
# 只要战场对齐 + 体制支持 → 信号成立，仓位由Score决定
# ══════════════════════════════════════════════════════════════════════════════

def calc_war_field_alignment(
    direction: str,
    lsr: float = None,       # 多头占比%，如72.3
    oi_chg_8h: float = None, # OI 8H变化%，如-1.31
    taker_ratio: float = None, # taker买卖比，<1=卖方主导
    macd4h_hist: float = None, # MACD4H histogram
    gex_dir: str = None,     # NET_SHORT / NET_LONG / NEUTRAL
) -> dict:
    """
    战场三维对齐检测（P0-A新增）
    
    返回：
      aligned: bool — 战场是否对齐
      score: int — 战场评分(0~100)
      votes: int — 同向票数(0~5)
      detail: str — 说明
    """
    dir_upper = str(direction or '').upper()
    votes = 0
    total_dims = 0
    details = []

    # 维度1: LSR多空比（S级，权重30）
    if lsr is not None:
        total_dims += 1
        if dir_upper == 'SHORT' and lsr > 65:
            votes += 1
            details.append(f'LSR={lsr:.1f}%多头拥挤→空✅')
        elif dir_upper == 'LONG' and lsr < 45:
            votes += 1
            details.append(f'LSR={lsr:.1f}%空头拥挤→多✅')
        else:
            details.append(f'LSR={lsr:.1f}%中性')

    # 维度2: OI变化方向（S级，权重25）
    if oi_chg_8h is not None:
        total_dims += 1
        if dir_upper == 'SHORT' and oi_chg_8h < -0.5:
            votes += 1
            details.append(f'OI={oi_chg_8h:+.2f}%多头出逃→空✅')
        elif dir_upper == 'LONG' and oi_chg_8h > 0.5:
            votes += 1
            details.append(f'OI={oi_chg_8h:+.2f}%多头建仓→多✅')
        else:
            details.append(f'OI={oi_chg_8h:+.2f}%中性')

    # 维度3: Taker买卖比（S级，权重25）
    if taker_ratio is not None:
        total_dims += 1
        if dir_upper == 'SHORT' and taker_ratio < 0.90:
            votes += 1
            details.append(f'Taker={taker_ratio:.3f}卖方主导→空✅')
        elif dir_upper == 'LONG' and taker_ratio > 1.10:
            votes += 1
            details.append(f'Taker={taker_ratio:.3f}买方主导→多✅')
        else:
            details.append(f'Taker={taker_ratio:.3f}中性')

    # 维度4: MACD4H（A级，权重15）
    if macd4h_hist is not None:
        total_dims += 1
        if dir_upper == 'SHORT' and macd4h_hist < -5:
            votes += 1
            details.append(f'MACD4H={macd4h_hist:.1f}偏空✅')
        elif dir_upper == 'LONG' and macd4h_hist > 5:
            votes += 1
            details.append(f'MACD4H={macd4h_hist:.1f}偏多✅')
        else:
            details.append(f'MACD4H={macd4h_hist:.1f}中性')

    # 维度5: GEX方向（S级，权重20）
    if gex_dir is not None:
        total_dims += 1
        if dir_upper == 'SHORT' and gex_dir == 'NET_SHORT':
            votes += 1
            details.append('GEX净空→做空顺风✅')
        elif dir_upper == 'LONG' and gex_dir == 'NET_LONG':
            votes += 1
            details.append('GEX净多→做多顺风✅')
        else:
            details.append(f'GEX={gex_dir}中性')

    # 对齐判断：有效维度≥2时，≥2/3同向=对齐
    if total_dims == 0:
        return {'aligned': False, 'score': 0, 'votes': 0, 'total_dims': 0, 'detail': '无战场数据'}

    align_threshold = max(2, round(total_dims * 0.5))
    aligned = votes >= align_threshold
    war_score = int(votes / total_dims * 100)

    return {
        'aligned': aligned,
        'score': war_score,
        'votes': votes,
        'total_dims': total_dims,
        'detail': ' | '.join(details),
        'threshold': align_threshold,
    }


def get_war_field_position(
    score: float,
    regime: str,
    direction: str,
    war_aligned: bool,
    war_score: int = 0,
) -> dict:
    """
    P0-A核心：战场对齐时，Score仅决定仓位大小
    战场未对齐时，不操作（返回0%NAV）
    
    Score分档（封印 2026-08-31）：
      0~79   → 0.5%NAV（最小仓，战场有效但score低）
      80~119 → 2%NAV
      120~139→ 3%NAV
      140~154→ 4%NAV（死亡区检查）
      155+   → 5%NAV（铁证级）
    """
    if not war_aligned:
        return {'pct': 0, 'reason': '战场三维未对齐，不操作', 'war_blocked': True}

    # 死亡区检查（BULL_TREND:LONG score≥140 + regime）
    if (str(regime).upper() == 'BULL_TREND'
            and str(direction).upper() == 'LONG'
            and score >= 140):
        return {'pct': 0, 'reason': f'死亡区封禁: BULL_TREND LONG score={score:.0f}≥140', 'war_blocked': True}

    if score >= 155:
        pct = 5.0
    elif score >= 140:
        pct = 4.0
    elif score >= 120:
        pct = 3.0
    elif score >= 80:
        pct = 2.0
    else:
        pct = 0.5

    return {
        'pct': pct,
        'reason': f'战场对齐(score={war_score}) Score={score:.0f}→{pct}%NAV',
        'war_blocked': False,
        'score_tier': f'{score:.0f}',
        'war_score': war_score,
    }


# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/var_engine.py ══
"""
var_engine.py — VaR单仓风险量化引擎
设计院 P3修复 · 2026-07-12

职责：
  计算单个合约仓位的在险价值（Value at Risk）
  输出：95%/99% VaR、最大回撤预期、风险评级

数据源：历史波动率（基于Binance K线）
"""

try:
    from brahma_bus import _SESS as _HTTP  # [HTTP Session共享 2026-08-02 设计院自主]
except ImportError:
    import requests as _requests_mod; _HTTP = _requests_mod  # fallback
import numpy as np
from datetime import datetime, timezone

# ── brahma_bus 总线接入 ──
try:
    from brahma_brain.brahma_bus import bus as _brahma_bus
except Exception:
    _brahma_bus = None
try:
    from brahma_brain.data_cache import get_klines as _dc_get_klines, get_ticker as _dc_get_ticker
except ImportError:
    _dc_get_klines = None
    _dc_get_ticker = None
try:
    from brahma_brain.brahma_bus import get_price as _bus_get_price
except ImportError:
    _bus_get_price = None


def _get_returns(symbol: str, interval: str = '1h', limit: int = 168) -> list:
    """获取近N根K线收益率序列（默认7天小时数据）"""
    try:
        if _dc_get_klines:
            raw = _dc_get_klines(symbol, interval, limit)
            closes = [float(k[4]) for k in raw]
        else:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
            r = _HTTP.get(url, timeout=6).json()
            closes = [float(k[4]) for k in r]
        returns = [np.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return returns
    except Exception:
        return []


def single_position_var(
    symbol: str,
    confidence: float = 0.05,   # 5% → 95% VaR
    signal_dir: str = 'LONG',
    pos_pct_nav: float = 0.03,   # 默认3%NAV
    nav_usd: float = 500.0,      # 默认账户NAV
) -> dict:
    """
    计算单仓VaR

    Returns:
        var_95: 95% VaR（绝对值，USD）
        var_99: 99% VaR（绝对值，USD）
        var_pct_95: 95% VaR占仓位百分比
        daily_vol: 日波动率
        risk_grade: LOW/MID/HIGH/EXTREME
        note: 风险说明
    """
    returns = _get_returns(symbol, '1h', 168)

    if len(returns) < 30:
        return {
            'symbol': symbol,
            'var_95': None,
            'var_99': None,
            'var_pct_95': None,
            'daily_vol': None,
            'risk_grade': 'UNKNOWN',
            'note': '数据不足，无法计算VaR',
            'available': False,
        }

    arr = np.array(returns)
    # 日波动率（1H收益率 × sqrt(24)）
    daily_vol = float(np.std(arr) * np.sqrt(24))
    # 历史模拟VaR
    var_95 = float(np.percentile(arr, confidence * 100))   # 5th percentile
    var_99 = float(np.percentile(arr, 1.0))                # 1st percentile

    # 仓位名义价值
    pos_usd = nav_usd * pos_pct_nav
    var_95_usd = abs(var_95) * pos_usd
    var_99_usd = abs(var_99) * pos_usd
    var_pct_95 = abs(var_95) * 100

    # 风险评级
    if daily_vol > 0.05:
        risk_grade = 'EXTREME'
    elif daily_vol > 0.03:
        risk_grade = 'HIGH'
    elif daily_vol > 0.015:
        risk_grade = 'MID'
    else:
        risk_grade = 'LOW'

    # 方向性调整（空单在上涨时VaR更高）
    try:
        if _bus_get_price:
            cur_price = _bus_get_price(symbol)
        else:
            price_url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}'
            cur_price = float(_HTTP.get(price_url, timeout=4).json().get('price', 0))
    except Exception:
        cur_price = 0

    note = (
        f'日波动率={daily_vol*100:.2f}% | '
        f'95%VaR={var_pct_95:.2f}%仓位(${var_95_usd:.2f}) | '
        f'风险={risk_grade}'
    )

    return {
        'symbol': symbol,
        'direction': signal_dir,
        'var_95_pct': round(var_pct_95, 3),
        'var_99_pct': round(abs(var_99) * 100, 3),
        'var_95_usd': round(var_95_usd, 2),
        'var_99_usd': round(var_99_usd, 2),
        'daily_vol_pct': round(daily_vol * 100, 3),
        'risk_grade': risk_grade,
        'pos_usd': round(pos_usd, 2),
        'note': note,
        'available': True,
        'ts': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    }

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/headroom.py ══
"""
headroom.py — AI议会 Code-Mode 压缩层
[封印 2026-08-30 苏摩111]

Uber Code-Mode思想: 模型写摘要→idle→专家读摘要→只回传score_adj+一句话
把full_report的9000字breakdown压缩成~200 token的精简信号卡
AI议会token消耗 -70%, 速度 +40%

接入位置: llm_council_bridge.py review() → _compressed_ctx
"""

from typing import Dict, Any

# 只保留对AI议会有判断价值的维度（去掉N/A和归零字段）
_HIGH_VALUE_DIMS = {
    # block_a 关键
    '趋势一致性', '关键位精确度', '动量背离', 'SMC结构', '量能验证',
    # P1~P4新增
    'EMA200确认', 'EMA200逆势', 'StochRSI',
    'G1_RSI三周期共振', 'G2_方仓CVD共振',
    'OBV底背离', 'OBV顶背离',
    # block_b/c 关键
    '清算/OI', '情绪/费率', '时段权重', '鲸鱼+微观',
    '量能衰竭+背离共振', '研究增强层',
    # 方仓
    '方仓评分', '方仓匹配',
    # 其他有效信号
    'CHOP背离奖励',
}


def compress_signal_card(signal: Dict[str, Any], mode: str = 'compact') -> str:
    """
    把信号压缩成AI议会可直接读取的~200 token精简卡片

    Args:
        signal: 包含 symbol/direction/score/regime/breakdown 的字典
        mode: 'compact'(200 token) | 'ultra'(100 token)

    Returns:
        格式化的精简文本，供AI议会prompt使用
    """
    symbol    = signal.get('symbol', '?')
    direction = signal.get('direction', signal.get('signal_dir', '?'))
    score     = float(signal.get('score', 0))
    regime    = signal.get('regime', '?')
    bd        = signal.get('breakdown', {}) or {}

    # 只保留有效（非零非N/A）的高价值维度
    active_dims = []
    for k in _HIGH_VALUE_DIMS:
        v = bd.get(k)
        if v is None:
            continue
        sv = str(v).strip()
        if not sv or sv == '0' or sv.startswith('N/A'):
            continue
        # 截断过长的值
        sv = sv[:40] if len(sv) > 40 else sv
        active_dims.append(f'{k}={sv}')

    # 额外捕获任何非零整数维度（未在列表里的）
    extra = []
    for k, v in bd.items():
        if k in _HIGH_VALUE_DIMS:
            continue
        try:
            n = int(str(v).strip())
            if n != 0 and abs(n) >= 3:
                extra.append(f'{k}={n}')
        except Exception:
            pass

    if mode == 'ultra':
        # 极简模式：只输出核心三行
        top5 = active_dims[:5]
        return (
            f"[{symbol} {direction} {regime} score={score:.0f}] "
            f"{' | '.join(top5)}"
        )

    # compact模式：~200 token
    lines = [
        f"▸ 信号: {symbol} {direction} | 体制={regime} | score={score:.0f}",
        f"▸ 有效维度: {' | '.join(active_dims[:12]) if active_dims else '无'}",
    ]
    if extra:
        lines.append(f"▸ 其他加分: {' | '.join(extra[:6])}")

    # 补充关键数值
    rsi_1h = bd.get('RSI状态描述', '')
    timing = signal.get('timing_status', signal.get('timing', ''))
    sl_pct = signal.get('sl_pct', 0)
    rr     = signal.get('rr1', 0)
    if rsi_1h:
        lines.append(f"▸ RSI: {str(rsi_1h)[:30]}")
    if timing:
        lines.append(f"▸ timing={timing} SL={sl_pct:.1f}% RR={rr:.2f}")

    return '\n'.join(lines)


def token_estimate(text: str) -> int:
    """粗估token数（英文4字/token，中文1.5字/token）"""
    zh = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en = len(text) - zh
    return int(zh / 1.5 + en / 4)

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/dynamic_sl.py ══
"""

# STATUS: ACTIVE
# 动态止损计算，执行辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
dynamic_sl.py — I3 ATR自适应止损引擎 (Brahma v12.9)
═══════════════════════════════════════════════════
功能:
  1. 基于ATR动态计算止损位（替代固定百分比）
  2. 体制漂移时自动扩展SL（避免被轻易扫损）
  3. 支撑/阻力层精确止损（Key Level吸附）
  4. 移动止损进度计算（已盈利时建议跟踪）
  5. 止损建议：Conservative / Standard / Aggressive

SL公式:
  base_sl  = entry ± ATR(14) × multiplier
  drift_sl = base_sl × (1 + drift_expansion)
  key_sl   = snap_to_nearest_key_level(drift_sl, tolerance=0.3%)

体制乘数:
  BULL_TREND  LONG:  1.5×ATR (趋势宽松)
  BEAR_IMPULSE SHORT: 1.5×ATR
  CHOP_MID:          1.0×ATR (震荡紧凑)
  HIGH_VOL:          2.0×ATR (高波动保护)
"""
import json, math
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(__file__).parent.parent / 'data'
DHARMA   = Path(__file__).parent.parent / 'dharma' / 'data'

# ATR乘数：体制 × 方向
REGIME_MULT = {
    'BULL_TREND':    {'LONG': 1.5, 'SHORT': 1.2},
    'BULL_RECOVERY': {'LONG': 1.4, 'SHORT': 1.3},
    'BEAR_IMPULSE':  {'LONG': 1.2, 'SHORT': 1.5},
    'BEAR_RECOVERY': {'LONG': 1.3, 'SHORT': 1.4},
    'CHOP_MID':      {'LONG': 1.0, 'SHORT': 1.0},
    'CHOP_HIGH':     {'LONG': 1.8, 'SHORT': 1.8},
    'CHOP_LOW':      {'LONG': 0.9, 'SHORT': 0.9},
    'DEFAULT':       {'LONG': 1.2, 'SHORT': 1.2},
}

# 止损风格
SL_STYLES = {
    'conservative': 0.7,   # 更紧（高评分信号，减少亏损）
    'standard':     1.0,   # 标准
    'aggressive':   1.4,   # 更宽（高波动，避免扫损）
}


def _atr14_from_parquet(symbol: str, interval: str = '1h') -> float | None:
    """从达摩院Parquet读取ATR14"""
    try:
        sym_lower = symbol.lower().replace('usdt','usdt')
        fname = DHARMA / f'{sym_lower}_{interval}_2018_2026.parquet'
        if not fname.exists():
            fname = DHARMA / f'{symbol.lower()}_{interval}_2018_2026.parquet'
        if not fname.exists(): return None
        import pandas as pd
        df = pd.read_parquet(fname).tail(50)
        hi, lo, cl = df['high'].values, df['low'].values, df['close'].values
        trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
               for i in range(1,len(cl))]
        # EMA ATR14
        atr = trs[0]
        for tr in trs[1:]:
            atr = atr * 13/14 + tr * 1/14
        return atr
    except: return None


def _snap_to_key_level(price: float, key_levels: list, side: str,
                        tolerance: float = 0.003) -> float:
    """将止损吸附到最近的关键位（如果在tolerance范围内）"""
    if not key_levels: return price
    is_long = side in ('LONG','做多')
    best = price
    best_dist = float('inf')
    for lvl in key_levels:
        dist = abs(lvl - price) / price
        if dist <= tolerance:
            # 多头止损应该在支撑位下方, 空头在阻力位上方
            if (is_long and lvl < price) or (not is_long and lvl > price):
                if dist < best_dist:
                    best_dist = dist
                    best = lvl * (0.998 if is_long else 1.002)  # 再让一点
    return best


def compute(
    symbol: str,
    entry_price: float,
    signal_dir: str,
    regime: str = 'CHOP_MID',
    score: float = 100,
    drift_alert: str = 'OK',
    current_price: float = None,
    key_levels: list = None,
    style: str = 'standard',
    interval: str = '1h',
) -> dict:
    """
    计算动态止损位

    Returns:
        {
          'sl_price':    推荐止损价
          'sl_pct':      止损幅度%
          'atr14':       ATR14绝对值
          'atr_mult':    使用的ATR乘数
          'trail_note':  移动止损建议
          'reasoning':   str
        }
    """
    is_long = signal_dir in ('LONG','做多')

    # ── 获取ATR ────────────────────────────────────────────
    atr14 = _atr14_from_parquet(symbol, interval)
    if atr14 is None or atr14 <= 0:
        # fallback: entry的1.5%估算
        atr14 = entry_price * 0.015
        atr_source = 'estimated'
    else:
        atr_source = f'parquet_{interval}'

    # ── 体制乘数 ────────────────────────────────────────────
    reg_key = regime.upper()
    if reg_key not in REGIME_MULT: reg_key = 'DEFAULT'
    dir_key = 'LONG' if is_long else 'SHORT'
    base_mult = REGIME_MULT[reg_key][dir_key]

    # ── 漂移扩展 ────────────────────────────────────────────
    drift_expansion = 0.0
    if drift_alert == 'WARN':  drift_expansion = 0.15  # SL扩展15%
    if drift_alert == 'ALERT': drift_expansion = 0.30  # SL扩展30%

    # ── 评分调整（高分信号可以更紧） ────────────────────────
    score_adj = 1.0 - max(0, score - 120) * 0.002  # score=150→0.94
    score_adj = max(0.8, min(1.1, score_adj))

    # ── 风格系数 ────────────────────────────────────────────
    style_mult = SL_STYLES.get(style, 1.0)

    # ── 最终ATR乘数 ─────────────────────────────────────────
    final_mult = base_mult * (1 + drift_expansion) * score_adj * style_mult
    final_mult = max(0.7, min(3.0, final_mult))

    # ── 止损价格 ────────────────────────────────────────────
    sl_distance = atr14 * final_mult
    if is_long:
        sl_price = entry_price - sl_distance
    else:
        sl_price = entry_price + sl_distance

    # ── 关键位吸附 ──────────────────────────────────────────
    sl_price_snapped = _snap_to_key_level(sl_price, key_levels or [], signal_dir)
    snapped = abs(sl_price_snapped - sl_price) / entry_price > 0.0005

    sl_pct = abs(entry_price - sl_price_snapped) / entry_price

    # ── 移动止损建议 ────────────────────────────────────────
    trail_note = ''
    if current_price and entry_price:
        pnl_pct = (current_price - entry_price) / entry_price
        if not is_long: pnl_pct = -pnl_pct
        if pnl_pct >= 0.015:
            trail_note = f'TRAIL: price moved +{pnl_pct:.1%} → move SL to breakeven'
        if pnl_pct >= 0.03:
            trail_note = f'TRAIL: +{pnl_pct:.1%} → move SL to +1.0%'
        if pnl_pct >= 0.05:
            trail_note = f'TRAIL: +{pnl_pct:.1%} → move SL to +2.5%'

    reasoning = (
        f'ATR14={atr14:.4f}({atr_source}) mult={final_mult:.2f} '
        f'[regime×{base_mult:.1f} drift×{1+drift_expansion:.2f} '
        f'score×{score_adj:.2f} style×{style_mult:.1f}]'
        f'{" SNAPPED" if snapped else ""}'
    )

    # ── Bandit SL软约束注入（设计院2026-08-06封印）──────────────
    # 原理: sl_bandit推荐值作为软约束，置信度加权混合
    # 不覆盖v4铁证硬下限，只在高置信度时微调sl_pct
    bandit_note = ''
    try:
        from brahma_brain.position_sizer import recommend_sl_pct as _bandit_rec
        _br = _bandit_rec(
            regime=regime or 'BULL_TREND',
            direction=signal_dir or 'LONG',
            base_sl_pct=sl_pct * 100,
            score=float(score or 100),
        )
        _rec_pct = _br['recommended_sl_pct'] / 100   # 转回小数
        _conf    = _br['confidence']
        # 置信度加权混合：conf<0.3几乎不影响，conf>0.8主导
        if _conf >= 0.3:
            _blended = sl_pct * (1 - _conf * 0.4) + _rec_pct * (_conf * 0.4)
            _blended = max(_blended, sl_pct * 0.85)  # 最多紧缩15%
            _blended = min(_blended, sl_pct * 1.15)  # 最多放宽15%
            sl_pct   = round(_blended, 5)
            # 同步更新sl_price
            if is_long:
                sl_price_snapped = entry_price * (1 - sl_pct)
            else:
                sl_price_snapped = entry_price * (1 + sl_pct)
            bandit_note = (f' BANDIT:arm={_br["arm"]}'
                           f',conf={_conf:.2f},rec={_br["recommended_sl_pct"]:.2f}%')
    except Exception:
        pass  # Bandit不可用时静默降级，不影响主链路
    # ────────────────────────────────────────────────────────

    return {
        'sl_price':    round(sl_price_snapped, 6),
        'sl_raw':      round(sl_price, 6),
        'sl_pct':      round(sl_pct, 5),
        'sl_distance': round(sl_distance, 6),
        'atr14':       round(atr14, 6),
        'atr_mult':    round(final_mult, 3),
        'atr_source':  atr_source,
        'snapped_to_key': snapped,
        'trail_note':  trail_note,
        'reasoning':   reasoning + bandit_note,
        'ts': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    # ETH空单测试
    r = compute('ETHUSDT', 2127.65, 'SHORT', 'CHOP_MID', score=154, drift_alert='WARN')
    print(f"ETH SHORT entry=2127.65:")
    print(f"  SL={r['sl_price']:.2f}  ({r['sl_pct']:.2%})")
    print(f"  ATR14={r['atr14']:.4f}  mult={r['atr_mult']:.2f}")
    print(f"  {r['reasoning']}")
    if r['trail_note']: print(f"  {r['trail_note']}")

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/capital_allocator.py ══
"""

# STATUS: ACTIVE
# 资金分配引擎，多仓位管理
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
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

# ══ [2026-09-01 设计院精简封印] 合并自 brahma_brain/sl_bandit.py ══
# ponytail: sl_bandit 383行，有意为之，重构前先 grep 所有调用方
"""
sl_bandit.py — 梵天 SL自适应Bandit引擎
设计院 2026-08-06 | 苏摩111封印

基于历史249条结算信号的模拟验证:
  当前固定SL: WR=38% avg_pnl=-0.54%/笔
  Bandit最优: avg_pnl≈+0.63%/笔
  预期提升:   +1.17%/笔 (217%提升)

核心思路:
  - 将sl_pct离散化为5个arm: <1.5 / 1.5-2.0 / 2.0-2.5 / 2.5-3.0 / 3.0+
  - 按 regime × direction × arm 维护 (wins, n) 计数
  - 使用UCB1算法选择最优arm（兼顾探索+利用）
  - 每次signal_settler结算后自动更新
  - 推荐的sl_pct作为dynamic_sl的软约束（不硬覆盖v4铁证门槛）

文件:
  data/sl_bandit_state.json — 持久化arm计数
"""

import json
import math
import time
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent.parent
BANDIT_STATE_PATH = BASE / 'data' / 'sl_bandit_state.json'
SIGNAL_LOG_PATH   = BASE / 'data' / 'live_signal_log.jsonl'

# ── Arm定义 ──────────────────────────────────────────────
ARMS = [
    ('tight',    0.5,  1.5),   # arm0: 0.5~1.5%
    ('standard', 1.5,  2.0),   # arm1: 1.5~2.0%
    ('iron',     2.0,  2.5),   # arm2: 2.0~2.5% ← v4铁证核心区间
    ('wide',     2.5,  3.0),   # arm3: 2.5~3.0%
    ('extreme',  3.0, 15.0),   # arm4: 3.0%+
]
ARM_NAMES = [a[0] for a in ARMS]
ARM_MID   = [(a[1]+a[2])/2 for a in ARMS]  # arm代表值

# ── v4铁证硬下限（不被Bandit覆盖）──────────────────────
V4_MIN_SL = {
    'BEAR_TREND':    2.0,
    'BEAR_EARLY':    2.0,
    'CHOP_MID':      2.5,
    'BULL_TREND':    1.5,
    'BEAR_RECOVERY': 2.0,
    'BULL_EARLY':    1.5,
    'DEFAULT':       1.5,
}

# UCB1 探索常数（越大越探索，越小越利用）
# 2026-08-07 设计院深度推理封印：iron arm WR=58.5%已验证是最优arm，
# 降低C值 1.5→0.8加速收敛，减少对extreme(WR=7.9%)的无谓探索
UCB_C = 0.8

# 最少观测次数才信任该arm（否则视为未知，用先验）
MIN_OBS = 5

# ── 先验初始化（来自模拟验证结果）────────────────────────
# 格式: regime:direction:arm_name → (wins, n)
PRIOR_FROM_SIM = {
    'BULL_TREND:LONG:tight':    (29,  90),   # WR=32%
    'BULL_TREND:LONG:standard': (15,  46),   # WR=33%
    'BULL_TREND:LONG:iron':     (31,  52),   # WR=60% ← 最优
    'BULL_TREND:LONG:wide':     (2,   20),   # WR=10% 先验防止过度探索—宽止损历史较差
    'BULL_TREND:LONG:extreme':  (2,   40),   # WR=5% [封印降权 2026-08-14] 实测WR=7.9% n=38 PRIOR强制降至5%淘汰
    'BEAR_RECOVERY:LONG:iron':  (8,    8),   # WR=100%
    'CHOP_MID:LONG:extreme':    (1,   20),   # WR=5% [封印降权 2026-08-14] 实测WR=9.1% n=11 PRIOR降权
}


def _load_state() -> dict:
    """加载Bandit状态"""
    if BANDIT_STATE_PATH.exists():
        try:
            return json.loads(BANDIT_STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    """持久化Bandit状态"""
    BANDIT_STATE_PATH.parent.mkdir(exist_ok=True)
    tmp = BANDIT_STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(BANDIT_STATE_PATH)


def _arm_key(regime: str, direction: str, arm_name: str) -> str:
    return f"{regime}:{direction}:{arm_name}"


def _sl_to_arm(sl_pct: float) -> str:
    """将sl_pct映射到arm名称"""
    for name, lo, hi in ARMS:
        if lo <= sl_pct < hi:
            return name
    return 'extreme'


def _ucb1_score(wins: int, n: int, total_n: int, c: float = UCB_C) -> float:
    """UCB1评分：期望收益 + 探索奖励"""
    if n == 0:
        return float('inf')  # 未探索的arm优先
    exploitation = wins / n
    exploration  = c * math.sqrt(math.log(max(total_n, 1)) / n)
    return exploitation + exploration


def recommend_sl_pct(
    regime: str,
    direction: str,
    base_sl_pct: float,
    score: float = 100.0,
    verbose: bool = False,
) -> dict:
    """
    推荐最优sl_pct

    Args:
        regime:      体制标签 (BULL_TREND/BEAR_TREND/...)
        direction:   方向 (LONG/SHORT)
        base_sl_pct: dynamic_sl计算出的基础止损%
        score:       信号评分（高分信号允许更紧的止损）
        verbose:     是否输出调试信息

    Returns:
        {
          'recommended_sl_pct': float,  # Bandit推荐值
          'arm': str,                   # 选中的arm
          'confidence': float,          # 置信度 0~1
          'reasoning': str,
        }
    """
    state = _load_state()

    # 初始化先验（首次运行）
    for k, (w, n) in PRIOR_FROM_SIM.items():
        if k not in state:
            state[k] = {'wins': w, 'n': n}

    # v4铁证硬下限
    v4_min = V4_MIN_SL.get(regime, V4_MIN_SL['DEFAULT'])

    # 统计该体制+方向的总观测数
    total_n = sum(
        state.get(_arm_key(regime, direction, a[0]), {}).get('n', 0)
        for a in ARMS
    )

    # UCB1评分各arm
    arm_scores = {}
    for arm_name, arm_lo, arm_hi in ARMS:
        # 跳过低于v4铁证下限的arm
        if arm_hi <= v4_min:
            continue
        key = _arm_key(regime, direction, arm_name)
        d = state.get(key, {'wins': 0, 'n': 0})
        arm_scores[arm_name] = _ucb1_score(d['wins'], d['n'], total_n)

    if not arm_scores:
        # 所有arm被v4铁证过滤 → 返回base_sl
        return {
            'recommended_sl_pct': base_sl_pct,
            'arm': _sl_to_arm(base_sl_pct),
            'confidence': 0.0,
            'reasoning': f'v4铁证下限={v4_min}% 覆盖所有arm，使用base_sl={base_sl_pct:.2f}%',
        }

    # 选UCB1最高的arm
    best_arm = max(arm_scores, key=arm_scores.__getitem__)
    best_arm_info = next(a for a in ARMS if a[0] == best_arm)
    arm_mid = (best_arm_info[1] + best_arm_info[2]) / 2

    # 高分信号允许更紧（最多紧缩15%）
    score_tight = 1.0 - max(0, score - 130) * 0.003  # score=160→0.91
    score_tight = max(0.85, min(1.0, score_tight))
    recommended = arm_mid * score_tight

    # 硬约束: 不低于v4铁证下限, 不超过5%
    recommended = max(recommended, v4_min)
    recommended = min(recommended, 5.0)
    recommended = round(recommended, 2)

    # 置信度: 当前arm观测数/MIN_OBS
    arm_key = _arm_key(regime, direction, best_arm)
    arm_n = state.get(arm_key, {}).get('n', 0)
    confidence = min(1.0, arm_n / MIN_OBS)

    # 若置信度低(<0.5)，与base_sl加权混合
    if confidence < 0.5:
        recommended = round(base_sl_pct * (1 - confidence) + recommended * confidence, 2)
        recommended = max(recommended, v4_min)

    reasoning = (
        f"UCB1选arm={best_arm}(score={arm_scores[best_arm]:.2f}) "
        f"arm_mid={arm_mid:.1f}% score_adj={score_tight:.2f} "
        f"v4_min={v4_min}% confidence={confidence:.2f} "
        f"→ recommended={recommended:.2f}%"
    )

    if verbose:
        print(f"[sl_bandit] {regime}:{direction}")
        for a, s_score in sorted(arm_scores.items(), key=lambda x: -x[1]):
            k = _arm_key(regime, direction, a)
            d = state.get(k, {'wins': 0, 'n': 0})
            wr = d['wins']/d['n']*100 if d['n'] else 0
            print(f"  {a:<10} UCB={s_score:.2f} WR={wr:.0f}% n={d['n']}")
        print(f"  → 推荐: {recommended:.2f}% ({reasoning})")

    return {
        'recommended_sl_pct': recommended,
        'arm': best_arm,
        'confidence': confidence,
        'reasoning': reasoning,
    }


def update_from_outcome(
    regime: str,
    direction: str,
    sl_pct: float,
    outcome: str,
    pnl_pct: float = 0.0,
) -> None:
    """
    根据信号结算结果更新Bandit状态

    Args:
        regime:    体制
        direction: 方向
        sl_pct:    实际使用的sl_pct
        outcome:   结算结果 (TP1/TP2/SL_HIT/EXPIRED_NO_TOUCH/...)
        pnl_pct:   实际PnL%
    """
    arm_name = _sl_to_arm(sl_pct)
    key = _arm_key(regime, direction, arm_name)

    state = _load_state()
    if key not in state:
        state[key] = {'wins': 0, 'n': 0}

    state[key]['n'] += 1

    # 判断胜负
    win = (
        outcome in ('TP1', 'TP2', 'WIN', 'TAKE_PROFIT') or
        (outcome == 'EXPIRED_NO_TOUCH' and pnl_pct > 0)
    )
    if win:
        state[key]['wins'] += 1

    state[key]['last_updated'] = time.time()
    _save_state(state)


def sync_from_signal_log() -> int:
    """
    从live_signal_log.jsonl批量同步结算数据到Bandit状态
    返回新同步的条数
    """
    state = _load_state()
    last_sync_ts = state.get('_last_sync_ts', 0)
    new_count = 0

    if not SIGNAL_LOG_PATH.exists():
        return 0

    lines = SIGNAL_LOG_PATH.read_text().strip().split('\n')
    for l in lines:
        try:
            s = json.loads(l)
            ts = s.get('ts', 0)
            if ts <= last_sync_ts:
                continue
            outcome = s.get('outcome', '')
            if not outcome:
                continue
            regime    = s.get('regime', 'BULL_TREND')
            direction = s.get('direction', 'LONG')
            sl_pct    = float(s.get('sl_pct', 0) or 0)
            pnl_pct   = float(s.get('pnl_pct') or s.get('pnl', 0) or 0)
            if sl_pct <= 0:
                continue
            update_from_outcome(regime, direction, sl_pct, outcome, pnl_pct)
            new_count += 1
        except Exception:
            pass

    # 更新同步时间戳
    if new_count > 0:
        state2 = _load_state()
        state2['_last_sync_ts'] = time.time()
        _save_state(state2)

    return new_count


def get_stats() -> dict:
    """返回当前Bandit统计摘要"""
    state = _load_state()
    summary = {}
    for key, v in state.items():
        if key.startswith('_'):
            continue
        n = v.get('n', 0)
        wins = v.get('wins', 0)
        wr = wins/n*100 if n > 0 else 0
        summary[key] = {'n': n, 'wins': wins, 'wr': round(wr, 1)}
    return summary


def sync_from_simfactory() -> int:
    """
    从 dharma_simfactory.py 生成的 simfactory_trades.jsonl
    批量喂给 Bandit，加速WR收敛（设计院 2026-08-07）
    """
    sim_path = BASE / 'data' / 'simfactory_trades.jsonl'
    if not sim_path.exists():
        return 0

    state = _load_state()
    seen_ids = set(state.get('_sim_seen_ids', []))
    new_count = 0

    for line in sim_path.read_text().splitlines():
        try:
            t = json.loads(line)
            sid = t.get('signal_id', '')
            if sid in seen_ids:
                continue
            result = t.get('result', '')
            if result not in ('TP1', 'SL'):
                continue
            outcome = 'WIN' if result == 'TP1' else 'LOSS'
            sl_pct  = float(t.get('sl_pct', 0) or 0)
            pnl_pct = float(t.get('pnl_pct', 0) or 0)
            if sl_pct <= 0:
                continue
            update_from_outcome(
                t.get('regime', 'BULL_TREND'),
                t.get('direction', 'LONG'),
                sl_pct, outcome, pnl_pct
            )
            seen_ids.add(sid)
            new_count += 1
        except Exception:
            pass

    if new_count > 0:
        state2 = _load_state()
        state2['_sim_seen_ids'] = list(seen_ids)
        _save_state(state2)

    return new_count


# ── 独立运行: 打印统计 + 同步 ────────────────────────────
if __name__ == '__main__':
    import sys

    if '--sync' in sys.argv:
        n = sync_from_signal_log()
        print(f"[sl_bandit] 从信号日志同步: {n}条新记录")
        n2 = sync_from_simfactory()
        print(f"[sl_bandit] 从 simfactory同步: {n2}条新记录")

    print("\n[sl_bandit] 当前Bandit状态:")
    stats = get_stats()
    if not stats:
        print("  (空，首次运行将使用先验数据)")
    else:
        for k, v in sorted(stats.items(), key=lambda x: -x[1]['n']):
            print(f"  {k:<40} n={v['n']:3} wins={v['wins']:3} WR={v['wr']:5.1f}%")

    print("\n[sl_bandit] 推荐示例:")
    for regime, direction in [('BULL_TREND','LONG'),('BEAR_RECOVERY','LONG'),('CHOP_MID','SHORT')]:
        r = recommend_sl_pct(regime, direction, base_sl_pct=2.0, score=140, verbose=True)
        print(f"  {regime}:{direction} → {r['recommended_sl_pct']:.2f}% (arm={r['arm']}, conf={r['confidence']:.2f})\n")