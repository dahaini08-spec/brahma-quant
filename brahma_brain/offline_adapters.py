#!/usr/bin/env python3
"""
offline_adapters.py — 达摩院全性能测试 · 实时引擎离线化适配器 v1.0
═══════════════════════════════════════════════════════════════════
将6个依赖实时API的引擎改造为可消费历史DataFrame的离线版本

引擎映射:
  whale_engine       → WhaleOffline     (成交量z-score代理大单)
  orderbook_engine   → OrderbookOffline (价格波动不对称代理OBI)
  options_engine     → OptionsOffline   (资金费率历史代理情绪/PCR)
  enhanced_signal    → EnhancedOffline  (OI变化率代理未平仓量)
  microstructure     → MicroOffline     (ATR/价格代理买卖价差)
  sentiment_engine   → SentimentOffline (RSI极值+FNG历史代理)

使用方式:
  from brahma_brain.offline_adapters import OfflineAdapterBundle
  bundle = OfflineAdapterBundle(df_1h, df_4h, df_1d, funding_df=None)
  extra = bundle.compute(i, direction)   # i = 当前bar索引
  # extra 格式与 brahma_brain.analyze() 的 extra_data 完全一致
"""


from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import math
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _zscore(series: pd.Series, window: int = 20) -> pd.Series:
    mu = series.rolling(window).mean()
    sd = series.rolling(window).std().replace(0, 1e-9)
    return (series - mu) / sd

# [math_utils] _ema 已统一到 brahma_brain.math_utils，此处保留备用
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    g = d.clip(lower=0).ewm(com=n-1, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(com=n-1, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, 1e-9))

def _atr(h, l, c, n=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(com=n-1, adjust=False).mean()


# ════════════════════════════════════════════════════════════════
# 1. Whale Engine 离线版
#    实时: Binance aggTrade大单流
#    离线: 成交量z-score > 2.5σ 代理大单; 方向用价格变化判断
# ════════════════════════════════════════════════════════════════

class WhaleOffline:
    """大单/鲸鱼信号离线代理"""

    def __init__(self, df: pd.DataFrame, vol_z_threshold: float = 2.5):
        df = df.copy()
        df['vol_z']      = _zscore(df['volume'], 20)
        df['price_chg']  = df['close'].pct_change()
        df['vol_z_ma5']  = df['vol_z'].rolling(5).mean()
        # 累积大单方向
        df['whale_buy']  = ((df['vol_z'] > vol_z_threshold) & (df['price_chg'] > 0)).astype(float)
        df['whale_sell'] = ((df['vol_z'] > vol_z_threshold) & (df['price_chg'] < 0)).astype(float)
        df['whale_net']  = df['whale_buy'].rolling(5).sum() - df['whale_sell'].rolling(5).sum()
        self.df = df

    def score(self, i: int, direction: str) -> Dict[str, Any]:
        row = self.df.iloc[i]
        vol_z    = float(row.get('vol_z', 0))
        whale_net = float(row.get('whale_net', 0))
        price_chg = float(row.get('price_chg', 0))

        score = 0
        signals = []
        d = 1 if direction in ('LONG', '做多') else -1

        # 近期大单净方向
        if whale_net * d > 2:
            score += 18; signals.append(f'大单净流入{whale_net:.1f}根 +18')
        elif whale_net * d > 1:
            score += 12; signals.append(f'鲸鱼偏向方向 +12')
        elif whale_net * d < -2:
            score -= 8; signals.append(f'⚠️大单逆向 -8')

        # 当前bar成交量异常
        if vol_z > 3.0 and price_chg * d > 0:
            score += 8; signals.append(f'极端放量同向(z={vol_z:.1f}) +8')
        elif vol_z > 2.0 and price_chg * d > 0:
            score += 5; signals.append(f'放量同向 +5')

        return {
            'score':     max(-15, min(score, 25)),
            'vol_z':     round(vol_z, 2),
            'whale_net': round(whale_net, 1),
            'signals':   signals,
            'source':    'offline',
        }


# ════════════════════════════════════════════════════════════════
# 2. Orderbook Engine 离线版
#    实时: REST /depth L2订单簿 OBI
#    离线: 买卖压力不对称 = 用价格位置+成交量不对称代理
# ════════════════════════════════════════════════════════════════

class OrderbookOffline:
    """L2订单簿OBI离线代理"""

    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        c = df['close']; h = df['high']; l = df['low']; v = df['volume']
        # 价格位置在高低范围内的比例 → 代理buy/sell pressure
        bar_range = (h - l).replace(0, 1e-9)
        df['buy_pressure']  = (c - l) / bar_range          # 0=底 1=顶
        df['sell_pressure'] = (h - c) / bar_range           # 0=顶 1=底
        df['obi_proxy']     = df['buy_pressure'] - 0.5     # -0.5~+0.5
        df['obi_ma10']      = df['obi_proxy'].rolling(10).mean()
        # 成交量加权OBI
        df['vol_obi']       = (df['obi_proxy'] * v).rolling(10).sum() / v.rolling(10).sum()
        # 流动性墙代理: 最近20根的分位数
        df['resist_proxy']  = h.rolling(20).max()
        df['support_proxy'] = l.rolling(20).min()
        self.df = df

    def score(self, i: int, direction: str) -> Dict[str, Any]:
        row = self.df.iloc[i]
        obi      = float(row.get('vol_obi', 0))
        resist   = float(row.get('resist_proxy', 0))
        support  = float(row.get('support_proxy', 0))
        price    = float(row['close'])

        score = 0
        d = 1 if direction in ('LONG', '做多') else -1

        # OBI方向
        if obi * d > 0.2:
            score += 8
        elif obi * d > 0.1:
            score += 4
        elif obi * d < -0.2:
            score -= 5

        # 价格接近支撑/阻力
        price_to_resist = (resist - price) / price if resist > price else 0
        price_to_support = (price - support) / price if support < price else 0
        if direction in ('SHORT', '做空') and price_to_resist < 0.005:
            score += 6  # 接近阻力做空
        if direction in ('LONG', '做多') and price_to_support < 0.005:
            score += 6  # 接近支撑做多

        slippage_est = abs(obi) * 0.001 + 0.0005  # 代理滑点

        return {
            'score':         max(-10, min(score, 15)),
            'obi':           round(obi, 4),
            'slippage_est':  round(slippage_est, 5),
            'resist':        round(resist, 4),
            'support':       round(support, 4),
            'source':        'offline',
        }


# ════════════════════════════════════════════════════════════════
# 3. Options Engine 离线版
#    实时: 资金费率/IV偏斜/PCR
#    离线: 资金费率历史 + RSI极值代理期权情绪
# ════════════════════════════════════════════════════════════════

class OptionsOffline:
    """期权/资金费率情绪离线代理"""

    def __init__(self, df: pd.DataFrame, funding_df: Optional[pd.DataFrame] = None):
        df = df.copy()
        df['rsi']         = _rsi(df['close'], 14)
        df['rsi_z']       = _zscore(df['rsi'], 20)
        # 价格动量代理IV
        df['vol_20']      = df['close'].pct_change().rolling(20).std() * math.sqrt(252 * 24)
        df['vol_ma']      = df['vol_20'].rolling(20).mean()
        df['iv_skew_proxy'] = df['rsi'] - 50  # RSI偏离中性 = 情绪偏斜

        if funding_df is not None and len(funding_df) > 0:
            # 对齐时间戳
            self.has_funding = True
            self.funding_df = funding_df
        else:
            self.has_funding = False
        self.df = df

    def score(self, i: int, direction: str) -> Dict[str, Any]:
        row = self.df.iloc[i]
        rsi  = float(row.get('rsi', 50))
        iv_skew = float(row.get('iv_skew_proxy', 0))

        score = 0
        d = 1 if direction in ('LONG', '做多') else -1

        # RSI极值代理情绪
        if direction in ('SHORT', '做空'):
            if rsi > 80: score += 12
            elif rsi > 70: score += 7
            elif rsi > 65: score += 3
            elif rsi < 40: score -= 5  # 超卖不宜做空
        else:
            if rsi < 20: score += 12
            elif rsi < 30: score += 7
            elif rsi < 35: score += 3
            elif rsi > 60: score -= 5

        # 资金费率（如有）
        funding_score = 0
        if self.has_funding:
            try:
                ts = self.df.index[i]
                near_funding = self.funding_df[self.funding_df.index <= ts].iloc[-1]
                fr = float(near_funding.get('fundingRate', 0))
                if direction in ('SHORT', '做空') and fr > 0.001:
                    funding_score = min(int(fr / 0.001 * 5), 8)
                elif direction in ('LONG', '做多') and fr < -0.001:
                    funding_score = min(int(abs(fr) / 0.001 * 5), 8)
                score += funding_score
            except Exception:
                pass

        return {
            'score':         max(-8, min(score, 15)),
            'rsi':           round(rsi, 1),
            'iv_skew_proxy': round(iv_skew, 2),
            'funding_score': funding_score,
            'source':        'offline',
        }


# ════════════════════════════════════════════════════════════════
# 4. Enhanced Signal Engine 离线版
#    实时: OI数据、清算数据、资金流
#    离线: 未平仓量变化率 = 价格+成交量组合代理
# ════════════════════════════════════════════════════════════════

class EnhancedOffline:
    """增强信号离线代理（OI/清算/资金流）"""

    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        c = df['close']; v = df['volume']

        # OI代理: 成交量加速度（OI通常与成交量正相关）
        df['vol_acc']     = v.pct_change(3)
        df['oi_proxy']    = v.rolling(5).mean() / v.rolling(20).mean() - 1  # OI变化率代理
        # 清算代理: 大跌+大量 = 多头清算；大涨+大量 = 空头清算
        df['price_chg3']  = c.pct_change(3)
        df['vol_z3']      = _zscore(v, 20)
        df['liq_long']    = ((df['price_chg3'] < -0.03) & (df['vol_z3'] > 2.0)).astype(float)
        df['liq_short']   = ((df['price_chg3'] > 0.03)  & (df['vol_z3'] > 2.0)).astype(float)
        # 资金流代理: 成交量×价格变化方向
        df['money_flow']  = v * df['price_chg3'].apply(lambda x: 1 if x > 0 else -1)
        df['mf_ma5']      = df['money_flow'].rolling(5).sum()
        self.df = df

    def score(self, i: int, direction: str) -> Dict[str, Any]:
        row = self.df.iloc[i]
        oi_proxy  = float(row.get('oi_proxy', 0))
        liq_long  = float(row.get('liq_long', 0))
        liq_short = float(row.get('liq_short', 0))
        mf        = float(row.get('mf_ma5', 0))

        score = 0
        signals = []
        d = 1 if direction in ('LONG', '做多') else -1

        # OI增加 + 价格方向一致 = 新增持仓支撑趋势
        if oi_proxy > 0.2 and d > 0:
            score += 8; signals.append('OI增加看多 +8')
        elif oi_proxy > 0.2 and d < 0:
            score += 6; signals.append('OI增加看空 +6')
        elif oi_proxy < -0.2:
            score -= 3; signals.append('OI萎缩 -3')

        # 清算事件
        if liq_long > 0 and d < 0:
            score += 10; signals.append('多头清算浪 +10')
        elif liq_short > 0 and d > 0:
            score += 10; signals.append('空头清算浪 +10')

        # 资金流方向
        if mf * d > 0:
            score += 5; signals.append('资金流向一致 +5')
        elif mf * d < 0:
            score -= 3

        return {
            'score':    max(-10, min(score, 20)),
            'oi_proxy': round(oi_proxy, 4),
            'liq_long': int(liq_long),
            'liq_short':int(liq_short),
            'signals':  signals,
            'source':   'offline',
        }


# ════════════════════════════════════════════════════════════════
# 5. Microstructure Engine 离线版
#    实时: 买卖价差、订单流毒性、VPIN
#    离线: ATR/价格比代理价差，成交量不平衡代理毒性
# ════════════════════════════════════════════════════════════════

class MicroOffline:
    """微观结构离线代理"""

    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        c = df['close']; h = df['high']; l = df['low']; v = df['volume']

        atr = _atr(h, l, c, 14)
        df['spread_proxy']  = atr / c  # 相对价差代理
        df['spread_z']      = _zscore(df['spread_proxy'], 20)

        # VPIN代理: 成交量不平衡
        bar_range = (h - l).replace(0, 1e-9)
        buy_vol  = v * (c - l) / bar_range
        sell_vol = v * (h - c) / bar_range
        df['vpin_proxy']    = (buy_vol - sell_vol).rolling(10).mean() / v.rolling(10).mean()

        # 流动性代理: 成交量/ATR = 单位价格移动的成交量（越高流动性越好）
        df['liquidity']     = v / (atr + 1e-9)
        df['liq_z']         = _zscore(df['liquidity'], 20)

        self.df = df

    def score(self, i: int, direction: str) -> Dict[str, Any]:
        row = self.df.iloc[i]
        spread  = float(row.get('spread_proxy', 0.001))
        spread_z = float(row.get('spread_z', 0))
        vpin    = float(row.get('vpin_proxy', 0))
        liq_z   = float(row.get('liq_z', 0))

        score = 0
        d = 1 if direction in ('LONG', '做多') else -1

        # 低价差 = 流动性好 = 更容易入场
        if spread_z < -1.0:
            score += 6  # 流动性异常好
        elif spread_z < 0:
            score += 3
        elif spread_z > 1.5:
            score -= 4  # 流动性差，滑点大

        # VPIN方向
        if vpin * d > 0.1:
            score += 5
        elif vpin * d < -0.1:
            score -= 3

        # 流动性高 = 低滑点
        if liq_z > 1.0:
            score += 4

        slippage_est = spread * (1 + max(0, spread_z) * 0.3)

        return {
            'score':        max(-8, min(score, 12)),
            'spread_proxy': round(spread, 6),
            'vpin_proxy':   round(vpin, 4),
            'slippage_est': round(slippage_est, 6),
            'source':       'offline',
        }


# ════════════════════════════════════════════════════════════════
# 6. Sentiment Engine 离线版
#    实时: FNG API + CoinGecko社区情绪
#    离线: RSI极值 + 价格偏离均值 + 历史情绪周期
# ════════════════════════════════════════════════════════════════

class SentimentOffline:
    """情绪引擎离线代理"""

    # FNG历史近似: 2019-2026年主要体制对应FNG均值
    YEAR_FNG = {
        '2019': 35, '2020': 45, '2021': 72, '2022': 18,
        '2023': 52, '2024': 65, '2025': 48, '2026': 35,
    }

    def __init__(self, df: pd.DataFrame):
        df = df.copy()
        df['rsi']      = _rsi(df['close'], 14)
        df['rsi14_ma'] = df['rsi'].rolling(14).mean()
        # 价格偏离200日均线 → 极端行情代理
        ema200 = _ema(df['close'], 200)
        df['price_dev_200'] = (df['close'] - ema200) / ema200

        # 历史波动情绪代理
        ret_20 = df['close'].pct_change(20)
        df['ret_z'] = _zscore(ret_20, 60)
        self.df = df

    def _fng_by_ts(self, ts) -> int:
        year = str(ts.year) if hasattr(ts, 'year') else '2025'
        return self.YEAR_FNG.get(year, 45)

    def score(self, i: int, direction: str) -> Dict[str, Any]:
        row = self.df.iloc[i]
        ts  = self.df.index[i]
        rsi = float(row.get('rsi', 50))
        dev = float(row.get('price_dev_200', 0))
        ret_z = float(row.get('ret_z', 0))
        fng = self._fng_by_ts(ts)

        score = 0
        d = 1 if direction in ('LONG', '做多') else -1

        # FNG极值
        if fng < 20 and d > 0:   score += 10  # 极端恐惧做多
        elif fng > 80 and d < 0: score += 10  # 极端贪婪做空
        elif fng < 35 and d > 0: score += 5
        elif fng > 65 and d < 0: score += 5

        # 价格偏离200日均
        if dev * (-d) > 0.3:     score += 8   # 极端偏离反向
        elif dev * (-d) > 0.15:  score += 4

        # RSI极值
        if direction in ('SHORT', '做空') and rsi > 75: score += 5
        elif direction in ('LONG', '做多') and rsi < 25: score += 5

        return {
            'score':   max(-10, min(score, 15)),
            'fng':     fng,
            'rsi':     round(rsi, 1),
            'dev_200': round(dev, 4),
            'source':  'offline',
        }


# ════════════════════════════════════════════════════════════════
# 总装: OfflineAdapterBundle
# ════════════════════════════════════════════════════════════════

class OfflineAdapterBundle:
    """
    统一打包所有离线适配器，输出与 brahma_brain.analyze() 的
    extra_data 格式完全兼容的字典
    """

    def __init__(self,
                 df_1h: pd.DataFrame,
                 df_4h: Optional[pd.DataFrame] = None,
                 df_1d: Optional[pd.DataFrame] = None,
                 funding_df: Optional[pd.DataFrame] = None):
        self.whale     = WhaleOffline(df_1h)
        self.orderbook = OrderbookOffline(df_1h)
        self.options   = OptionsOffline(df_1h, funding_df=funding_df)
        self.enhanced  = EnhancedOffline(df_1h)
        self.micro     = MicroOffline(df_1h)
        self.sentiment = SentimentOffline(df_1h)
        self.df_1h     = df_1h
        self._ready    = True

    def compute(self, i: int, direction: str) -> Dict[str, Any]:
        """
        计算第 i 根K线的所有离线引擎输出
        返回格式与 brahma_brain.py analyze() 内 extra_data 一致
        """
        if i < 200:
            return {}  # 预热期不足
        try:
            whale_res = self.whale.score(i, direction)
            ob_res    = self.orderbook.score(i, direction)
            opt_res   = self.options.score(i, direction)
            enh_res   = self.enhanced.score(i, direction)
            micro_res = self.micro.score(i, direction)
            sent_res  = self.sentiment.score(i, direction)

            return {
                # 直接映射到 brahma_brain 的 extra_data key
                'whale':      whale_res,
                'orderbook':  ob_res,
                'options':    opt_res,
                'enhanced':   enh_res,
                'microstructure': micro_res,
                'sentiment':  sent_res,
                # 订单流代理 (映射到 order_flow key)
                'order_flow': {
                    'score':     enh_res['score'],
                    'direction': direction,
                    'source':    'offline_proxy',
                },
                # 滑点
                'slippage':   ob_res.get('slippage_est', 0.001),
            }
        except Exception as e:
            return {'_error': str(e)}

    @staticmethod
    def from_parquet(sym: str, data_dir: str) -> 'OfflineAdapterBundle':
        """便捷构造：从 dharma/data 目录直接加载"""
        from pathlib import Path
        import pandas as pd
        base = Path(data_dir)
        sym_l = sym.lower()

        def load(iv):
            f = base / f'{sym_l}_{iv}_2018_2026.parquet'
            if f.exists():
                return pd.read_parquet(f)
            return None

        df_1h = load('1h')
        if df_1h is None:
            raise FileNotFoundError(f'找不到 {sym} 1h 数据')

        df_4h = load('4h')
        df_1d = load('1d')

        # 尝试加载资金费率
        fr_f = base / f'{sym_l}_funding_rate.parquet'
        funding_df = pd.read_parquet(fr_f) if fr_f.exists() else None

        return OfflineAdapterBundle(df_1h, df_4h, df_1d, funding_df)


# ════════════════════════════════════════════════════════════════
# 快速验证
# ════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    from pathlib import Path
    data_dir = Path(__file__).parent.parent / 'dharma' / 'data'
    print('🔧 离线适配器快速验证')

    try:
        bundle = OfflineAdapterBundle.from_parquet('ETHUSDT', str(data_dir))
        df = bundle.df_1h

        # 测试第500根K线
        i = 500
        for dire in ['LONG', 'SHORT']:
            extra = bundle.compute(i, dire)
            if '_error' not in extra:
                scores = {k: v.get('score', 0) for k, v in extra.items()
                          if isinstance(v, dict) and 'score' in v}
                print(f'  {dire}: {scores}')
            else:
                print(f'  {dire}: ERROR {extra["_error"]}')
        print('✅ 离线适配器正常')
    except Exception as e:
        print(f'❌ {e}')
        import traceback; traceback.print_exc()
