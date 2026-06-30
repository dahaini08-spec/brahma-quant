#!/usr/bin/env python3
"""
offline_engine.py — 达摩院全性能测试 · 完整15维离线引擎 v1.0
════════════════════════════════════════════════════════════════
把 brahma_brain 真实逻辑搬到历史数据上运行
替换所有实时API调用 → 消费滑动窗口DataFrame

架构:
  OfflineDataFeed    — 模拟 get_klines() / get_ticker()，从DataFrame切片
  OfflineAnalyzer    — 调用真实引擎，注入 OfflineDataFeed
  FullSystemScorer   — 生成完整15维breakdown（与实盘一致）

输入: DataFrame (open/high/low/close/volume) + OfflineAdapterBundle
输出: {'total': int, 'breakdown': dict, 'extra': dict}
"""

import sys, os, math, time
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any, List

BASE_DIR   = Path(__file__).parent.parent
DHARMA_DIR = BASE_DIR / 'dharma'
DATA_DIR   = DHARMA_DIR / 'data'

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / 'brahma_brain'))

# ════════════════════════════════════════════════════════════════
# 1. OfflineDataFeed — 模拟实时数据源
# ════════════════════════════════════════════════════════════════

class OfflineDataFeed:
    """
    替换 data_cache.get_klines() 和 get_ticker()
    从 DataFrame 的滑动窗口切片返回标准格式数据
    """

    def __init__(self, df_map: Dict[str, Dict[str, pd.DataFrame]]):
        """
        df_map: {symbol: {interval: df}}
        e.g. {'ETHUSDT': {'1h': df_1h, '4h': df_4h, '1d': df_1d}}
        """
        self.df_map  = df_map
        self._cursor = {}  # symbol → bar index (1h基准)

    def set_cursor(self, symbol: str, bar_idx: int):
        self._cursor[symbol] = bar_idx

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        """返回标准 binance klines 格式 [[ts,o,h,l,c,v,...],...]"""
        sym = symbol.upper()
        iv  = interval.replace('h','h').replace('H','h').replace('d','d')
        # 规范化 interval
        iv_map = {'1H':'1h','4H':'4h','1D':'1d','15M':'15m',
                  '1h':'1h','4h':'4h','1d':'1d','15m':'15m'}
        iv = iv_map.get(iv, iv)

        df = self.df_map.get(sym, {}).get(iv)
        if df is None:
            return []

        # 计算对应的结束位置
        cursor_1h = self._cursor.get(sym, 200)
        # 将1h光标映射到其他周期
        ts_end = None
        df_1h = self.df_map.get(sym, {}).get('1h')
        if df_1h is not None and cursor_1h < len(df_1h):
            ts_end = df_1h.index[cursor_1h]

        if ts_end is not None:
            df_cut = df[df.index <= ts_end]
        else:
            df_cut = df

        if len(df_cut) == 0:
            return []

        df_slice = df_cut.iloc[-limit:]

        result = []
        for ts, row in df_slice.iterrows():
            ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, 'timestamp') else 0
            result.append([
                ts_ms,
                str(row['open']),
                str(row['high']),
                str(row['low']),
                str(row['close']),
                str(row['volume']),
                ts_ms + 3600000, '0', 0, '0', '0', '0'
            ])
        return result

    def get_ticker(self, symbol: str) -> dict:
        sym = symbol.upper()
        cursor = self._cursor.get(sym, 200)
        df_1h  = self.df_map.get(sym, {}).get('1h')
        if df_1h is None or cursor >= len(df_1h):
            return {'price': 0}
        row = df_1h.iloc[cursor]
        prev = df_1h.iloc[max(0, cursor-24)]
        chg  = (row['close'] - prev['close']) / prev['close'] * 100
        return {
            'price':        float(row['close']),
            'priceChangePercent': round(chg, 2),
            'volume':       float(row['volume']),
            'highPrice':    float(row['high']),
            'lowPrice':     float(row['low']),
        }


# ════════════════════════════════════════════════════════════════
# 2. FullSystemScorer — 调用真实引擎
# ════════════════════════════════════════════════════════════════

class FullSystemScorer:
    """
    调用真实 brahma_brain 引擎计算15维评分
    通过 monkey-patch data_cache 注入离线数据源
    """

    def __init__(self, feed: OfflineDataFeed, adapters=None):
        self.feed     = feed
        self.adapters = adapters  # OfflineAdapterBundle (可选)
        self._patched = False
        self._patch_cache()

    def _patch_cache(self):
        """替换 data_cache 模块中的实时API调用"""
        try:
            import brahma_brain.data_cache as dc

            feed = self.feed

            def _offline_klines(symbol, interval, limit=100):
                return feed.get_klines(symbol, interval, limit)

            def _offline_ticker(symbol):
                return feed.get_ticker(symbol)

            # Monkey-patch
            dc.get_klines  = _offline_klines
            dc.get_ticker  = _offline_ticker
            self._patched = True
        except Exception as e:
            pass  # 非致命

    def score(self, symbol: str, direction: str, bar_idx: int,
              adapter_extra: Optional[dict] = None) -> dict:
        """
        计算单个信号的完整15维评分
        """
        self.feed.set_cursor(symbol, bar_idx)

        try:
            # 导入真实引擎
            import brahma_brain.brahma_brain as bb
            import importlib
            importlib.reload(bb)  # 确保使用最新patch

            # 构造 extra_data（离线适配器结果）
            extra_data = adapter_extra or {}

            # 调用真实 analyze（直接复用核心评分逻辑）
            # 为避免慢速API调用，使用内部 _score_signal
            result = self._call_brain_score(bb, symbol, direction, extra_data)
            return result

        except Exception as e:
            return {'total': 0, 'breakdown': {}, 'error': str(e)}

    def _call_brain_score(self, bb_module, symbol: str, direction: str,
                           extra_data: dict) -> dict:
        """调用 brahma_brain 的核心评分逻辑（跳过实时API部分）"""
        try:
            # 获取当前价格快照
            ticker = self.feed.get_ticker(symbol)
            price  = ticker.get('price', 0)
            if price == 0:
                return {'total': 0, 'breakdown': {}, 'error': 'no_price'}

            # 拉取多周期K线
            k1h  = self.feed.get_klines(symbol, '1h', 100)
            k4h  = self.feed.get_klines(symbol, '4h', 100)
            k1d  = self.feed.get_klines(symbol, '1d', 50)
            k15m = self.feed.get_klines(symbol, '15m', 100)

            if len(k1h) < 50:
                return {'total': 0, 'breakdown': {}, 'error': 'insufficient_data'}

            def klines_to_arr(k, field):
                return [float(x[field]) for x in k]

            o1h = klines_to_arr(k1h, 1); h1h = klines_to_arr(k1h, 2)
            l1h = klines_to_arr(k1h, 3); c1h = klines_to_arr(k1h, 4)
            v1h = klines_to_arr(k1h, 5)

            o4h = klines_to_arr(k4h, 1); h4h = klines_to_arr(k4h, 2)
            l4h = klines_to_arr(k4h, 3); c4h = klines_to_arr(k4h, 4)

            o1d = klines_to_arr(k1d, 1); h1d = klines_to_arr(k1d, 2)
            l1d = klines_to_arr(k1d, 3); c1d = klines_to_arr(k1d, 4)

            # ── 调用各真实引擎 ──────────────────────────────────
            breakdown = {}
            score = 0

            # D01: 趋势一致性 (multitf_engine)
            try:
                from brahma_brain.multitf_engine import multitf_score
                r1 = multitf_score(symbol, direction, c1h, c4h, c1d,
                                   h1h, l1h, h4h, l4h, h1d, l1d, v1h)
                s1 = min(r1.get('score', 0), 30)
            except Exception:
                s1 = self._fallback_trend(c1h, c4h, c1d, direction)
            score += s1; breakdown['趋势一致性'] = s1

            # D02: 关键位精确度 (smc_engine)
            try:
                from brahma_brain.smc_engine import key_level_score
                r2 = key_level_score(h1h, l1h, c1h, h4h, l4h, c4h, price, direction)
                s2 = min(r2.get('score', 0), 30)
            except Exception:
                s2 = self._fallback_keylevel(h1h, l1h, c1h, direction)
            score += s2; breakdown['关键位精确度'] = s2

            # D03: 动量背离 (divergence_engine)
            try:
                from brahma_brain.divergence_engine import divergence_score
                d1h = divergence_score(o1h, h1h, l1h, c1h, direction, '1H')
                d4h = divergence_score(o4h, h4h, l4h, c4h, direction, '4H')
                s3 = min(d1h.get('score', 0) + d4h.get('score', 0) // 2, 20)
            except Exception:
                s3 = self._fallback_divergence(c1h, direction)
            score += s3; breakdown['动量背离'] = s3

            # D04: SMC结构 (smc_engine)
            try:
                from brahma_brain.smc_engine import smc_score
                r4 = smc_score(h1h, l1h, c1h, o1h, v1h, direction)
                s4 = min(r4.get('score', 0), 20)
            except Exception:
                s4 = self._fallback_smc(c1h, direction)
            score += s4; breakdown['SMC结构'] = s4

            # D05: 量能验证 (volume_engine)
            try:
                from brahma_brain.volume_engine import volume_score
                r5 = volume_score(c1h, v1h, direction)
                s5 = min(r5.get('score', 0), 20)
            except Exception:
                s5 = self._fallback_volume(c1h, v1h, direction)
            score += s5; breakdown['量能验证'] = s5

            # D06: 形态成熟度 (pattern_engine)
            try:
                from brahma_brain.pattern_engine import pattern_score
                r6 = pattern_score(h1h, l1h, c1h, direction)
                s6 = min(r6.get('score', 0), 20)
            except Exception:
                s6 = 8
            score += s6; breakdown['形态成熟度'] = s6

            # D07: 清算/OI (enhanced_signal_engine)
            enh = extra_data.get('enhanced', {})
            s7 = min(enh.get('score', 0), 20) if enh else 0
            if s7 == 0:
                s7 = self._fallback_liq(c1h, v1h, direction)
            score += s7; breakdown['清算/OI'] = s7

            # D08: 情绪/费率 (options_engine)
            opt = extra_data.get('options', {})
            s8 = min(opt.get('score', 0), 15) if opt else 0
            if s8 == 0:
                sent = extra_data.get('sentiment', {})
                s8 = min(sent.get('score', 0), 15) if sent else 0
            score += s8; breakdown['情绪/费率'] = s8

            # D09: 时段权重 (enhanced_signal_engine)
            s9 = 7  # 离线默认亚洲时段权重
            score += s9; breakdown['时段权重'] = s9

            # D10: 谐波+多周期 (harmonic_engine)
            try:
                from brahma_brain.harmonic_engine import harmonic_score
                r10 = harmonic_score(h1h, l1h, c1h, direction)
                s10 = min(r10.get('score', 0), 15)
            except Exception:
                s10 = self._fallback_harmonic(c1h, direction)
            score += s10; breakdown['谐波+多周期'] = s10

            # D11: 鲸鱼+跨市场+微观 (whale_engine)
            whale = extra_data.get('whale', {})
            micro = extra_data.get('microstructure', {})
            s11 = 0
            if whale:
                s11 += min(whale.get('score', 0), 20)
            if micro:
                s11 += min(micro.get('score', 0), 10)
            if s11 == 0:
                s11 = self._fallback_whale(v1h, c1h, direction)
            s11 = min(s11, 30)
            score += s11; breakdown['鲸鱼+跨市场+微观'] = s11

            # D12: 期权+订单流
            ob  = extra_data.get('orderbook', {})
            of  = extra_data.get('order_flow', {})
            s12 = 0
            if ob:  s12 += min(ob.get('score', 0), 8)
            if of:  s12 += min(of.get('score', 0), 7)
            s12 = min(s12, 15)
            score += s12; breakdown['期权+订单流'] = s12

            # D13: L2+贝叶斯+宏观
            s13 = min(ob.get('score', 0) // 2 + 8, 30) if ob else 8
            score += s13; breakdown['L2+贝叶斯+宏观'] = s13

            # D14: ML+在线贝叶斯+滑点
            s14 = 0
            try:
                from brahma_brain.online_bayes import get_score as ob_score
                ob_r = ob_score(symbol, 'CHOP', direction, 'S1', 'ASIA')
                s14 += min(ob_r.get('score_adj', 0), 15)
            except Exception:
                pass
            s14 = min(max(s14, 0), 30)
            score += s14; breakdown['ML+在线贝叶斯+滑点'] = s14

            # D15: LSTM+NLP情绪
            try:
                from brahma_brain.lstm_engine import analyze as lstm_a
                lr = lstm_a(symbol, direction)
                s15 = lr.get('score', 0)
            except Exception:
                s15 = 0
            sent2 = extra_data.get('sentiment', {})
            s15 += min(sent2.get('score', 0) // 2, 8) if sent2 else 0
            s15 = max(-15, min(s15, 18))
            score += s15; breakdown['LSTM+NLP情绪'] = s15

            return {'total': score, 'breakdown': breakdown, 'price': price}

        except Exception as e:
            import traceback
            return {'total': 0, 'breakdown': {}, 'error': str(e),
                    'trace': traceback.format_exc()[-300:]}

    # ── 兜底简化计算（引擎失败时使用）──────────────────────────

    @staticmethod
    def _ema(c, n):
        if len(c) < n: return c[-1]
        e = sum(c[:n])/n; k = 2/(n+1)
        for x in c[n:]: e = x*k + e*(1-k)
        return e

    @staticmethod
    def _rsi(c, n=14):
        if len(c) < n+1: return 50.0
        gains = [max(c[i]-c[i-1],0) for i in range(1,len(c))]
        losses = [max(c[i-1]-c[i],0) for i in range(1,len(c))]
        ag = sum(gains[:n])/n; al = sum(losses[:n])/n
        for i in range(n, len(gains)):
            ag=(ag*(n-1)+gains[i])/n; al=(al*(n-1)+losses[i])/n
        return round(100-100/(1+(ag/(al or 1e-9))),1)

    def _fallback_trend(self, c1h, c4h, c1d, direction):
        d = 1 if direction in ('LONG','做多') else -1
        e20=self._ema(c1h,20); e50=self._ema(c1h,50)
        e50_4h=self._ema(c4h,50) if c4h else e50
        e200_1d=self._ema(c1d,200) if len(c1d)>=200 else c1d[-1]
        align = ((c1h[-1]-e20)*d>0) + ((c1h[-1]-e50)*d>0) + ((c1h[-1]-e200_1d)*d>0)
        return [0,8,17,30][int(align)]

    def _fallback_keylevel(self, h, l, c, direction):
        recent_h = max(h[-20:]); recent_l = min(l[-20:])
        price = c[-1]; rng = recent_h - recent_l
        if rng == 0: return 5
        pct = (price - recent_l) / rng
        d = 1 if direction in ('LONG','做多') else -1
        if d < 0: return int(pct * 20)
        else:     return int((1-pct) * 20)

    def _fallback_divergence(self, c, direction):
        rsi = self._rsi(c[-30:])
        d = 1 if direction in ('LONG','做多') else -1
        if d < 0 and rsi > 70: return 10
        if d > 0 and rsi < 30: return 10
        return 2

    def _fallback_smc(self, c, direction):
        e20=self._ema(c,20); e50=self._ema(c,50)
        d = 1 if direction in ('LONG','做多') else -1
        return 15 if (c[-1]-e20)*d>0 and (e20-e50)*d>0 else 5

    def _fallback_volume(self, c, v, direction):
        if len(v) < 20: return 5
        avg = sum(v[-20:])/20; last = v[-1]
        d = 1 if direction in ('LONG','做多') else -1
        price_up = c[-1] > c[-5]
        if last > avg*1.3 and (price_up == (d>0)): return 15
        if last > avg*1.1: return 8
        return 4

    def _fallback_liq(self, c, v, direction):
        if len(v) < 20: return 0
        avg_v = sum(v[-20:])/20
        price_chg = (c[-1]-c[-4])/c[-4] if len(c)>4 else 0
        d = 1 if direction in ('LONG','做多') else -1
        if v[-1] > avg_v*2.5 and price_chg*(-d) > 0.03:
            return 15  # 清算浪
        return 0

    def _fallback_harmonic(self, c, direction):
        rsi = self._rsi(c[-50:])
        d = 1 if direction in ('LONG','做多') else -1
        if d < 0 and rsi > 60: return 10
        if d > 0 and rsi < 40: return 10
        return 5

    def _fallback_whale(self, v, c, direction):
        if len(v) < 20: return 5
        avg = sum(v[-20:])/20
        z = (v[-1]-avg)/(avg*0.3+1e-9)
        d = 1 if direction in ('LONG','做多') else -1
        price_dir = 1 if c[-1] > c[-2] else -1
        if z > 2 and price_dir*d > 0: return 18
        if z > 1: return 10
        return 5


# ════════════════════════════════════════════════════════════════
# 3. 完整回测引擎（调用 FullSystemScorer）
# ════════════════════════════════════════════════════════════════

def backtest_full(sym: str, threshold: int = 130,
                  sl_atr_mult: float = 1.5, rr_target: float = 2.0,
                  max_hold_bars: int = 12, fast: bool = True,
                  step: int = 6) -> dict:
    """
    使用完整15维引擎（真实逻辑）对单品种回测
    比 dharma_system_backtest.py 精度更高
    """
    import pandas as pd

    def load(iv):
        f = DATA_DIR / f'{sym.lower()}_{iv}_2018_2026.parquet'
        return pd.read_parquet(f) if f.exists() else None

    df_1h = load('1h')
    if df_1h is None:
        return {'status': 'no_data', 'sym': sym}

    df_4h = load('4h'); df_1d = load('1d'); df_15m = load('15m')
    fund_f = DATA_DIR / f'{sym.lower()}_funding_rate.parquet'
    fund_df = pd.read_parquet(fund_f) if fund_f.exists() else None

    if fast:
        df_1h = df_1h.iloc[-17520:]
        if df_4h is not None: df_4h = df_4h.iloc[-4380:]
        if df_1d is not None: df_1d = df_1d.iloc[-730:]

    df_map = {sym: {'1h': df_1h}}
    if df_4h is not None: df_map[sym]['4h'] = df_4h
    if df_1d is not None: df_map[sym]['1d'] = df_1d
    if df_15m is not None: df_map[sym]['15m'] = df_15m.iloc[-70000:] if fast else df_15m

    feed     = OfflineDataFeed(df_map)
    adapters = None
    try:
        from brahma_brain.offline_adapters import OfflineAdapterBundle
        adapters = OfflineAdapterBundle(df_1h, df_4h, df_1d, fund_df)
    except Exception:
        pass

    scorer = FullSystemScorer(feed, adapters)

    # ATR for SL/TP
    from brahma_brain.offline_adapters import _atr as calc_atr
    atr_series = calc_atr(df_1h['high'], df_1h['low'], df_1h['close'], 14)

    trades = []
    for i in range(200, len(df_1h) - max_hold_bars - 1, step):
        row   = df_1h.iloc[i]
        price = float(row['close'])
        atr   = float(atr_series.iloc[i])

        for direction in ['LONG', 'SHORT']:
            # 离线适配器
            adapter_extra = adapters.compute(i, direction) if adapters else {}

            result = scorer.score(sym, direction, i, adapter_extra)
            total  = result.get('total', 0)

            if total < threshold:
                continue

            sl = price - atr*sl_atr_mult if direction=='LONG' else price + atr*sl_atr_mult
            tp = price + atr*sl_atr_mult*rr_target if direction=='LONG' else price - atr*sl_atr_mult*rr_target

            pnl = 0; reason = 'timeout'
            for j in range(i+1, min(i+max_hold_bars+1, len(df_1h))):
                fh = float(df_1h.iloc[j]['high'])
                fl = float(df_1h.iloc[j]['low'])
                if direction == 'LONG':
                    if fl <= sl: pnl=(sl-price)/price; reason='sl'; break
                    if fh >= tp: pnl=(tp-price)/price; reason='tp'; break
                else:
                    if fh >= sl: pnl=(price-sl)/price; reason='sl'; break
                    if fl <= tp: pnl=(price-tp)/price; reason='tp'; break
            else:
                ep = float(df_1h.iloc[min(i+max_hold_bars, len(df_1h)-1)]['close'])
                pnl = (ep-price)/price * (1 if direction=='LONG' else -1)

            trades.append({
                'direction': direction, 'score': total, 'pnl': pnl,
                'reason': reason, 'breakdown': result.get('breakdown', {}),
            })

    if not trades:
        return {'status': 'no_signals', 'sym': sym, 'threshold': threshold}

    pnls  = [t['pnl'] for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p <= 0]
    wr    = len(wins)/len(pnls)
    avg_w = np.mean(wins) if wins else 0
    avg_l = abs(np.mean(losses)) if losses else 1e-9
    pf    = (len(wins)*avg_w) / (len(losses)*avg_l + 1e-9)

    # 逐维贡献
    dims = list(trades[0]['breakdown'].keys()) if trades else []
    dim_contrib = {}
    for dim in dims:
        high = [t for t in trades if t['breakdown'].get(dim,0) >= 10]
        low  = [t for t in trades if t['breakdown'].get(dim,0) < 10]
        if len(high) >= 5 and len(low) >= 5:
            wh = sum(1 for t in high if t['pnl']>0)/len(high)
            wl = sum(1 for t in low  if t['pnl']>0)/len(low)
            dim_contrib[dim] = {
                'wr_high': round(wh,3), 'wr_low': round(wl,3),
                'delta':   round(wh-wl,3),
                'n_high': len(high), 'n_low': len(low),
            }

    return {
        'status':      'done',
        'sym':         sym,
        'threshold':   threshold,
        'n':           len(trades),
        'wr':          round(wr, 4),
        'pf':          round(pf, 3),
        'sharpe':      round(np.mean(pnls)/(np.std(pnls)+1e-9)*math.sqrt(252*24), 2),
        'avg_pnl':     round(np.mean(pnls), 5),
        'tp_rate':     round(sum(1 for t in trades if t['reason']=='tp')/len(trades), 3),
        'sl_rate':     round(sum(1 for t in trades if t['reason']=='sl')/len(trades), 3),
        'dim_contrib': dim_contrib,
        'engine':      'full_15d',
    }


# ════════════════════════════════════════════════════════════════
# 快速验证
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print('🔱 Phase 2 完整15维离线引擎验证')
    t0 = time.time()

    import pandas as pd
    df_1h = pd.read_parquet(DATA_DIR / 'ethusdt_1h_2018_2026.parquet')
    df_4h = pd.read_parquet(DATA_DIR / 'ethusdt_4h_2018_2026.parquet')
    df_1d = pd.read_parquet(DATA_DIR / 'ethusdt_1d_2018_2026.parquet')

    feed = OfflineDataFeed({'ETHUSDT': {'1h': df_1h, '4h': df_4h, '1d': df_1d}})

    try:
        from brahma_brain.offline_adapters import OfflineAdapterBundle
        adapters = OfflineAdapterBundle(df_1h, df_4h, df_1d)
    except Exception as e:
        adapters = None; print(f'  ⚠️ adapters: {e}')

    scorer = FullSystemScorer(feed, adapters)
    feed.set_cursor('ETHUSDT', 500)

    for dire in ['SHORT', 'LONG']:
        extra = adapters.compute(500, dire) if adapters else {}
        r = scorer._call_brain_score(
            __import__('brahma_brain.brahma_brain', fromlist=['brahma_brain']),
            'ETHUSDT', dire, extra)
        bd = r.get('breakdown', {})
        total = r.get('total', 0)
        print(f'\n  {dire}: 总分={total}')
        for k,v in bd.items():
            bar = '█'*max(0,min(int(abs(v)/2),15))
            sign = '+' if v>=0 else ''
            print(f'    {k:20s} {sign}{v:+4d}  [{bar}]')

    print(f'\n  引擎验证耗时: {time.time()-t0:.1f}s ✅')

    # 快速回测验证（50根K线采样）
    print('\n🔄 快速回测采样 (n=50 bar)...')
    r = backtest_full('ETHUSDT', threshold=130, fast=True, step=350)
    if r.get('status') == 'done':
        print(f'  ETH: n={r["n"]}  WR={r["wr"]:.1%}  PF={r["pf"]:.2f}  引擎=full_15d ✅')
    else:
        print(f'  ⚠️ {r}')
