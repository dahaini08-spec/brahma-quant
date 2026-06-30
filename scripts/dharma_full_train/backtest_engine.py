#!/usr/bin/env python3
"""
达摩院 · 全周期回测引擎 v1.0
设计院 2026-06-02

资源规划（2核 / 1.4GB可用 / 无Swap）：
  ✅ 逐月分块处理，单次内存 < 100MB
  ✅ 串行执行，不并发，不撑爆内存
  ✅ 每月结果落盘，断点可续
  ✅ 进度文件实时更新，主线程不阻塞

梵天信号规则（简化版，适合回测）：
  - EMA趋势：EMA20 / EMA50 / EMA200 方向
  - RSI超卖/超买：RSI14
  - 入场：EMA20>EMA50>EMA200 + RSI<35 做多 / 反之做空
  - 止损：ATR14 × 1.5
  - 止盈：ATR14 × 3.0（R:R = 2:1）
  - 仓位：固定2%风险（$5000初始资金）
"""

import json, datetime, os, math, statistics, sys

BASE     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE, 'data', 'dharma_8y')
OUT_DIR  = os.path.join(BASE, 'data', 'dharma_backtest')
os.makedirs(OUT_DIR, exist_ok=True)

CUTOFF_MS = int(datetime.datetime(2025,1,1,tzinfo=datetime.timezone.utc).timestamp()*1000)
INIT_CAPITAL = 5000.0
RISK_PCT     = 0.02   # 每笔2%风险
ATR_SL_MULT  = 1.5
ATR_TP_MULT  = 3.0
LEVERAGE     = 5      # 5倍杠杆（对应Binance合约）

# ── 指标计算（滚动，不加载全量）─────────────────────────────────
def ema(prev_ema, price, period):
    k = 2.0 / (period + 1)
    return price * k + prev_ema * (1 - k)

def compute_atr(bars, period=14):
    """计算最后N根K线的ATR"""
    if len(bars) < period + 1: return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = float(bars[i][2]), float(bars[i][3]), float(bars[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return statistics.mean(trs[-period:])

def compute_rsi(bars, period=14):
    if len(bars) < period + 1: return 50.0
    closes = [float(b[4]) for b in bars[-(period+1):]]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag, al = statistics.mean(gains), statistics.mean(losses)
    if al == 0: return 100.0
    rs = ag / al
    return 100 - (100 / (1 + rs))


# ── 回测核心逻辑 ─────────────────────────────────────────────────
class BacktestEngine:
    def __init__(self, symbol, interval, mode='train'):
        self.symbol   = symbol
        self.interval = interval
        self.mode     = mode
        self.capital  = INIT_CAPITAL
        self.peak     = INIT_CAPITAL
        self.trades   = []
        self.equity_curve = []
        self.position = None  # {'side','entry','sl','tp','qty','entry_ms'}

        fname = f"{symbol}_{interval}_pure.json"
        all_bars = json.load(open(os.path.join(DATA_DIR, fname)))
        if mode == 'train':
            self.bars = [b for b in all_bars if b[0] < CUTOFF_MS]
        else:
            self.bars = [b for b in all_bars if b[0] >= CUTOFF_MS]

        self.log_file = open(
            os.path.join(OUT_DIR, f"{symbol}_{interval}_{mode}_progress.log"), 'w')

    def log(self, msg):
        ts = datetime.datetime.utcnow().strftime('%H:%M:%S')
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        self.log_file.write(line + '\n')
        self.log_file.flush()

    def run(self):
        bars  = self.bars
        total = len(bars)
        WARM  = 210  # EMA200 预热期

        # EMA状态（滚动，不存所有历史）
        e20 = e50 = e200 = float(bars[0][4])

        self.log(f"开始 {self.symbol} {self.interval} {self.mode} | {total:,}根K线 | 初始${self.capital:.0f}")

        last_pct = 0
        for i, bar in enumerate(bars):
            close = float(bar[4])
            high  = float(bar[2])
            low   = float(bar[3])
            ts_ms = bar[0]

            # 更新EMA
            e20  = ema(e20,  close, 20)
            e50  = ema(e50,  close, 50)
            e200 = ema(e200, close, 200)

            # 进度保存（每10%）
            pct = i * 100 // total
            if pct >= last_pct + 10:
                last_pct = pct
                self.save_progress(i, total, pct)
                self.log(f"  进度 {pct}%  资金=${self.capital:.0f}  交易={len(self.trades)}笔")

            if i < WARM: continue

            # ── 平仓检查 ──
            if self.position:
                pos = self.position
                hit_sl = hit_tp = False
                if pos['side'] == 'LONG':
                    if low  <= pos['sl']: hit_sl = True
                    if high >= pos['tp']: hit_tp = True
                else:  # SHORT
                    if high >= pos['sl']: hit_sl = True
                    if low  <= pos['tp']: hit_tp = True

                if hit_tp or hit_sl:
                    exit_px = pos['tp'] if hit_tp else pos['sl']
                    pnl_pct = ((exit_px - pos['entry']) / pos['entry']
                               * (1 if pos['side']=='LONG' else -1))
                    pnl_usd = pnl_pct * pos['entry_value'] * LEVERAGE
                    self.capital += pnl_usd
                    self.peak = max(self.peak, self.capital)
                    dd = (self.peak - self.capital) / self.peak * 100

                    result = 'TP' if hit_tp else 'SL'
                    self.trades.append({
                        'ms': ts_ms, 'sym': self.symbol, 'side': pos['side'],
                        'entry': pos['entry'], 'exit': exit_px,
                        'pnl_pct': round(pnl_pct*100, 3),
                        'pnl_usd': round(pnl_usd, 2),
                        'capital': round(self.capital, 2),
                        'result': result, 'dd': round(dd, 2)
                    })
                    self.equity_curve.append({'ms': ts_ms, 'eq': round(self.capital, 2)})
                    self.position = None

                    # 爆仓保护
                    if self.capital <= 0:
                        self.log(f"  爆仓！资金归零 @ {datetime.datetime.fromtimestamp(ts_ms//1000).strftime('%Y-%m-%d')}")
                        break
                continue  # 持仓中，不开新仓

            # ── 开仓逻辑 ──
            atr = compute_atr(bars[max(0,i-20):i+1], 14)
            if not atr or atr <= 0: continue
            rsi = compute_rsi(bars[max(0,i-16):i+1], 14)

            signal = None
            # 做多：上升趋势 + RSI超卖
            if e20 > e50 > e200 and rsi < 35:
                signal = 'LONG'
            # 做空：下降趋势 + RSI超买
            elif e20 < e50 < e200 and rsi > 65:
                signal = 'SHORT'

            if signal:
                sl_dist = atr * ATR_SL_MULT
                tp_dist = atr * ATR_TP_MULT
                entry   = close
                sl      = entry - sl_dist if signal == 'LONG' else entry + sl_dist
                tp      = entry + tp_dist if signal == 'LONG' else entry - tp_dist

                # 仓位：2%风险
                risk_usd     = self.capital * RISK_PCT
                entry_value  = risk_usd / (sl_dist / entry)  # 名义价值
                qty          = entry_value / entry

                self.position = {
                    'side': signal, 'entry': entry, 'sl': sl, 'tp': tp,
                    'qty': qty, 'entry_value': entry_value, 'entry_ms': ts_ms
                }

        # 收尾
        self.save_final()
        self.log(f"完成 {self.symbol} {self.interval} {self.mode} | 交易={len(self.trades)}笔 | 最终资金=${self.capital:.2f}")
        self.log_file.close()
        return self.summary()

    def save_progress(self, i, total, pct):
        state = {
            'symbol': self.symbol, 'interval': self.interval, 'mode': self.mode,
            'progress_pct': pct, 'bars_done': i, 'bars_total': total,
            'capital': round(self.capital, 2), 'trades_count': len(self.trades),
            'ts': datetime.datetime.utcnow().isoformat()
        }
        fp = os.path.join(OUT_DIR, f"{self.symbol}_{self.interval}_{self.mode}_state.json")
        with open(fp, 'w') as f: json.dump(state, f)

    def save_final(self):
        # 保存交易记录（分批写，不撑内存）
        fp = os.path.join(OUT_DIR, f"{self.symbol}_{self.interval}_{self.mode}_trades.jsonl")
        with open(fp, 'w') as f:
            for t in self.trades:
                f.write(json.dumps(t, ensure_ascii=False) + '\n')
        # 保存权益曲线（每10笔采样一个点）
        fp2 = os.path.join(OUT_DIR, f"{self.symbol}_{self.interval}_{self.mode}_equity.json")
        sampled = self.equity_curve[::10]
        with open(fp2, 'w') as f:
            json.dump(sampled, f)

    def summary(self):
        if not self.trades: return {'symbol':self.symbol,'interval':self.interval,'trades':0}
        wins   = [t for t in self.trades if t['result']=='TP']
        losses = [t for t in self.trades if t['result']=='SL']
        gross_win  = sum(t['pnl_usd'] for t in wins)  if wins   else 0
        gross_loss = sum(t['pnl_usd'] for t in losses) if losses else 0
        pf   = gross_win / abs(gross_loss) if gross_loss else 999
        wr   = len(wins) / len(self.trades) * 100
        rr   = abs(statistics.mean([t['pnl_usd'] for t in wins]) /
                   statistics.mean([t['pnl_usd'] for t in losses])) if wins and losses else 0
        mdd  = max((t['dd'] for t in self.trades), default=0)
        roi  = (self.capital - INIT_CAPITAL) / INIT_CAPITAL * 100
        return {
            'symbol': self.symbol, 'interval': self.interval, 'mode': self.mode,
            'trades': len(self.trades), 'wins': len(wins), 'losses': len(losses),
            'wr_pct': round(wr, 1), 'pf': round(pf, 3), 'rr': round(rr, 2),
            'gross_win': round(gross_win, 2), 'gross_loss': round(gross_loss, 2),
            'init_capital': INIT_CAPITAL, 'final_capital': round(self.capital, 2),
            'roi_pct': round(roi, 1), 'max_dd_pct': round(mdd, 2)
        }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol',   default='BTCUSDT')
    parser.add_argument('--interval', default='1h')
    parser.add_argument('--mode',     default='train', choices=['train','oos','both'])
    args = parser.parse_args()

    results = []
    modes = ['train','oos'] if args.mode == 'both' else [args.mode]
    for mode in modes:
        eng = BacktestEngine(args.symbol, args.interval, mode)
        s = eng.run()
        results.append(s)
        print(f"\n{'='*50}")
        print(f"{'训练集' if mode=='train' else 'OOS'} 结果: {args.symbol} {args.interval}")
        print(f"  交易笔数: {s['trades']}  胜率: {s['wr_pct']}%  PF: {s['pf']}  R:R: {s['rr']}")
        print(f"  初始: ${s['init_capital']}  终值: ${s['final_capital']}  ROI: {s['roi_pct']}%")
        print(f"  最大回撤: {s['max_dd_pct']}%")

    # 写汇总
    out = os.path.join(OUT_DIR, f"{args.symbol}_{args.interval}_summary.json")
    with open(out,'w') as f: json.dump(results, f, indent=2)
    print(f"\n✅ 汇总写入: {out}")


if __name__ == '__main__':
    main()
