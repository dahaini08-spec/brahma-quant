"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 多时框架引擎，MTF计算
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
multitf_engine.py · 多周期趋势对齐引擎
brahma_brain · P0

覆盖: 15m / 1H / 4H / 1D / 1W / 1M
输出: 六框架方向共识 + 对齐评分 + 大周期偏差预警
"""

from brahma_brain.math_utils import _ema, _rsi, calc_rsi, rsi, ema  # 统一数学库

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from data_cache import get_klines, klines_to_ohlcv

# [math_utils] _ema 已统一到 brahma_brain.math_utils，此处保留备用
def _ema(arr, n):
    if len(arr) < n:
        return arr[-1] if arr else 0
    k = 2 / (n + 1)
    e = sum(arr[:n]) / n
    for x in arr[n:]:
        e = x * k + e * (1 - k)
    return e

def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n-1) + gains[i]) / n
        al = (al * (n-1) + losses[i]) / n
    return 100 - 100 / (1 + ag / (al + 1e-9))

def _macd_signal(closes):
    if len(closes) < 35:
        return 0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    return 1 if ema12 > ema26 else -1

def _trend_dir(closes, highs, lows):
    """综合判断趋势方向: +1=多, -1=空, 0=中性"""
    if len(closes) < 50:
        return 0, 'INSUFFICIENT'

    c = closes
    ema7  = _ema(c, 7)
    ema25 = _ema(c, 25)
    ema99 = _ema(c, 99) if len(c) >= 99 else _ema(c, len(c)//2)
    rsi   = _rsi(c)
    macd_sig = _macd_signal(c)
    price = c[-1]

    score = 0
    # EMA排列
    if ema7 > ema25 > ema99:  score += 3
    elif ema7 < ema25 < ema99: score -= 3
    elif ema7 > ema25:         score += 1
    elif ema7 < ema25:         score -= 1

    # 价格与EMA99
    if price > ema99:  score += 2
    elif price < ema99: score -= 2

    # RSI
    if rsi > 55: score += 1
    elif rsi < 45: score -= 1

    # MACD
    score += macd_sig

    # 近期高低点结构
    last = min(20, len(highs))
    rec_h = highs[-last:]
    rec_l = lows[-last:]
    if rec_h[-1] > rec_h[0] and rec_l[-1] > rec_l[0]:
        score += 1  # 更高高/更高低
    elif rec_h[-1] < rec_h[0] and rec_l[-1] < rec_l[0]:
        score -= 1

    if score >= 4:
        return 1, 'BULL'
    elif score <= -4:
        return -1, 'BEAR'
    else:
        return 0, 'NEUTRAL'

# 时间框架配置
TIMEFRAMES = [
    ('1M',  '1M',  50,  '月线'),
    ('1W',  '1w',  50,  '周线'),
    ('1D',  '1d',  100, '日线'),
    ('4H',  '4h',  100, '4小时'),
    ('1H',  '1h',  100, '1小时'),
    ('15m', '15m', 100, '15分钟'),
]

# 各周期权重（越大周期权重越高）
TF_WEIGHT = {'1M': 6, '1W': 5, '1D': 4, '4H': 3, '1H': 2, '15m': 0}
# [达摩院V7校准 2026-05-19] 15m全周期指标 PF<0.95，全线失效（器10m器15m器30m器60m有效）
# 15m权重从1降至0：不再计入评分，但保留周期获取（供显示）

def analyze_multitf(symbol: str) -> dict:
    """
    六周期完整分析
    返回：
      directions: {tf: dir}
      consensus:  加权共识方向
      alignment:  对齐分数 0~10
      misalign:   大周期空头但小周期多头 = 危险警告
    """
    results = {}
    err_tfs = []

    for tf_id, tf_api, limit, tf_name in TIMEFRAMES:
        try:
            k = klines_to_ohlcv(get_klines(symbol, tf_api, limit))
            if not k or len(k.get('c', [])) < 20:
                err_tfs.append(tf_id)
                continue
            dir_val, dir_str = _trend_dir(k['c'], k['h'], k['l'])
            rsi_val = round(_rsi(k['c']), 1)
            ema7  = round(_ema(k['c'], 7), 2)
            ema99 = round(_ema(k['c'], min(99, len(k['c'])//2)), 2)
            results[tf_id] = {
                'dir':    dir_val,
                'label':  dir_str,
                'rsi':    rsi_val,
                'ema7':   ema7,
                'ema99':  ema99,
                'name':   tf_name,
            }
        except Exception:
            err_tfs.append(tf_id)

    if not results:
        return {'error': 'no data', 'alignment': 0, 'consensus': 0}

    # 加权共识
    total_w = 0
    score_w = 0
    for tf_id, data in results.items():
        w = TF_WEIGHT.get(tf_id, 1)
        total_w += w
        score_w += data['dir'] * w

    consensus_raw = score_w / (total_w + 1e-9)
    if consensus_raw >= 0.4:
        consensus = 1
        consensus_label = 'FULL_BULL'
    elif consensus_raw >= 0.1:
        consensus = 1
        consensus_label = 'BULL'
    elif consensus_raw <= -0.4:
        consensus = -1
        consensus_label = 'FULL_BEAR'
    elif consensus_raw <= -0.1:
        consensus = -1
        consensus_label = 'BEAR'
    else:
        consensus = 0
        consensus_label = 'NEUTRAL'

    # 对齐分（相同方向的权重占比）
    agree_w = sum(TF_WEIGHT.get(tf, 1) for tf, d in results.items()
                  if d['dir'] == consensus)
    alignment = round(agree_w / (total_w + 1e-9) * 10, 1)

    # 大周期 vs 小周期冲突检测
    big_tfs  = [tf for tf in ['1M','1W','1D'] if tf in results]
    small_tfs= [tf for tf in ['4H','1H','15m'] if tf in results]
    big_dir  = sum(results[tf]['dir'] for tf in big_tfs) / (len(big_tfs) + 1e-9)
    small_dir= sum(results[tf]['dir'] for tf in small_tfs) / (len(small_tfs) + 1e-9)

    misalign = False
    misalign_note = ''
    if big_dir < -0.3 and small_dir > 0.3:
        misalign = True
        misalign_note = '⚠️ 大周期空头 小周期多头 → 反弹陷阱风险'
    elif big_dir > 0.3 and small_dir < -0.3:
        misalign = True
        misalign_note = '⚠️ 大周期多头 小周期空头 → 回调入场机会'

    return {
        'tfs':            results,
        'consensus':      consensus,
        'consensus_label': consensus_label,
        'consensus_raw':  round(consensus_raw, 3),
        'alignment':      alignment,
        'misalign':       misalign,
        'misalign_note':  misalign_note,
        'errs':           err_tfs,
    }


def multitf_score(symbol: str, signal_dir: str) -> dict:
    """
    多周期对齐评分接口 → 0~20分
    替换/补充现有 趋势一致性 维度
    """
    r = analyze_multitf(symbol)
    if 'error' in r:
        return {'score': 0, 'max': 20, 'notes': ['数据不足'], 'detail': r}

    dir_val = 1 if signal_dir == 'LONG' else -1
    alignment = r['alignment']
    consensus  = r['consensus']
    tfs        = r['tfs']

    score = 0
    notes = []

    # 1. 大周期对齐（月/周/日）
    big_agree = 0
    for tf in ['1M', '1W', '1D']:
        if tf in tfs and tfs[tf]['dir'] == dir_val:
            big_agree += 1
    if big_agree == 3:
        score += 8; notes.append(f'月周日全线{signal_dir} +8')
    elif big_agree == 2:
        score += 5; notes.append(f'大周期2/3对齐 +5')
    elif big_agree == 1:
        score += 2; notes.append(f'大周期1/3对齐 +2')

    # 2. 中周期对齐（4H）
    if '4H' in tfs and tfs['4H']['dir'] == dir_val:
        score += 5; notes.append(f'4H趋势对齐 +5')

    # 3. 小周期动量（1H为主，15m已附除）
    # [达摩院V7] 15m验证全线失效，不再计入对齐分
    small_agree = sum(1 for tf in ['1H'] if tf in tfs and tfs[tf]['dir'] == dir_val)
    if small_agree == 1:
        score += 4; notes.append(f'1H动量对齐 +4')
    # 15m仅作显示用，不加分

    # 4. 反向惩罚
    if r['misalign'] and dir_val == 1:
        score = max(0, score - 4)
        notes.append(r['misalign_note'] + ' -4')
    elif r['misalign'] and dir_val == -1:
        score = max(0, score - 2)
        notes.append(r['misalign_note'])

    score = min(score, 20)

    # 各周期方向摘要
    tf_summary = {}
    for tf_id in ['1M','1W','1D','4H','1H','15m']:
        if tf_id in tfs:
            d = tfs[tf_id]
            icon = '🟢' if d['dir'] == 1 else ('🔴' if d['dir'] == -1 else '⚪')
            tf_summary[tf_id] = f"{icon}{d['label']} RSI={d['rsi']}"

    return {
        'score':      score,
        'max':        20,
        'notes':      notes,
        'consensus':  r['consensus_label'],
        'alignment':  r['alignment'],
        'tf_summary': tf_summary,
        'misalign':   r.get('misalign_note', ''),
        'raw':        r,
    }
