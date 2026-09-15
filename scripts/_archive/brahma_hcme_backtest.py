#!/usr/bin/env python3
"""
梵天方仓数据库实景回测引擎
三方联合开发：梵天大脑 × 达摩院 × 设计院
2026-09-08 苏摩111封印

实景回测逻辑：
1. 从历史K线逐根重建94维特征向量
2. 每根K线用HCMEMatcher查询top-K相似案例
3. 按相似度加权计算预期收益/胜率/EV
4. 统计回测结果：总收益/胜率/Sharpe/最大回撤
"""
import sys, json, os, argparse, time
from datetime import datetime, timezone
import numpy as np
import requests

sys.path.insert(0, '/root/.openclaw/workspace/trading-system')
sys.path.insert(0, '/root/.openclaw/workspace/trading-system/brahma_brain')

from brahma_brain.fangcang_engine import HCMEMatcher

# ── 配置 ──────────────────────────────────────────────
DEFAULT_SYMBOL  = 'BTCUSDT'
DEFAULT_TF      = '4h'
DEFAULT_BARS    = 180          # 回测K线数量（4H×180=30天）
DEFAULT_TOPK    = 8            # 相似案例取前K个
DEFAULT_MIN_SIM = 0.85         # 最低相似度阈值
SL_PCT          = 0.020        # 止损2%
TP_MULT         = 1.5          # TP = SL × 1.5（RR=1.5）
MIN_EV          = 0.3          # 最低EV%才入场

# ── 实用工具 ──────────────────────────────────────────
def ts_str(ts_ms):
    return datetime.utcfromtimestamp(ts_ms/1000).strftime('%m/%d %H:00')

def get_klines(symbol, interval, limit=500):
    r = requests.get('https://fapi.binance.com/fapi/v1/klines',
        params={'symbol': symbol, 'interval': interval, 'limit': limit},
        timeout=10)
    return r.json()

def get_oi_hist(symbol, period, limit=500):
    r = requests.get('https://fapi.binance.com/futures/data/openInterestHist',
        params={'symbol': symbol, 'period': period, 'limit': limit},
        timeout=10)
    return r.json()

def get_lsr_hist(symbol, period, limit=500):
    r = requests.get('https://fapi.binance.com/futures/data/globalLongShortAccountRatio',
        params={'symbol': symbol, 'period': period, 'limit': limit},
        timeout=10)
    return r.json()

# ── 特征向量构建（与fangcang_builder一致的15维）────────
def build_vec_from_bar(bars, idx, oi_series, lsr_series):
    """
    从K线数据重建15维特征向量（与历史案例对齐）
    bars: [(ts,o,h,l,c,v), ...]
    idx: 当前bar索引
    """
    if idx < 30:
        return None

    prices = [b[4] for b in bars[:idx+1]]  # 收盘价序列
    closes = np.array(prices[-50:])         # 取最近50根
    n = len(closes)

    # 1. RSI14
    deltas = np.diff(closes[-15:])
    gains  = np.where(deltas>0, deltas, 0)
    losses = np.where(deltas<0, -deltas, 0)
    avg_g  = np.mean(gains) if gains.any() else 1e-9
    avg_l  = np.mean(losses) if losses.any() else 1e-9
    rsi    = 100 - (100/(1 + avg_g/max(avg_l, 1e-9)))

    # 2. BBW（布林带宽度）
    if n >= 20:
        ma20   = np.mean(closes[-20:])
        std20  = np.std(closes[-20:])
        bbw    = std20 / ma20
    else:
        bbw = 0.02

    # 3. ATR14（归一化）
    highs  = [b[2] for b in bars[max(0,idx-14):idx+1]]
    lows_  = [b[3] for b in bars[max(0,idx-14):idx+1]]
    atrs   = [h-l for h,l in zip(highs, lows_)]
    atr    = np.mean(atrs) / closes[-1] if atrs else 0.01

    # 4. ADX14
    if len(highs) >= 14:
        tr_list = [max(h-l, abs(h-closes[-2]), abs(l-closes[-2]))
                   for h,l in zip(highs[1:], lows_[1:])]
        adx = min(np.mean(tr_list) / closes[-1] * 100, 100) if tr_list else 25.0
    else:
        adx = 25.0

    # 5. 动量（5bar收益率）
    mom5 = (closes[-1]/closes[-6] - 1)*100 if len(closes)>=6 else 0

    # 6. 成交量比率
    vols  = [b[5] for b in bars[max(0,idx-10):idx+1]]
    vol_r = vols[-1] / np.mean(vols[:-1]) if len(vols)>1 and np.mean(vols[:-1])>0 else 1.0

    # 7. OI变化率（最近4根）
    if oi_series and idx < len(oi_series) and idx >= 4:
        oi_now  = float(oi_series[min(idx, len(oi_series)-1)].get('sumOpenInterest', 0))
        oi_prev = float(oi_series[max(0, idx-4)].get('sumOpenInterest', 1))
        oi_chg  = (oi_now/max(oi_prev,1) - 1)*100
    else:
        oi_chg = 0.0

    # 8. LSR
    if lsr_series and idx < len(lsr_series):
        lsr_val = float(lsr_series[min(idx, len(lsr_series)-1)].get('longAccount', 0.5))
    else:
        lsr_val = 0.5

    # 9. Hurst（R/S法）
    if len(closes) >= 32:
        log_ret = np.diff(np.log(closes[-32:]))
        rs_vals = []
        for lag in [8, 16]:
            chunks = [log_ret[i:i+lag] for i in range(0, len(log_ret)-lag, lag)]
            rs_list = []
            for c in chunks:
                dev = np.cumsum(c - np.mean(c))
                S = np.std(c)
                if S > 0: rs_list.append((dev.max()-dev.min())/S)
            if rs_list:
                rs_vals.append((lag, np.mean(rs_list)))
        if len(rs_vals) >= 2:
            hurst = float(np.polyfit(np.log([x[0] for x in rs_vals]),
                                     np.log([x[1] for x in rs_vals]), 1)[0])
        else:
            hurst = 0.5
    else:
        hurst = 0.5

    # 10. 价格位置（相对20日高低点）
    high20 = max(b[2] for b in bars[max(0,idx-20):idx+1])
    low20  = min(b[3] for b in bars[max(0,idx-20):idx+1])
    price_pos = (closes[-1]-low20) / max(high20-low20, 1e-9)

    # 11. 趋势方向（EMA8 vs EMA21）
    if len(closes) >= 21:
        ema8  = float(np.convolve(closes[-8:],  np.ones(8)/8,  'valid')[-1])
        ema21 = float(np.convolve(closes[-21:], np.ones(21)/21,'valid')[-1])
        trend = 1.0 if ema8 > ema21 else -1.0
    else:
        trend = 0.0

    # 12-15. 波动率结构
    vol_std  = np.std(closes[-10:]) / closes[-1] if len(closes)>=10 else 0.01
    ret_1h   = (closes[-1]/closes[-2] - 1)*100 if len(closes)>=2 else 0
    ret_4h   = (closes[-1]/closes[-5] - 1)*100 if len(closes)>=5 else 0
    ret_24h  = (closes[-1]/closes[-7] - 1)*100 if len(closes)>=7 else 0

    vec = [
        rsi/100,           # 1. RSI归一化
        bbw,               # 2. 布林带宽度
        atr,               # 3. ATR归一化
        adx/100,           # 4. ADX归一化
        mom5/10,           # 5. 5bar动量
        min(vol_r, 5)/5,   # 6. 成交量比率
        oi_chg/10,         # 7. OI变化率
        lsr_val,           # 8. 散户多空比
        hurst,             # 9. Hurst指数
        price_pos,         # 10. 价格位置
        (trend+1)/2,       # 11. 趋势方向归一化
        vol_std,           # 12. 收盘价波动率
        ret_1h/5,          # 13. 1bar收益率
        ret_4h/10,         # 14. 4bar收益率
        ret_24h/15,        # 15. 24bar收益率
    ]
    return [max(min(v, 3.0), -3.0) for v in vec]  # 截断极值


# ── 回测执行引擎 ──────────────────────────────────────
def run_backtest(symbol=DEFAULT_SYMBOL, tf=DEFAULT_TF,
                 bars=DEFAULT_BARS, topk=DEFAULT_TOPK,
                 min_sim=DEFAULT_MIN_SIM, verbose=False):

    print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print(f'🏛️ 梵天方仓实景回测 | {symbol} {tf} | {bars}根K线')
    print(f'   top_k={topk}  min_sim={min_sim}  SL={SL_PCT*100:.1f}%  RR={TP_MULT}x')
    print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')

    # 加载方仓匹配器
    matcher = HCMEMatcher()
    print(f'方仓案例总量: {len(matcher.cases)}条')

    # 拉历史数据（多拉50根用于预热）
    print(f'拉取K线数据...')
    kl = get_klines(symbol, tf, bars+50)
    oi_hist  = get_oi_hist(symbol, tf, bars+50)
    lsr_hist = get_lsr_hist(symbol, tf, bars+50)
    print(f'K线: {len(kl)}根 | OI: {len(oi_hist)}条 | LSR: {len(lsr_hist)}条')

    bars_data = [(
        int(k[0]),        # ts
        float(k[1]),      # open
        float(k[2]),      # high
        float(k[3]),      # low
        float(k[4]),      # close
        float(k[5]),      # volume
    ) for k in kl]

    # ── 回测主循环 ──
    signals = []        # 所有信号
    trades  = []        # 实际成交
    warmup  = 50        # 预热根数

    print(f'\n开始回测（跳过前{warmup}根预热）...')

    for i in range(warmup, len(bars_data)-1):
        bar      = bars_data[i]
        ts_str_  = ts_str(bar[0])
        close    = bar[4]

        # 构建特征向量
        vec = build_vec_from_bar(bars_data, i, oi_hist, lsr_hist)
        if vec is None:
            continue

        # HCME相似度查询
        try:
            similar = matcher.find_similar(vec, top_k=topk)
        except Exception as e:
            continue

        if not similar:
            continue

        # 过滤低相似度
        filtered = [s for s in similar if s.get('similarity', s.get('score', 0)) >= min_sim]
        if not filtered:
            continue

        # 统计相似案例的加权预期
        total_sim = sum(s.get('similarity', s.get('score', 0)) for s in filtered)
        if total_sim <= 0:
            continue

        weighted_win  = sum(s.get('similarity', s.get('score',0)) * (1 if s.get('is_win') else 0)
                           for s in filtered) / total_sim
        weighted_pnl  = sum(s.get('similarity', s.get('score',0)) * s.get('pnl_pct', 0)
                           for s in filtered) / total_sim
        avg_direction = sum(s.get('similarity', s.get('score',0)) *
                           (1 if s.get('direction','LONG')=='LONG' else -1)
                           for s in filtered) / total_sim

        # 方向判断
        if avg_direction > 0.3:
            direction = 'LONG'
        elif avg_direction < -0.3:
            direction = 'SHORT'
        else:
            continue  # 方向不明确，跳过

        # EV过滤
        ev = weighted_win * SL_PCT*100*TP_MULT - (1-weighted_win) * SL_PCT*100
        if ev < MIN_EV:
            continue

        # 记录信号
        sig = {
            'ts': ts_str_,
            'close': close,
            'direction': direction,
            'win_prob': round(weighted_win, 3),
            'exp_pnl': round(weighted_pnl, 3),
            'ev': round(ev, 3),
            'n_similar': len(filtered),
            'top_sim': round(max(s.get('similarity',s.get('score',0)) for s in filtered), 3),
        }
        signals.append(sig)

        # 模拟交易执行（用下一根K线）
        if i+1 < len(bars_data):
            next_bar = bars_data[i+1]
            entry    = next_bar[1]  # 下根开盘价
            sl       = entry * (1 - SL_PCT) if direction=='LONG' else entry * (1 + SL_PCT)
            tp       = entry * (1 + SL_PCT*TP_MULT) if direction=='LONG' else entry * (1 - SL_PCT*TP_MULT)

            # 用下根K线内判断是否触及SL/TP
            bar_high = next_bar[2]
            bar_low  = next_bar[3]
            bar_close= next_bar[4]

            if direction == 'LONG':
                if bar_low <= sl:
                    result = 'SL'
                    pnl    = -SL_PCT * 100
                elif bar_high >= tp:
                    result = 'TP'
                    pnl    = SL_PCT * TP_MULT * 100
                else:
                    result = 'OPEN'
                    pnl    = (bar_close - entry) / entry * 100
            else:
                if bar_high >= sl:
                    result = 'SL'
                    pnl    = -SL_PCT * 100
                elif bar_low <= tp:
                    result = 'TP'
                    pnl    = SL_PCT * TP_MULT * 100
                else:
                    result = 'OPEN'
                    pnl    = (entry - bar_close) / entry * 100

            trade = {**sig, 'entry': round(entry, 2), 'sl': round(sl, 2),
                     'tp': round(tp, 2), 'result': result, 'pnl': round(pnl, 3)}
            trades.append(trade)

            if verbose:
                icon = '✅' if result=='TP' else ('❌' if result=='SL' else '⏳')
                print(f'  {icon} {ts_str_} {direction} @{entry:.0f} → {result} {pnl:+.2f}%  '
                      f'EV={ev:.2f}% sim={sig["top_sim"]} n={len(filtered)}')

    # ── 统计分析 ──
    print(f'\n━━ 回测结果 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    print(f'信号总数:    {len(signals)}个')
    print(f'交易总数:    {len(trades)}笔')

    if not trades:
        print('无有效交易')
        return

    tp_trades = [t for t in trades if t['result']=='TP']
    sl_trades = [t for t in trades if t['result']=='SL']
    op_trades = [t for t in trades if t['result']=='OPEN']
    longs     = [t for t in trades if t['direction']=='LONG']
    shorts    = [t for t in trades if t['direction']=='SHORT']

    wr = len(tp_trades)/len(trades)*100
    total_pnl = sum(t['pnl'] for t in trades)
    avg_win   = sum(t['pnl'] for t in tp_trades)/len(tp_trades) if tp_trades else 0
    avg_loss  = sum(t['pnl'] for t in sl_trades)/len(sl_trades) if sl_trades else 0

    # Sharpe（简化版）
    pnls = [t['pnl'] for t in trades]
    sharpe = (np.mean(pnls)/np.std(pnls)*np.sqrt(252)) if np.std(pnls)>0 else 0

    # 最大回撤
    equity = np.cumsum([0] + pnls)
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = float(dd.min())

    # 盈亏比
    payoff = abs(avg_win/avg_loss) if avg_loss!=0 else 0

    print(f'胜率:        {wr:.1f}%  ({len(tp_trades)}盈/{len(sl_trades)}亏/{len(op_trades)}未结)')
    print(f'总收益:      {total_pnl:+.2f}%')
    print(f'平均盈:      {avg_win:+.2f}%  平均亏: {avg_loss:+.2f}%  盈亏比: {payoff:.2f}x')
    print(f'Sharpe:      {sharpe:.2f}')
    print(f'最大回撤:    {max_dd:.2f}%')
    _long_wr = sum(1 for t in longs if t['result']=='TP')/max(len(longs),1)*100
    _short_wr = sum(1 for t in shorts if t['result']=='TP')/max(len(shorts),1)*100
    print(f'多头信号:    {len(longs)}笔  多头胜率: {_long_wr:.1f}%')
    print(f'空头信号:    {len(shorts)}笔  空头胜率: {_short_wr:.1f}%')
    print(f'\n━━ 信号质量分布 ━━')
    sim_buckets = {'高质量(sim≥0.95)':0, '中质量(0.9~0.95)':0, '一般(0.85~0.9)':0}
    for t in trades:
        s = t['top_sim']
        if s >= 0.95:   sim_buckets['高质量(sim≥0.95)'] += 1
        elif s >= 0.90: sim_buckets['中质量(0.9~0.95)'] += 1
        else:           sim_buckets['一般(0.85~0.9)']   += 1
    for k,v in sim_buckets.items():
        pct = v/len(trades)*100
        print(f'  {k}: {v}笔 ({pct:.0f}%)')

    # EV分层
    print(f'\n━━ EV分层胜率 ━━')
    ev_tiers = [(2.0,'EV≥2%'),(1.0,'EV 1~2%'),(0.5,'EV 0.5~1%'),(0.0,'EV <0.5%')]
    prev_ev = 99
    for threshold, label in ev_tiers:
        tier = [t for t in trades if threshold <= t['ev'] < prev_ev]
        if tier:
            tier_wr = sum(1 for t in tier if t['result']=='TP')/len(tier)*100
            tier_pnl = sum(t['pnl'] for t in tier)
            print(f'  {label}: {len(tier)}笔 WR={tier_wr:.0f}% 总PnL={tier_pnl:+.2f}%')
        prev_ev = threshold

    # 保存结果
    result_file = f'data/hcme_backtest_{symbol}_{tf}.json'
    result_data = {
        'symbol': symbol, 'tf': tf, 'bars': bars,
        'topk': topk, 'min_sim': min_sim,
        'total_signals': len(signals),
        'total_trades': len(trades),
        'win_rate_pct': round(wr, 2),
        'total_pnl_pct': round(total_pnl, 2),
        'sharpe': round(sharpe, 3),
        'max_drawdown_pct': round(max_dd, 2),
        'payoff_ratio': round(payoff, 2),
        'trades': trades[-20:],  # 保存最近20笔
        'run_at': datetime.utcnow().isoformat(),
    }
    with open(result_file, 'w') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {result_file}')
    print(f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    return result_data


# ── CLI入口 ────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='梵天方仓实景回测')
    parser.add_argument('--symbol', default=DEFAULT_SYMBOL)
    parser.add_argument('--tf',     default=DEFAULT_TF)
    parser.add_argument('--bars',   type=int, default=DEFAULT_BARS)
    parser.add_argument('--topk',   type=int, default=DEFAULT_TOPK)
    parser.add_argument('--min-sim',type=float, default=DEFAULT_MIN_SIM, dest='min_sim')
    parser.add_argument('--verbose',action='store_true')
    args = parser.parse_args()

    run_backtest(
        symbol=args.symbol,
        tf=args.tf,
        bars=args.bars,
        topk=args.topk,
        min_sim=args.min_sim,
        verbose=args.verbose,
    )
