#!/usr/bin/env python3

from typing import Any, Optional
"""
brahma_w5_paper_live.py — 梵天v5.0 W5: 模拟盘验证
================================================
W5封印：苏摩111 2026-09-15

不是"再跑3个月模拟盘等WR验证"——6年回测305笔+CPCV+DSR已经够了。
W5真正验证的3件事：
1. 执行链路：信号→挂单→止盈→止损→记录 全流程在实时环境是否稳定
2. 参数稳定性：W3 ETH参数在当前市场是否产生合理信号
3. 体制适配：当前CHOP_MID下信号频率（预期=0或极低，这是正确的）

配置：
- ETH: W3优化参数（EV +0.367% 确实提升）
- BTC: 保守参数（W3优化不如保守，-0.016%）
- 观察期：2周
- 入场逻辑：复用BrahmaStrategy12 + 实时K线
- 输出：data/w5_paper_signals.jsonl

决策标准（2周后）：
- ≥5个信号 + 链路稳定 → W6实盘小仓位
- 0信号 → CHOP_MID休眠正确，等体制切换
- 链路异常 → 修复后再观察
"""

import sys, json, time, math, os
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BRAIN = Path(__file__).parent
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BRAIN.parent))

from jesse.indicators import rsi as jesse_rsi
from jesse.indicators import ema as jesse_ema
from jesse.indicators import bollinger_bands_width as jesse_bbw
from jesse.indicators import atr as jesse_atr

from brahma_engine_v5 import load_candles, load_regimes, BrahmaStrategy12

DATA_DIR = BRAIN.parent / "data"
HIST_DIR = DATA_DIR / "historical"
OUT_FILE = DATA_DIR / "w5_paper_signals.jsonl"

# 参数配置
PARAMS = {
    'BTCUSDT': {  # 保守参数（W3优化不如保守）
        'rsi_long': 40, 'rsi_short': 69, 'bb_squeeze': 0.049,
        'atr_sl_mult': 2.3, 'rr_ratio': 2.9, 'holding_bars': 43,
        'source': 'conservative',
    },
    'ETHUSDT': {  # W3优化参数（EV确实提升+0.367%）
        'rsi_long': 33, 'rsi_short': 55, 'bb_squeeze': 0.0561,
        'atr_sl_mult': 2.87, 'rr_ratio': 3.42, 'holding_bars': 60,
        'source': 'w3_optimized',
    },
}

# 实时K线获取
def fetch_recent_klines(symbol, interval='1h', limit=500) -> Optional[Any]:
    """从Binance API获取最近K线"""
    import urllib.request
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        candles = []
        for k in raw:
            candles.append([
                int(k[0]),  # ts
                float(k[1]),  # open
                float(k[2]),  # high
                float(k[3]),  # low
                float(k[4]),  # close
                float(k[5]),  # volume
            ])
        return np.array(candles, dtype=np.float64)
    except Exception as e:
        print(f"[W5] {symbol} fetch error: {e}", file=sys.stderr)
        return None

def get_current_regime(symbol) -> Any:
    """从regime_state.json读取当前体制"""
    try:
        d = json.load(open(DATA_DIR / 'regime_state.json'))
        sym_key = symbol.replace('USDT', 'USDT')
        info = d.get(symbol, d.get(symbol.upper(), {}))
        return info.get('confirmed') or info.get('regime') or 'UNKNOWN'
    except Exception as _e:
        return 'UNKNOWN'

def run_w5_scan() -> Any:
    """扫描当前市场，检查是否产生W5信号"""
    now = datetime.now(timezone.utc)
    print(f"[W5] 扫描时间: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"[W5] 观察期: 2周 ({(now+timedelta(days=14)).strftime('%Y-%m-%d')}结束)")
    print()
    
    all_signals = []
    
    for symbol, params in PARAMS.items():
        regime = get_current_regime(symbol)
        print(f"--- {symbol} ---")
        print(f"  体制: {regime}")
        print(f"  参数来源: {params['source']}")
        
        # 获取最近K线
        candles = fetch_recent_klines(symbol, '1h', 500)
        if candles is None or len(candles) < 210:
            print(f"  ❌ K线数据不足")
            continue
        
        # 预计算指标
        rsi_seq = jesse_rsi(candles, 14, sequential=True)
        ema_seq = jesse_ema(candles, 200, sequential=True)
        bbw_seq = jesse_bbw(candles, 20, sequential=True)
        atr_seq = jesse_atr(candles, 14, sequential=True)
        
        # 构建regime map（简化：用当前体制填充）
        regimes = {}
        for c in candles:
            regimes[int(c[0])] = regime
        
        # 运行策略
        strategy = BrahmaStrategy12({k: v for k, v in params.items() if k != 'source'})
        result = strategy.run(candles, regimes, rsi_seq, ema_seq, bbw_seq, atr_seq)
        
        n_signals = result['n']
        wr = result['wr']
        ev = result['ev']
        trades = result.get('trades', [])
        
        print(f"  信号数: {n_signals} | WR: {wr:.1f}% | EV: {ev:+.3f}%")
        
        # 检查最近的信号（最后5笔）
        recent = trades[-5:] if len(trades) >= 5 else trades
        if recent:
            print(f"  最近信号:")
            for t in recent:
                entry_ts = datetime.fromtimestamp(getattr(t, 'entry_ts', 0), tz=timezone.utc)
                sig = "LONG" if getattr(t, 'signal', 0) == 1 else "SHORT"
                net = getattr(t, 'net_ret', 0)
                reason = getattr(t, 'exit_reason', '?')
                print(f"    {entry_ts.strftime('%m-%d %H:00')} {sig} entry=${getattr(t,'entry',0):,.1f} net={net*100:+.2f}% ({reason})")
        
        # 检查最后一根K线是否产生新信号
        last_bar_idx = len(candles) - 1
        last_trade = trades[-1] if trades else None
        is_new_signal = False
        
        if last_trade:
            last_trade_ts = getattr(last_trade, 'entry_ts', 0)
            last_bar_ts = int(candles[-1][0])
            # 如果最后一笔交易的entry_ts在最后3根K线内
            if last_bar_ts - last_trade_ts < 3 * 3600 * 1000:
                is_new_signal = True
                sig = "LONG" if getattr(last_trade, 'signal', 0) == 1 else "SHORT"
                signal_record = {
                    'ts': time.time(),
                    'symbol': symbol,
                    'direction': sig,
                    'entry': getattr(last_trade, 'entry', 0),
                    'sl': getattr(last_trade, 'sl', 0),
                    'tp': getattr(last_trade, 'tp', 0),
                    'regime': regime,
                    'params_source': params['source'],
                    'score': 0,  # W5不用94维score
                    'status': 'PAPER',
                    'source': 'w5_paper_live',
                }
                all_signals.append(signal_record)
                print(f"  🆕 新信号: {sig} @ ${getattr(last_trade,'entry',0):,.1f}")
        
        if not is_new_signal:
            print(f"  无新信号（当前体制={regime}）")
        
        # 当前价格位置
        px = float(candles[-1][4])
        rsi_now = float(rsi_seq[-1]) if not math.isnan(rsi_seq[-1]) else 0
        bbw_now = float(bbw_seq[-1]) if not math.isnan(bbw_seq[-1]) else 0
        print(f"  当前: ${px:,.1f} RSI={rsi_now:.1f} BBW={bbw_now:.4f}")
        print()
    
    # 保存信号
    if all_signals:
        with open(OUT_FILE, 'a') as f:
            for s in all_signals:
                f.write(json.dumps(s) + '\n')
        print(f"[W5] {len(all_signals)}个新信号已记录到 {OUT_FILE}")
    else:
        print(f"[W5] 本次扫描无新信号")
    
    # 统计已有W5信号
    if OUT_FILE.exists():
        with open(OUT_FILE) as f:
            existing = [json.loads(l) for l in f if l.strip()]
        print(f"[W5] 累计信号: {len(existing)}条")
        by_sym = defaultdict(int)
        for s in existing:
            by_sym[s.get('symbol', '?')] += 1
        for sym, n in by_sym.items():
            print(f"  {sym}: {n}条")
    
    return all_signals

if __name__ == '__main__':
    run_w5_scan()
