"""
fangcang_hcme_bridge.py — 方仓增强型HCME桥接引擎
设计院 2026-08-23 苏摩111封印

架构升级：
  旧：HCME(pseudo伪信号2177条) → -9分（无效惩罚，基于假数据）
  新：方仓(真实案例1597条) → 相似度匹配 → 置信度门控 → 精准评分

三层设计：
  Step A: HCME伪数据权重归零（hcme_source=pseudo → 完全忽略）
  Step B: 方仓1597条真实案例相似度匹配
  Step C: 置信度门控（n<3→0分中性 / n3~9→半权重 / n≥10→全权重）

铁证基础：
  方仓BTC=255条 / ETH=280条 / SOL=1062条 = 1597条真实6.5年案例
  字段：compress_bbw_min / rsi_at_end / breakout_direction / future_return_24h
  vs HCME：45670条pseudo合成 WR=46.4% EV=-0.146%（统计上无意义）
"""
import os
import json
import math
from pathlib import Path

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'

# 缓存方仓案例（全局单例，避免重复读盘）
_FANGCANG_CACHE: list = []
_CACHE_LOADED = False


# 30个新币种列表（今日新建）
_NEW_30_SYMBOLS = [
    'xrp','zec','doge','bnb','link','ada','bch','ltc','xlm','xmr',
    'dash','trx','etc','dot','crv','atom','algo','ont','trb','rune',
    'vet','egld','comp','snx','theta','iota','kava','neo','sushi','zil',
]


def _normalize_new_case(d: dict, sym: str) -> dict:
    """
    把新建的fangcang_cases_xxx.json字段统一化为标准格式
    字段映射: min_bb_width(百分比)→compress_bbw_min(小数)/rsi_at_burst→rsi_at_end
    """
    return {
        'symbol':             sym.upper() + 'USDT',
        '_src_sym':           sym.upper(),
        'compress_bbw_min':   float(d.get('min_bb_width', 0) or 0) / 100,
        'rsi_at_end':         float(d.get('rsi_at_burst', 50) or 50),
        'compress_bars':      int(d.get('squeeze_bars', 0) or 0),
        'breakout_direction': 'LONG' if str(d.get('direction', '')).upper() == 'UP'
                              else ('SHORT' if str(d.get('direction', '')).upper() == 'DOWN'
                                    else 'CHOP'),
        'future_return_24h':  float(d.get('future_return_24h', 0) or 0),
        'volume_trend':       'expand' if float(d.get('vol_ratio_peak', 1) or 1) > 1.5 else 'flat',
        'is_genuine_breakout': bool(d.get('is_genuine_breakout', False)),
    }


def _load_fangcang_cases() -> list:
    """加载方仓真实案例库（BTC+ETH+SOL+TradFi+30个新币种），全局缓存"""
    global _FANGCANG_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return _FANGCANG_CACHE

    cases = []
    # BTC
    btc_path = _DATA / 'fangcang_cases_btc.jsonl'
    if btc_path.exists():
        for line in btc_path.read_text().splitlines():
            try:
                d = json.loads(line)
                d['_src_sym'] = 'BTC'
                cases.append(d)
            except Exception:
                pass

    # ETH
    eth_path = _DATA / 'fangcang_cases_eth.jsonl'
    if eth_path.exists():
        for line in eth_path.read_text().splitlines():
            try:
                d = json.loads(line)
                d['_src_sym'] = 'ETH'
                cases.append(d)
            except Exception:
                pass

    # SOL
    sol_path = _DATA / 'fangcang_cases_sol.json'
    if sol_path.exists():
        sol_raw = json.loads(sol_path.read_text())
        if isinstance(sol_raw, list):
            for d in sol_raw:
                cases.append(_normalize_new_case(d, 'sol'))

    # 新墖30个币种（自动扫描 data/fangcang_cases_xxx.json）
    _new_loaded = 0
    for sym in _NEW_30_SYMBOLS:
        fpath = _DATA / f'fangcang_cases_{sym}.json'
        if not fpath.exists():
            continue
        try:
            raw = json.loads(fpath.read_text())
            if isinstance(raw, list):
                for d in raw:
                    cases.append(_normalize_new_case(d, sym))
                _new_loaded += len(raw)
        except Exception:
            pass

    import logging as _lg
    _lg.getLogger('brahma.fangcang').info(
        f'[fangcang_hcme_bridge] 加载完成: 总{len(cases)}条案例 '
        f'(新墖30币种贡献{_new_loaded}条)'
    )

    _FANGCANG_CACHE = cases
    _CACHE_LOADED = True
    return cases


def fangcang_context_match(
    symbol: str,
    current_bbw: float,
    current_rsi: float,
    current_regime: str,
    signal_dir: str,
    current_bars: int = 0,
) -> dict:
    """
    方仓相似度匹配：在真实案例库中找相似压缩案例，输出方向概率评分

    参数：
      symbol       : 当前标的（用于优先同标的案例）
      current_bbw  : 当前布林带宽度（小数，如0.0084=0.84%）
      current_rsi  : 当前RSI_1H
      current_regime: 当前体制（BULL_TREND等）
      signal_dir   : 信号方向（LONG/SHORT）
      current_bars : 当前压缩持续bars（可选）

    返回：
      {
        'score_adj': float,     # 评分调整（-10~+10）
        'confidence': str,      # HIGH/MED/LOW/NONE
        'n_similar': int,       # 相似案例数
        'long_pct': float,      # 历史做多突破概率
        'short_pct': float,     # 历史做空突破概率
        'chop_pct': float,      # 历史横盘概率
        'source': str,          # 数据来源说明
        'hcme_source': str,     # 'real_fangcang' | 'no_match'
      }
    """
    cases = _load_fangcang_cases()
    if not cases:
        return {
            'score_adj': 0, 'confidence': 'NONE', 'n_similar': 0,
            'long_pct': 0, 'short_pct': 0, 'chop_pct': 0,
            'source': 'no_data', 'hcme_source': 'no_data'
        }

    # ── 相似度匹配（三维：BBW + RSI + 体制） ──
    similar = []
    for c in cases:
        c_bbw = float(c.get('compress_bbw_min', 0) or 0)
        c_rsi = float(c.get('rsi_at_end', 50) or 50)
        c_regime = str(c.get('regime_guess', '') or '').lower()

        # BBW相似度：±30%容差（压缩程度相近）
        if c_bbw <= 0:
            continue
        bbw_ratio = abs(c_bbw - current_bbw) / max(current_bbw, 1e-9)
        if bbw_ratio > 0.35:
            continue

        # RSI相似度：±18容差
        if abs(c_rsi - current_rsi) > 18:
            continue

        # [设计院 2026-08-25 苏摩111] 体制过滤升级：精确与当前体制匹配
        # 原来用模糊字符串，现在用梵天标准体制分组
        _REGIME_GROUP = {
            'BULL': {'BULL_TREND','BULL_EARLY','BULL_PEAK','BULL_CORRECTION','bull','bullish','trending'},
            'BEAR': {'BEAR_TREND','BEAR_EARLY','BEAR_CRASH','BEAR_RECOVERY','bear','bearish','downtrend'},
            'CHOP': {'CHOP_MID','CHOP_HIGH','CHOP_LOW','BREAKOUT','ranging','chop',''},
        }
        cur_grp = next((g for g,s in _REGIME_GROUP.items() if current_regime.upper() in {x.upper() for x in s}), 'CHOP')
        c_grp   = next((g for g,s in _REGIME_GROUP.items() if c_regime in {x.lower() for x in s}), 'CHOP')

        regime_match = (cur_grp == c_grp)
        if not regime_match:
            # 体制不匹配：BBW+RSI非常相近时降权计入，否则跳过
            if bbw_ratio < 0.15 and abs(c_rsi - current_rsi) < 8:
                c['_regime_penalty'] = 0.5
            else:
                continue

        # [设计院 2026-08-25] 时间衰减权重：近期案例更可信
        import time as _t
        _case_ts = c.get('compress_end_ts', 0) or 0
        if _case_ts > 1e10: _case_ts /= 1000  # ms转s
        _age_years = (_t.time() - _case_ts) / (365.25 * 86400) if _case_ts > 0 else 3.0
        _time_weight = 2.0 if _age_years < 1 else (1.0 if _age_years < 2 else 0.5)

        # 计算综合相似度分数
        bbw_score = 1.0 - bbw_ratio / 0.35
        rsi_score = 1.0 - abs(c_rsi - current_rsi) / 18
        sim_score = (bbw_score * 0.6 + rsi_score * 0.4) * _time_weight * c.get('_regime_penalty', 1.0)
        c['_sim_score'] = sim_score
        c['_time_weight'] = _time_weight
        similar.append(c)

    # 按相似度排序，取Top20
    similar.sort(key=lambda x: x.get('_sim_score', 0), reverse=True)
    similar = similar[:20]
    n = len(similar)

    if n == 0:
        return {
            'score_adj': 0, 'confidence': 'NONE', 'n_similar': 0,
            'long_pct': 0, 'short_pct': 0, 'chop_pct': 0,
            'source': 'no_match', 'hcme_source': 'no_match'
        }

    # ── 方向概率统计 ──
    long_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'LONG')
    short_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'SHORT')
    chop_n = sum(1 for c in similar if c.get('breakout_direction', '') == 'CHOP')
    long_pct = long_n / n
    short_pct = short_n / n
    chop_pct = chop_n / n

    # ── Step C：置信度门控 ──
    if n < 3:
        confidence = 'NONE'
        weight = 0.0    # 样本不足 → 完全中性
    elif n < 5:
        confidence = 'LOW'
        weight = 0.3    # 低置信
    elif n < 10:
        confidence = 'MED'
        weight = 0.6    # 半权重
    else:
        confidence = 'HIGH'
        weight = 1.0    # 全权重

    # ── 评分计算（基线50%，偏离基线 × 最大±12分） ──
    MAX_ADJ = 12.0
    if signal_dir == 'LONG':
        # 做多方向：历史多头突破率越高越加分
        raw_adj = (long_pct - 0.40) * MAX_ADJ / 0.40  # 40%基线（压缩多为CHOP）
        raw_adj = max(-MAX_ADJ, min(MAX_ADJ, raw_adj))
    elif signal_dir == 'SHORT':
        raw_adj = (short_pct - 0.30) * MAX_ADJ / 0.30  # 空头基线更低
        raw_adj = max(-MAX_ADJ, min(MAX_ADJ, raw_adj))
    else:
        raw_adj = 0.0

    score_adj = round(raw_adj * weight, 1)

    return {
        'score_adj': score_adj,
        'confidence': confidence,
        'n_similar': n,
        'long_pct': round(long_pct, 3),
        'short_pct': round(short_pct, 3),
        'chop_pct': round(chop_pct, 3),
        'source': f'fangcang_real_{n}cases',
        'hcme_source': 'real_fangcang',
    }


def get_fangcang_hcme_score(
    symbol: str,
    ms: dict,
    signal_dir: str,
) -> dict:
    """
    主入口：替换旧HCME调用
    从ms提取BBW/RSI/regime，调用方仓相似度匹配，返回评分

    返回格式与旧HCME兼容：
      {'hcme_score_adj': float, 'context_summary': str, 'hcme_source': str}
    """
    # 提取当前市场参数
    try:
        bbw = float((ms.get('bb') or ms.get('momentum', {}).get('bb', {})).get('width', 0) or 0)
        rsi = float(ms.get('rsi_1h') or ms.get('momentum', {}).get('rsi_1h', 50) or 50)
        regime = str(ms.get('regime', '') or '')
    except Exception:
        bbw, rsi, regime = 0.01, 50.0, ''

    # BBW为0时用默认值（非压缩状态，评分中性）
    if bbw <= 0:
        return {
            'hcme_score_adj': 0,
            'context_summary': 'BBW数据缺失，HCME中性',
            'hcme_source': 'no_bbw_data',
        }

    result = fangcang_context_match(
        symbol=symbol,
        current_bbw=bbw,
        current_rsi=rsi,
        current_regime=regime,
        signal_dir=signal_dir,
    )

    score_adj = result['score_adj']
    n = result['n_similar']
    conf = result['confidence']
    long_pct = result['long_pct']
    short_pct = result['short_pct']

    context_summary = (
        f"方仓匹配n={n}({conf}) "
        f"多={long_pct*100:.0f}%空={short_pct*100:.0f}% "
        f"adj={score_adj:+.1f}"
    )

    return {
        'hcme_score_adj': score_adj,
        'context_summary': context_summary,
        'hcme_source': result['hcme_source'],
        'n_similar': n,
        'confidence': conf,
        'long_pct': long_pct,
        'short_pct': short_pct,
    }


# ════════════════════════════════════════════════════════════════════
# P1-2: 方仓自学习反馈回路（设计院 2026-08-25 苏摩111）
# 信号结算后 → 对比方仓预测vs实际 → 更新案例权重
# ════════════════════════════════════════════════════════════════════
import json as _json
import time as _time
from pathlib import Path as _Path

_WEIGHT_FILE = _Path(__file__).parent.parent / 'data' / 'fangcang_case_weights.json'


def _load_weights() -> dict:
    try:
        if _WEIGHT_FILE.exists():
            return _json.loads(_WEIGHT_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_weights(w: dict):
    try:
        _WEIGHT_FILE.parent.mkdir(exist_ok=True)
        _WEIGHT_FILE.write_text(_json.dumps(w, ensure_ascii=False))
    except Exception:
        pass


def feedback_settlement(symbol: str, signal_dir: str, predicted_hint: str,
                        actual_direction: str, pnl_pct: float) -> dict:
    """
    信号结算后调用：对比方仓预测 vs 实际结果，更新案例权重。

    参数：
      symbol:           交易标的
      signal_dir:       梵天信号方向（LONG/SHORT）
      predicted_hint:   方仓预测（LONG_BIAS/SHORT_BIAS/NEUTRAL）
      actual_direction: 实际市场方向（LONG=盈利/SHORT=亏损结算）
      pnl_pct:          实际PnL百分比

    逻辑：
      预测与实际一致 → 相关案例权重 × 1.1（正确案例加权）
      预测与实际相反 → 相关案例权重 × 0.9（错误案例降权）
      权重上限2.0，下限0.1（避免极端）
    """
    weights = _load_weights()
    key = f'{symbol}:{signal_dir}'

    predicted_correct = (
        (predicted_hint == 'LONG_BIAS' and actual_direction == 'LONG' and pnl_pct > 0) or
        (predicted_hint == 'SHORT_BIAS' and actual_direction == 'SHORT' and pnl_pct > 0)
    )

    current_weight = weights.get(key, {}).get('weight', 1.0)
    if predicted_correct:
        new_weight = min(2.0, current_weight * 1.1)
        outcome = 'CORRECT'
    else:
        new_weight = max(0.1, current_weight * 0.9)
        outcome = 'WRONG'

    weights[key] = {
        'weight':    round(new_weight, 4),
        'outcome':   outcome,
        'pnl_pct':   pnl_pct,
        'ts':        _time.time(),
        'predicted': predicted_hint,
        'actual':    actual_direction,
        'count':     weights.get(key, {}).get('count', 0) + 1,
    }
    _save_weights(weights)

    return {
        'ok':         True,
        'key':        key,
        'outcome':    outcome,
        'old_weight': current_weight,
        'new_weight': new_weight,
    }


def get_feedback_stats() -> dict:
    """获取方仓自学习统计"""
    weights = _load_weights()
    if not weights:
        return {'total': 0, 'correct': 0, 'wrong': 0, 'wr': 0.0}
    correct = sum(1 for v in weights.values() if v.get('outcome') == 'CORRECT')
    wrong   = sum(1 for v in weights.values() if v.get('outcome') == 'WRONG')
    total   = correct + wrong
    return {
        'total':   total,
        'correct': correct,
        'wrong':   wrong,
        'wr':      round(correct / total * 100, 1) if total > 0 else 0.0,
        'avg_weight': round(sum(v.get('weight',1) for v in weights.values()) / max(len(weights),1), 3),
    }
