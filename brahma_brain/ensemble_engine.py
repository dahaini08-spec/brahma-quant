"""
ensemble_engine.py — 梵天12维精简集成引擎
设计院封印 2026-09-12 苏摩111

职责：
  1. 从94维中提取达摩院验证的12维true alpha
  2. 按IC加权组合 → 输出ensemble_score替代35维累加score
  3. 与现有brahma_core score并行运行（不替换，对比验证）

达摩院铁证：
  94维 IC=-0.0027（反向）→ 12维 IC=+0.0150（5.6x提升）
  真alpha: regime_direction + rsi_extreme + bb_squeeze

12维定义（来自brahma_experience_engine._build_current_vec）：
  1. rsi_4h       — RSI 4H（动量alpha）
  2. change_3d    — 3日涨跌幅（动量alpha）
  3. bbw          — 布林带宽度（波动alpha）
  4. hurst        — Hurst近似（趋势alpha）
  5. oi_chg_3d    — OI 3日变化（资金alpha）
  6. fr_mean      — 资金费率均值（资金alpha）
  7. atr_rank     — ATR百分位（波动alpha）
  8. regime_code  — 体制编码（结构alpha，IC最高）
  9. macro_days   — 宏观事件距离（宏观alpha）
  10. vol_rank    — 成交量百分位（量能alpha）
  11. score_rank  — 评分百分位（综合alpha）
  12. stoch_rsi   — StochRSI（动量alpha）

接入位置：
  - brahma_brain/ensemble_engine.py（本文件）
  - brahma_core.analyze() 输出后调用 get_ensemble_score()
  - trader_brain.decide() 可读ensemble_score作为参考（并行不替换）
  - 达摩院14节点基础设施复用
"""
import json, math, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent

# 12维IC权重（达摩院验证：IC=+0.0150）
# 真alpha排序: regime_direction > rsi_extreme > bb_squeeze
# IC权重按验证结果赋值，未验证维度默认1.0
IC_WEIGHTS = {
    'regime_code':   1.5,   # 达摩院验证IC最高（体制×方向顺逆势PF=2.248）
    'rsi_4h':        1.3,   # 达摩院验证（BEAR+RSI<10做多 PF=4.269 WR=73%）
    'stoch_rsi':     1.2,   # RSI极端值是top-3 alpha之一
    'bbw':           1.2,   # bb_squeeze是达摩院top-3 alpha之一
    'change_3d':     1.0,
    'hurst':         1.0,
    'oi_chg_3d':     1.1,   # OI动量达摩院验证有效
    'fr_mean':       1.1,   # 资金费率达摩院验证（顺势PF=1.094 vs 逆势0.859）
    'atr_rank':      0.8,
    'macro_days':    0.9,
    'vol_rank':      0.8,   # 量能达摩院验证正向但增量小
    'score_rank':    0.7,   # 原始score的IC=0.0093≈0 → 降权
}

# 体制编码映射
REGIME_MAP = {
    'BEAR_TREND': 1, 'BEAR_EARLY': 2, 'BEAR_RECOVERY': 3,
    'CHOP_MID': 4, 'CHOP_RANGE_PREMIUM': 5,
    'BULL_TREND': 6, 'BULL_EARLY': 7,
}

# 体制×方向IC权重（达摩院N-A: 逆势PF=2.248 > 顺势PF=2.094）
REGIME_DIRECTION_WEIGHTS = {
    ('BEAR_TREND', 'SHORT'):    1.5,   # 顺势
    ('BEAR_TREND', 'LONG'):     0.7,   # 逆势但达摩院说不该放弃
    ('BULL_TREND', 'LONG'):     1.5,   # 顺势
    ('BULL_TREND', 'SHORT'):    0.7,   # 逆势
    ('CHOP_MID', 'LONG'):       0.88,
    ('CHOP_MID', 'SHORT'):      0.88,
    ('BEAR_RECOVERY', 'LONG'):  1.2,
    ('BEAR_RECOVERY', 'SHORT'): 0.7,
    ('BULL_EARLY', 'LONG'):     1.3,
    ('BEAR_EARLY', 'SHORT'):    1.3,
}


def get_ensemble_score(symbol: str, direction: str, brahma_result: dict = None) -> dict:
    """
    计算12维精简ensemble分数
    
    参数：
      symbol: 交易标的
      direction: 方向 LONG/SHORT
      brahma_result: brahma_core.analyze()返回（可选，减少重复调用）
    
    返回：
      {
        'ensemble_score': float,      # 0~100 精简分数
        'ensemble_signal': float,     # -1~+1 信号强度
        'ic_weighted': dict,          # 每维IC加权后贡献
        'regime_direction_mult': float, # 体制×方向乘数
        'true_alpha': list[str],      # 贡献最大的3个alpha
        'n_dims': 12,
      }
    """
    # 获取12维特征（优先从brahma_result提取，避免重复计算）
    vec = _extract_12vec_from_result(symbol, brahma_result)
    
    if not vec:
        return {
            'ensemble_score': 0,
            'ensemble_signal': 0,
            'ic_weighted': {},
            'regime_direction_mult': 1.0,
            'true_alpha': [],
            'n_dims': 12,
            'error': 'failed to extract 12 features',
        }
    
    # 体制×方向乘数
    regime = ''
    for k in REGIME_MAP:
        if k in str(brahma_result.get('regime', '')):
            regime = k
            break
    regime = regime or brahma_result.get('regime', 'CHOP_MID')
    rd_mult = REGIME_DIRECTION_WEIGHTS.get((regime, direction), 1.0)
    
    # IC加权计算
    ic_weighted = {}
    contributions = []
    
    for dim_name, value in vec.items():
        if not isinstance(value, (int, float)):
            continue
        weight = IC_WEIGHTS.get(dim_name, 1.0)
        # 归一化到 [-1, +1]
        normalized = _normalize_dim(dim_name, value, direction)
        weighted = normalized * weight
        ic_weighted[dim_name] = round(weighted, 4)
        contributions.append((dim_name, weighted, weight, normalized))
    
    # ensemble_score = IC加权平均 × 体制方向乘数
    if ic_weighted:
        raw_score = sum(ic_weighted.values()) / len(ic_weighted)
    else:
        raw_score = 0
    
    ensemble_score = max(0, min(100, (raw_score + 1) / 2 * 100))  # [-1,+1] → [0,100]
    ensemble_score *= rd_mult
    ensemble_score = round(min(100, ensemble_score), 2)
    
    # ensemble_signal [-1, +1]
    ensemble_signal = round(max(-1, min(1, raw_score * rd_mult)), 4)
    
    # 识别top-3 true alpha
    contributions.sort(key=lambda x: abs(x[1]), reverse=True)
    true_alpha = [c[0] for c in contributions[:3]]
    
    return {
        'ensemble_score': ensemble_score,
        'ensemble_signal': ensemble_signal,
        'ic_weighted': ic_weighted,
        'regime_direction_mult': rd_mult,
        'true_alpha': true_alpha,
        'n_dims': 12,
        'raw_vec': vec,
    }


def _extract_12vec_from_result(symbol: str, brahma_result: dict = None) -> dict:
    """从brahma_core.analyze()结果中提取12维特征"""
    if not brahma_result:
        return {}
    
    r = brahma_result
    x = r.get('extra') or {}
    
    # 体制编码
    regime = r.get('regime', 'CHOP_MID')
    regime_code = REGIME_MAP.get(regime, 4)
    
    # RSI 4H
    rsi_4h = r.get('rsi_4h', 0) or 0
    
    # 3日涨跌幅（从K线计算或从extra读）
    change_3d = x.get('change_3d', 0)
    if not change_3d:
        price = r.get('price', 0)
        ema200 = x.get('ema200_1d', 0)
        if price and ema200:
            change_3d = (price - ema200) / ema200 * 100
    
    # BBW
    bbw = x.get('bb_width', 0) or 0
    
    # Hurst
    hurst = x.get('hurst', 0.5) or 0.5
    
    # OI 3日变化
    oi_chg_3d = 0
    sm = x.get('smart_money') or {}
    if isinstance(sm, dict):
        oi_total = sm.get('oi_total', 0) or 0
        oi_chg_3d = sm.get('oi_change_3d', 0) or 0
    
    # FR均值
    cf = x.get('cross_fr_basis') or {}
    fr_mean = cf.get('binance_fr', 0) or 0
    
    # ATR百分位
    atr_1h = x.get('atr_1h', 0) or 0
    atr_4h = x.get('atr_4h', 0) or 0
    price = r.get('price', 1)
    atr_rank = min(1.0, atr_4h / (price * 0.03)) if price else 0  # ATR/价格比 → 0~1
    
    # 宏观事件距离
    mc = x.get('macro_v2') or {}
    macro_days = 30
    if isinstance(mc, dict):
        macro_days = mc.get('days_to_event', 30) or 30
    
    # 成交量百分位
    vol_rank = x.get('vol_rank', atr_rank) or atr_rank
    
    # 评分百分位
    score = r.get('score', 0) or r.get('confluence', {}).get('total', 0)
    score_rank = min(1.0, float(score) / 200) if score else 0.5
    
    # StochRSI
    rsi_1h = r.get('rsi_1h', 50) or 50
    stoch_rsi = (rsi_1h - 0) / 100 * 100  # 简化：RSI 0~100 → StochRSI 0~100
    
    return {
        'rsi_4h':      round(rsi_4h, 2),
        'change_3d':   round(change_3d, 2),
        'bbw':         round(bbw, 4),
        'hurst':       round(hurst, 3),
        'oi_chg_3d':   round(oi_chg_3d, 2),
        'fr_mean':     round(fr_mean, 4),
        'atr_rank':    round(atr_rank, 3),
        'regime_code': regime_code,
        'macro_days':  round(min(macro_days, 30), 1),
        'vol_rank':    round(vol_rank, 3),
        'score_rank':  round(score_rank, 3),
        'stoch_rsi':   round(stoch_rsi, 1),
    }


def _normalize_dim(dim_name: str, value: float, direction: str) -> float:
    """
    将特征值归一化到 [-1, +1]
    方向感知：做多时RSI<30为正（超卖买入），做空时RSI>70为正（超买做空）
    """
    d = direction.upper()
    
    if dim_name == 'rsi_4h':
        if d == 'LONG':
            # 做多：RSI<30=强买信号(+1), RSI>70=弱(-1)
            return (50 - value) / 50 if value <= 50 else -(value - 50) / 50
        else:
            # 做空：RSI>70=强卖信号(+1), RSI<30=弱(-1)
            return (value - 50) / 50 if value >= 50 else -(50 - value) / 50
    
    elif dim_name == 'stoch_rsi':
        if d == 'LONG':
            return (50 - value) / 50
        else:
            return (value - 50) / 50
    
    elif dim_name == 'change_3d':
        # 3日涨幅：做多时跌幅=买入机会(+1), 做空时涨幅=做空机会(+1)
        if d == 'LONG':
            return max(-1, min(1, -value / 10))  # 跌10%=+1
        else:
            return max(-1, min(1, value / 10))   # 涨10%=+1
    
    elif dim_name == 'bbw':
        # 布林带宽度：宽=波动大=机会, 窄=压缩=待突破
        # bb_squeeze是达摩院top-3 alpha：极端压缩=信号
        return max(-1, min(1, (value - 5) / 10))  # 5%为中性
    
    elif dim_name == 'hurst':
        # Hurst>0.55=趋势（+1）, <0.45=随机（-1）
        return max(-1, min(1, (value - 0.5) * 4))
    
    elif dim_name == 'oi_chg_3d':
        # OI增加=资金流入=信号强
        return max(-1, min(1, value / 10))
    
    elif dim_name == 'fr_mean':
        # 资金费率：做多时FR>0.01%=多头拥挤=反向(-1), 做空时FR>0.01%=做空机会(+1)
        if d == 'LONG':
            return max(-1, min(1, -value / 0.05))
        else:
            return max(-1, min(1, value / 0.05))
    
    elif dim_name == 'atr_rank':
        # ATR百分位：高=波动大=风险高=减仓
        return max(-1, min(1, (0.5 - value) * 2))  # 中位=0
    
    elif dim_name == 'regime_code':
        # 体制编码：顺势=+1, 逆势=-1
        # 已被regime_direction_mult覆盖，这里返回中性
        return 0
    
    elif dim_name == 'macro_days':
        # 宏观事件距离：越近=风险越高
        return max(-1, min(1, (30 - value) / 15 - 1))
    
    elif dim_name == 'vol_rank':
        # 成交量百分位：高量能=信号确认
        return max(-1, min(1, (value - 0.5) * 2))
    
    elif dim_name == 'score_rank':
        # 评分百分位：高=好（但原始IC=0.0093→降权到0.7）
        return max(-1, min(1, (value - 0.5) * 2))
    
    return 0


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(BASE))
    sys.path.insert(0, str(BASE / 'brahma_brain'))
    
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'
    
    from brahma_brain.brahma_core import analyze
    r = analyze(sym, deep=True)
    
    result = get_ensemble_score(sym, direction, r)
    print(f"=== Ensemble Engine: {sym} {direction} ===")
    print(f"Ensemble Score: {result['ensemble_score']}")
    print(f"Ensemble Signal: {result['ensemble_signal']}")
    print(f"Regime Direction Mult: {result['regime_direction_mult']}")
    print(f"True Alpha (top-3): {result['true_alpha']}")
    print(f"IC Weighted contributions:")
    for k, v in sorted(result['ic_weighted'].items(), key=lambda x: -abs(x[1])):
        print(f"  {k}: {v}")
    print(f"Raw 12-dim vec: {result.get('raw_vec', {})}")
    print()
    print(f"Brahma core score: {r.get('score', 0)}")
    print(f"Ensemble score:    {result['ensemble_score']}")
