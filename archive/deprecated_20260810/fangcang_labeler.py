#!/usr/bin/env python3
"""
fangcang_labeler.py — 方仓标注引擎 v1.0
设计院封印 2026-08-08 Step2

功能:
  遍历BTC/ETH 6.5年15m历史数据
  识别所有「极压缩→爆发」案例
  标注每根K线的方仓阶段
  输出标注数据集 + 案例库

用法:
  python3 scripts/fangcang_labeler.py --symbol BTC --output data/fangcang_labeled_btc.parquet
  python3 scripts/fangcang_labeler.py --all
"""

import sys, os, argparse, json, time
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

try:
    import pandas as pd
    import numpy as np
    HAS_LIBS = True
except ImportError:
    HAS_LIBS = False
    print("❌ 需要 pandas + numpy，请先运行: venv/bin/pip install pandas numpy")
    sys.exit(1)


# ═══════════════════════════════════════════════════════════
# 核心指标计算
# ═══════════════════════════════════════════════════════════

def compute_indicators(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """计算方仓核心指标"""
    # BB 宽度（压缩度核心指标）
    df['sma20']    = df['close'].rolling(window).mean()
    df['std20']    = df['close'].rolling(window).std()
    df['bb_up']    = df['sma20'] + 2 * df['std20']
    df['bb_dn']    = df['sma20'] - 2 * df['std20']
    df['bb_width'] = (df['bb_up'] - df['bb_dn']) / df['sma20'] * 100  # 百分比

    # ATR（止损基准）
    df['tr'] = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low']  - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df['atr14'] = df['tr'].rolling(14).mean()
    df['atr_pct'] = df['atr14'] / df['close'] * 100

    # RSI
    delta = df['close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df['rsi14'] = 100 - (100 / (1 + gain / loss.replace(0, 1e-9)))

    # 量比（相对成交量）
    df['vol_ma20']  = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma20'].replace(0, 1e-9)

    # EMA
    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

    # 价格位置（相对BB）
    df['price_pos'] = (df['close'] - df['bb_dn']) / (df['bb_up'] - df['bb_dn'] + 1e-9)

    return df


# ═══════════════════════════════════════════════════════════
# 方仓阶段识别（4阶段模型）
# ═══════════════════════════════════════════════════════════

def identify_squeeze_phases(df: pd.DataFrame) -> pd.DataFrame:
    """
    使用相对百分位阈值（适配不同资产/时期的波动率）
    Phase0: 普通行情（BB宽 > P30）
    Phase1: 压缩酝酿（BB宽 P10~P30）
    Phase2: 临爆期   （BB宽 P5~P10）
    Phase3: 爆发期   （量比>1.5 + 突破BB外轨，且BB宽 < P20）
    Phase4: 延伸/回调（爆发后1-20根）
    """
    bb_w  = df['bb_width'].values
    vol_r = df['vol_ratio'].values
    close = df['close'].values
    bb_up = df['bb_up'].values
    bb_dn = df['bb_dn'].values

    # 动态百分位阈值（用滚动窗口，适配不同市场阶段）
    bb_series = df['bb_width']
    p5  = bb_series.rolling(2000, min_periods=200).quantile(0.05).values
    p10 = bb_series.rolling(2000, min_periods=200).quantile(0.10).values
    p20 = bb_series.rolling(2000, min_periods=200).quantile(0.20).values
    p30 = bb_series.rolling(2000, min_periods=200).quantile(0.30).values

    phases = np.zeros(len(df), dtype=int)

    for i in range(200, len(df)):
        bw = bb_w[i]
        vr = vol_r[i]
        cl = close[i]
        bu = bb_up[i]
        bd = bb_dn[i]
        _p5  = p5[i]  if not np.isnan(p5[i])  else 0.15
        _p10 = p10[i] if not np.isnan(p10[i]) else 0.25
        _p20 = p20[i] if not np.isnan(p20[i]) else 0.40
        _p30 = p30[i] if not np.isnan(p30[i]) else 0.60

        # Phase3: 量比放大 + 突破BB + BB处于压缩区
        if vr > 1.5 and (cl > bu * 1.001 or cl < bd * 0.999) and bw < _p20:
            phases[i] = 3
        elif bw <= _p5:
            phases[i] = 2  # 极度压缩临爆
        elif bw <= _p10:
            phases[i] = 2  # 临爆
        elif bw <= _p30:
            phases[i] = 1  # 酝酿
        else:
            phases[i] = 0  # 普通

    # Phase4: 爆发后延伸
    in_post = 0
    for i in range(len(phases)):
        if phases[i] == 3:
            in_post = 20
        elif in_post > 0 and phases[i] == 0:
            phases[i] = 4
            in_post -= 1

    df['phase'] = phases
    return df


# ═══════════════════════════════════════════════════════════
# 案例挖掘：提取所有「压缩→爆发」事件
# ═══════════════════════════════════════════════════════════

def extract_squeeze_cases(df: pd.DataFrame, symbol: str) -> list:
    """
    从标注数据中提取所有完整的「压缩→爆发」案例
    每个案例记录：
      - 压缩开始/结束时间
      - 压缩最低BB宽度
      - 爆发方向（UP/DOWN）
      - 爆发幅度（ATR倍数）
      - 量比峰值
      - 当时RSI
      - 结果（24H后涨跌幅）
    """
    cases = []
    phases = df['phase'].values
    n = len(df)
    i = 0

    while i < n - 50:
        # 找Phase2开始
        if phases[i] not in (2, 3):
            i += 1
            continue

        # 找压缩段起点（往前找Phase1开始）
        squeeze_start = i
        for j in range(i, max(0, i-200), -1):
            if phases[j] == 0:
                squeeze_start = j + 1
                break

        # 找Phase3爆发点
        burst_idx = None
        for j in range(i, min(n-1, i+100)):
            if phases[j] == 3:
                burst_idx = j
                break

        if burst_idx is None:
            i += 1
            continue

        # 爆发方向
        pre_close  = df['close'].iloc[burst_idx - 1]
        burst_close = df['close'].iloc[burst_idx]
        direction = 'UP' if burst_close > pre_close else 'DOWN'

        # 压缩最低BB宽度
        squeeze_slice = df.iloc[squeeze_start:burst_idx]
        min_bb_width  = squeeze_slice['bb_width'].min() if len(squeeze_slice) > 0 else 0
        squeeze_bars  = burst_idx - squeeze_start

        # 爆发幅度
        atr = df['atr14'].iloc[burst_idx]
        price_move = abs(burst_close - pre_close)
        burst_atr_mult = price_move / atr if atr > 0 else 0

        # 量比峰值（爆发后5根）
        vol_peak = df['vol_ratio'].iloc[burst_idx:min(n, burst_idx+5)].max()

        # RSI at burst
        rsi_at_burst = df['rsi14'].iloc[burst_idx]

        # 24H后结果（96根15m）
        future_idx = min(n-1, burst_idx + 96)
        future_return = (df['close'].iloc[future_idx] - burst_close) / burst_close * 100

        # 是否真突破（收盘站稳BB外+量比>1.2）
        post_close = df['close'].iloc[min(n-1, burst_idx+3)]
        is_genuine = (
            (direction == 'UP'   and post_close > df['bb_up'].iloc[burst_idx]) or
            (direction == 'DOWN' and post_close < df['bb_dn'].iloc[burst_idx])
        ) and vol_peak > 1.2

        cases.append({
            'symbol':         symbol,
            'ts_squeeze_start': str(df.index[squeeze_start]),
            'ts_burst':         str(df.index[burst_idx]),
            'direction':        direction,
            'min_bb_width':     round(min_bb_width, 4),
            'squeeze_bars':     int(squeeze_bars),
            'burst_atr_mult':   round(burst_atr_mult, 2),
            'vol_ratio_peak':   round(float(vol_peak), 2),
            'rsi_at_burst':     round(float(rsi_at_burst), 1),
            'future_return_24h':round(float(future_return), 3),
            'is_genuine_breakout': bool(is_genuine),
            'pnl_long_24h':     round(float(future_return), 3) if direction == 'UP' else round(-float(future_return), 3),
        })

        i = burst_idx + 10  # 跳过爆发后延伸段

    return cases


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def process_symbol(symbol: str, save_labeled: bool = True) -> dict:
    """处理单个资产"""
    sym_lower = symbol.lower()
    if not sym_lower.endswith('usdt'):
        sym_lower += 'usdt'

    fpath = BASE / 'data' / 'historical' / sym_lower / f'{sym_lower}_15m.parquet'
    if not fpath.exists():
        print(f"  ❌ {symbol}: 历史数据不存在 {fpath}")
        return {}

    print(f"\n{'='*55}")
    print(f"📊 处理 {symbol.upper()}")

    t0 = time.time()
    df = pd.read_parquet(fpath)
    print(f"  读取: {len(df):,}根K线 {df.index[0].strftime('%Y-%m-%d')}→{df.index[-1].strftime('%Y-%m-%d')}")

    # 计算指标
    df = compute_indicators(df)
    print(f"  指标计算完成 ({time.time()-t0:.1f}s)")

    # 阶段标注
    df = identify_squeeze_phases(df)
    phase_dist = df['phase'].value_counts().to_dict()
    print(f"  阶段分布: P0={phase_dist.get(0,0):,} P1={phase_dist.get(1,0):,} P2={phase_dist.get(2,0):,} P3={phase_dist.get(3,0):,} P4={phase_dist.get(4,0):,}")

    # 提取案例
    cases = extract_squeeze_cases(df, symbol.upper())
    genuine = [c for c in cases if c['is_genuine_breakout']]
    up_cases = [c for c in genuine if c['direction']=='UP']
    dn_cases = [c for c in genuine if c['direction']=='DOWN']

    # 统计
    if genuine:
        avg_squeeze_bars = sum(c['squeeze_bars'] for c in genuine) / len(genuine)
        avg_burst_atr    = sum(c['burst_atr_mult'] for c in genuine) / len(genuine)
        win_long = sum(1 for c in up_cases if c['future_return_24h'] > 0)
        win_short= sum(1 for c in dn_cases if c['future_return_24h'] < 0)
        wr_long  = win_long/len(up_cases)*100 if up_cases else 0
        wr_short = win_short/len(dn_cases)*100 if dn_cases else 0
    else:
        avg_squeeze_bars = avg_burst_atr = wr_long = wr_short = 0

    print(f"  案例总数: {len(cases)} | 真突破: {len(genuine)} | UP={len(up_cases)} DOWN={len(dn_cases)}")
    if genuine:
        print(f"  平均压缩时长: {avg_squeeze_bars:.0f}根 ({avg_squeeze_bars*15/60:.1f}H)")
        print(f"  平均爆发幅度: {avg_burst_atr:.2f}×ATR")
        print(f"  24H胜率: LONG={wr_long:.1f}% SHORT={wr_short:.1f}%")

    # 保存标注数据
    if save_labeled:
        out_labeled = BASE / 'data' / f'fangcang_labeled_{sym_lower[:3]}.parquet'
        df.to_parquet(out_labeled)
        print(f"  标注数据保存: {out_labeled} ({out_labeled.stat().st_size//1024//1024}MB)")

        out_cases = BASE / 'data' / f'fangcang_cases_{sym_lower[:3]}.json'
        with open(out_cases, 'w') as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
        print(f"  案例库保存: {out_cases} ({len(cases)}个案例)")

    return {
        'symbol': symbol.upper(),
        'total_bars': len(df),
        'total_cases': len(cases),
        'genuine_cases': len(genuine),
        'up_cases': len(up_cases),
        'down_cases': len(dn_cases),
        'wr_long': round(wr_long, 1),
        'wr_short': round(wr_short, 1),
        'avg_squeeze_bars': round(avg_squeeze_bars, 0),
        'avg_burst_atr': round(avg_burst_atr, 2),
        'elapsed_s': round(time.time()-t0, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', type=str, default='BTC')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--quick', action='store_true', help='仅处理最近2年（快速验证）')
    args = parser.parse_args()

    symbols = ['BTC','ETH','SOL'] if args.all else [args.symbol]

    print(f"🏛️ 方仓标注引擎 v1.0 Step2")
    print(f"目标: {symbols} | {'全量6.5年' if not args.quick else '最近2年(快速)'}")

    results = []
    for sym in symbols:
        r = process_symbol(sym)
        if r:
            results.append(r)

    # 汇总报告
    if results:
        print(f"\n{'='*55}")
        print(f"📊 Step2 方仓标注汇总")
        print(f"{'='*55}")
        total_cases = sum(r['genuine_cases'] for r in results)
        for r in results:
            print(f"\n{r['symbol']}:")
            print(f"  K线数: {r['total_bars']:,}根")
            print(f"  真突破案例: {r['genuine_cases']}个 (UP={r['up_cases']} DOWN={r['down_cases']})")
            print(f"  24H胜率: LONG={r['wr_long']}% SHORT={r['wr_short']}%")
            print(f"  平均压缩: {r['avg_squeeze_bars']:.0f}根({r['avg_squeeze_bars']*15/60:.1f}H)")
            print(f"  平均爆发: {r['avg_burst_atr']}×ATR")
            print(f"  耗时: {r['elapsed_s']}s")

        print(f"\n总案例数: {total_cases}个真突破案例")
        print(f"数据存储: data/fangcang_labeled_*.parquet + data/fangcang_cases_*.json")
        print(f"✅ Step2完成，可执行Step3: fangcang_engine接入")


if __name__ == '__main__':
    main()
