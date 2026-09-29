from typing import Any, Optional
#!/usr/bin/env python3
"""
brahma_w4_fangcang_ml.py — 梵天v5.0 W4: 方仓+ML
================================================
W4封印：苏摩111 2026-09-15

核心思路：
1. 用W3最优参数在6年历史数据上生成所有交易信号
2. 每个信号点提取方仓25维特征+市场状态特征
3. 训练LightGBM预测每笔交易的实际收益
4. CPCV验证ML模型不过拟合
5. 对比：W3策略 vs W3策略+ML过滤

输出：
- ML模型 + 特征重要性
- CPCV各fold结果
- W3 vs W3+ML对比
- 最佳阈值（ML预测值>阈值才入场）
"""

import sys, math, time, gzip, json, random, os
import numpy as np
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Dict, Tuple

BRAIN = Path(__file__).parent
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BRAIN.parent))

from jesse.indicators import rsi as jesse_rsi
from jesse.indicators import ema as jesse_ema
from jesse.indicators import bollinger_bands_width as jesse_bbw
from jesse.indicators import atr as jesse_atr

from brahma_engine_v5 import load_candles, load_regimes, BrahmaStrategy12, Trade

DATA_DIR = BRAIN.parent / "data"
HIST_DIR = DATA_DIR / "historical"
OUT_DIR = DATA_DIR

# W3最优参数
W3_PARAMS = {
    'BTCUSDT': {
        'rsi_long': 36, 'rsi_short': 78, 'bb_squeeze': 0.0103,
        'atr_sl_mult': 2.71, 'rr_ratio': 3.29, 'holding_bars': 54,
    },
    'ETHUSDT': {
        'rsi_long': 33, 'rsi_short': 55, 'bb_squeeze': 0.0561,
        'atr_sl_mult': 2.87, 'rr_ratio': 3.42, 'holding_bars': 60,
    },
}


# ============================================================
# 1. 特征提取 — 从每笔交易时点提取特征
# ============================================================

def extract_trade_features(candles, trade, rsi_seq, ema_seq, bbw_seq, atr_seq,
                           regimes, idx=None) -> Optional[Any]:
    """
    在交易入场时点提取特征向量
    trade: Trade对象 或 dict
    idx: 入场K线索引（可选，从trade推断）
    """
    if idx is None:
        entry_ts = getattr(trade, 'entry_ts', None)
        if entry_ts is None:
            return None
        # 用entry_ts找到K线索引
        ts_array = candles[:, 0].astype(int)
        idx = int(np.searchsorted(ts_array, entry_ts, side='right') - 1)
        if idx < 0 or idx >= len(candles):
            return None
    
    # 安全切片
    if idx < 210:
        return None
    
    features = {}
    
    # 1. 技术指标特征（入场时点）
    features['rsi'] = float(rsi_seq[idx]) if not math.isnan(rsi_seq[idx]) else 50.0
    features['bbw'] = float(bbw_seq[idx]) if not math.isnan(bbw_seq[idx]) else 0.05
    features['atr'] = float(atr_seq[idx]) if not math.isnan(atr_seq[idx]) else 0
    features['atr_pct'] = features['atr'] / float(candles[idx][4]) if candles[idx][4] > 0 else 0
    
    # 2. 价格相对EMA200位置
    px = float(candles[idx][4])
    ema200 = float(ema_seq[idx]) if not math.isnan(ema_seq[idx]) else px
    features['px_vs_ema200'] = (px - ema200) / ema200 if ema200 > 0 else 0
    
    # 3. 近期动量
    if idx >= 20:
        ret_20 = (float(candles[idx][4]) / float(candles[idx-20][4]) - 1) * 100
        features['ret_20'] = ret_20
    else:
        features['ret_20'] = 0
    
    if idx >= 5:
        ret_5 = (float(candles[idx][4]) / float(candles[idx-5][4]) - 1) * 100
        features['ret_5'] = ret_5
    else:
        features['ret_5'] = 0
    
    # 4. 波动率特征
    if idx >= 20:
        returns = []
        for i in range(idx-20, idx):
            o, h, l, c = float(candles[i][1]), float(candles[i][2]), float(candles[i][3]), float(candles[i][4])
            returns.append((c - o) / o if o > 0 else 0)
        features['vol_20'] = float(np.std(returns)) if returns else 0
        features['skew_20'] = float(len([r for r in returns if r > 0]) / max(len(returns), 1))
    else:
        features['vol_20'] = 0
        features['skew_20'] = 0.5
    
    # 5. 体制
    ts = int(candles[idx][0])
    regime = 'UNKNOWN'
    for r_ts, r_val in regimes.items():
        if r_ts <= ts:
            regime = r_val
        elif r_ts > ts:
            break
    features['regime'] = regime
    
    # 6. 交易方向
    features['signal'] = int(getattr(trade, 'signal', 0))
    features['signal'] = int(features['signal'])
    
    # 7. ATR/SL距离
    features['sl_dist_atr'] = features['atr_pct'] * 100 / (features['atr'] / float(candles[idx][4]) * 100) if features['atr'] > 0 else 0
    
    # 8. 近期成交量趋势
    if idx >= 10:
        vols_recent = [float(candles[i][5]) for i in range(idx-10, idx)]
        vols_prev = [float(candles[i][5]) for i in range(idx-20, idx-10)] if idx >= 30 else vols_recent
        features['vol_trend'] = (np.mean(vols_recent) / max(np.mean(vols_prev), 1)) - 1 if vols_prev else 0
    else:
        features['vol_trend'] = 0
    
    # 9. RSI极端度
    features['rsi_extreme'] = abs(features['rsi'] - 50) / 50  # 0=中性, 1=极端
    
    # 10. BBW相对位置
    if idx >= 100:
        bbw_recent = [float(bbw_seq[i]) for i in range(idx-100, idx) if not math.isnan(bbw_seq[i])]
        features['bbw_percentile'] = float(np.percentile(bbw_recent, 90) < features['bbw']) if bbw_recent else 0
    else:
        features['bbw_percentile'] = 0
    
    return features


# ============================================================
# 2. 生成训练数据 — W3参数回测→每笔交易→特征+标签
# ============================================================

def generate_training_data(symbol: str) -> Dict:
    """用W3最优参数回测，生成(特征, 标签)训练集"""
    print(f"  [{symbol}] 加载数据...")
    candles, _ = load_candles(symbol)
    regimes = load_regimes(symbol)
    print(f"  [{symbol}] {len(candles)}根K线, {len(regimes)}个regime")
    
    # 预计算指标
    print(f"  [{symbol}] 计算Jesse指标...")
    rsi_seq = jesse_rsi(candles, 14, sequential=True)
    ema_seq = jesse_ema(candles, 200, sequential=True)
    bbw_seq = jesse_bbw(candles, 20, sequential=True)
    atr_seq = jesse_atr(candles, 14, sequential=True)
    
    # W3最优参数回测
    print(f"  [{symbol}] W3参数回测...")
    params = W3_PARAMS.get(symbol, W3_PARAMS['BTCUSDT'])
    strategy = BrahmaStrategy12(params)
    result = strategy.run(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq)
    
    trades = result.get('trades', [])
    print(f"  [{symbol}] {len(trades)}笔交易")
    
    # 提取特征+标签
    print(f"  [{symbol}] 提取特征...")
    X = []
    y = []
    trade_info = []
    
    for t in trades:
        feat = extract_trade_features(candles, t, rsi_seq, ema_seq, bbw_seq, atr_seq, regimes)
        if feat is None:
            continue
        net_ret = getattr(t, 'net_ret', 0)
        if net_ret == 0:
            continue
        
        # 特征向量（数值型）
        x_vec = [
            feat['rsi'], feat['bbw'], feat['atr_pct'], feat['px_vs_ema200'],
            feat['ret_20'], feat['ret_5'], feat['vol_20'], feat['skew_20'],
            feat['vol_trend'], feat['rsi_extreme'], feat['bbw_percentile'],
            feat['signal'],
        ]
        X.append(x_vec)
        y.append(1 if net_ret > 0 else 0)  # 二分类：盈利=1
        trade_info.append({
            'net_ret': net_ret,
            'signal': feat['signal'],
            'regime': feat['regime'],
            'rsi': feat['rsi'],
        })
    
    print(f"  [{symbol}] 训练集: {len(X)}样本, 正样本率={np.mean(y)*100:.1f}%")
    
    return {
        'X': np.array(X),
        'y': np.array(y),
        'trade_info': trade_info,
        'n_trades': len(trades),
        'wr': result['wr'],
        'ev': result['ev'],
    }


# ============================================================
# 3. LightGBM训练 + CPCV验证
# ============================================================

def train_and_validate(X, y, symbol, n_splits=6) -> Optional[dict]:
    """LightGBM训练 + CPCV验证"""
    try:
        from sklearn.model_selection import KFold
        from sklearn.metrics import accuracy_score, precision_score, recall_score
        import warnings
        warnings.filterwarnings('ignore')
        
        # 尝试LightGBM
        try:
            import lightgbm as lgb
            use_lgb = True
        except ImportError:
            use_lgb = False
            from sklearn.ensemble import GradientBoostingClassifier
            print("  LightGBM不可用，使用GradientBoosting")
    except ImportError:
        print("  sklearn不可用，跳过ML")
        return None
    
    n = len(X)
    if n < 100:
        print(f"  样本太少({n})，跳过ML")
        return None
    
    # CPCV式分割（简化版：KFold with purge）
    purge = 5  # purge 5个样本
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    
    fold_results = []
    all_predictions = np.zeros(n)
    
    for fold_id, (train_idx, test_idx) in enumerate(kf.split(X)):
        # Purge: 移除train中靠近test的样本
        test_set = set(test_idx)
        purged_train = [i for i in train_idx if abs(i - min(test_idx)) > purge and abs(i - max(test_idx)) > purge]
        
        X_train, y_train = X[purged_train], y[purged_train]
        X_test, y_test = X[test_idx], y[test_idx]
        
        if len(X_train) < 20 or len(X_test) < 5:
            continue
        
        if use_lgb:
            model = lgb.LGBMClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8, random_state=42,
                verbose=-1, min_child_samples=5,
            )
        else:
            model = GradientBoostingClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.1,
                subsample=0.8, random_state=42,
            )
        
        model.fit(X_train, y_train)
        pred = model.predict(X_test)
        proba = model.predict_proba(X_test)[:, 1]
        
        all_predictions[test_idx] = proba
        
        acc = accuracy_score(y_test, pred)
        prec = precision_score(y_test, pred, zero_division=0)
        rec = recall_score(y_test, pred, zero_division=0)
        
        fold_results.append({
            'fold': fold_id,
            'n_train': len(X_train),
            'n_test': len(X_test),
            'accuracy': acc,
            'precision': prec,
            'recall': rec,
            'wr_train': float(np.mean(y_train)),
            'wr_test': float(np.mean(y_test)),
        })
        
        print(f"  Fold {fold_id+1}: train={len(X_train)} test={len(X_test)} acc={acc:.3f} prec={prec:.3f} rec={rec:.3f}")
    
    # 特征重要性（用全量训练）
    if use_lgb:
        final_model = lgb.LGBMClassifier(
            n_estimators=50, max_depth=3, learning_rate=0.1,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            verbose=-1, min_child_samples=5,
        )
    else:
        final_model = GradientBoostingClassifier(
            n_estimators=50, max_depth=3, learning_rate=0.1,
            subsample=0.8, random_state=42,
        )
    final_model.fit(X, y)
    
    feature_names = [
        'rsi', 'bbw', 'atr_pct', 'px_vs_ema200',
        'ret_20', 'ret_5', 'vol_20', 'skew_20',
        'vol_trend', 'rsi_extreme', 'bbw_percentile', 'signal',
    ]
    
    if hasattr(final_model, 'feature_importances_'):
        importances = final_model.feature_importances_
        feat_imp = sorted(zip(feature_names, importances), key=lambda x: -x[1])
        print(f"  特征重要性:")
        for name, imp in feat_imp:
            print(f"    {name:20s}: {imp:.4f}")
    else:
        feat_imp = []
    
    # 找最佳阈值
    best_threshold = 0.5
    best_ev = 0
    for thresh in np.arange(0.3, 0.7, 0.05):
        mask = all_predictions > thresh
        if mask.sum() < 10:
            continue
        wr_filtered = np.mean(y[mask])
        n_filtered = mask.sum()
        # EV估算：WR × 平均收益 - (1-WR) × 平均损失
        # 简化：WR > 55% 认为有正EV
        ev_est = (wr_filtered - 0.45) * n_filtered  # 超过45%即正
        if ev_est > best_ev:
            best_ev = ev_est
            best_threshold = thresh
    
    mask = all_predictions > best_threshold
    if mask.sum() > 0:
        wr_filtered = float(np.mean(y[mask]))
        n_filtered = int(mask.sum())
    else:
        wr_filtered = 0
        n_filtered = 0
    
    return {
        'folds': fold_results,
        'avg_accuracy': np.mean([f['accuracy'] for f in fold_results]) if fold_results else 0,
        'avg_precision': np.mean([f['precision'] for f in fold_results]) if fold_results else 0,
        'feature_importance': feat_imp,
        'best_threshold': float(best_threshold),
        'wr_at_threshold': wr_filtered,
        'n_at_threshold': n_filtered,
        'wr_baseline': float(np.mean(y)),
        'n_baseline': len(y),
    }


# ============================================================
# 4. W3 vs W3+ML对比
# ============================================================

def compare_w3_vs_w3ml(symbol, train_data, ml_result) -> dict:
    """对比W3策略 vs W3+ML过滤"""
    if ml_result is None:
        return {'error': 'ML训练失败'}
    
    thresh = ml_result['best_threshold']
    
    # 从trade_info中获取每笔交易的ML预测
    # 这里简化：用整体WR对比
    baseline_wr = ml_result['wr_baseline']
    filtered_wr = ml_result['wr_at_threshold']
    baseline_n = ml_result['n_baseline']
    filtered_n = ml_result['n_at_threshold']
    
    # EV估算（简化：WR>50%为正EV）
    baseline_ev = (baseline_wr - 0.5) * baseline_n
    filtered_ev = (filtered_wr - 0.5) * filtered_n if filtered_n > 0 else 0
    
    return {
        'symbol': symbol,
        'w3_baseline': {
            'n_trades': baseline_n,
            'wr': baseline_wr,
            'ev_estimate': baseline_ev,
        },
        'w3_ml': {
            'n_trades': filtered_n,
            'wr': filtered_wr,
            'ev_estimate': filtered_ev,
            'threshold': thresh,
            'filter_rate': 1 - filtered_n / baseline_n if baseline_n > 0 else 0,
        },
        'improvement': {
            'wr_delta': filtered_wr - baseline_wr if filtered_n > 0 else 0,
            'ev_delta': filtered_ev - baseline_ev,
        },
    }


# ============================================================
# 主入口
# ============================================================

if __name__ == '__main__':
    print("=" * 70)
    print("梵天v5.0 W4: 方仓+ML")
    print("=" * 70)
    
    all_results = {}
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n{'='*60}")
        print(f"{symbol}")
        print(f"{'='*60}")
        
        t0 = time.time()
        
        # 1. 生成训练数据
        print(f"\n[1] 生成训练数据...")
        train_data = generate_training_data(symbol)
        
        # 2. ML训练+CPCV验证
        print(f"\n[2] ML训练+CPCV验证...")
        ml_result = train_and_validate(train_data['X'], train_data['y'], symbol)
        
        # 3. 对比
        print(f"\n[3] W3 vs W3+ML对比...")
        cmp = compare_w3_vs_w3ml(symbol, train_data, ml_result)
        
        print(f"\n  结果:")
        if ml_result:
            print(f"    W3基准: n={ml_result['n_baseline']} WR={ml_result['wr_baseline']*100:.1f}%")
            print(f"    W3+ML:  n={ml_result['n_at_threshold']} WR={ml_result['wr_at_threshold']*100:.1f}% (阈值={ml_result['best_threshold']:.2f})")
            print(f"    WR提升: +{(ml_result['wr_at_threshold']-ml_result['wr_baseline'])*100:.1f}%")
            print(f"    过滤率: {(1-ml_result['n_at_threshold']/ml_result['n_baseline'])*100:.1f}%")
            print(f"    CPCV平均准确率: {ml_result['avg_accuracy']*100:.1f}%")
        else:
            print(f"    ML训练失败")
        
        all_results[symbol] = {
            'train_data': {
                'n_samples': len(train_data['X']),
                'n_trades': train_data['n_trades'],
                'wr': train_data['wr'],
                'ev': train_data['ev'],
            },
            'ml': ml_result,
            'comparison': cmp,
        }
        
        print(f"\n  耗时: {time.time()-t0:.1f}s")
    
    # 保存
    out_file = OUT_DIR / "w4_fangcang_ml_results.json"
    serializable = {}
    for sym, r in all_results.items():
        serializable[sym] = {
            'train_data': r['train_data'],
            'ml': r['ml'],
            'comparison': r['comparison'],
        }
    with open(out_file, 'w') as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\n结果已保存: {out_file}")
    
    # 最终判定
    print(f"\n{'='*70}")
    print("W4 最终判定")
    print(f"{'='*70}")
    
    for sym, r in all_results.items():
        ml = r['ml']
        if ml is None:
            print(f"  {sym}: ❌ ML训练失败")
            continue
        
        wr_improve = (ml['wr_at_threshold'] - ml['wr_baseline']) * 100
        filter_rate = (1 - ml['n_at_threshold'] / ml['n_baseline']) * 100
        cpcv_acc = ml['avg_accuracy'] * 100
        
        wr_pass = ml['wr_at_threshold'] > ml['wr_baseline']
        cpcv_pass = cpcv_acc > 55
        n_pass = ml['n_at_threshold'] > 50
        
        print(f"\n  {sym}:")
        print(f"    W3 WR={ml['wr_baseline']*100:.1f}% → W3+ML WR={ml['wr_at_threshold']*100:.1f}% ({'✅' if wr_pass else '❌'} +{wr_improve:.1f}%)")
        print(f"    过滤率={filter_rate:.1f}% {'✅' if 30 < filter_rate < 70 else '⚠️'}")
        print(f"    CPCV准确率={cpcv_acc:.1f}% {'✅' if cpcv_pass else '❌'}")
        print(f"    ML后样本数={ml['n_at_threshold']} {'✅' if n_pass else '⚠️'}")
        
        if ml.get('feature_importance'):
            print(f"    Top3特征: {', '.join(f'{n}({v:.3f})' for n,v in ml['feature_importance'][:3])}")
    
    print(f"\n  下一步: W5(模拟盘) → W6(实盘试运行)")
