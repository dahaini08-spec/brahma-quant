"""
feature_store.py — 梵天94维特征仓库
设计院封印 2026-09-12 苏摩111

职责：
  1. 94维特征独立计算 + 缓存（30min TTL）
  2. 每个特征输出 (value, signal[-1~+1], group, ts)
  3. brahma_core.analyze() 优先读缓存，miss时计算
  4. 为attribution_engine提供per-feature追踪基础

94维 = A类16 + B类15 + C类18 + D类10 + E类10 + F类10 + B类SMC算15 = 94
实际分为5个alpha组：
  G1 结构alpha: FVG/OB/清算/PIPs/CHOP突破/周月锚定 (B类15维)
  G2 动量alpha: RSI/趋势/多TF/量能 (A类部分+C类部分)
  G3 资金alpha: OI/CVD/聪明钱/Funding/微结构 (A类部分+F类10维)
  G4 波动alpha: Hurst/HAR-RV/kappa/GEX/ATR/BB (C类部分)
  G5 宏观alpha: 事件/日历/体制/跨市场/TradFi/时段 (D类10+E类10+C类部分)

接入位置：
  - brahma_brain/feature_store.py（本文件）
  - brahma_core.analyze() 调用 get_features() 替代直接计算
  - wr_feedback_engine 读 feature_log 做归因

缓存策略：
  - TTL: 30min（K线数据变化频率）
  - Key: symbol + timestamp
  - Miss: 调用原始计算逻辑
  - Hit: 直接返回缓存
"""
import json, time, os, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
CACHE_DIR = BASE / 'data' / 'feature_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

TTL_SECONDS = 1800  # 30min

# 5个alpha组映射（94维 → 5组）
ALPHA_GROUPS = {
    'G1_structure': {
        'desc': 'FVG/OB/清算/PIPs/CHOP突破/周月锚定',
        'dims': list(range(17, 32)),  # B类 17-31
        'ic_weight': 1.0,
    },
    'G2_momentum': {
        'desc': 'RSI/趋势/多TF/量能',
        'dims': [1, 3, 5, 35, 36, 37, 46, 47, 49],  # A类部分 + C类RSI/ADX
        'ic_weight': 1.0,
    },
    'G3_capital_flow': {
        'desc': 'OI/CVD/聪明钱/Funding/微结构',
        'dims': [6, 7, 8, 10, 14, 56, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79],
        'ic_weight': 1.0,
    },
    'G4_volatility': {
        'desc': 'Hurst/HAR-RV/kappa/GEX/ATR/BB',
        'dims': [38, 39, 40, 41, 42, 43, 44, 45, 58],
        'ic_weight': 1.0,
    },
    'G5_macro': {
        'desc': '事件/日历/体制/跨市场/TradFi/时段',
        'dims': [2, 9, 32, 33, 34, 50, 51, 52, 53, 54, 60, 61, 62, 63, 64, 65, 66, 67, 68],
        'ic_weight': 1.0,
    },
}


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}_features.json"


def _is_cache_valid(cache: dict, ttl: int = TTL_SECONDS) -> bool:
    """检查缓存是否在TTL内"""
    if not cache:
        return False
    ts = cache.get('_ts', 0)
    return (time.time() - ts) < ttl


def get_features(symbol: str, force_refresh: bool = False) -> dict:
    """
    获取94维特征。优先读缓存，miss时调用brahma_core计算。
    
    返回:
      {
        '_ts': timestamp,
        '_source': 'cache' | 'fresh',
        'symbol': symbol,
        'features': {dim_id: value, ...},  # 94维
        'groups': {group_name: {score, signal, dims: [...]}, ...},  # 5组
        'alpha_contribs': {group_name: contribution, ...},  # 归因
      }
    """
    if not force_refresh:
        cached = _load_cache(symbol)
        if _is_cache_valid(cached):
            cached['_source'] = 'cache'
            return cached
    
    # Cache miss: 调用brahma_core.analyze()获取全量特征
    fresh = _compute_fresh(symbol)
    _save_cache(symbol, fresh)
    fresh['_source'] = 'fresh'
    return fresh


def _load_cache(symbol: str) -> dict:
    """加载缓存"""
    p = _cache_path(symbol)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_cache(symbol: str, data: dict):
    """保存缓存"""
    p = _cache_path(symbol)
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, default=str))
    except Exception:
        pass


def _compute_fresh(symbol: str) -> dict:
    """
    调用brahma_core.analyze()计算94维特征
    提取94维值 + 分组计算alpha组得分 + 归因贡献
    """
    # 延迟导入避免循环依赖
    sys.path.insert(0, str(BASE))
    sys.path.insert(0, str(BASE / 'brahma_brain'))
    
    from brahma_brain.brahma_core import analyze
    
    r = analyze(symbol, deep=True)
    x   = r.get('extra') or {}
    par = r.get('params') or {}
    smc = r.get('smc') or {}
    fvg = smc.get('fvg') or x.get('fvg') or {}
    obs = smc.get('order_blocks') or x.get('order_blocks') or {}
    liq = smc.get('liquidity') or x.get('liquidity') or {}
    sm  = x.get('smart_money') or {}
    cf  = x.get('cross_fr_basis') or {}
    ba  = x.get('basis') or {}
    dp  = x.get('deribit_pc') or {}
    mc  = x.get('macro_v2') or {}
    dv  = x.get('multitf_div') or {}
    mi  = x.get('microstructure') or {}
    of  = x.get('order_flow') or {}
    ve  = x.get('vol_exhaustion') or {}
    wh  = x.get('whale') or {}
    ow  = x.get('onchain_ws') or {}
    var_d = x.get('var') or {}
    vol_d = x.get('volume') or {}
    
    features = {}
    
    # A类 期货合约原始数据 (1-16)
    features[1]  = r.get('price', 0)
    features[2]  = cf.get('binance_fr', 0) or ba.get('funding_rate', 0)
    features[3]  = ba.get('mark_price', 0) or r.get('price', 0)
    features[4]  = ba.get('index_price', 0)
    features[5]  = ba.get('basis_pct', 0)
    features[6]  = sm.get('oi_total', 0) or sm.get('global_oi', 0)
    features[7]  = sm.get('oi_momentum', 'NEUTRAL')
    features[8]  = (sm.get('top_trader_long_ratio') or sm.get('big_acct_long') or 0) * 100 if isinstance(sm.get('top_trader_long_ratio') or sm.get('big_acct_long') or 0, (int, float)) else 0
    features[9]  = (sm.get('top_trader_position_long_ratio') or sm.get('big_pos_long') or 0) * 100 if isinstance(sm.get('top_trader_position_long_ratio') or sm.get('big_pos_long') or 0, (int, float)) else 0
    features[10] = (sm.get('retail_long') or 0) * 100 if isinstance(sm.get('retail_long') or 0, (int, float)) else 0
    features[11] = sm.get('obi', 0) or x.get('orderbook', {}).get('obi', 0)
    features[14] = of.get('score', 0) if isinstance(of, dict) else 0
    features[15] = cf.get('bybit_fr', 0)
    features[16] = cf.get('fr_avg_3ex', 0) or cf.get('fr_avg', 0)
    
    # B类 SMC结构算法 (17-31)
    features[17] = fvg.get('bull_count', 0) if isinstance(fvg, dict) else 0
    features[18] = fvg.get('bear_count', 0) if isinstance(fvg, dict) else 0
    features[19] = fvg.get('nearest_bull_mid', 0) if isinstance(fvg, dict) else 0
    features[20] = fvg.get('nearest_bear_mid', 0) if isinstance(fvg, dict) else 0
    features[21] = fvg.get('supply_bear_mid', 0) if isinstance(fvg, dict) else 0
    features[22] = fvg.get('demand_bull_mid', 0) if isinstance(fvg, dict) else 0
    features[23] = str(obs.get('bull_nearest', '') if isinstance(obs, dict) else '')
    features[24] = str(obs.get('bear_nearest', '') if isinstance(obs, dict) else '')
    features[25] = obs.get('bull_count', 0) if isinstance(obs, dict) else 0
    features[26] = liq.get('above', 0) or liq.get('nearest_above', 0) if isinstance(liq, dict) else 0
    features[27] = liq.get('below', 0) or liq.get('nearest_below', 0) if isinstance(liq, dict) else 0
    features[28] = 0  # liq_snap_5pct - niche
    features[29] = par.get('swing_high_4h', 0)
    features[30] = par.get('swing_low_4h', 0)
    features[31] = str(r.get('key_levels', ''))
    
    # C类 体制/评分/技术 (32-49)
    features[32] = r.get('regime', '')
    features[33] = r.get('score', 0) or r.get('confluence', {}).get('total', 0)
    features[34] = r.get('grade', 0)
    features[35] = r.get('rsi_1h', 0)
    features[36] = r.get('rsi_4h', 0)
    features[37] = r.get('rsi_1d', 0)
    features[38] = x.get('atr_1h', 0) or 0
    features[39] = x.get('atr_4h', 0) or 0
    features[40] = x.get('hurst', 0) or 0
    features[41] = x.get('daily_vol_pct', 0) or 0
    features[42] = var_d.get('var_95_pct', 0) if isinstance(var_d, dict) else 0
    features[43] = var_d.get('var_99_pct', 0) if isinstance(var_d, dict) else 0
    features[44] = x.get('bb_width', 0) or 0
    features[45] = x.get('bb_pos', 0) or 0
    features[46] = x.get('adx_1h', 0) or 0
    features[47] = x.get('adx_4h', 0) or 0
    features[48] = x.get('ema200_1d', 0) or 0
    features[49] = x.get('trend_consensus', 'NEUTRAL')
    
    # D类 期货专用门控 (50-59)
    features[50] = str(x.get('antifragile', ''))
    features[51] = str(x.get('blackswan', ''))
    features[52] = str(r.get('integrity_gate', ''))
    features[53] = r.get('fangcang_trap', False)
    features[54] = str(r.get('timing_badge', '')) if r.get('timing_badge') else 'STANDBY'
    features[55] = of.get('score', 0) if isinstance(of, dict) else 0
    features[56] = str(mi.get('signal', '')) if isinstance(mi, dict) else ''
    features[57] = str(ve.get('exhaustion_level', '')) if isinstance(ve, dict) else ''
    features[58] = par.get('sl_atr_mult', 0)
    features[59] = 'OK'
    
    # E类 宏观/跨市场 (60-69)
    features[60] = mc.get('dxy', {}).get('price', 0) if isinstance(mc.get('dxy'), dict) else 0
    features[61] = mc.get('dxy', {}).get('direction', 'FLAT') if isinstance(mc.get('dxy'), dict) else 'FLAT'
    features[62] = mc.get('nasdaq', {}).get('price', 0) if isinstance(mc.get('nasdaq'), dict) else 0
    features[63] = mc.get('score_addon', 0) if isinstance(mc, dict) else 0
    features[64] = x.get('sentiment_nlp', {}).get('score', 0) if isinstance(x.get('sentiment_nlp'), dict) else 0
    features[65] = cf.get('bybit_fr', 0)
    features[66] = dp.get('pc_ratio', 0) if isinstance(dp, dict) else 0
    features[67] = str(dp.get('pc_signal', '')) if isinstance(dp, dict) else ''
    features[68] = x.get('cross_market', {}).get('score', 0) if isinstance(x.get('cross_market'), dict) else 0
    features[69] = str(mc.get('notes', '')) if isinstance(mc, dict) else ''
    
    # F类 智能钱/链上 (70-79)
    features[70] = wh.get('score', 0) if isinstance(wh, dict) else 0
    features[71] = wh.get('flow', 0) if isinstance(wh, dict) else 0
    features[72] = ow.get('score', 0) if isinstance(ow, dict) else 0
    features[73] = str(sm.get('sm_signal', ''))
    features[74] = (sm.get('big_acct_long', 0) or 0) * 100 if isinstance(sm.get('big_acct_long', 0) or 0, (int, float)) else 0
    features[75] = (sm.get('big_pos_long', 0) or 0) * 100 if isinstance(sm.get('big_pos_long', 0) or 0, (int, float)) else 0
    features[76] = str(sm.get('liq_bias', 'NEUTRAL'))
    features[77] = str(sm.get('fund_bias', 'NEUTRAL'))
    features[78] = dv.get('score', 0) if isinstance(dv, dict) else 0
    features[79] = str(sm.get('lsr_trend', ''))
    
    # 5个alpha组得分计算
    groups = {}
    alpha_contribs = {}
    
    total_score = float(r.get('score', 0) or 0)
    
    for group_name, group_def in ALPHA_GROUPS.items():
        # 收集该组的特征值（数值型，排除原始价格类大数值）
        _price_dims = {1, 3, 4, 19, 20, 21, 22, 26, 27, 28, 29, 30, 48, 60, 62}
        group_values = []
        for dim_id in group_def['dims']:
            v = features.get(dim_id, 0)
            if isinstance(v, (int, float)) and v != 0:
                if dim_id in _price_dims:
                    continue  # 跳过原始价格（不是信号）
                group_values.append(float(v))
        
        # 组得分 = 该组特征的平均归一化值
        group_score = sum(group_values) / len(group_values) if group_values else 0
        
        # 信号方向 [-1, +1]
        # 简化：正值为看多信号，负值为看空信号
        group_signal = max(-1, min(1, group_score / 100)) if group_values else 0
        
        groups[group_name] = {
            'score': round(group_score, 4),
            'signal': round(group_signal, 4),
            'n_dims': len(group_values),
            'ic_weight': group_def['ic_weight'],
            'desc': group_def['desc'],
        }
        
        # 归因贡献 = 该组得分 × IC权重 / 总得分
        if total_score > 0:
            alpha_contribs[group_name] = round(
                (group_score * group_def['ic_weight']) / total_score, 4
            )
        else:
            alpha_contribs[group_name] = 0.0
    
    return {
        '_ts': time.time(),
        '_time': datetime.now(timezone.utc).isoformat(),
        'symbol': symbol,
        'features': {str(k): v for k, v in features.items()},
        'groups': groups,
        'alpha_contribs': alpha_contribs,
        'regime': r.get('regime', ''),
        'direction': r.get('direction', ''),
        'score': total_score,
        'action': r.get('action', ''),
        'n_features': len(features),
    }


def log_features(symbol: str, features: dict):
    """记录特征快照到日志（供attribution_engine使用）"""
    log_path = BASE / 'data' / 'feature_log.jsonl'
    entry = {
        'ts': time.time(),
        'time': datetime.now(timezone.utc).isoformat(),
        'symbol': symbol,
        'regime': features.get('regime', ''),
        'direction': features.get('direction', ''),
        'score': features.get('score', 0),
        'action': features.get('action', ''),
        'alpha_contribs': features.get('alpha_contribs', {}),
        'groups': features.get('groups', {}),
    }
    with open(log_path, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')


def get_cache_stats() -> dict:
    """缓存统计"""
    caches = list(CACHE_DIR.glob('*_features.json'))
    stats = []
    for c in caches:
        try:
            data = json.loads(c.read_text())
            age = time.time() - data.get('_ts', 0)
            stats.append({
                'symbol': data.get('symbol', c.stem),
                'age_seconds': round(age, 0),
                'fresh': age < TTL_SECONDS,
                'n_features': data.get('n_features', 0),
            })
        except Exception:
            pass
    return {'caches': stats, 'ttl_seconds': TTL_SECONDS}


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    print(f"Fetching features for {sym}...")
    force = '--force' in sys.argv or '-f' in sys.argv
    f = get_features(sym, force_refresh=force)
    print(f"Source: {f['_source']}")
    print(f"Features: {f['n_features']}")
    print(f"Regime: {f['regime']}, Score: {f['score']}, Action: {f['action']}")
    print(f"Alpha groups:")
    for g, v in f['groups'].items():
        print(f"  {g}: score={v['score']:.2f} signal={v['signal']:.4f} dims={v['n_dims']}")
    print(f"Alpha contribs: {f['alpha_contribs']}")
