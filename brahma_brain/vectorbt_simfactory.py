#!/usr/bin/env python3
"""
vectorbt_simfactory.py — 梵天信号Replay × SimFactory
设计院 P3 | 2026-07-11

功能:
  1. 从 live_signal_log.jsonl 读取历史信号
  2. 拉取对应时段的OHLCV数据
  3. 构造 vectorbt 兼容的回测矩阵（entries/exits/sl/tp）
  4. 如果 vectorbt 可用: 运行Portfolio.from_signals()
  5. 如果不可用: 降级到内置 SimpleReplay 引擎（零依赖）

使用:
  python3 brahma_brain/vectorbt_simfactory.py --symbols BTCUSDT ETHUSDT
  python3 brahma_brain/vectorbt_simfactory.py --all --valid-only
"""
import json, math, time, sys, os, argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

# ── 路径注入 ─────────────────────────────────────────────────
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

DATA_DIR   = _root / 'data'
SIGNAL_LOG = DATA_DIR / 'live_signal_log.jsonl'
RESULT_DIR = DATA_DIR / 'simfactory_results'
RESULT_DIR.mkdir(exist_ok=True)

FAPI_BASE = 'https://fapi.binance.com'

# ── vectorbt 可用性检测 ──────────────────────────────────────
try:
    import vectorbt as vbt
    import numpy as np
    import pandas as pd
    VBT_AVAILABLE = True
except ImportError:
    VBT_AVAILABLE = False

try:
    import numpy as np
    import pandas as pd
    NP_AVAILABLE = True
except ImportError:
    NP_AVAILABLE = False


# ════════════════════════════════════════════════════════════
# 一、信号加载
# ════════════════════════════════════════════════════════════

def load_signals(symbols: Optional[List[str]] = None,
                 valid_only: bool = False,
                 min_score: float = 0.0) -> List[Dict]:
    """从 live_signal_log.jsonl 加载信号"""
    if not SIGNAL_LOG.exists():
        return []
    signals = []
    with open(SIGNAL_LOG) as f:
        for line in f:
            try:
                s = json.loads(line.strip())
                if not s:
                    continue
                if symbols and s.get('symbol') not in symbols:
                    continue
                if valid_only and not s.get('valid'):
                    continue
                if s.get('score', 0) < min_score:
                    continue
                signals.append(s)
            except Exception:
                pass
    return signals


# ════════════════════════════════════════════════════════════
# 二、OHLCV 拉取（带缓存）
# ════════════════════════════════════════════════════════════

def _klines_cache_path(symbol: str, interval: str) -> Path:
    return DATA_DIR / f'klines_cache_{symbol}_{interval}.json'


def fetch_klines(symbol: str, interval: str = '1h', limit: int = 500,
                 end_ts: Optional[int] = None) -> List[List]:
    """拉取Binance合约K线，带本地缓存（TTL=5min）"""
    cache_path = _klines_cache_path(symbol, interval)
    cache_key = f'{symbol}_{interval}_{limit}_{end_ts}'

    # 读缓存
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
            if (cache.get('key') == cache_key and
                    time.time() - cache.get('ts', 0) < 300):
                return cache['data']
        except Exception:
            pass

    # 拉取
    try:
        import requests
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        if end_ts:
            params['endTime'] = end_ts
        r = requests.get(f'{FAPI_BASE}/fapi/v1/klines', params=params, timeout=10)
        data = r.json()
        if isinstance(data, list) and data:
            # 写缓存（仅历史数据，不缓存当前）
            if end_ts:
                try:
                    cache_path.write_text(json.dumps({'key': cache_key, 'ts': time.time(), 'data': data}))
                except Exception:
                    pass
            return data
    except Exception as e:
        pass
    return []


# ════════════════════════════════════════════════════════════
# 三、SimpleReplay — 零依赖内置回测引擎
# ════════════════════════════════════════════════════════════

class SimpleReplay:
    """
    内置零依赖信号回测引擎（vectorbt降级版）
    逐笔模拟：入场 → TP1(50%) → 追踪止损(50%) / SL全平
    """

    def __init__(self, initial_capital: float = 10000.0,
                 fee_rate: float = 0.0004):
        self.capital   = initial_capital
        self.fee_rate  = fee_rate
        self.nav       = initial_capital
        self.trades    = []
        self.equity    = [initial_capital]

    def replay(self, signals: List[Dict],
               lookahead_bars: int = 48) -> Dict:
        """
        回放信号列表
        每笔信号: 拉取后续K线，模拟TP/SL

        Returns: {
            'total_trades': int,
            'win_rate': float,
            'total_pnl_pct': float,
            'avg_pnl_pct': float,
            'max_drawdown': float,
            'trades': list
        }
        """
        for sig in signals:
            self._simulate_trade(sig, lookahead_bars)

        return self._summary()

    def _simulate_trade(self, sig: Dict, lookahead: int):
        sym       = sig.get('symbol', 'UNKNOWN')
        direction = sig.get('direction', 'LONG')
        score     = float(sig.get('score', 100))
        regime    = sig.get('regime', '')
        sig_ts    = int(sig.get('ts', 0) * 1000)

        # 入场价格
        entry_price = float(sig.get('entry_hi') or sig.get('price') or 0)
        if not entry_price:
            return

        # SL/TP 从信号取，否则按体制默认值
        sl_pct = abs(float(sig.get('sl_pct') or
                           _default_sl_pct(regime, direction)))
        rr     = float(sig.get('rr1') or 1.0)
        tp_pct = sl_pct * rr

        # 计算SL/TP价格
        if direction in ('LONG', 'BUY'):
            sl_price = entry_price * (1 - sl_pct / 100)
            tp1_price = entry_price * (1 + tp_pct / 100)
        else:
            sl_price  = entry_price * (1 + sl_pct / 100)
            tp1_price = entry_price * (1 - tp_pct / 100)

        # 拉取入场后K线
        klines = fetch_klines(sym, '1h', limit=lookahead + 5,
                               end_ts=sig_ts + lookahead * 3600000)
        if not klines or len(klines) < 5:
            return  # 无数据跳过

        # 找入场后的K线
        entry_idx = None
        for i, k in enumerate(klines):
            if int(k[0]) >= sig_ts:
                entry_idx = i
                break
        if entry_idx is None:
            entry_idx = max(0, len(klines) - lookahead)

        future_klines = klines[entry_idx:entry_idx + lookahead]
        if not future_klines:
            return

        # 仓位大小（占NAV）
        nav_pct = float(sig.get('nav_pct') or _default_nav_pct(score))
        pos_size = self.nav * nav_pct

        # 模拟逐根K线
        hit_tp1 = False
        closed  = False
        exit_price = entry_price
        exit_reason = 'timeout'

        for bar in future_klines:
            high  = float(bar[2])
            low   = float(bar[3])
            close = float(bar[4])

            if direction in ('LONG', 'BUY'):
                # 检查SL
                if low <= sl_price:
                    if hit_tp1:
                        exit_price = sl_price
                        exit_reason = 'trailing_sl'
                    else:
                        exit_price = sl_price
                        exit_reason = 'sl'
                    closed = True
                    break
                # 检查TP1
                if not hit_tp1 and high >= tp1_price:
                    hit_tp1 = True
                    # 追踪止损上移到入场价
                    sl_price = entry_price
            else:
                # SHORT
                if high >= sl_price:
                    exit_price = sl_price
                    exit_reason = 'trailing_sl' if hit_tp1 else 'sl'
                    closed = True
                    break
                if not hit_tp1 and low <= tp1_price:
                    hit_tp1 = True
                    sl_price = entry_price

        if not closed:
            exit_price = float(future_klines[-1][4])

        # 计算PnL
        if direction in ('LONG', 'BUY'):
            raw_pnl_pct = (exit_price - entry_price) / entry_price
        else:
            raw_pnl_pct = (entry_price - exit_price) / entry_price

        fee = pos_size * self.fee_rate * 2
        pnl_usd = pos_size * raw_pnl_pct - fee
        pnl_pct = raw_pnl_pct - self.fee_rate * 2

        self.nav += pnl_usd
        self.equity.append(self.nav)

        trade = {
            'symbol':      sym,
            'direction':   direction,
            'score':       score,
            'regime':      regime,
            'entry_price': entry_price,
            'exit_price':  round(exit_price, 4),
            'sl_price':    round(sl_price, 4),
            'tp1_price':   round(tp1_price, 4),
            'pnl_pct':     round(pnl_pct * 100, 3),
            'hit_tp1':     hit_tp1,
            'exit_reason': exit_reason,
            'win':         pnl_pct > 0,
        }
        self.trades.append(trade)

    def _summary(self) -> Dict:
        if not self.trades:
            return {'total_trades': 0, 'status': 'no_trades'}

        wins      = [t for t in self.trades if t['win']]
        pnl_list  = [t['pnl_pct'] for t in self.trades]
        total_pnl = sum(pnl_list)
        avg_pnl   = total_pnl / len(self.trades)

        # 最大回撤
        peak = self.equity[0]
        max_dd = 0.0
        for eq in self.equity:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd

        return {
            'total_trades':  len(self.trades),
            'win_count':     len(wins),
            'win_rate':      round(len(wins) / len(self.trades) * 100, 1),
            'total_pnl_pct': round(total_pnl, 3),
            'avg_pnl_pct':   round(avg_pnl, 3),
            'max_drawdown':  round(max_dd, 2),
            'final_nav':     round(self.nav, 2),
            'initial_nav':   self.equity[0],
            'trades':        self.trades,
            'equity':        self.equity,
        }


# ════════════════════════════════════════════════════════════
# 四、vectorbt SimFactory（vbt可用时激活）
# ════════════════════════════════════════════════════════════

def run_vbt_sim(signals: List[Dict], symbol: str,
                interval: str = '1h') -> Optional[Dict]:
    """
    用 vectorbt Portfolio.from_signals 运行回测
    仅当 VBT_AVAILABLE=True 时可用
    """
    if not VBT_AVAILABLE or not NP_AVAILABLE:
        return None

    sig_list = [s for s in signals if s.get('symbol') == symbol]
    if not sig_list:
        return None

    # 拉取足够的历史OHLCV
    klines = fetch_klines(symbol, interval, limit=1000)
    if not klines or len(klines) < 50:
        return None

    closes = pd.Series([float(k[4]) for k in klines],
                       index=pd.to_datetime([k[0] for k in klines], unit='ms', utc=True))
    highs  = pd.Series([float(k[2]) for k in klines],
                       index=closes.index)
    lows   = pd.Series([float(k[3]) for k in klines],
                       index=closes.index)

    # 构建 entries/exits/sl/tp 序列
    entries_long  = pd.Series(False, index=closes.index)
    entries_short = pd.Series(False, index=closes.index)
    sl_stop       = pd.Series(np.nan, index=closes.index)
    tp_stop       = pd.Series(np.nan, index=closes.index)

    for sig in sig_list:
        sig_ts = pd.Timestamp(int(sig['ts'] * 1000), unit='ms', tz='UTC')
        # 找最近的K线时间
        idx = closes.index.get_indexer([sig_ts], method='nearest')[0]
        if idx < 0 or idx >= len(closes):
            continue

        direction = sig.get('direction', 'LONG')
        regime    = sig.get('regime', '')
        sl_pct    = abs(float(sig.get('sl_pct') or _default_sl_pct(regime, direction))) / 100
        rr        = float(sig.get('rr1') or 1.0)
        tp_pct    = sl_pct * rr

        if direction in ('LONG', 'BUY'):
            entries_long.iloc[idx] = True
            sl_stop.iloc[idx]      = sl_pct
            tp_stop.iloc[idx]      = tp_pct
        else:
            entries_short.iloc[idx] = True
            sl_stop.iloc[idx]       = sl_pct
            tp_stop.iloc[idx]       = tp_pct

    # 运行 vectorbt
    try:
        pf = vbt.Portfolio.from_signals(
            closes,
            entries=entries_long,
            short_entries=entries_short,
            sl_stop=sl_stop,
            tp_stop=tp_stop,
            init_cash=10000,
            fees=0.0004,
            freq='1h',
        )
        return {
            'engine':        'vectorbt',
            'symbol':        symbol,
            'total_trades':  int(pf.trades.count()),
            'win_rate':      round(float(pf.trades.win_rate) * 100, 1),
            'total_return':  round(float(pf.total_return) * 100, 3),
            'sharpe':        round(float(pf.sharpe_ratio), 3),
            'max_drawdown':  round(float(pf.max_drawdown) * 100, 2),
            'final_value':   round(float(pf.final_value), 2),
        }
    except Exception as e:
        return {'engine': 'vectorbt', 'error': str(e)}


# ════════════════════════════════════════════════════════════
# 五、SimFactory 统一入口
# ════════════════════════════════════════════════════════════

def run_sim(symbols: Optional[List[str]] = None,
            valid_only: bool = False,
            min_score: float = 100.0,
            save_result: bool = True) -> Dict:
    """
    SimFactory 统一入口
    1. 加载信号
    2. 优先用 vectorbt，降级到 SimpleReplay
    3. 输出回测报告
    """
    signals = load_signals(symbols, valid_only=valid_only, min_score=min_score)

    if not signals:
        return {'status': 'no_signals', 'count': 0}

    print(f"\n📊 SimFactory — {len(signals)} 笔信号")
    print(f"   引擎: {'vectorbt ✅' if VBT_AVAILABLE else 'SimpleReplay (vectorbt未安装)'}")
    print(f"   有效信号: {len([s for s in signals if s.get('valid')])}/{len(signals)}")
    print()

    results = {}
    syms = symbols or list({s['symbol'] for s in signals})

    for sym in syms:
        sym_sigs = [s for s in signals if s.get('symbol') == sym]
        if not sym_sigs:
            continue

        print(f"  ── {sym} ({len(sym_sigs)}笔) ──")

        # 尝试 vectorbt
        vbt_result = run_vbt_sim(sym_sigs, sym) if VBT_AVAILABLE else None

        if vbt_result and 'error' not in vbt_result:
            r = vbt_result
            print(f"     [vbt] trades={r['total_trades']} WR={r['win_rate']}% "
                  f"return={r['total_return']:+.2f}% sharpe={r['sharpe']:.2f} "
                  f"maxDD={r['max_drawdown']:.1f}%")
        else:
            # SimpleReplay 降级
            replay = SimpleReplay()
            r = replay.replay(sym_sigs)
            r['engine'] = 'simple_replay'
            r['symbol'] = sym
            if r.get('total_trades', 0) > 0:
                print(f"     [replay] trades={r['total_trades']} WR={r['win_rate']}% "
                      f"totalPnL={r['total_pnl_pct']:+.2f}% "
                      f"avgPnL={r['avg_pnl_pct']:+.3f}% "
                      f"maxDD={r['max_drawdown']:.1f}%")
            else:
                print(f"     [replay] {r.get('status', 'no data')}")

        results[sym] = r

    # 保存结果
    if save_result:
        out_path = RESULT_DIR / f'simfactory_{int(time.time())}.json'
        try:
            # Remove non-serializable equity list for JSON
            clean = {}
            for k, v in results.items():
                c = {kk: vv for kk, vv in v.items()
                     if kk not in ('equity', 'trades')}
                c['trades_sample'] = v.get('trades', [])[:5]
                clean[k] = c
            out_path.write_text(json.dumps(clean, indent=2, ensure_ascii=False))
            print(f"\n  💾 结果保存: {out_path.name}")
        except Exception as e:
            print(f"  ⚠️  保存失败: {e}")

    return results


# ════════════════════════════════════════════════════════════
# 六、工具函数
# ════════════════════════════════════════════════════════════

def _default_sl_pct(regime: str, direction: str) -> float:
    """按体制和方向返回默认止损百分比（对标MEMORY.md封印）"""
    if direction in ('SHORT', 'SELL'):
        return {'BEAR_TREND': 2.0, 'CHOP_MID': 2.5, 'BULL_TREND': 2.5}.get(regime, 2.0)
    return 2.0  # 做多默认 2%


def _default_nav_pct(score: float) -> float:
    """按score返回默认仓位比例"""
    if score >= 165:
        return 0.10
    elif score >= 155:
        return 0.075
    elif score >= 140:
        return 0.05
    return 0.03


# ════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='梵天 SimFactory — 信号回放验证')
    parser.add_argument('--symbols', nargs='+', default=None, help='指定标的列表')
    parser.add_argument('--all', action='store_true', help='全部信号')
    parser.add_argument('--valid-only', action='store_true', help='仅有效信号')
    parser.add_argument('--min-score', type=float, default=100.0, help='最低score过滤')
    parser.add_argument('--no-save', action='store_true', help='不保存结果')
    args = parser.parse_args()

    symbols = None if args.all else (args.symbols or ['BTCUSDT', 'ETHUSDT'])
    result = run_sim(
        symbols=symbols,
        valid_only=args.valid_only,
        min_score=args.min_score,
        save_result=not args.no_save,
    )

    print(f"\n✅ SimFactory 完成 — {len(result)} 个标的")
    return result


if __name__ == '__main__':
    main()
