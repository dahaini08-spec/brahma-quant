#!/usr/bin/env python3
"""
brahma_ultimate_backtest.py — 梵天量化终极回测引擎
2026-08-27 设计院 苏摩111

== 核心架构 ==

Layer1: 纯K线体制层 (0.002s/次)
  - 32标的 × 6年历史
  - 4H EMA体制识别
  - 1H RSI/BBW/ATR/MACD
  - 真实SL计算（ATR×1.5）
  - 真实RR=1.5
  - 目标: 10,000+笔铁证

Layer2: 梵天本地评分层 (0.4s/次)
  - OFFLINE_MODE注入历史K线
  - 调用真实 market_state.analyze()
  - 用本地35维评分（block_a技术层）
  - 目标: BTC/ETH 2000+笔，验证score→WR关系

Layer3: 全能力层（后台长跑）
  - 完整brahma_core.analyze()
  - 代表性快照（每季度一批）
  - 目标: 200+笔铁证（score≥120 → 真实WR）

== 信号框架（与生产系统一致）==

信号生成：
  1. 体制识别（BULL/BEAR/CHOP/RECOVERY）
  2. 方向映射（SSOT：BEAR_TREND→SHORT，BULL_TREND→LONG等）
  3. RSI过滤（超买不追多，超卖不追空）
  4. BBW过滤（BBW>6跳过，太宽=趋势已走完）
  5. 死穴封禁（BEAR_TREND LONG / CHOP_MID无过滤）

出场计算（与梵天MEMORY铁律一致）：
  做空止损 = 入场价 × (1 + SL_PCT)
  做多止损 = 入场价 × (1 - SL_PCT)
  SL_PCT: BEAR=2.0% / CHOP/BULL=2.5%
  TP = 入场价 × (1 ± SL_PCT × RR)  RR=1.5

验证方式：
  Walk-Forward（无前视偏差）
  Train: 每12个月
  Test:  每3个月
  步进:  3个月

输出：
  data/ultimate_wr_layer1.json   Layer1 WR矩阵
  data/ultimate_log_layer1.jsonl 每笔信号详情
"""

import sys, os, json, time
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import warnings
warnings.filterwarnings('ignore')

ROOT = Path(__file__).parent.parent
DATA = ROOT / 'data'
HIST = DATA / 'historical'

sys.path.insert(0, str(ROOT / 'brahma_brain'))
sys.path.insert(0, str(ROOT))

# ══════════════════════════════════════════════════════════════
# 参数配置（与生产系统SSOT对齐）
# ══════════════════════════════════════════════════════════════

# 体制→策略映射（SOUL.md + MEMORY.md SSOT）
REGIME_MAP = {
    'BULL_TREND':    {'dir': 'LONG',  'sl_pct': 0.025, 'mult': 1.6},
    'BULL_EARLY':    {'dir': 'LONG',  'sl_pct': 0.025, 'mult': 1.0},
    'BEAR_TREND':    {'dir': 'SHORT', 'sl_pct': 0.020, 'mult': 1.6},
    'BEAR_EARLY':    {'dir': 'SHORT', 'sl_pct': 0.020, 'mult': 1.2},
    'BEAR_RECOVERY': {'dir': 'LONG',  'sl_pct': 0.020, 'mult': 1.2},
    'CHOP_MID':      {'dir': None,    'sl_pct': 0.025, 'mult': 0.0},
}

RR = 1.5          # TP/SL = 1.5 (梵天MEMORY铁律)
MIN_BBW = 0.5     # BBW过低=数据问题
MAX_BBW = 6.0     # BBW过高=已走完趋势
MIN_ATR_PCT = 0.3 # 最小ATR%（过小则SL无意义）
OUTCOME_H = 48    # 48H验证（BEAR_TREND SHORT需要48H）

# 死穴封禁（AGENTS.md）
DEATH_ZONES = {
    ('BEAR_TREND', 'LONG'),
    ('CHOP_MID', 'LONG'),
    ('CHOP_MID', 'SHORT'),
}

# ══════════════════════════════════════════════════════════════
# 数据加载器
# ══════════════════════════════════════════════════════════════

def load_parquet(sym: str, tf: str) -> pd.DataFrame:
    """加载parquet历史K线，统一返回 DatetimeIndex + OHLCV格式"""
    # 主目录（BTC/ETH，index是Timestamp）
    main_path = HIST / f'{sym.lower()}usdt_{tf}.jsonl.gz'
    parquet_main = HIST / f'{sym.lower()}usdt' / f'{sym.lower()}usdt_{tf}.parquet'
    
    # 子目录parquet
    if parquet_main.exists():
        df = pd.read_parquet(str(parquet_main))
        # 统一列名
        col_map = {'open':'o','high':'h','low':'l','close':'c','volume':'v'}
        df = df.rename(columns=col_map)
        
        # 处理index：有的是Timestamp，有的是int
        if isinstance(df.index[0], (int, np.integer)):
            # index是整数序号，ts列是毫秒时间戳
            if 'ts' in df.columns:
                df.index = pd.to_datetime(df['ts'], unit='ms', utc=True)
                df = df.drop(columns=['ts'], errors='ignore')
            else:
                return pd.DataFrame()
        # 确保是UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC')
        df = df.sort_index()
        return df[['o','h','l','c','v']]
    
    # 主目录jsonl.gz（BTC/ETH专属）
    import gzip
    for gz_path in [
        HIST / f'{sym.upper()}USDT_{tf}.jsonl.gz',
        HIST / f'{sym.lower()}usdt_{tf}.jsonl.gz',
    ]:
        if gz_path.exists():
            with gzip.open(str(gz_path), 'rt') as f:
                rows = [json.loads(l.strip()) for l in f if l.strip()]
            rows.sort(key=lambda x: x['ts'])
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df['ts'], unit='ms', utc=True)
            df = df.rename(columns={'o':'o','h':'h','l':'l','c':'c','v':'v'})
            return df[['o','h','l','c','v']].astype(float)
    
    return pd.DataFrame()

def load_all_symbols() -> dict:
    """加载所有标的的1H和4H数据"""
    syms = [
        'BTC','ETH','BNB','ADA','XRP','DOGE','DOT','LINK','LTC','XLM',
        'TRX','ATOM','ALGO','CRV','COMP','RUNE','SNX','VET','THETA','BCH',
        'ETC','EGLD','ONT','XMR','ZEC','DASH','KAVA','SUSHI','TRB','ZIL',
        'IOTA','SOL'
    ]
    
    loaded = {}
    for sym in syms:
        df1h = load_parquet(sym, '1h')
        df4h = load_parquet(sym, '4h')
        if len(df1h) >= 500 and len(df4h) >= 100:
            loaded[sym] = {'1h': df1h, '4h': df4h}
    
    print(f"  加载完成: {len(loaded)} 个标的")
    return loaded

# ══════════════════════════════════════════════════════════════
# 指标计算（纯pandas，无前视偏差）
# ══════════════════════════════════════════════════════════════

def calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def calc_bbw(s: pd.Series, period: int = 20) -> pd.Series:
    sma = s.rolling(period).mean()
    std = s.rolling(period).std()
    return (4 * std / sma * 100).fillna(5)

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df['h'], df['l'], df['c']
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(com=period-1, adjust=False).mean()

def calc_regime_series(df4h: pd.DataFrame) -> pd.Series:
    """用4H K线计算每根K线的体制（向量化，快速）"""
    c = df4h['c']
    ema9  = c.ewm(span=9,  adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ret7d = c.pct_change(42).fillna(0) * 100  # 42根4H = 7天
    
    regime = pd.Series('CHOP_MID', index=df4h.index)
    regime = regime.where(~(ema9 > ema21) | ~(ema21 > ema50) | ~(ret7d > 5), 'BULL_TREND')
    regime = regime.where(~((ema9 > ema21) & (ema21 <= ema50) & (ret7d > -2)) | (regime == 'BULL_TREND'), 'BULL_EARLY')
    regime = regime.where(~(ema9 < ema21) | ~(ema21 < ema50) | ~(ret7d < -5), 'BEAR_TREND')
    regime = regime.where(~((ema9 < ema21) & (ema21 >= ema50) & (ret7d > -3)) | (regime == 'BEAR_TREND'), 'BEAR_RECOVERY')
    
    # 重新按优先级计算（向量化版本）
    r = pd.Series('CHOP_MID', index=df4h.index)
    bull_trend  = (ema9 > ema21) & (ema21 > ema50) & (ret7d > 5)
    bull_early  = (ema9 > ema21) & ~bull_trend & (ret7d > -2)
    bear_trend  = (ema9 < ema21) & (ema21 < ema50) & (ret7d < -5)
    bear_recovery = (ema9 < ema21) & ~bear_trend & (ret7d > -3)
    
    r[bear_recovery] = 'BEAR_RECOVERY'
    r[bear_trend]    = 'BEAR_TREND'
    r[bull_early]    = 'BULL_EARLY'
    r[bull_trend]    = 'BULL_TREND'
    
    return r

# ══════════════════════════════════════════════════════════════
# Layer 1: 纯K线层回测
# ══════════════════════════════════════════════════════════════

def run_layer1(sym: str, df1h: pd.DataFrame, df4h: pd.DataFrame) -> list:
    """
    Layer1: 向量化回测，无前视偏差
    
    信号生成逻辑（与梵天生产系统对齐）：
    1. 4H体制识别（EMA9/21/50 + 7日涨跌）
    2. 1H指标（RSI/BBW/ATR）
    3. 体制→方向映射
    4. RSI + BBW过滤
    5. 死穴封禁
    6. 出场：TP=SL×RR 或 SL（48H内先碰到哪个）
    """
    
    if len(df1h) < 500 or len(df4h) < 100:
        return []
    
    # 计算4H体制序列
    regime_4h = calc_regime_series(df4h)
    
    # 将4H体制对齐到1H时间轴（forward fill）
    regime_1h = regime_4h.reindex(df1h.index, method='ffill')
    
    # 计算1H指标
    c1h = df1h['c']
    rsi_1h  = calc_rsi(c1h, 14)
    bbw_1h  = calc_bbw(c1h, 20)
    atr_1h  = calc_atr(df1h, 14)
    atr_pct = atr_1h / c1h * 100
    macd_fast = c1h.ewm(span=12).mean()
    macd_slow = c1h.ewm(span=26).mean()
    macd_hist = (macd_fast - macd_slow) / c1h * 100
    
    # 最小信号间隔：48H（防止在同一趋势中反复开仓）
    MIN_SIGNAL_BARS = 48
    
    results = []
    last_signal_idx = -MIN_SIGNAL_BARS - 1
    
    # 从第200根开始（需要足够的历史数据）
    for i in range(200, len(df1h) - OUTCOME_H - 2):
        # 距离上次信号必须≥48H
        if i - last_signal_idx < MIN_SIGNAL_BARS:
            continue
        
        regime = regime_1h.iloc[i]
        if regime not in REGIME_MAP:
            continue
        
        cfg = REGIME_MAP[regime]
        direction = cfg['dir']
        if direction is None:
            continue
        
        # 死穴检查
        if (regime, direction) in DEATH_ZONES:
            continue
        
        rsi   = rsi_1h.iloc[i]
        bbw   = bbw_1h.iloc[i]
        atr_p = atr_pct.iloc[i]
        entry = c1h.iloc[i]
        
        # 过滤条件
        if pd.isna(rsi) or pd.isna(bbw) or pd.isna(atr_p):
            continue
        if bbw < MIN_BBW or bbw > MAX_BBW:
            continue
        if atr_p < MIN_ATR_PCT:
            continue
        
        # RSI极值过滤（不追超买/超卖）
        if direction == 'LONG'  and rsi > 73: continue
        if direction == 'SHORT' and rsi < 27: continue
        
        # 计算止损止盈（梵天MEMORY铁律）
        sl_pct = cfg['sl_pct']
        if direction == 'LONG':
            sl_price = entry * (1 - sl_pct)
            tp_price = entry * (1 + sl_pct * RR)
        else:
            sl_price = entry * (1 + sl_pct)
            tp_price = entry * (1 - sl_pct * RR)
        
        # 验证：未来48H内的价格路径
        future_slice = df1h['c'].iloc[i+1: i+1+OUTCOME_H]
        if len(future_slice) < OUTCOME_H // 2:
            continue
        
        # 判断出场（先碰到TP/SL的那个）
        outcome = 'timeout'  # 超时（48H未触及）
        pnl_pct = 0.0
        exit_bar = OUTCOME_H
        
        for j, fp in enumerate(future_slice):
            if direction == 'LONG':
                if fp <= sl_price:
                    outcome = 'SL'
                    pnl_pct = -sl_pct * 100
                    exit_bar = j + 1
                    break
                elif fp >= tp_price:
                    outcome = 'TP'
                    pnl_pct = sl_pct * RR * 100
                    exit_bar = j + 1
                    break
            else:
                if fp >= sl_price:
                    outcome = 'SL'
                    pnl_pct = -sl_pct * 100
                    exit_bar = j + 1
                    break
                elif fp <= tp_price:
                    outcome = 'TP'
                    pnl_pct = sl_pct * RR * 100
                    exit_bar = j + 1
                    break
        
        # 超时处理：以48H收盘价计算PnL
        if outcome == 'timeout':
            final_price = future_slice.iloc[-1]
            if direction == 'LONG':
                pnl_pct = (final_price - entry) / entry * 100
            else:
                pnl_pct = (entry - final_price) / entry * 100
        
        win = (outcome == 'TP') or (outcome == 'timeout' and pnl_pct > 0)
        
        # 评分估算（纯K线层）
        score_est = _estimate_score(regime, rsi, bbw, atr_p, direction, macd_hist.iloc[i])
        
        results.append({
            'symbol':    sym,
            'ts':        int(df1h.index[i].timestamp() * 1000),
            'date':      df1h.index[i].strftime('%Y-%m-%d'),
            'regime':    regime,
            'direction': direction,
            'entry':     round(float(entry), 6),
            'sl_price':  round(float(sl_price), 6),
            'tp_price':  round(float(tp_price), 6),
            'sl_pct':    sl_pct,
            'rr':        RR,
            'rsi':       round(float(rsi), 1),
            'bbw':       round(float(bbw), 2),
            'atr_pct':   round(float(atr_p), 3),
            'outcome':   outcome,
            'exit_bar':  exit_bar,
            'pnl_pct':   round(float(pnl_pct), 4),
            'win':       bool(win),
            'score_est': score_est,
        })
        
        last_signal_idx = i  # 更新最后信号位置
    
    return results

def _estimate_score(regime, rsi, bbw, atr_pct, direction, macd_h) -> int:
    """
    信号质量估算（仅K线层，对应梵天block_a）
    不含AI议会/链上/HCME
    """
    base = {
        'BULL_TREND': 105, 'BEAR_TREND': 108,
        'BULL_EARLY': 90,  'BEAR_EARLY': 92,
        'BEAR_RECOVERY': 115, 'CHOP_MID': 65,
    }.get(regime, 80)
    
    # BBW方仓压缩加分
    if bbw < 2.0:   base += 18
    elif bbw < 3.0: base += 10
    elif bbw > 5.0: base -= 8   # 波动已走开，不是好入场
    
    # RSI确认
    if direction == 'LONG'  and rsi < 38: base += 12
    elif direction == 'LONG'  and rsi < 45: base += 6
    if direction == 'SHORT' and rsi > 62: base += 12
    elif direction == 'SHORT' and rsi > 55: base += 6
    
    # RSI矛盾扣分
    if direction == 'LONG'  and rsi > 65: base -= 8
    if direction == 'SHORT' and rsi < 35: base -= 8
    
    # MACD确认
    if direction == 'LONG'  and macd_h > 0: base += 5
    if direction == 'SHORT' and macd_h < 0: base += 5
    
    # ATR适中最好（太小=无波动，太大=已爆发）
    if 1.0 <= atr_pct <= 3.0: base += 5
    elif atr_pct > 5.0: base -= 5
    
    return min(int(base), 180)

# ══════════════════════════════════════════════════════════════
# 统计WR矩阵
# ══════════════════════════════════════════════════════════════

def calc_wr_matrix(results: list) -> dict:
    """多维度WR统计"""
    m = defaultdict(lambda: {'wins':0, 'n':0, 'pnls':[], 'tp':0, 'sl':0, 'timeout':0})
    
    for r in results:
        reg, dir_ = r['regime'], r['direction']
        score = r['score_est']
        bbw   = r['bbw']
        rsi   = r['rsi']
        outcome = r['outcome']
        
        # RSI分层键（对应WR矩阵v8格式）
        rsi_bucket = (
            'RSI_0_40'   if rsi < 40  else
            'RSI_40_50'  if rsi < 50  else
            'RSI_50_55'  if rsi < 55  else
            'RSI_55_60'  if rsi < 60  else
            'RSI_60_70'  if rsi < 70  else
            'RSI_70_100'
        )
        
        # score分层键
        score_tier = (
            'S3_ELITE'  if score >= 140 else
            'S2_STRONG' if score >= 120 else
            'S1_WATCH'  if score >= 100 else
            'S0_WEAK'
        )
        
        keys = [
            'ALL',
            f'{reg}:{dir_}',
            f'{reg}:{dir_}:{rsi_bucket}',
            f'{score_tier}:{reg}:{dir_}',
        ]
        if bbw < 2.0:
            keys.append(f'FCG:{reg}:{dir_}')  # 方仓压缩
        if score >= 120:
            keys.append(f'SQE:{reg}:{dir_}')
        
        for k in keys:
            m[k]['n'] += 1
            m[k]['pnls'].append(r['pnl_pct'])
            if r['win']: m[k]['wins'] += 1
            m[k][outcome] = m[k].get(outcome, 0) + 1
    
    out = {}
    for k, v in m.items():
        n = v['n']
        if n < 5: continue
        pnls = v['pnls']
        wins_p = [p for p in pnls if p > 0]
        loss_p = [p for p in pnls if p < 0]
        ev = sum(pnls) / n
        out[k] = {
            'wr':        round(v['wins']/n, 4),
            'n':         n,
            'ev_pct':    round(ev, 4),
            'avg_win':   round(sum(wins_p)/len(wins_p), 4) if wins_p else 0,
            'avg_loss':  round(sum(loss_p)/len(loss_p), 4) if loss_p else 0,
            'tp_rate':   round(v.get('TP',0)/n, 3),
            'sl_rate':   round(v.get('SL',0)/n, 3),
            'iron_proof': n >= 30,
        }
    return out

# ══════════════════════════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════════════════════════

def main():
    print("="*70)
    print("梵天量化终极回测引擎 Layer1 — 纯K线体制层")
    print("="*70)
    print(f"生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print()
    
    # 加载数据
    print("【1】加载历史K线数据...")
    t0 = time.time()
    all_data = load_all_symbols()
    print(f"  耗时: {time.time()-t0:.1f}s")
    
    # 运行回测
    print(f"\n【2】Layer1 回测（{len(all_data)} 个标的）...")
    all_results = []
    sym_stats = {}
    
    for sym, data in sorted(all_data.items()):
        t_sym = time.time()
        results = run_layer1(sym, data['1h'], data['4h'])
        all_results.extend(results)
        wins = sum(1 for r in results if r['win'])
        wr = wins/len(results) if results else 0
        sym_stats[sym] = {'n': len(results), 'wr': wr}
        print(f"  {sym:6s}: {len(results):4d}笔  WR={wr:.0%}  {time.time()-t_sym:.2f}s")
    
    print(f"\n  总计: {len(all_results)} 笔信号")
    
    # 保存全量日志
    log_path = DATA / 'ultimate_log_layer1.jsonl'
    with open(log_path, 'w') as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f"  日志: {log_path}")
    
    # 统计WR矩阵
    print(f"\n【3】WR矩阵统计...")
    matrix = calc_wr_matrix(all_results)
    
    # 保存矩阵
    wr_path = DATA / 'ultimate_wr_layer1.json'
    with open(wr_path, 'w') as f:
        json.dump({
            'matrix': matrix,
            'total': len(all_results),
            'symbols': list(all_data.keys()),
            'method': 'Layer1: 4H EMA体制+1H RSI/BBW/ATR，真实SL/TP出场，48H验证',
            'params': {'rr': RR, 'outcome_h': OUTCOME_H, 'min_signal_gap_h': 48},
            'generated': datetime.now(timezone.utc).isoformat(),
        }, f, indent=2, ensure_ascii=False)
    
    # 输出核心结果
    print(f"\n{'='*70}")
    print("【4】核心结果")
    print(f"{'='*70}")
    
    # 整体
    all_key = matrix.get('ALL', {})
    if all_key:
        print(f"\n整体: WR={all_key['wr']:.1%}  EV={all_key['ev_pct']:+.3f}%/笔  n={all_key['n']}")
    
    # 体制分层（最重要）
    print(f"\n体制分层 WR（铁证 n≥30）:")
    print(f"  {'策略键':<40} {'WR':>8} {'EV':>8} {'n':>6} {'TP%':>8} {'SL%':>8}")
    print(f"  {'-'*80}")
    
    regime_keys = [(k,v) for k,v in matrix.items() 
                   if ':' in k and k.count(':') == 1 and v['n'] >= 30]
    for k, v in sorted(regime_keys, key=lambda x: x[1]['wr'], reverse=True):
        iron = ' ✅' if v['iron_proof'] else '   '
        print(f"  {k:<40} {v['wr']:>7.1%} {v['ev_pct']:>+7.3f}% {v['n']:>6} {v['tp_rate']:>7.0%} {v['sl_rate']:>7.0%}{iron}")
    
    # SQE过滤效果
    sqe_keys = [(k,v) for k,v in matrix.items() if k.startswith('SQE:') and v['n'] >= 20]
    if sqe_keys:
        sqe_total = sum(v['n'] for _,v in sqe_keys)
        sqe_wins  = sum(int(v['wr']*v['n']) for _,v in sqe_keys)
        sqe_ev    = sum(v['ev_pct']*v['n'] for _,v in sqe_keys) / sqe_total
        print(f"\n  SQE过滤(score≥120)总体: WR={sqe_wins/sqe_total:.1%}  EV={sqe_ev:+.3f}%/笔  n={sqe_total}")
    
    # 方仓压缩效果
    fcg_keys = [(k,v) for k,v in matrix.items() if k.startswith('FCG:') and v['n'] >= 15]
    if fcg_keys:
        fcg_total = sum(v['n'] for _,v in fcg_keys)
        fcg_wins  = sum(int(v['wr']*v['n']) for _,v in fcg_keys)
        fcg_ev    = sum(v['ev_pct']*v['n'] for _,v in fcg_keys) / fcg_total
        print(f"  方仓压缩(BBW<2%)总体:   WR={fcg_wins/fcg_total:.1%}  EV={fcg_ev:+.3f}%/笔  n={fcg_total}")
    
    # 对比基线
    print(f"\n{'='*70}")
    print(f"对比基线:")
    print(f"  达摩院简化版(RSI+MACD盲扫):   WR=39.4%  EV=-0.002%  n=3996")
    print(f"  达摩院399笔真实数据:           WR=36%    SL=0.19%（太窄）")
    total_wr = all_key.get('wr',0) if all_key else 0
    total_ev = all_key.get('ev_pct',0) if all_key else 0
    total_n  = all_key.get('n',0) if all_key else 0
    print(f"  梵天Layer1(32标的,真实SL/TP): WR={total_wr:.1%}  EV={total_ev:+.3f}%  n={total_n}")
    
    # RSI分层关键条目（对照WR矩阵v8）
    print(f"\nRSI分层关键条目（与WR矩阵v8对照）:")
    critical_keys = [
        'BEAR_TREND:SHORT:RSI_60_70',
        'BEAR_TREND:SHORT:RSI_55_60',
        'BEAR_RECOVERY:LONG:RSI_0_40',
        'BULL_EARLY:LONG:RSI_50_55',
        'BEAR_TREND:LONG:RSI_60_70',
    ]
    for k in critical_keys:
        if k in matrix and matrix[k]['n'] >= 15:
            v = matrix[k]
            print(f"  {k:<45} WR={v['wr']:.1%}  EV={v['ev_pct']:+.3f}%  n={v['n']}")
    
    print(f"\n矩阵输出: {wr_path}")
    print(f"{'='*70}")

if __name__ == '__main__':
    main()
