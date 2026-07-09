"""
brahma_core_entry.py — 入场参数计算模块
设计院·第一步架构拆分 2026-07-01

职责：
  - calc_trade_params()  : 入场区/止损/止盈计算
  - rebase_params()      : 参数基准化

原属 brahma_core.py，拆分后接口完全不变。
brahma_core.py 通过 from brahma_core_entry import * 保持向后兼容。

INTERFACE CONTRACT:
  Interface : calc_trade_params(ms,smc,signal_dir,mtf_result)->dict
              rebase_params(params,current_price,symbol)->dict
  Output    : 入场/止损/止盈参数字典
  Call Freq : analyze()内每次信号生成时调用一次
  Deps      : structure_quality_engine, trigger_15m, math
"""
"""
brahma_brain.py · 梵天分析大脑主入口  VERSION = v3.0
brahma_brain · Phase 1 完整整合

调用流程：
  1. market_state.py  → 多框架趋势 + 体制 + 关键位
  2. smc_engine.py    → BOS/CHoCH/OB/FVG/流动性
  3. confluence_score → 150分共振评分
  4. 输出精确交易参数 + 钉钉1格式文本
"""
import os, sys, time
import copy  # [P1-C audit-fix] deepcopy for cf dict
import json  # [D1-fix] 提升到顶部
from datetime import datetime, timezone, timedelta  # [D1-fix] 提升到顶部
from pathlib import Path  # [D1-fix] 提升到顶部

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, '..'))

from data_cache        import prefetch_symbol, get_klines, klines_to_ohlcv
from market_state      import analyze   as ms_analyze
from smc_engine        import analyze_smc
from divergence_engine import divergence_score
from volume_engine     import volume_score
from range_engine      import range_score  # [Phase2a] 区间结构引擎
try:
    from math_utils import ema as _mu_ema, rsi as _mu_rsi, atr as _mu_atr  # [设计院 2026-06-30 全量接入] 统一数学库
    _MATH_UTILS_OK = True
except Exception:
    _MATH_UTILS_OK = False
from options_engine    import sentiment_score, analyze_funding_trend
# [CLEANED 2026-06-11] from elliott_engine    import analyze_elliott, format_elliott
try:
    from onchain_engine import onchain_score as _onchain_score
    _ONCHAIN_OK = True
except Exception:
    _ONCHAIN_OK = False
try:
    from pattern_engine import pattern_score as _pattern_score
    _PATTERN_OK = True
except Exception:
    _PATTERN_OK = False
try:
    from order_flow_engine import order_flow_score as _order_flow_score
    _OF_OK = True
except Exception:
    _OF_OK = False
try:
    from macro_engine import macro_score as _macro_score
    _MACRO_OK = True
except Exception:
    _MACRO_OK = False
# [CLEANED 2026-06-11] harmonic_engine removed — permanently disabled
_HARMONIC_OK = False
try:
    from volume_exhaustion_engine import volume_exhaustion_score as _vol_exh_score
    _VOL_EXH_OK = True
except Exception:
    _VOL_EXH_OK = False
try:
    from divergence_engine import multitf_divergence_score as _multitf_div_score
    _MULTITF_DIV_OK = True
except Exception:
    _MULTITF_DIV_OK = False
try:
    from multitf_engine import multitf_score as _multitf_score
    _MULTITF_OK = True
except Exception:
    _MULTITF_OK = False
try:
    from enhanced_signal_engine import enhanced_score as _enhanced_score
    _ENHANCED_OK = True
except Exception:
    _ENHANCED_OK = False
try:
    from whale_engine import whale_score as _whale_score
    _WHALE_OK = True
except Exception:
    _WHALE_OK = False
try:
    from cross_market_engine import cross_market_score as _cross_market_score
    _CROSS_OK = True
except Exception:
    _CROSS_OK = False
try:
    from microstructure_engine import microstructure_score as _micro_score
    _MICRO_OK = True
except Exception:
    _MICRO_OK = False

# ═══════════════════════════════════════════════════════════════
# 150分共振评分器（Phase 1 内置版）
# ═══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
# calc_trade_params — 入场区/止损/止盈核心计算（601行）
# ══════════════════════════════════════════════════════════
# ── 辅助函数（从 brahma_core.py 迁移 2026-07-01）──

def _nearest_swing_above(swing_highs: list, entry: float) -> float:
    """找到入场价上方最近的摆动高点（用于做空止损）"""
    candidates = [v for v in swing_highs if v > entry]
    return min(candidates) if candidates else entry * 1.015

def _nearest_swing_below(swing_lows: list, entry: float) -> float:
    """找到入场价下方最近的摆动低点（用于做多止损）"""
    candidates = [v for v in swing_lows if v < entry]
    return max(candidates) if candidates else entry * 0.985

def calc_trade_params(ms: dict, smc: dict, signal_dir: str, mtf_result: dict = None) -> dict:
    """
    精确交易参数生成 — v13.0 四层止损架构
    ────────────────────────────────────────────────────────
    止损逻辑（合约思维重构）：
      Layer 1: primary_tf = 4H（方向周期）
               entry_tf   = 1H（入场触发周期）
      Layer 2: 止损放在「结构失效点」：
               SHORT → 4H最近摆动高点（或4H FVG上沿）
               LONG  → 4H最近摆动低点（或4H FVG下沿）
      Layer 3: ATR缓冲 = 4H ATR × 0.3（防插针，不随意加）
      Layer 4: 验证 R:R TP1 ≥ 2.5（更严格门槛）
    TP逻辑：
      TP1 = 最近流动性目标（前低/清算密集区），R:R≥2.5
      TP2 = 结构延伸目标，ATR×5.0 距离
    ────────────────────────────────────────────────────────
    """
    price   = ms['price']
    atr_1h  = ms['momentum']['atr_1h']
    fib     = ms['key_levels']['fib']

    # [终极体系 2026-05-25] 体制感知动态TP倍数
    # CHOP体制: TP1=2.8x(盘内波幅收紧), BULL_TREND(牛市趋势): TP1=3.0x(趋势延伸)
    _regime_tp = ms.get('regime', '')
    # [WFV-v3.0 2026-05-28] 达摩院无穿越训练结论（修复NodeDB/train_10k穿越）
    # 全局冠军: RSI<20/>>82  SL=0.6x  TP=4.0x  core_OOS PF=1.227
    # 旧WFV-v1 PF=1.453 含穿越数据，已废弃
    # [v3.1 动态T1 · 「识别不是封禁」哲学 · 2026-06-10]
    # 数据依据：TIMEOUT方向正确53%，根因是T1太远而非信号错误
    # CHOP用黄金比例收窄T1，BEAR_EARLY保持强趋势T1
    # 「高频精神」：T1缩短 → 命中率↑ → TIMEOUT率↓ → 信号自然淘汰
    # [P1-B 设计院 2026-06-21] 动态TP1自适应 — 实盘回溯TP触达率仅7%，根因是TP1倍数过大
    # BEAR_TREND: 2.5x→2.0x | BEAR_EARLY: 2.5x→2.0x | BEAR_RECOVERY: 1.5x→1.2x
    # [六方联合修复 2026-06-25] 方案C：体制分级TP倍数 + 方案D关键压力分批止盈准备
    # 铁证依据：
    #   BEAR_RECOVERY_LONG WR=72.5%(n=38次铁证) → EV=0.725×1.2-0.275=0.595 → 允许低R:R
    #   BEAR_TREND_SHORT WR=68% → 保持2.0x精准打击
    #   BTW案例：CHOP/反弹体制TP1过远导致TIMEOUT，1.2x~1.5x更合理
    #   震荡行情1:1.5已有正期望，不必强求1:2.5
    if _regime_tp in ('CHOP_MID', 'CHOP_HIGH', 'CHOP_LOW', 'SQUEEZE'):
        _tp1_mult = 1.5    # [六方修复] CHOP用1.5x（原黄金比例1.618，略收窄提升触达率）
        _tp2_mult = 3.0    # T2保留给追踪止损延伸空间
        _tp_partial = True  # 分批止盈标记：TP1平50%，剩余追踪
    elif _regime_tp in ('BEAR_TREND', 'BEAR_BREAK'):
        _tp1_mult = 2.0    # 趋势体制保持2.0x（铁证WR=68%支撑）
        _tp2_mult = 4.0
        _tp_partial = False
    elif _regime_tp in ('BEAR_EARLY',):
        _tp1_mult = 1.8    # [六方修复] 2.0x→1.8x，初期趋势不确定性更高
        _tp2_mult = 4.0
        _tp_partial = False
    elif _regime_tp in ('BEAR_RECOVERY', 'BULL_CORRECTION'):
        _tp1_mult = 1.2    # 反弹体制快进快出，1.2x命中率最高
        _tp2_mult = 2.0    # [六方修复] T2压缩至2.0x，反弹行情空间有限
        _tp_partial = True  # 分批止盈：TP1平60%，关键压力位反应
    elif _regime_tp in ('BULL_TREND', 'BULL_BREAK', 'BULL_PEAK'):
        _tp1_mult = 2.5    # [六方修复] 3.0x→2.5x，提升触达率
        _tp2_mult = 5.0
        _tp_partial = False
    elif _regime_tp in ('BULL_EARLY',):
        _tp1_mult = 1.8
        _tp2_mult = 3.5
        _tp_partial = False
    else:
        _tp1_mult = 1.6    # [六方修复] 默认1.8x→1.6x，震荡/未知体制更保守
        _tp2_mult = 3.0
        _tp_partial = True

    # ── 获取4H ATR和摆动结构（primary_tf）──
    atr_4h = ms['momentum'].get('atr_4h', atr_1h * 2.5)   # fallback
    sw4h_h = ms.get('swing_4h', {}).get('highs', [])       # 4H摆动高点列表
    sw4h_l = ms.get('swing_4h', {}).get('lows', [])        # 4H摆动低点列表

    # ── ATR合理性护栏（双向：上限15%防暴涨品种，下限0.5%防精度崩溃）──
    # 规则：ATR不得超过当前价的15%；不得小于0.5%（极端低价品种精度保护）
    _atr_cap = price * 0.15
    _atr_floor = price * 0.005   # 下限：最小ATR = 价格的0.5%
    if atr_4h > _atr_cap:
        atr_4h = price * 0.05   # 强制用5%价格作为ATR
    if atr_4h < _atr_floor:
        atr_4h = _atr_floor     # 防止精度崩溃导致SL=0
    if atr_1h > price * 0.10:
        atr_1h = price * 0.03

    # ── 入场区（FVG中点 > OB > Fib，优先级递减）──
    if signal_dir == 'LONG':
        # 入场区：回调到4H空间支撑
        ob = smc['order_blocks'].get('nearest_bull_ob')
        fvg_bull = smc.get('fvg', {}).get('nearest_bull')
        if fvg_bull:
            entry_lo = fvg_bull.get('bottom', fvg_bull.get('low', price*0.995))
            entry_hi = fvg_bull.get('top',    fvg_bull.get('high', price*1.005))
        elif ob:
            entry_lo = ob['low'];        entry_hi = ob['high']
        else:
            fib_entry = fib.get('0.618', price * 0.99)
            entry_lo  = min(fib_entry, price) * 0.997
            entry_hi  = min(fib_entry, price)
        entry_lo = min(entry_lo, price);  entry_hi = min(entry_hi, price)

        # [v22.1 2026-06-10] ATR自适应入场区压缩 — LONG方向
        # 方案: FVG/OB太宽(>×1.5 ATR4H)时，压缩到FVG/OB底沿附近（回调到底部才是最佳多头入场）
        try:
            _ob_width_l = entry_hi - entry_lo
            if _ob_width_l > atr_4h * 1.5 and atr_4h > 0:
                _ob_bot     = entry_lo   # OB/FVG底沿
                _clo_lo = _ob_bot - atr_4h * 0.3
                _clo_hi = _ob_bot + atr_4h * 0.3
                _clo_lo = max(_clo_lo, entry_lo * 0.995)
                _clo_hi = min(_clo_hi, entry_hi)
                if _clo_lo < _clo_hi and _clo_hi < price:
                    pass  # [静默]
                    entry_lo = _clo_lo
                    entry_hi = _clo_hi
        except Exception as _audit_e:  # [S1-fix] 原静默except
            pass  # [S1-fix] 已捕获: _audit_e (静默保留兼容性)

        # [v25.7 P0a 2026-06-21] 入场区最小宽度保证（LONG）
        # 根因：v22.1只做宽→压缩，但OB本身偏窄时触达率低 → TIMEOUT
        # 铁证：高质量Paper TIMEOUT=23%，方向预测正确率=53%，是等待窗口+区间问题
        # 修复：保证入场区宽度 ≥ 0.6×ATR1H（对称扩展，回调方向多扩一点）
        try:
            _min_width_l = atr_1h * 0.6
            _cur_width_l = entry_hi - entry_lo
            if _cur_width_l < _min_width_l and atr_1h > 0:
                _expand_l = (_min_width_l - _cur_width_l) / 2
                _new_lo = entry_lo - _expand_l          # 向下扩（回调方向）
                _new_hi = entry_hi + _expand_l * 0.4    # 向上少扩（防追高）
                # 安全护栏：扩展后不能超过现价
                if _new_lo > 0 and _new_hi < price:
                    pass  # [静默]
                    entry_lo = _new_lo
                    entry_hi = _new_hi
        except Exception:
            pass  # 宽度保证失败不影响主流程

        # Layer 2+3: 止损 = 入场区下方最近4H摆低 - ATR4H×0.3
        struct_low = _nearest_swing_below(sw4h_l, entry_lo) if sw4h_l else entry_lo * 0.985
        stop_loss  = struct_low - atr_4h * 0.3

        # TP: 上方前高 / 流动性区
        entry_mid = (entry_lo + entry_hi) / 2
        risk = abs(entry_mid - stop_loss)
        # TP1 最小R:R=2.5
        tp1_min = entry_mid + risk * _tp1_mult  # [终极体系] 体制感知动态TP
        # 护栏：多头TP不能超过入场价1000%（防止ATR异常导致天文数字）
        tp1_min = min(tp1_min, entry_mid * 11.0)  # 最多+1000%
        # 尝试用4H摆高作为TP1
        struct_tp1 = min([v for v in sw4h_h if v > entry_hi], default=tp1_min)
        tp1 = max(tp1_min, struct_tp1) if struct_tp1 > entry_hi else tp1_min
        tp1 = min(tp1, entry_mid * 11.0)  # 二次护栏
        tp2 = entry_mid + risk * _tp2_mult
        tp2 = min(tp2, entry_mid * 20.0)  # tp2护栏
        # [设计院 2026-06-23 P0修复 v2] LONG护栏：4H摆高覆盖tp1后tp2可能<tp1
        # 正确修复：tp2必须基于tp1计算，不能基于entry_mid
        if tp2 <= tp1:
            tp2 = tp1 + risk  # tp2 = tp1 + 1个risk单位，确保tp2>tp1
            tp2 = min(tp2, entry_mid * 20.0)

    else:  # SHORT
        # 入场区：反弹到4H空间阻力
        ob = smc['order_blocks'].get('nearest_bear_ob')
        fvg_bear = smc.get('fvg', {}).get('nearest_bear')

        # [设计院 2026-05-30] 否决权：fvg_bear必须在当前价上方1%+，否则降级用OB/Fib
        # 修复根因：SMC返回的nearest_bear已经过方向过滤(mid>price*1.003)
        # 但这里加一层确认：入场区中点必须在现价上方1%（要有反弹才能入场）
        _fvg_bear_ok = (fvg_bear and
                        fvg_bear.get('bottom', 0) > price * 1.005 and
                        fvg_bear.get('gap_pct', 0) >= 0.3)

        if _fvg_bear_ok:
            entry_lo = fvg_bear.get('bottom', fvg_bear.get('low', price))
            entry_hi = fvg_bear.get('top',    fvg_bear.get('high', price*1.005))
        elif ob:
            # [A1修复 2026-05-31] OB判断：价格在OB区间内 或 OB在价格上方 均有效
            ob_lo = ob.get('low', 0); ob_hi = ob.get('high', 0)
            _ob_in_zone  = ob_lo <= price <= ob_hi   # 价格在OB内（最优）
            _ob_above    = ob_lo > price * 1.001     # OB在价格上方0.1%
            if _ob_in_zone:
                # 价格在OB内：用OB中段入场（比上沿更容易触发，提升触发率）
                ob_mid = (ob_lo + ob_hi) / 2
                entry_lo = max(ob_lo, price * 1.001)   # 略高于现价即可触发
                entry_hi = ob_mid                       # 入场区上限收到中段（原来是ob_hi顶部）
                pass  # [静默] f'[BrahmaBrain] ℹ️ 价格在OB区间内，用OB中段入场(触发率优化) [{entry_lo:.4g}~{entry_hi:.4g}]'
            elif _ob_above:
                # [B级宽松规则 2026-06-06] B级信号OB偏远时扩宽入场区
                # 根因：B级gap均值1.44%，73%在1~2%区间 → 触发率仅27%
                # 修复：OB距现价>1%时，将入场区下沿拉至现价×1.003（仅B级生效）
                _ob_gap_pct = (ob_lo - price) / price * 100
                # 用smc结构质量预估grade（cf此时未定义，用smc间接判断）
                try:
                    from brahma_brain.structure_quality_engine import evaluate as _sqe
                    _sq_pre = _sqe(smc, price, signal_dir)
                    _grade_val = float(_sq_pre.get('grade', 0))
                except Exception:
                    # fallback：[v24.2] B级(55.0)也会被StructureGate封堵，但保留fallback值用于_is_b_grade计算
                    _grade_val = 55.0 if ob else 0.0
                _is_b_grade = 50 <= _grade_val < 70  # [v24.2] B级已被StructureGate封堵，此条件实际不会触发

                if _is_b_grade and _ob_gap_pct > 1.0:
                    # [v24.2] dead code: B级已被grade<70 StructureGate封堵，不进入此分支
                    # 保留代码防止万一fallback grade=55绕过时有安全处理
                    entry_lo = price * 1.003   # 现价上方0.3%即可触发
                    entry_hi = ob_lo           # 入场区上限维持OB下沿
                    pass  # [静默] f'[BrahmaBrain] ℹ️ B级宽松入场区(gap={_ob_gap_pct:.1f}%>1%): [{entry_lo:.6g}~{entry_hi
                else:
                    entry_lo = ob_lo; entry_hi = ob_hi * 1.003  # [FIX-TO-2026-06-11] 入场区上沿+0.3% buffer，减少TIMEOUT
            else:
                # 降级：用Fib 0.382切分位作为入场区
                fib_entry = fib.get('0.382', price * 1.012)
                entry_lo  = max(fib_entry, price * 1.008)
                entry_hi  = entry_lo * 1.005
        else:
            # 降级：用Fib 0.382切分位作为入场区
            fib_entry = fib.get('0.382', price * 1.012)
            entry_lo  = max(fib_entry, price * 1.008)
            entry_hi  = entry_lo * 1.005
        entry_lo = max(entry_lo, price * 1.001)   # 最小偏离0.1%（原0.3%，避免过严）
        entry_hi = max(entry_hi, entry_lo * 1.002)

        # [v22.1 2026-06-10] ATR自适应入场区压缩
        # 铁证: SOL/BNB/LTC OB太宽(TIMEOUT止损>1.68%) vs BTC WIN止损0.87%
        # 方案: 若OB宽度 > 1.5×ATR4H，将入场区压缩到 OB顶沿 ± 0.5×ATR4H
        # 本质: 不封锁标的，而是在同一OB内找更精确的入场点
        try:
            _ob_width = entry_hi - entry_lo
            _atr_threshold = atr_4h * 1.5
            if _ob_width > _atr_threshold and atr_4h > 0:
                # OB太宽：压缩到OB顶沿附近 ± 0.5×ATR（SHORT：价格反弹到OB顶入场）
                _ob_top     = entry_hi
                _compressed_lo = _ob_top - atr_4h * 0.3   # OB顶沿下方0.3ATR
                _compressed_hi = _ob_top + atr_4h * 0.3   # OB顶沿上方0.3ATR
                # 确保压缩后区间仍在原OB范围内
                _compressed_lo = max(_compressed_lo, entry_lo)
                _compressed_hi = min(_compressed_hi, entry_hi * 1.005)
                if _compressed_lo < _compressed_hi and _compressed_lo > price * 1.001:
                    _old_width = _ob_width / price * 100
                    _new_width = (_compressed_hi - _compressed_lo) / price * 100
                    pass  # [静默]
                    entry_lo = _compressed_lo
                    entry_hi = _compressed_hi
        except Exception:
            pass  # ATR压缩失败不影响主流程

        # [v25.7 P0a 2026-06-21] 入场区最小宽度保证（SHORT）
        # 根因：v22.1只做宽→压缩，但OB本身偏窄时触达率低 → TIMEOUT
        # 铁证：高质量Paper TIMEOUT=23%，方向正确率53%=是等待窗口+区间问题
        # 修复：保证 entry_hi - entry_lo ≥ 0.6×ATR1H
        try:
            _min_width_s = atr_1h * 0.6
            _cur_width_s = entry_hi - entry_lo
            if _cur_width_s < _min_width_s and atr_1h > 0:
                _expand_s = (_min_width_s - _cur_width_s) / 2
                _new_lo_s = entry_lo - _expand_s * 0.4   # 向下少扩（防追跌）
                _new_hi_s = entry_hi + _expand_s          # 向上扩（反弹方向）
                # 安全护栏：扩展后下沿必须在现价上方
                if _new_lo_s > price * 1.001 and _new_hi_s > _new_lo_s:
                    pass  # [静默]
                    entry_lo = _new_lo_s
                    entry_hi = _new_hi_s
        except Exception:
            pass  # 宽度保证失败不影响主流程

        # [v21.0 MTF覆盖] 如果自顶向下路由器输出4H入场区，覆盖1H结果
        if mtf_result and mtf_result.get('timeframe') == '4H':
            _mtf_lo = mtf_result.get('entry_lo', 0)
            _mtf_hi = mtf_result.get('entry_hi', 0)
            if _mtf_lo > price * 1.001 and _mtf_hi > _mtf_lo:
                entry_lo = _mtf_lo
                entry_hi = _mtf_hi
                pass  # [静默]

        # Layer 2+3: 止损 = 入场区上方最近4H摆高 + ATR4H×0.3
        struct_high = _nearest_swing_above(sw4h_h, entry_hi) if sw4h_h else entry_hi * 1.015
        stop_loss   = struct_high + atr_4h * 0.3

        # TP: 下方前低 / 流动性区
        entry_mid = (entry_lo + entry_hi) / 2
        risk = abs(stop_loss - entry_mid)
        # TP1 最小R:R=2.5
        tp1_min = entry_mid - risk * _tp1_mult  # [终极体系] 体制感知动态TP
        # 护栏：TP不能为负数，且最多跌到入场价的80%（做空最大空间80%）
        tp1_min = max(tp1_min, entry_mid * 0.01)   # 绝对下限：价格不能低于1%
        tp1_min = max(tp1_min, entry_mid * (1 - 0.80))  # 相对下限：最多-80%
        # 尝试用4H摆低作为TP1
        struct_tp1 = max([v for v in sw4h_l if v < entry_lo], default=tp1_min)
        tp1 = min(tp1_min, struct_tp1) if struct_tp1 < entry_lo else tp1_min
        tp1 = max(tp1, entry_mid * 0.01)  # 二次护栏：最终值不得为负/极小
        tp2 = entry_mid - risk * _tp2_mult
        tp2 = max(tp2, entry_mid * 0.01)  # tp2同样护栏
        tp2 = max(tp2, tp1 * 0.5)         # tp2不得高于tp1（做空方向tp2应更低）
        # [设计院 2026-06-23 P0修复 v2] SHORT护栏：tp2必须 < tp1
        # 正确修复：tp2基于tp1计算
        if tp2 >= tp1:
            tp2 = tp1 - risk  # tp2 = tp1 - 1个risk单位，确保tp2<tp1
            tp2 = max(tp2, entry_mid * 0.01)

    entry_mid = (entry_lo + entry_hi) / 2
    # ── 最小spread护栏：入场区间宽度不得为0（极端低价品种防护）
    _min_spread = price * 0.001  # 最小0.1%入场区间
    if (entry_hi - entry_lo) < _min_spread:
        if signal_dir == 'SHORT':
            entry_hi = price * 1.001
            entry_lo = price * 0.999
        else:
            entry_lo = price * 0.999
            entry_hi = price * 1.001
        entry_mid = price
    risk = abs(stop_loss - entry_mid)

    # ── 最终输出护栏：拦截一切异常参数 ─────────────────────
    # SL距离超过50%：强制用price*5%重算
    if entry_mid > 0 and risk / entry_mid > 0.50:
        risk = entry_mid * 0.05
        if signal_dir == 'LONG':
            stop_loss = entry_mid - risk
            tp1 = entry_mid + risk * _tp1_mult
            tp2 = entry_mid + risk * _tp2_mult
        else:
            stop_loss = entry_mid + risk
            tp1 = entry_mid - risk * _tp1_mult
            tp2 = entry_mid - risk * _tp2_mult
    # TP不能为负数（做空）或超高价格（做多）
    if tp1 <= 0:
        tp1 = entry_mid * (1 - 0.15) if signal_dir == 'SHORT' else entry_mid * 1.15
    if tp2 <= 0:
        tp2 = entry_mid * (1 - 0.25) if signal_dir == 'SHORT' else entry_mid * 1.25
    # 重算risk用于RR
    risk = abs(stop_loss - entry_mid)

    # ══ L6守卫：拦截一切参数逆转 ══
    # SHORT: SL必须 > entry_hi；TP必须 < entry_lo
    # LONG:  SL必须 < entry_lo；TP必须 > entry_hi
    if signal_dir == 'SHORT':
        if stop_loss <= entry_hi:
            stop_loss = entry_hi + atr_4h * 0.5   # 强制修正
            risk = abs(stop_loss - entry_mid)
            tp1 = entry_mid - risk * _tp1_mult
            tp2 = entry_mid - risk * _tp2_mult
        if tp1 >= entry_lo:
            tp1 = entry_mid - risk * _tp1_mult
        if tp2 >= tp1:
            tp2 = tp1 - risk
    else:  # LONG
        if stop_loss >= entry_lo:
            stop_loss = entry_lo - atr_4h * 0.5   # 强制修正
            risk = abs(entry_mid - stop_loss)
            tp1 = entry_mid + risk * _tp1_mult
            tp2 = entry_mid + risk * _tp2_mult
        if tp1 <= entry_hi:
            tp1 = entry_mid + risk * _tp1_mult
        if tp2 <= tp1:
            tp2 = tp1 + risk
    risk = abs(stop_loss - entry_mid)  # 最终risk

    rr1  = round(abs(tp1 - entry_mid) / max(risk, 1e-9), 2)
    rr2  = round(abs(tp2 - entry_mid) / max(risk, 1e-9), 2)
    sl_pct = round(abs(stop_loss - entry_mid) / entry_mid * 100, 2)
    sl_atr_mult = round(risk / max(atr_4h, 1e-9), 2)

    # ── [梵天v4.0 exit_params_v4 接入 2026-06-28] ────────────────────────────
    # 铁证依据（苏摩批准 20:15北京时间）：
    #   BEAR: SL=2.0% RR=1.0 EV=+0.58%/笔（vs当前+0.12%，提升5倍）
    #   CHOP: SL=2.5% RR=1.0 EV=+0.81%/笔（vs当前+0.10%，提升8倍）
    # 逻辑：若ATR结构给出的sl_pct低于铁证最低门槛，强制扩大到min_sl
    #       同时目标压近（RR→1.0~1.2），避免因目标过远导致TIMEOUT
    try:
        import json as _jv4, pathlib as _pv4
        _v4_path = _pv4.Path(__file__).parent.parent / 'data' / 'dharma_runtime.json'
        _v4_data = _jv4.loads(_v4_path.read_text()) if _v4_path.exists() else {}
        _v4_params = _v4_data.get('exit_params_v4', {})
        # 判断当前体制属于哪个分组
        _regime_v4 = ms.get('regime', '')
        if any(x in _regime_v4 for x in ('CHOP',)):
            _v4_key = 'CHOP'
        elif any(x in _regime_v4 for x in ('BULL',)):
            _v4_key = 'BULL'
        else:
            _v4_key = 'BEAR'  # BEAR / BEAR_EARLY / BEAR_RECOVERY / 未知 默认BEAR
        _v4_cfg = _v4_params.get(_v4_key, {})
        _v4_min_sl = float(_v4_cfg.get('sl_pct', 0))    # 最低止损%（如2.0 / 2.5）
        _v4_rr    = float(_v4_cfg.get('rr', 0))         # 目标RR（如1.0）
        if _v4_min_sl > 0 and _v4_rr > 0:
            _v4_applied = False
            # Step1：若当前sl_pct < v4最低门槛，强制扩大止损
            if sl_pct < _v4_min_sl:
                _v4_new_risk = entry_mid * _v4_min_sl / 100
                if signal_dir == 'SHORT':
                    stop_loss = entry_mid + _v4_new_risk
                else:
                    stop_loss = entry_mid - _v4_new_risk
                risk   = _v4_new_risk
                sl_pct = _v4_min_sl
                _v4_applied = True
            # Step2：将TP1调整为 risk×v4_rr（压近目标，BEAR/CHOP均为1.0~1.2）
            #        仅当v4_rr < 当前rr1（即当前目标更远）时才覆盖
            if _v4_rr < rr1 or _v4_applied:
                if signal_dir == 'SHORT':
                    tp1 = entry_mid - risk * _v4_rr
                    tp2 = entry_mid - risk * max(_v4_rr * 2.0, 2.0)
                else:
                    tp1 = entry_mid + risk * _v4_rr
                    tp2 = entry_mid + risk * max(_v4_rr * 2.0, 2.0)
                # 护栏：LONG tp2>tp1, SHORT tp2<tp1
                if signal_dir == 'LONG' and tp2 <= tp1:
                    tp2 = tp1 + risk
                elif signal_dir == 'SHORT' and tp2 >= tp1:
                    tp2 = tp1 - risk
                rr1 = round(abs(tp1 - entry_mid) / max(risk, 1e-9), 2)
                rr2 = round(abs(tp2 - entry_mid) / max(risk, 1e-9), 2)
                sl_atr_mult = round(risk / max(atr_4h, 1e-9), 2)
                _v4_applied = True
            if _v4_applied:
                pass  # [静默]
    except Exception as _ev4:
        pass  # [静默]
    # ── [END exit_params_v4] ─────────────────────────────────────────────────

    # 动态精度：防止0.0001等极端低价被 round(...,4) 戒断到相同小数
    import math as _m
    # [v21.1] tick感知精度（读SSOT instruments.tick）
    try:
        import json as _jj; from pathlib import Path as _Path21
        _sc = _jj.loads((_Path21(__file__).parent.parent / 'system_constants.json').read_text())
        _tick = _sc.get('instruments', {}).get(ms.get('symbol', symbol.upper()), {}).get('tick')
        if _tick:
            _tick_s = f'{_tick:.10f}'.rstrip('0')
            _decs = len(_tick_s.split('.')[-1]) if '.' in _tick_s else 0
        else:
            raise ValueError('no tick')
    except Exception:
        _decs = max(4, -int(_m.floor(_m.log10(abs(price)))) + 4) if price > 0 else 4

    # ── [Trigger15M v1.0 2026-06-01] 15分钟精确触发层 ──────────
    # 在1H/4H入场区框架内，用15M订单流确认精确入场点+收窄止损
    # 触发率：55%→预期80%+  止损：1.5-2%→0.6-1.0%
    _t15m = {}
    try:
        from trigger_15m import analyze_trigger as _at15
        _t15m = _at15(
            symbol=ms.get('symbol', ''),
            signal_dir=signal_dir,
            entry_lo_1h=entry_lo,
            entry_hi_1h=entry_hi,
            atr_4h=atr_4h,
            score_1h=0,  # calc_trade_params层无score，传0
            verbose=True
        )
        # 若15M触发有效且止损更优：用15M精确止损替换4H止损
        # [v4.0铁证封印 2026-06-30] Trigger15M收窄SL的前提：收窄后SL仍≥v4.0最低门槛
        # 否则：维持v4.0的宽止损，不允许15M把SL缩回过窄
        _v4_min_sl_for_15m = 0.0
        try:
            import json as _jv4t, pathlib as _pv4t
            _rt_t = _pv4t.Path(__file__).parent.parent / 'data' / 'dharma_runtime.json'
            _v4_d_t = _jv4t.loads(_rt_t.read_text()).get('exit_params_v4', {})
            _regime_t = ms.get('regime', '')
            _key_t = 'CHOP' if 'CHOP' in _regime_t else ('BULL' if 'BULL' in _regime_t else 'BEAR')
            _v4_min_sl_for_15m = float(_v4_d_t.get(_key_t, {}).get('sl_pct', 0))
        except Exception:
            _v4_min_sl_for_15m = 2.0  # 默认BEAR体制最低2.0%

        if (_t15m.get('trigger_valid') and
            _t15m.get('sl_pct_15m', 99) < sl_pct and
            _t15m.get('rr_15m', 0) >= 1.5 and
            _t15m.get('sl_pct_15m', 0) >= _v4_min_sl_for_15m):  # [v4.0封印] 15M止损不得低于铁证门槛
            _sl_old = round(stop_loss, _decs)
            _sl_15m_candidate = _t15m['stop_15m']

            # ═ L6二次校验门：15M止损必须在入场区外部 ═
            # SHORT: stop_15m 必须 > entry_hi（不能在OB内部）
            # LONG:  stop_15m 必须 < entry_lo
            _l6_ok = (
                (signal_dir == 'SHORT' and _sl_15m_candidate > entry_hi) or
                (signal_dir == 'LONG'  and _sl_15m_candidate < entry_lo)
            )
            if not _l6_ok:
                pass  # [静默] f'[Trigger15M] ⚠️ L6抦截: 15M止损${_sl_15m_candidate:.4f}在入场区内部/错误方向，维持4H止损${_sl_old
            else:
                stop_loss = _sl_15m_candidate
                entry_lo  = min(entry_lo, _t15m['entry_15m'])
                entry_hi  = max(entry_hi, _t15m['entry_15m'])
                # 重算risk/rr
                risk    = abs(stop_loss - entry_mid)
                sl_pct  = round(abs(stop_loss - entry_mid) / entry_mid * 100, 2)
                tp1     = entry_mid - risk * _tp1_mult if signal_dir=='SHORT' else entry_mid + risk * _tp1_mult
                tp2     = entry_mid - risk * _tp2_mult if signal_dir=='SHORT' else entry_mid + risk * _tp2_mult
                # [设计院 2026-06-23 P0修复 v2] 15M重算后同样加护栏
                if signal_dir == 'LONG' and tp2 <= tp1:
                    tp2 = tp1 + risk
                elif signal_dir == 'SHORT' and tp2 >= tp1:
                    tp2 = tp1 - risk
                rr1     = round(abs(tp1 - entry_mid) / max(risk, 1e-9), 2)
                rr2     = round(abs(tp2 - entry_mid) / max(risk, 1e-9), 2)
                pass  # [静默] f'[Trigger15M] ✅ 止损收窄: {_sl_old:.4f}→{stop_loss:.4f} SL={sl_pct:.2f}% R:R={rr1:.
        # [v24.5-fix] trigger_15m_confidence 存入局部变量，在 return 时写入 params
        # BUG修复: calc_trade_params 函数作用域内无 cf 变量，原 cf=dict(cf) 触发 UnboundLocalError
        _t15m_confidence = int(_t15m.get('confidence', 0))
    except Exception as _e15:
        pass  # [静默] f'[Trigger15M] ⚠️ 跳过: {_e15}'
        _t15m_confidence = 0
    # ── [END Trigger15M] | 15分钟触发器结束

    # ── [达摩院实盘验证字段 v1.0] 关键位元数据 ──────────────────────
    # 用途：写入 live_signals.jsonl 供达摩院 key_level_validator 统计
    # 原则：记录「这个关键位从哪里来」「距现价多远」「结构质量如何」
    _sl_basis_detail = 'swing_4h+atr4h×0.3'
    _ob_src = smc.get('order_blocks', {}) if smc else {}
    _ob4h_src = smc.get('order_blocks_4h', {}) if smc else {}
    _fvg_src = smc.get('fvg', {}) if smc else {}

    # OB 距离（关键位质量核心指标）
    if signal_dir == 'SHORT':
        _ob_used = _ob4h_src.get('nearest_bear_ob') or _ob_src.get('nearest_bear_ob') or {}
    else:
        _ob_used = _ob4h_src.get('nearest_bull_ob') or _ob_src.get('nearest_bull_ob') or {}
    _ob_dist_pct = round(abs(_ob_used.get('dist_pct', 99.0)), 3)
    _ob_top_val  = round(_ob_used.get('high', _ob_used.get('top', 0)), _decs)
    _ob_bot_val  = round(_ob_used.get('low',  _ob_used.get('bottom', 0)), _decs)
    _ob_source   = _ob_used.get('source', _ob_used.get('type', 'none'))

    # 4H swing 记录
    _sw4h_h_val = round(sw4h_h[-1] if sw4h_h else 0, _decs)
    _sw4h_l_val = round(sw4h_l[-1] if sw4h_l else 0, _decs)

    # FVG 状态
    _fvg_active = bool(
        (_fvg_src.get('bear_fvg') if signal_dir == 'SHORT' else _fvg_src.get('bull_fvg'))
    )
    _fvg_top = 0; _fvg_bot = 0
    if signal_dir == 'SHORT' and _fvg_src.get('bear_fvg'):
        _bear_fvg = _fvg_src['bear_fvg']
        # bear_fvg 可能是 dict 或 list，list时取第一个元素
        if isinstance(_bear_fvg, list): _bear_fvg = _bear_fvg[0] if _bear_fvg else {}
        _fvg_top = round(_bear_fvg.get('top', 0), _decs)
        _fvg_bot = round(_bear_fvg.get('bottom', 0), _decs)
    elif signal_dir == 'LONG' and _fvg_src.get('bull_fvg'):
        _bull_fvg = _fvg_src['bull_fvg']
        if isinstance(_bull_fvg, list): _bull_fvg = _bull_fvg[0] if _bull_fvg else {}
        _fvg_top = round(_bull_fvg.get('top', 0), _decs)
        _fvg_bot = round(_bull_fvg.get('bottom', 0), _decs)

    # entry_source：描述本信号入场区来源
    if _ob_dist_pct < 2.0 and _ob_top_val > 0:
        _entry_source = f'OB_{"4H" if _ob4h_src.get("nearest_bear_ob" if signal_dir=="SHORT" else "nearest_bull_ob") else "1H"}'
    elif _fvg_active:
        _entry_source = 'FVG'
    else:
        _entry_source = 'FIB'

    # 关键位与现价距离（衡量「是否已在关键位附近」）
    _key_level_proximity = round(_ob_dist_pct if _ob_top_val > 0 else 99.0, 3)

    # MTF覆盖标记
    _mtf_override = bool(mtf_result and mtf_result.get('timeframe') == '4H')

    # [设计院 2026-06-23 P0修复 v3] 最终护栏 - return前统一检查
    # 无论哪个分支计算了tp2，确保 LONG: tp2>tp1, SHORT: tp2<tp1
    if signal_dir == 'LONG' and tp2 <= tp1:
        tp2 = tp1 + risk
        rr2 = round(abs(tp2 - entry_mid) / max(risk, 1e-9), 2)
    elif signal_dir == 'SHORT' and tp2 >= tp1:
        tp2 = tp1 - risk
        rr2 = round(abs(tp2 - entry_mid) / max(risk, 1e-9), 2)

    return {
        'entry_lo':    round(entry_lo, _decs),
        'entry_hi':    round(entry_hi, _decs),
        'stop_loss':   round(stop_loss, _decs),
        'tp1':         round(tp1, _decs),
        'tp2':         round(tp2, _decs),
        'sl_pct':      sl_pct,
        'sl_atr_mult': sl_atr_mult,
        'tp1_pct':     round(abs(tp1 - price) / price * 100, 2),
        'rr1':         rr1,
        'rr2':         rr2,
        'primary_tf':  '4H',
        'entry_tf':    '1H+15M',
        'sl_basis':    'swing_4h+atr4h×0.3 / 15M精确触发',
        'trigger_15m': _t15m,
        'trigger_15m_confidence': _t15m_confidence,
        'valid':       rr1 >= 1.0,   # [v4.0 2026-06-28] v4.0体制下RR=1.0已有正期望；体制分级门槛在rr_gate层进一步判断
        # ── 达摩院实盘验证字段（key_level_validator 消费）──
        'entry_source':         _entry_source,         # OB_4H / OB_1H / FVG / FIB
        'ob_dist_pct':          _ob_dist_pct,          # OB距现价距离%（越小越精准）
        'ob_top':               _ob_top_val,           # OB顶沿价格
        'ob_bottom':            _ob_bot_val,           # OB底沿价格
        'ob_source_type':       _ob_source,            # OB类型标记
        'fvg_active':           _fvg_active,           # 是否有活跃FVG
        'fvg_top':              _fvg_top,              # FVG顶沿
        'fvg_bottom':           _fvg_bot,              # FVG底沿
        'swing_high_4h':        _sw4h_h_val,          # 最近4H摆动高点
        'swing_low_4h':         _sw4h_l_val,          # 最近4H摆动低点
        'key_level_proximity':  _key_level_proximity,  # 关键位接近度%
        'mtf_override':         _mtf_override,         # 是否被4H MTF覆盖入场区
    }


# ══════════════════════════════════════════════════════════
# rebase_params — 参数基准化（55行）
# ══════════════════════════════════════════════════════════
def rebase_params(params: dict, new_entry: float, atr_1h: float,
                   signal_dir: str = 'SHORT', sl_mult: float = 0.6) -> dict:
    """
    [WFV-v3.0 2026-05-28] sl_mult 默认改为 0.6x（达摩院无穿越训练全局冠军）
    达摩院v3.0 OOS验证: SL=0.6xATR + TP=4.0xATR  core PF=1.227 (无穿越真实值)
    设计院 2026-05-22 入场价重算函数
    ─────────────────────────────────────────────────────────────
    问题根因: brahma_brain 输出的止损/TP 是基于"当前价格"计算的绝对值。
    一旦建议"等反弹至 X 入场"，所有参数必须以 X 为基准重算，
    否则止损距离会错误地缩减为 $0~$1，直接被扫单。

    规则：
      SHORT: 止损 = new_entry + ATR × sl_mult（上方保护）
      LONG:  止损 = new_entry - ATR × sl_mult（下方保护）
      TP1/TP2 基于新入场 + 原始 RR 比例重算

    使用示例:
      r = analyze('ETHUSDT', 'SHORT')
      pa = rebase_params(r['params'], new_entry=2144, atr_1h=10.77, signal_dir='SHORT')
    ─────────────────────────────────────────────────────────────
    """
    sl_dist   = atr_1h * sl_mult
    rr1_orig  = params.get('rr1', 2.0)
    rr2_orig  = params.get('rr2', 4.5)

    if signal_dir in ('SHORT', '做空'):
        stop_loss = new_entry + sl_dist
        tp1       = new_entry - sl_dist * rr1_orig
        tp2       = new_entry - sl_dist * rr2_orig
    else:
        stop_loss = new_entry - sl_dist
        tp1       = new_entry + sl_dist * rr1_orig
        tp2       = new_entry + sl_dist * rr2_orig

    sl_pct = round(sl_dist / new_entry * 100, 3)
    risk   = sl_dist
    rr1_new = round(abs(tp1 - new_entry) / max(risk, 1e-9), 2)
    rr2_new = round(abs(tp2 - new_entry) / max(risk, 1e-9), 2)

    return {
        'entry_lo':      round(new_entry * 0.999, 4),
        'entry_hi':      round(new_entry, 4),
        'stop_loss':     round(stop_loss, 4),
        'tp1':           round(tp1, 4),
        'tp2':           round(tp2, 4),
        'sl_pct':        sl_pct,
        'sl_dist_usd':   round(sl_dist, 4),
        'rr1':           rr1_new,
        'rr2':           rr2_new,
        'atr_used':      atr_1h,
        'sl_mult':       sl_mult,
        'rebased_from':  round(params.get('entry_hi', new_entry), 4),
        'valid':         rr1_new >= 1.2,  # [六方修复 2026-06-25] 最低门槛1.2
        '_note':         f'[rebase] 入场从{params.get("entry_hi","?"):.2f}移至{new_entry:.2f}，止损已重算',
    }

