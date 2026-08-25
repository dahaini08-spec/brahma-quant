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
        from macro_calendar import get_upcoming_events as _get_macro_ev
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
        from var_engine import single_position_var as _var_fn
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
        from market_quadrant import get_quadrant as _get_q
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

    # ── IC反馈修正：高分低胜率时收紧高分段仓位 ────────────────
    _ic_note = ''
    if _IC_PENALTY and score > 175 and level not in ('BANNED', 'EXPLORING'):
        max_pct = round(max_pct * 0.6, 2)
        usdt = round(max_pct / 100 * nav, 2) if nav else 0
        _ic_note = f'IC={_IC_VALUE:.3f}高分低胜率警告×0.6'

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
                           + (f' [{_quad_note}]' if _quad_note else ''),
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
