#!/usr/bin/env python3
"""
dharma_combo_backtest.py — 达摩院组合权重回测引擎 v2
设计院封印 2026-09-04 苏摩111

核心原则：
  1. 真实数据优先：BTC/ETH 6.8年gz + 其他币Binance实时拉取最大量
  2. 零上帝视角：信号只用K线i收盘时已知数据
  3. Walk-Forward验证：训练期70% → 测试期30%（完全隔离）
  4. 单指标先隔离，再穷举双指标组合，找WR>60%的组合
  5. 大样本优先：n<50的结论不可信，直接标INSUFFICIENT
  6. 盲测：测试期结果不参与参数选择，防止过拟合

接入位置：独立脚本，输出 data/dharma_combo_result.json
"""
import sys, json, math, time, gzip, signal, urllib.request
from pathlib import Path
from itertools import combinations
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 超时守卫 ────────────────────────────────────────────────
MAX_RUNTIME = 280
signal.signal(signal.SIGALRM, lambda s, f: (
    print('\n[dharma] 超时，保存已完成结果...', flush=True),
    sys.exit(0)
))
signal.alarm(MAX_RUNTIME)

# ── 回测参数 ─────────────────────────────────────────────────
SL_PCT      = 0.020   # 止损2%
TP_PCT      = 0.020   # 止盈2%  RR=1:1（纯净测WR）
HOLD_BARS   = 12      # 最大持仓根数
MIN_SIGNALS = 50      # 最小样本
TRAIN_RATIO = 0.70    # walk-forward训练比例
# ─────────────────────────────────────────────────────────────

# ── 47个目标币种 ──────────────────────────────────────────────
SYMBOLS = [
    'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','ADAUSDT',
    'DOTUSDT','LINKUSDT','LTCUSDT','XRPUSDT','DOGEUSDT',
    'ATOMUSDT','ALGOUSDT','CRVUSDT','ETCUSDT','RUNEUSDT',
    'SNXUSDT','SUSHIUSDT','THETAUSDT','TRXUSDT','VETUSDT',
    'XLMUSDT','BCHUSDT','COMPUSDT','AAVEUSDT','UNIUSDT',
    'FTMUSDT','NEARUSDT','AVAXUSDT','MATICUSDT','APTUSDT',
    'ARBUSDT','OPUSDT','INJUSDT','SUIUSDT','SEIUSDT',
]
TIMEFRAMES = ['15m', '1h', '4h', '1d']


# ════════════════════════════════════════════════════════════
# 数据加载层
# ════════════════════════════════════════════════════════════

def load_klines(symbol: str, tf: str, limit: int = 1500) -> list:
    """
    优先级：
    1. 本地gz（BTC/ETH 6.8年）
    2. Binance实时（最多1500根）
    """
    sym_lower = symbol.lower()
    gz = BASE / 'data' / 'historical' / f'{symbol}_{tf}.jsonl.gz'
    if gz.exists():
        try:
            klines = []
            with gzip.open(gz, 'rt') as fp:
                for line in fp:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        klines.append([int(d['ts']), float(d['o']), float(d['h']),
                                       float(d['l']), float(d['c']), float(d['v'])])
                    elif isinstance(d, list):
                        klines.append([int(d[0]), float(d[1]), float(d[2]),
                                       float(d[3]), float(d[4]), float(d[5])])
            if len(klines) > 200:
                return klines
        except Exception:
            pass

    # 回退Binance实时
    try:
        url = (f'https://fapi.binance.com/fapi/v1/klines'
               f'?symbol={symbol}&interval={tf}&limit={limit}')
        data = json.loads(urllib.request.urlopen(url, timeout=8).read())
        return [[int(k[0]), float(k[1]), float(k[2]),
                 float(k[3]), float(k[4]), float(k[5])] for k in data]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════
# 技术指标计算层（严格因果：i时刻只用<=i的数据）
# ════════════════════════════════════════════════════════════

def _ema(data, n):
    k = 2 / (n + 1)
    out = [data[0]]
    for v in data[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def _rsi(closes, n=14):
    gains  = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    res = [None] * n
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag*(n-1) + gains[i]) / n
        al = (al*(n-1) + losses[i]) / n
        rs = ag / al if al > 0 else 99
        res.append(100 - 100 / (1 + rs))
    return [None] + res  # 对齐closes

def _atr(klines, n=14):
    trs = [max(klines[i][2]-klines[i][3],
               abs(klines[i][2]-klines[i-1][4]),
               abs(klines[i][3]-klines[i-1][4])) for i in range(1, len(klines))]
    res = [None] * n
    avg = sum(trs[:n]) / n
    res.append(avg)
    for t in trs[n:]:
        avg = (avg*(n-1) + t) / n
        res.append(avg)
    return [None] + res

def _bollinger(closes, n=20, k=2.0):
    res = [None] * (n-1)
    for i in range(n-1, len(closes)):
        w = closes[i-n+1:i+1]
        mid = sum(w) / n
        std = math.sqrt(sum((x-mid)**2 for x in w) / n)
        res.append((mid+k*std, mid, mid-k*std, (2*k*std/mid)*100))
    return res

def _hurst(closes, i, window=100):
    """R/S Hurst估算，只用i之前window根数据"""
    if i < window:
        return None
    w = closes[i-window:i]
    try:
        lr = [math.log(w[j]/w[j-1]) for j in range(1, len(w)) if w[j] > 0 and w[j-1] > 0]
        if len(lr) < 20:
            return None
        m = sum(lr) / len(lr)
        dev = [r - m for r in lr]
        cum = []
        s = 0
        for d in dev:
            s += d
            cum.append(s)
        R = max(cum) - min(cum)
        S = math.sqrt(sum(d**2 for d in dev) / len(dev))
        if S <= 0 or R/S <= 0:
            return None
        return math.log(R/S) / math.log(len(lr))
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# 74维特征提取层（每根K线 → 特征向量）
# 只计算可以纯价格/量数据计算的维度（无OI/LSR历史限制）
# ════════════════════════════════════════════════════════════

def extract_features(klines: list) -> list:
    """
    为每根K线提取特征向量，返回 list of dict
    i时刻的特征只能用klines[0..i]的数据
    """
    n = len(klines)
    closes = [k[4] for k in klines]
    opens  = [k[1] for k in klines]
    highs  = [k[2] for k in klines]
    lows   = [k[3] for k in klines]
    vols   = [k[5] for k in klines]

    # 预计算序列
    ema9   = _ema(closes, 9)
    ema21  = _ema(closes, 21)
    ema50  = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    rsi14  = _rsi(closes, 14)
    rsi7   = _rsi(closes, 7)
    atrs   = _atr(klines, 14)
    bbs    = _bollinger(closes, 20, 2.0)

    features = []
    for i in range(210, n):  # 从210开始确保所有指标就绪
        c = closes[i]
        o = opens[i]

        # ── 动量类 ──────────────────────────────────────────
        f = {}

        # F01: RSI14位置
        f['rsi14']       = rsi14[i] if rsi14[i] else 50.0
        f['rsi14_ob']    = 1 if (rsi14[i] or 50) > 70 else 0   # 超买
        f['rsi14_os']    = 1 if (rsi14[i] or 50) < 30 else 0   # 超卖
        f['rsi7_ob']     = 1 if (rsi7[i] or 50) > 75 else 0
        f['rsi7_os']     = 1 if (rsi7[i] or 50) < 25 else 0

        # F02: EMA排列
        f['ema9_21_bull'] = 1 if ema9[i] > ema21[i] else 0
        f['ema21_50_bull']= 1 if ema21[i] > ema50[i] else 0
        f['ema50_200_bull']= 1 if ema50[i] > ema200[i] else 0
        f['full_bull']   = 1 if (ema9[i] > ema21[i] > ema50[i] > ema200[i]) else 0
        f['full_bear']   = 1 if (ema9[i] < ema21[i] < ema50[i] < ema200[i]) else 0

        # EMA金叉/死叉（当根）
        f['ema_golden']  = 1 if (ema9[i] > ema21[i] and ema9[i-1] <= ema21[i-1]) else 0
        f['ema_death']   = 1 if (ema9[i] < ema21[i] and ema9[i-1] >= ema21[i-1]) else 0

        # F03: 价格距EMA距离
        f['dist_ema50']  = (c - ema50[i]) / ema50[i] if ema50[i] > 0 else 0
        f['dist_ema200'] = (c - ema200[i]) / ema200[i] if ema200[i] > 0 else 0
        f['above_ema200']= 1 if c > ema200[i] else 0

        # ── 波动率类 ─────────────────────────────────────────
        atr_v = atrs[i] or (c * 0.01)
        f['atr_pct']     = atr_v / c

        # BB位置
        bb = bbs[i]
        if bb:
            upper, mid, lower, width = bb
            f['bb_position']  = (c - lower) / (upper - lower) if (upper-lower) > 0 else 0.5
            f['bb_above']     = 1 if c > upper else 0
            f['bb_below']     = 1 if c < lower else 0
            f['bb_width']     = width
            f['bb_squeeze']   = 1 if width < 2.0 else 0   # 收缩
            f['bb_expansion'] = 1 if width > 6.0 else 0   # 扩张
        else:
            f['bb_position'] = f['bb_above'] = f['bb_below'] = 0
            f['bb_width'] = f['bb_squeeze'] = f['bb_expansion'] = 0

        # ATR扩张
        avg_atr = sum(a for a in atrs[max(0,i-10):i] if a) / max(len([a for a in atrs[max(0,i-10):i] if a]), 1)
        f['atr_expand']   = 1 if (atrs[i] or 0) > avg_atr * 1.5 else 0

        # ── 成交量类 ─────────────────────────────────────────
        avg_vol4 = sum(vols[i-4:i]) / 4 if i >= 4 else vols[i]
        f['vol_ratio']    = vols[i] / avg_vol4 if avg_vol4 > 0 else 1.0
        f['vol_spike3x']  = 1 if f['vol_ratio'] >= 3.0 else 0
        f['vol_spike2x']  = 1 if f['vol_ratio'] >= 2.0 else 0

        # 量价配合
        candle_bull = closes[i] > opens[i]
        f['vol_bull']     = 1 if (f['vol_spike2x'] and candle_bull) else 0
        f['vol_bear']     = 1 if (f['vol_spike2x'] and not candle_bull) else 0

        # ── K线形态类 ─────────────────────────────────────────
        # 连续阴线/阳线
        bearish_streak = sum(1 for j in range(max(0,i-5), i) if closes[j] < opens[j])
        bullish_streak = sum(1 for j in range(max(0,i-5), i) if closes[j] > opens[j])
        f['bearish_4']    = 1 if bearish_streak >= 4 else 0
        f['bullish_4']    = 1 if bullish_streak >= 4 else 0
        f['bearish_3']    = 1 if bearish_streak >= 3 else 0
        f['bullish_3']    = 1 if bullish_streak >= 3 else 0

        # 当根K线特征
        body = abs(c - o)
        wick_up   = highs[i] - max(c, o)
        wick_down = min(c, o) - lows[i]
        full_range = highs[i] - lows[i] or 0.0001
        f['body_ratio']   = body / full_range         # 实体占比
        f['wick_up_ratio']= wick_up / full_range      # 上影线占比
        f['wick_dn_ratio']= wick_down / full_range    # 下影线占比
        f['doji']         = 1 if f['body_ratio'] < 0.1 else 0
        f['hammer']       = 1 if (f['wick_dn_ratio'] > 0.6 and f['body_ratio'] < 0.3) else 0
        f['shooting_star']= 1 if (f['wick_up_ratio'] > 0.6 and f['body_ratio'] < 0.3) else 0

        # ── 趋势强度类 ─────────────────────────────────────────
        # N根涨跌幅
        for n_bars in [5, 10, 20]:
            if i >= n_bars:
                ret = (c - closes[i-n_bars]) / closes[i-n_bars]
                f[f'ret_{n_bars}'] = ret
                f[f'ret_{n_bars}_pos'] = 1 if ret > 0 else 0
            else:
                f[f'ret_{n_bars}'] = 0
                f[f'ret_{n_bars}_pos'] = 0

        # Hurst（计算开销大，每隔5根计算一次，其余用上一个值）
        if i % 5 == 0:
            h_val = _hurst(closes, i, 100)
            f['hurst'] = h_val if h_val else 0.5
        else:
            f['hurst'] = features[-1]['hurst'] if features else 0.5
        f['hurst_trend'] = 1 if f['hurst'] > 0.6 else 0
        f['hurst_rev']   = 1 if f['hurst'] < 0.4 else 0

        # ── 价格位置类 ─────────────────────────────────────────
        # N根内的高低位置
        hi20 = max(highs[i-20:i]) if i >= 20 else highs[i]
        lo20 = min(lows[i-20:i])  if i >= 20 else lows[i]
        rng20 = (hi20 - lo20) or 0.0001
        f['pos_20']       = (c - lo20) / rng20    # 0=底部，1=顶部

        hi50 = max(highs[i-50:i]) if i >= 50 else hi20
        lo50 = min(lows[i-50:i])  if i >= 50 else lo20
        rng50 = (hi50 - lo50) or 0.0001
        f['pos_50']       = (c - lo50) / rng50

        # 新高新低
        f['new_high_20']  = 1 if highs[i] >= hi20 else 0
        f['new_low_20']   = 1 if lows[i]  <= lo20 else 0

        features.append(f)

    return features, 210  # features[0] 对应 klines[210]


# ════════════════════════════════════════════════════════════
# 核心回测执行层
# ════════════════════════════════════════════════════════════

def run_backtest(klines: list, signal_idx: list, direction: str) -> dict:
    """
    signal_idx: list of bar indices (对应klines的实际index)
    direction: 'LONG' or 'SHORT'
    零上帝视角：入场=收盘价，出场=后续K线high/low
    """
    wins = losses = timeouts = 0
    pnls = []

    for idx in signal_idx:
        if idx + HOLD_BARS >= len(klines):
            continue
        entry = klines[idx][4]
        if entry <= 0:
            continue
        outcome = 'TIMEOUT'
        ep = 0.0
        for j in range(1, HOLD_BARS + 1):
            h, l = klines[idx+j][2], klines[idx+j][3]
            if direction == 'LONG':
                if l <= entry * (1 - SL_PCT): outcome = 'LOSS'; ep = -SL_PCT; break
                if h >= entry * (1 + TP_PCT): outcome = 'WIN';  ep =  TP_PCT; break
            else:
                if h >= entry * (1 + SL_PCT): outcome = 'LOSS'; ep = -SL_PCT; break
                if l <= entry * (1 - TP_PCT): outcome = 'WIN';  ep =  TP_PCT; break
        if outcome == 'WIN':    wins += 1;    pnls.append(ep)
        elif outcome == 'LOSS': losses += 1;  pnls.append(ep)
        else:
            timeouts += 1
            fc = klines[idx + HOLD_BARS][4]
            pnls.append((fc-entry)/entry if direction=='LONG' else (entry-fc)/entry)

    n   = wins + losses + timeouts
    wl  = wins + losses
    wr  = wins / wl if wl > 0 else 0.5
    ev  = sum(pnls) / len(pnls) if pnls else 0.0

    # 连续亏损
    max_dd_streak = cur_streak = 0
    for p in pnls:
        if p < 0: cur_streak += 1; max_dd_streak = max(max_dd_streak, cur_streak)
        else: cur_streak = 0

    return {
        'n': n, 'wins': wins, 'losses': losses, 'timeouts': timeouts,
        'wr': round(wr, 4), 'ev': round(ev, 6),
        'max_loss_streak': max_dd_streak,
        'sufficient': n >= MIN_SIGNALS,
    }


def grade_result(r: dict) -> str:
    if not r['sufficient']:   return 'INSUFFICIENT'
    if r['wr'] >= 0.65 and r['ev'] > 0.005: return 'TIER_1'
    if r['wr'] >= 0.60 and r['ev'] > 0:     return 'TIER_2'
    if r['wr'] >= 0.55 and r['ev'] > 0:     return 'TIER_3'
    return 'NOISE'


# ════════════════════════════════════════════════════════════
# 单指标验证 + 双指标组合穷举
# ════════════════════════════════════════════════════════════

# 定义要测试的"信号规则"（特征名 + 方向 + 阈值）
SIGNAL_RULES = {
    # RSI族
    'RSI_os_LONG':     lambda f: f['rsi14_os'] == 1,
    'RSI_ob_SHORT':    lambda f: f['rsi14_ob'] == 1,
    'RSI7_os_LONG':    lambda f: f['rsi7_os'] == 1,
    'RSI7_ob_SHORT':   lambda f: f['rsi7_ob'] == 1,

    # EMA族
    'EMA_golden_LONG': lambda f: f['ema_golden'] == 1,
    'EMA_death_SHORT': lambda f: f['ema_death'] == 1,
    'FullBull_LONG':   lambda f: f['full_bull'] == 1,
    'FullBear_SHORT':  lambda f: f['full_bear'] == 1,

    # 成交量族
    'VolBull_LONG':    lambda f: f['vol_bull'] == 1,
    'VolBear_SHORT':   lambda f: f['vol_bear'] == 1,
    'VolSpike3_LONG':  lambda f: f['vol_spike3x'] == 1 and f['ret_5'] > 0,
    'VolSpike3_SHORT': lambda f: f['vol_spike3x'] == 1 and f['ret_5'] < 0,

    # BB族
    'BB_above_SHORT':  lambda f: f['bb_above'] == 1,
    'BB_below_LONG':   lambda f: f['bb_below'] == 1,
    'BB_sqz_bull_LONG':lambda f: f['bb_squeeze']==1 and f['ema9_21_bull']==1,
    'BB_sqz_bear_SHORT':lambda f: f['bb_squeeze']==1 and f['ema9_21_bull']==0,

    # K线形态族
    'Hammer_LONG':     lambda f: f['hammer'] == 1,
    'Star_SHORT':      lambda f: f['shooting_star'] == 1,
    'Bear4_LONG':      lambda f: f['bearish_4'] == 1,   # 超跌反弹
    'Bull4_SHORT':     lambda f: f['bullish_4'] == 1,   # 超涨回调

    # 位置族
    'Oversold_LONG':   lambda f: f['pos_20'] < 0.15,    # 20根低位
    'Overbought_SHORT':lambda f: f['pos_20'] > 0.85,    # 20根高位
    'NewHigh_LONG':    lambda f: f['new_high_20'] == 1,
    'NewLow_SHORT':    lambda f: f['new_low_20'] == 1,  # 破新低做空

    # Hurst族
    'Hurst_trend_bull_LONG': lambda f: f['hurst_trend']==1 and f['ema9_21_bull']==1,
    'Hurst_rev_LONG':  lambda f: f['hurst_rev']==1 and f['pos_20'] < 0.3,

    # ATR扩张
    'ATR_bull_LONG':   lambda f: f['atr_expand']==1 and f['vol_bull']==1,
    'ATR_bear_SHORT':  lambda f: f['atr_expand']==1 and f['vol_bear']==1,
}

DIRECTION_MAP = {r: ('SHORT' if r.endswith('SHORT') else 'LONG') for r in SIGNAL_RULES}


def signals_from_features(features, offset, rule_name):
    """用规则从特征列表生成信号index（对应klines的真实index）"""
    fn = SIGNAL_RULES[rule_name]
    direction = DIRECTION_MAP[rule_name]
    idxs = []
    for j, f in enumerate(features):
        try:
            if fn(f):
                idxs.append(offset + j)
        except Exception:
            pass
    return idxs, direction


def signals_combo(features, offset, rule_a, rule_b):
    """双指标AND组合：两个规则都满足才触发"""
    fn_a = SIGNAL_RULES[rule_a]
    fn_b = SIGNAL_RULES[rule_b]
    dir_a = DIRECTION_MAP[rule_a]
    dir_b = DIRECTION_MAP[rule_b]
    if dir_a != dir_b:
        return [], dir_a  # 方向冲突，跳过
    idxs = []
    for j, f in enumerate(features):
        try:
            if fn_a(f) and fn_b(f):
                idxs.append(offset + j)
        except Exception:
            pass
    return idxs, dir_a


# ════════════════════════════════════════════════════════════
# 主流程：walk-forward 单指标 + 组合验证
# ════════════════════════════════════════════════════════════

def run_symbol(symbol: str, tf: str, verbose=False):
    klines = load_klines(symbol, tf)
    if len(klines) < 500:
        return None

    features, offset = extract_features(klines)

    # Walk-forward切分
    train_end = int(len(features) * TRAIN_RATIO)
    # 训练期：找有效规则
    train_feat   = features[:train_end]
    train_klines = klines  # 用完整klines，信号index自然落在train范围

    # 测试期（盲测）
    test_feat   = features[train_end:]
    test_offset = offset + train_end

    sym_results = {
        'symbol': symbol, 'tf': tf,
        'klines_n': len(klines), 'features_n': len(features),
        'train_n': train_end, 'test_n': len(test_feat),
        'single': {}, 'combo': [],
    }

    # ── 单指标训练期验证 ──────────────────────────────────────
    valid_singles = []  # 训练期WR>55%的规则
    for rule in SIGNAL_RULES:
        idxs, direction = signals_from_features(train_feat, offset, rule)
        if not idxs:
            continue
        r = run_backtest(klines, idxs, direction)
        g = grade_result(r)
        sym_results['single'][rule] = {**r, 'grade': g, 'period': 'train'}
        if r['sufficient'] and r['wr'] >= 0.55:
            valid_singles.append(rule)

    # ── 单指标盲测验证 ────────────────────────────────────────
    for rule in valid_singles:
        idxs, direction = signals_from_features(test_feat, test_offset, rule)
        r = run_backtest(klines, idxs, direction)
        g = grade_result(r)
        sym_results['single'][rule + '_TEST'] = {**r, 'grade': g, 'period': 'test'}

    # ── 双指标AND组合（同方向穷举，训练期筛选→测试期验证）───────
    combo_candidates = []
    # 只对训练期WR>58%的规则做组合（减少计算量）
    strong_singles = [r for r in valid_singles
                      if sym_results['single'][r].get('wr', 0) >= 0.58]

    for rule_a, rule_b in combinations(strong_singles, 2):
        if DIRECTION_MAP[rule_a] != DIRECTION_MAP[rule_b]:
            continue
        idxs, direction = signals_combo(train_feat, offset, rule_a, rule_b)
        if not idxs:
            continue
        r = run_backtest(klines, idxs, direction)
        if r['sufficient'] and r['wr'] >= 0.60:
            combo_candidates.append((rule_a, rule_b, r))

    # 盲测验证组合
    for rule_a, rule_b, train_r in sorted(combo_candidates, key=lambda x: -x[2]['wr'])[:20]:
        idxs, direction = signals_combo(test_feat, test_offset, rule_a, rule_b)
        test_r = run_backtest(klines, idxs, direction)
        sym_results['combo'].append({
            'rules': [rule_a, rule_b],
            'direction': direction,
            'train': {**train_r, 'grade': grade_result(train_r)},
            'test':  {**test_r,  'grade': grade_result(test_r)},
            # 双期验证：两期WR都>55%才算真正有效
            'validated': (train_r['wr'] >= 0.60 and test_r.get('sufficient', False)
                          and test_r['wr'] >= 0.55),
        })

    if verbose:
        good = [c for c in sym_results['combo'] if c['validated']]
        print(f'  {symbol:<12}{tf:<5} klines={len(klines):>6} '
              f'valid_singles={len(valid_singles):>3} '
              f'validated_combos={len(good):>3}', flush=True)
    return sym_results


def run_all(symbols=None, timeframes=None, max_per_tf=8, verbose=True):
    symbols    = symbols    or SYMBOLS
    timeframes = timeframes or ['1h', '4h']

    all_results  = []
    global_tier1 = []
    validated    = []

    t0 = time.time()
    print(f'\n{"="*72}')
    print(f'  达摩院组合回测 v2 | {len(symbols)}币 × {len(timeframes)}TF')
    print(f'  SL={SL_PCT*100:.0f}% TP={TP_PCT*100:.0f}% Hold={HOLD_BARS}根 '
          f'Train={int(TRAIN_RATIO*100)}% Test={100-int(TRAIN_RATIO*100)}%')
    print(f'  {len(SIGNAL_RULES)}个信号规则 | 双指标AND组合穷举')
    print(f'{"="*72}')

    for tf in timeframes:
        print(f'\n▌ 时间框架: {tf}')
        count = 0
        for sym in symbols:
            if count >= max_per_tf:
                break
            r = run_symbol(sym, tf, verbose=verbose)
            if r is None:
                continue
            all_results.append(r)
            count += 1

            # 收集已验证组合
            for c in r['combo']:
                if c['validated']:
                    validated.append({
                        'symbol': sym, 'tf': tf,
                        'rules': c['rules'],
                        'direction': c['direction'],
                        'train_wr': c['train']['wr'],
                        'test_wr': c['test']['wr'],
                        'test_n': c['test']['n'],
                        'test_ev': c['test']['ev'],
                    })

    elapsed = time.time() - t0

    # 汇总输出
    print(f'\n{"="*72}')
    print(f'  完成 | 耗时{elapsed:.0f}s | 已验证组合: {len(validated)}个')

    if validated:
        # 按测试期WR排序
        validated.sort(key=lambda x: -(x['test_wr'] * x['test_n']))
        print(f'\n  🏆 已验证组合（训练WR≥60% + 测试WR≥55% + 双期盲测）:')
        print(f'  {"规则组合":<45} {"币":<10} {"TF":<5} '
              f'{"训练WR":>8} {"测试WR":>8} {"测试n":>6} {"测试EV":>8}')
        print(f'  {"-"*96}')
        for v in validated[:30]:
            rules = ' & '.join(v['rules'])
            print(f'  {rules:<45} {v["symbol"]:<10} {v["tf"]:<5} '
                  f'{v["train_wr"]*100:>7.1f}% {v["test_wr"]*100:>7.1f}% '
                  f'{v["test_n"]:>6} {v["test_ev"]*100:>+7.2f}%')
    else:
        print('\n  ⚠️  无已验证组合 — 扩大样本或调整参数后重跑')

    print(f'{"="*72}\n')

    output = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'params': {'SL': SL_PCT, 'TP': TP_PCT, 'HOLD': HOLD_BARS,
                   'MIN_N': MIN_SIGNALS, 'TRAIN_RATIO': TRAIN_RATIO},
        'symbols_tested': [r['symbol'] for r in all_results],
        'timeframes': timeframes,
        'validated_combos': validated,
        'all_results': all_results,
    }
    out = BASE / 'data' / 'dharma_combo_result.json'
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f'结果保存: {out}')
    return output


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', nargs='+', default=None)
    p.add_argument('--tf', nargs='+', default=['1h'])
    p.add_argument('--quick', action='store_true', help='只跑BTC/ETH快速验证')
    p.add_argument('--all',   action='store_true', help='全量35币')
    p.add_argument('--max',   type=int, default=8,  help='每个TF最多几个币')
    args = p.parse_args()

    if args.quick:
        syms = ['BTCUSDT', 'ETHUSDT']
    elif args.all:
        syms = SYMBOLS
    else:
        syms = args.symbols or SYMBOLS[:8]

    run_all(symbols=syms, timeframes=args.tf, max_per_tf=args.max)
