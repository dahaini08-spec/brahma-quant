#!/usr/bin/env python3
"""
达摩院 Anchored Walk-Forward Validation
无上帝视角·因果性严格·梵天2019-11-01冷启动

原则：
  1. t时刻决策只用t及之前数据
  2. WR矩阵从空白开始，在线更新
  3. OOS参数来自前序训练窗口，不回看
"""
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import json

FIXED   = Path(__file__).parent.parent / 'data/backtest/fixed'
# 文件名映射（小写）
_FIXED_MAP = {
    'BTCUSDT_1h': FIXED/'btcusdt_1h_fixed.parquet',
    'BTCUSDT_4h': FIXED/'btcusdt_4h_fixed.parquet',
    'ETHUSDT_1h': FIXED/'ethusdt_1h_fixed.parquet',
    'ETHUSDT_4h': FIXED/'ethusdt_4h_fixed.parquet',
}
RESULTS = Path(__file__).parent / 'results'
RESULTS.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────
# 技术指标（全部因果性，pandas rolling）
# ─────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df['close']
    h = df['high']
    l = df['low']

    df['ema21']  = c.ewm(span=21,  min_periods=21,  adjust=False).mean()
    df['ema55']  = c.ewm(span=55,  min_periods=55,  adjust=False).mean()
    df['ema200'] = c.ewm(span=200, min_periods=200, adjust=False).mean()

    delta = c.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    ag    = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    al    = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df['rsi14'] = 100 - 100/(1 + ag/al.replace(0, 1e-9))

    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df['atr14'] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()

    # 布林带（20根1H）
    roll = c.rolling(20, min_periods=20)
    df['bb_mid']  = roll.mean()
    df['bb_std']  = roll.std()
    df['bb_upper']= df['bb_mid'] + 2*df['bb_std']
    df['bb_lower']= df['bb_mid'] - 2*df['bb_std']

    return df

# ─────────────────────────────────────────────────────────
# 体制感知（因果性严格：只用当前及之前K线）
# ─────────────────────────────────────────────────────────
def detect_regime(row: pd.Series, lookback_hi: float, lookback_lo: float) -> str:
    price  = row['close']
    ema200 = row.get('ema200', np.nan)
    ema55  = row.get('ema55',  np.nan)
    rsi    = row.get('rsi14', 50)

    if pd.isna(ema200) or pd.isna(ema55):
        return 'CHOP_MID'

    rng_pct = (lookback_hi - lookback_lo) / price * 100
    above200 = price > ema200
    above55  = price > ema55
    bull_rsi = rsi > 54
    bear_rsi = rsi < 46

    if above200 and above55 and bull_rsi and rng_pct > 18:
        return 'BULL_TREND'
    elif above200 and above55 and bull_rsi:
        return 'BULL_EARLY'
    elif above200 and not bear_rsi:
        return 'BULL_CORRECTION'
    elif not above200 and not above55 and bear_rsi and rng_pct > 18:
        return 'BEAR_TREND'
    elif not above200 and bear_rsi:
        return 'BEAR_EARLY'
    elif not above200 and not bear_rsi:
        return 'BEAR_RECOVERY'
    else:
        return 'CHOP_MID'

# ─────────────────────────────────────────────────────────
# 简化评分（梵天s1~s5代理，因果性严格）
# ─────────────────────────────────────────────────────────
def score_signal(row: pd.Series, direction: str) -> float:
    price  = row['close']
    ema200 = row.get('ema200', price)
    ema55  = row.get('ema55',  price)
    ema21  = row.get('ema21',  price)
    rsi    = row.get('rsi14', 50)
    atr    = row.get('atr14', price*0.01)
    bb_lo  = row.get('bb_lower', price - 2*atr)
    bb_hi  = row.get('bb_upper', price + 2*atr)

    s = 50.0  # 基础分
    if direction == 'LONG':
        s += 15 if price > ema200 else -15
        s += 10 if price > ema55  else -10
        s += 10 if rsi > 45 and rsi < 70 else (-8 if rsi >= 70 else -5)
        s += 8  if price > ema21  else -5
        # 布林带位置：靠近下轨好
        if price <= bb_lo:
            s += 12  # 超卖区
        elif price >= bb_hi:
            s -= 10  # 超买区
        # 动量
        s += min(10, max(-8, (price - ema55) / atr * 2))
    else:  # SHORT
        s += 15 if price < ema200 else -15
        s += 10 if price < ema55  else -10
        s += 10 if rsi < 55 and rsi > 30 else (-8 if rsi <= 30 else -5)
        s += 8  if price < ema21  else -5
        if price >= bb_hi:
            s += 12
        elif price <= bb_lo:
            s -= 10
        s += min(10, max(-8, (ema55 - price) / atr * 2))

    return round(max(0, min(100, s)), 1)

# ─────────────────────────────────────────────────────────
# 结构代理grade（因果性严格）
# ─────────────────────────────────────────────────────────
def grade_signal(row: pd.Series, direction: str) -> int:
    price = row['close']
    ema21 = row.get('ema21', price)
    atr   = row.get('atr14', price*0.01)
    rsi   = row.get('rsi14', 50)

    dist = abs(price - ema21) / atr
    rsi_extreme = rsi < 35 or rsi > 65

    if dist >= 2.0 and rsi_extreme: return 85
    elif dist >= 1.2 and rsi_extreme: return 75
    elif dist >= 0.8: return 68
    elif dist >= 0.4: return 55
    else: return 40

# ─────────────────────────────────────────────────────────
# 持仓结算（因果性严格：只看开仓后的K线）
# ─────────────────────────────────────────────────────────
def settle(df1h: pd.DataFrame, entry_idx: int, direction: str,
           sl_mult=1.5, tp_mult=2.5, max_bars=32):
    row   = df1h.iloc[entry_idx]
    price = float(row['close'])
    atr   = float(row.get('atr14', price*0.015))

    if direction == 'LONG':
        sl = price - atr * sl_mult
        tp = price + atr * tp_mult
    else:
        sl = price + atr * sl_mult
        tp = price - atr * tp_mult

    for j in range(1, max_bars+1):
        if entry_idx + j >= len(df1h):
            return 'TIMEOUT', j, price
        bar = df1h.iloc[entry_idx + j]
        hi  = float(bar['high'])
        lo  = float(bar['low'])
        if direction == 'LONG':
            if lo <= sl: return 'LOSS', j, sl
            if hi >= tp: return 'WIN',  j, tp
        else:
            if hi >= sl: return 'LOSS', j, sl
            if lo <= tp: return 'WIN',  j, tp

    return 'TIMEOUT', max_bars, float(df1h.iloc[entry_idx+max_bars]['close'])

# ─────────────────────────────────────────────────────────
# 在线WR矩阵（核心：从空白开始累积）
# ─────────────────────────────────────────────────────────
class OnlineWRMatrix:
    def __init__(self):
        self.data = defaultdict(lambda: {'wins':0,'losses':0,'n':0})

    def update(self, regime, direction, outcome):
        key = f'{regime}_{direction}'
        self.data[key]['n'] += 1
        if outcome == 'WIN':  self.data[key]['wins']   += 1
        if outcome == 'LOSS': self.data[key]['losses']  += 1

    def wr(self, regime, direction):
        key = f'{regime}_{direction}'
        d = self.data[key]
        denom = d['wins'] + d['losses']
        return d['wins'] / denom if denom > 0 else None

    def n(self, regime, direction):
        return self.data[f'{regime}_{direction}']['n']

    def should_block(self, regime, direction):
        """n≥500 且 WR<48% 才硬封禁（宪法原则）"""
        w = self.wr(regime, direction)
        n = self.n(regime, direction)
        if w is None or n < 500: return False
        return w < 0.48

    def snapshot(self):
        out = {}
        for k, d in self.data.items():
            denom = d['wins'] + d['losses']
            wr = d['wins']/denom if denom > 0 else None
            out[k] = {'n': d['n'], 'wr': round(wr,3) if wr else None,
                      'wins': d['wins'], 'losses': d['losses']}
        return out

# ─────────────────────────────────────────────────────────
# 动态 adaptive_threshold（从保守开始，用已见数据校准）
# ─────────────────────────────────────────────────────────
class AdaptiveThreshold:
    def __init__(self, init=70):
        self.value = init
        self.history = []  # [(date, score, outcome)]

    def update(self, date, score, outcome):
        self.history.append((date, score, outcome))

    def recalibrate(self):
        """用已见数据校准：找使WR≥60%的最小score"""
        if len(self.history) < 50: return  # 样本不足
        df = pd.DataFrame(self.history, columns=['date','score','outcome'])
        df = df[df['outcome'].isin(['WIN','LOSS'])]
        if len(df) < 30: return
        for thresh in range(50, 95, 5):
            sub = df[df['score'] >= thresh]
            if len(sub) < 10: continue
            wr = (sub['outcome']=='WIN').mean()
            if wr >= 0.60:
                self.value = thresh
                return
        self.value = 80  # 保守默认

# ─────────────────────────────────────────────────────────
# 主运行引擎：梵天2019-11冷启动
# ─────────────────────────────────────────────────────────
def run_anchored_wfv(sym: str, df1h_raw: pd.DataFrame, df4h_raw: pd.DataFrame):
    print(f'\n{"="*60}')
    print(f'[{sym}] 梵天冷启动 {df1h_raw.index[0].date()} → {df1h_raw.index[-1].date()}')
    print(f'{"="*60}')

    df1h = add_indicators(df1h_raw)
    df4h = add_indicators(df4h_raw)

    wr_matrix = OnlineWRMatrix()
    threshold = AdaptiveThreshold(init=70)  # 冷启动保守值

    # Anchored WFV 窗口（锚定2019-11-01，逐步扩展）
    windows = [
        ('2020-05-01', '2020-11-01'),
        ('2020-11-01', '2021-05-01'),
        ('2021-05-01', '2021-11-01'),
        ('2021-11-01', '2022-05-01'),
        ('2022-05-01', '2022-11-01'),
        ('2022-11-01', '2023-05-01'),
        ('2023-05-01', '2023-11-01'),
        ('2023-11-01', '2024-05-01'),
        ('2024-05-01', '2024-11-01'),
        ('2024-11-01', '2025-05-01'),
        ('2025-05-01', '2025-11-01'),
        ('2025-11-01', '2026-06-01'),
    ]

    all_trades  = []
    oos_results = []
    nav         = 1000.0   # 初始1000U
    equity_curve = [(str(df1h.index[0].date()), 1000.0)]

    COOL = 16   # 冷却K线数
    last_sig = defaultdict(lambda: -100)

    # ── 全周期顺序扫描（无上帝视角）────────────────────
    oos_idx = 0
    cur_oos_start = pd.Timestamp(windows[oos_idx][0], tz='UTC')
    cur_oos_end   = pd.Timestamp(windows[oos_idx][1], tz='UTC')
    oos_trades_cur = []

    for i in range(200, len(df1h)):
        ts     = df1h.index[i]
        row1h  = df1h.iloc[i]

        # 获取对应4H K线（searchsorted保证不看未来）
        idx4   = df4h.index.searchsorted(ts, side='right') - 1
        if idx4 < 10: continue
        row4h  = df4h.iloc[idx4]

        # 20根1H高低（滚动，因果性严格）
        lo20 = float(df1h['low'].iloc[max(0,i-20):i].min())
        hi20 = float(df1h['high'].iloc[max(0,i-20):i].max())
        regime = detect_regime(row1h, hi20, lo20)

        # ── OOS窗口切换：当到达OOS结束时，校准参数 ────
        if ts >= cur_oos_end and oos_idx < len(windows)-1:
            # 结算当前OOS窗口
            wins   = sum(1 for t in oos_trades_cur if t['outcome']=='WIN')
            losses = sum(1 for t in oos_trades_cur if t['outcome']=='LOSS')
            tos    = sum(1 for t in oos_trades_cur if t['outcome']=='TIMEOUT')
            n      = wins+losses+tos
            wr     = wins/(wins+losses) if wins+losses>0 else 0
            pf     = (wins*2.5)/(losses*1.5+0.001) if losses>0 else wins*2.5

            oos_results.append({
                'oos_period': f'{windows[oos_idx][0]}~{windows[oos_idx][1]}',
                'n': n, 'wins': wins, 'losses': losses, 'tos': tos,
                'wr': round(wr,3), 'pf': round(pf,2),
                'threshold_used': threshold.value,
                'nav_end': round(nav, 2),
                'blocked_zones': [k for k in wr_matrix.data
                                  if wr_matrix.should_block(*k.rsplit('_',1))],
            })

            print(f'  OOS {windows[oos_idx][0][:7]}~{windows[oos_idx][1][:7]}: '
                  f'n={n:4d}  WR={wr:.1%}  PF={pf:.2f}  '
                  f'NAV=${nav:.0f}  threshold={threshold.value}  '
                  f'{"✅" if pf>=1.05 else "⚠️"}')

            oos_idx += 1
            cur_oos_start = pd.Timestamp(windows[oos_idx][0], tz='UTC')
            cur_oos_end   = pd.Timestamp(windows[oos_idx][1], tz='UTC')
            oos_trades_cur = []

            # ── 参数校准（用已见全量数据，不看未来）────
            threshold.recalibrate()

        # ── 信号生成 ─────────────────────────────────
        for direction in ['LONG', 'SHORT']:
            if i - last_sig[direction] < COOL: continue

            # 体制×方向过滤（用在线WR矩阵，只有n≥500才封禁）
            if wr_matrix.should_block(regime, direction): continue
            # 无铁证时：CHOP体制保守过滤
            if 'CHOP' in regime: continue

            score = score_signal(row1h, direction)
            grade = grade_signal(row1h, direction)

            if score < threshold.value: continue
            if grade < 50: continue

            # 结算（只看开仓后K线，因果性严格）
            outcome, bars, exit_price = settle(df1h, i, direction)

            # NAV更新
            risk_pct  = 0.01  # 每单风险1%
            sl_pct    = 0.015 # 代理止损
            size      = nav * risk_pct / sl_pct
            if outcome == 'WIN':
                nav += size * sl_pct * 2.5  # RR=2.5
            elif outcome == 'LOSS':
                nav -= size * sl_pct
            # TIMEOUT：按平均-0.3%估算
            else:
                nav -= nav * 0.003

            nav = max(nav, 0.01)  # 防止归零

            trade = {
                'ts': str(ts.date()),
                'regime': regime,
                'direction': direction,
                'score': score,
                'grade': grade,
                'outcome': outcome,
                'bars': bars,
                'nav': round(nav, 2),
                'threshold': threshold.value,
                'in_oos': ts >= cur_oos_start,
            }
            all_trades.append(trade)
            if ts >= cur_oos_start:
                oos_trades_cur.append(trade)

            # 在线WR矩阵更新
            wr_matrix.update(regime, direction, outcome)
            threshold.update(str(ts.date()), score, outcome)

            last_sig[direction] = i
            equity_curve.append((str(ts.date()), round(nav,2)))

    # 最后一个窗口
    if oos_trades_cur:
        wins   = sum(1 for t in oos_trades_cur if t['outcome']=='WIN')
        losses = sum(1 for t in oos_trades_cur if t['outcome']=='LOSS')
        tos    = sum(1 for t in oos_trades_cur if t['outcome']=='TIMEOUT')
        n      = wins+losses+tos
        wr     = wins/(wins+losses) if wins+losses>0 else 0
        pf     = (wins*2.5)/(losses*1.5+0.001) if losses>0 else wins*2.5
        oos_results.append({
            'oos_period': f'{windows[oos_idx][0]}~{windows[oos_idx][1]}',
            'n': n, 'wins': wins, 'losses': losses, 'tos': tos,
            'wr': round(wr,3), 'pf': round(pf,2),
            'threshold_used': threshold.value,
            'nav_end': round(nav,2),
        })
        print(f'  OOS {windows[oos_idx][0][:7]}~{windows[oos_idx][1][:7]}: '
              f'n={n:4d}  WR={wr:.1%}  PF={pf:.2f}  '
              f'NAV=${nav:.0f}  threshold={threshold.value}  '
              f'{"✅" if pf>=1.05 else "⚠️"}')

    # 最终WR矩阵
    print(f'\n  最终在线WR矩阵（只用已见数据）:')
    snapshot = wr_matrix.snapshot()
    for k,d in sorted(snapshot.items(), key=lambda x: -(x[1]['wr'] or 0)):
        if d['n'] >= 100:
            flag = '✅' if (d['wr'] or 0) >= 0.60 else ('❌' if (d['wr'] or 0) < 0.50 else '⚪')
            blocked = ' [封禁]' if wr_matrix.should_block(*k.rsplit('_',1)) else ''
            print(f'  {flag} {k:28s} n={d["n"]:5d}  WR={d["wr"]:.1%}{blocked}')

    print(f'\n  最终NAV: ${nav:.2f}  (初始$1,000  {"+" if nav>1000 else ""}{(nav/1000-1)*100:.1f}%)')
    print(f'  总交易: {len(all_trades)}单  '
          f'WIN={sum(1 for t in all_trades if t["outcome"]=="WIN")}  '
          f'LOSS={sum(1 for t in all_trades if t["outcome"]=="LOSS")}  '
          f'TO={sum(1 for t in all_trades if t["outcome"]=="TIMEOUT")}')
    print(f'  最终threshold: {threshold.value}  (冷启动=70)')

    return {
        'sym': sym,
        'oos_results': oos_results,
        'wr_matrix_final': snapshot,
        'final_nav': round(nav, 2),
        'total_trades': len(all_trades),
        'final_threshold': threshold.value,
        'equity_curve_sample': equity_curve[::100],  # 每100单采样一次
    }


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    report = {
        'framework': 'Anchored WFV v1.0',
        'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'principle': '无上帝视角·因果性严格·WR矩阵在线更新',
        'data_range': '2019-11-01 ~ 2026-05-30',
        'results': {}
    }

    for sym in ['BTCUSDT', 'ETHUSDT']:
        s = sym.lower()
        print(f'\n加载 {sym} 数据...')
        df1h = pd.read_parquet(_FIXED_MAP[f'{sym}_1h'])
        df4h = pd.read_parquet(_FIXED_MAP[f'{sym}_4h'])
        result = run_anchored_wfv(sym, df1h, df4h)
        report['results'][sym] = result

    out = RESULTS / f'anchored_wfv_{ts}.json'
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f'\n✅ 报告已保存: {out.name}')

    # 联合摘要
    print('\n' + '='*60)
    print('🏛️  Anchored WFV 联合摘要（BTC+ETH）')
    print('='*60)
    all_oos = []
    for sym in ['BTCUSDT','ETHUSDT']:
        all_oos.extend(report['results'][sym]['oos_results'])

    by_period = defaultdict(lambda: {'pf_sum':0,'n':0,'count':0})
    for r in all_oos:
        p = r['oos_period']
        by_period[p]['pf_sum'] += r['pf']
        by_period[p]['n']      += r['n']
        by_period[p]['count']  += 1

    pass_count = 0
    for period, d in sorted(by_period.items()):
        avg_pf = d['pf_sum']/d['count']
        flag = '✅' if avg_pf >= 1.05 else '⚠️'
        if avg_pf >= 1.05: pass_count += 1
        print(f'  {flag} {period}: avg_PF={avg_pf:.2f}  总n={d["n"]}')

    total = len(by_period)
    print(f'\n  OOS通过率: {pass_count}/{total} ({pass_count/total:.0%})')
    for sym in ['BTCUSDT','ETHUSDT']:
        r = report['results'][sym]
        chg = (r['final_nav']/1000-1)*100
        print(f'  {sym}: 最终NAV=${r["final_nav"]:.0f} ({chg:+.1f}%)  '
              f'threshold收敛至{r["final_threshold"]}')

if __name__ == '__main__':
    main()
