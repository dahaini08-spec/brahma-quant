#!/usr/bin/env python3
"""
梵天暴涨猎手 - Phase2: 妖币特征工程
对每个暴涨事件提取启动前后的6大特征，输出规律矩阵
"""
import sqlite3, json, os, datetime, math
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), 'pump_hunter.db')
OUT_PATH = os.path.join(os.path.dirname(__file__), 'pump_feature_matrix.json')

# ── 特征计算 ──────────────────────────────────────────
def calc_features(symbol, event_ts, conn):
    c = conn.cursor()
    ONE_H = 3_600_000

    # 前30天 + 后7天窗口
    t_end   = event_ts
    t_start = event_ts - 30 * 24 * ONE_H
    t_post  = event_ts + 7  * 24 * ONE_H

    # 拉K线
    c.execute('''SELECT open_time,open,high,low,close,volume,quote_volume
        FROM klines_1h WHERE symbol=? AND open_time>=? AND open_time<=?
        ORDER BY open_time''', (symbol, t_start, t_post))
    rows = c.fetchall()
    if len(rows) < 48:
        return None

    pre  = [r for r in rows if r[0] < event_ts]
    post = [r for r in rows if r[0] >= event_ts]

    if len(pre) < 24:
        return None

    closes_pre = [r[4] for r in pre]
    vols_pre   = [r[6] for r in pre]  # quote_volume
    highs_pre  = [r[2] for r in pre]
    lows_pre   = [r[3] for r in pre]

    # ── F1: 价格压缩度（启动前48H振幅）──────────────────
    pre_48 = pre[-48:] if len(pre) >= 48 else pre
    h48 = max(r[2] for r in pre_48)
    l48 = min(r[3] for r in pre_48)
    center = (h48 + l48) / 2
    f1_compression = (h48 - l48) / center * 100 if center > 0 else 0

    # ── F2: 成交量爆发倍数（启动前vs启动时）────────────
    avg_vol_pre = sum(vols_pre[-72:]) / min(72, len(vols_pre)) if vols_pre else 0
    launch_vol  = sum(r[6] for r in post[:6]) / 6 if len(post) >= 6 else 0
    f2_vol_ratio = launch_vol / avg_vol_pre if avg_vol_pre > 0 else 0

    # ── F3: 启动前趋势（7日涨跌%）────────────────────
    pre_168 = [r[4] for r in pre[-168:]]
    f3_trend_7d = (pre_168[-1] - pre_168[0]) / pre_168[0] * 100 if len(pre_168) >= 2 and pre_168[0] > 0 else 0

    # ── F4: 成交量萎缩程度（启动前量 vs 7-14日均量）──
    vol_7d  = sum(vols_pre[-168:]) / max(1, len(vols_pre[-168:]))
    vol_14d = sum(vols_pre[-336:]) / max(1, len(vols_pre[-336:]))
    f4_vol_shrink = vol_7d / vol_14d if vol_14d > 0 else 1.0

    # ── F5: 启动前RSI（简化版，14H RSI）────────────────
    def simple_rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(0, d) for d in deltas[-period:]]
        losses = [max(0, -d) for d in deltas[-period:]]
        ag = sum(gains) / period
        al = sum(losses) / period
        return 100 - 100 / (1 + ag / al) if al > 0 else 100

    f5_rsi_before = simple_rsi(closes_pre[-48:])

    # ── F6: 量能爆发集中度（连续放量天数）──────────────
    consecutive_low_vol = 0
    for v in reversed(vols_pre[-72:]):
        if v < avg_vol_pre * 0.5:
            consecutive_low_vol += 1
        else:
            break
    f6_low_vol_bars = consecutive_low_vol  # 启动前连续缩量的小时数

    # ── 启动后最大涨幅（7天内）────────────────────────
    if post:
        price_launch = pre[-1][4]
        max_price_post = max(r[2] for r in post[:168])  # 最高价
        post_max_gain  = (max_price_post - price_launch) / price_launch * 100 if price_launch > 0 else 0
        price_7d_close = post[-1][4] if post else price_launch
        post_7d_return = (price_7d_close - price_launch) / price_launch * 100 if price_launch > 0 else 0
    else:
        post_max_gain = 0
        post_7d_return = 0

    # ── 事件类型判断 ──────────────────────────────────
    if f1_compression < 15:
        event_type = 'TIGHT_BOX'    # 极度压缩
    elif f1_compression < 30:
        event_type = 'MODERATE_BOX' # 适度压缩
    else:
        event_type = 'WIDE_RANGE'   # 宽幅震荡

    return {
        'symbol': symbol,
        'event_ts': event_ts,
        'event_date': datetime.datetime.utcfromtimestamp(event_ts/1000).strftime('%Y-%m-%d'),
        # 6大特征
        'f1_compression_pct': round(f1_compression, 1),
        'f2_vol_explosion': round(f2_vol_ratio, 1),
        'f3_trend_7d_pct': round(f3_trend_7d, 1),
        'f4_vol_shrink': round(f4_vol_shrink, 2),
        'f5_rsi_before': round(f5_rsi_before, 1),
        'f6_low_vol_bars': f6_low_vol_bars,
        # 结果
        'post_max_gain_pct': round(post_max_gain, 1),
        'post_7d_return_pct': round(post_7d_return, 1),
        'event_type': event_type,
    }


def run_phase2():
    print('=' * 60)
    print('梵天暴涨猎手 Phase2 - 特征工程')
    print('=' * 60)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('SELECT symbol, event_time, pump_pct, vol_24h FROM pump_events ORDER BY pump_pct DESC')
    events = c.fetchall()
    print(f'待分析事件: {len(events)}个')

    features = []
    errors = 0
    for i, (sym, ts, pct, vol) in enumerate(events):
        feat = calc_features(sym, ts, conn)
        if feat:
            feat['pump_pct'] = pct
            feat['vol_24h'] = vol
            features.append(feat)
        else:
            errors += 1

        if (i + 1) % 100 == 0:
            print(f'  [{i+1}/{len(events)}] 成功={len(features)} 跳过={errors}')

    conn.close()

    if not features:
        print('无有效特征数据！')
        return

    # ── 统计分析：找共性规律 ────────────────────────────
    print(f'\n✅ 有效特征样本: {len(features)}个')
    print('\n=== 六大特征统计 ===')

    def stat(vals):
        if not vals: return {'mean':0,'median':0,'p25':0,'p75':0}
        s = sorted(vals)
        n = len(s)
        return {
            'mean':   round(sum(s)/n, 1),
            'median': round(s[n//2], 1),
            'p25':    round(s[n//4], 1),
            'p75':    round(s[3*n//4], 1),
            'n':      n
        }

    for fname, key in [
        ('F1 压缩度%（越低=越压缩）',  'f1_compression_pct'),
        ('F2 量能爆发倍数',           'f2_vol_explosion'),
        ('F3 启动前7日趋势%',         'f3_trend_7d_pct'),
        ('F4 量能萎缩比',             'f4_vol_shrink'),
        ('F5 启动前RSI',             'f5_rsi_before'),
        ('F6 连续缩量小时数',         'f6_low_vol_bars'),
        ('结果: 7日内最大涨幅%',      'post_max_gain_pct'),
        ('结果: 7日后收益%',          'post_7d_return_pct'),
    ]:
        vals = [f[key] for f in features if f.get(key) is not None]
        s = stat(vals)
        print(f'  {fname}: 均值={s["mean"]} 中位={s["median"]} P25={s["p25"]} P75={s["p75"]}')

    # ── 分组分析：事件类型 vs 后续涨幅 ─────────────────
    print('\n=== 按启动形态分组 ===')
    by_type = defaultdict(list)
    for f in features:
        by_type[f['event_type']].append(f)

    for etype, group in sorted(by_type.items()):
        gains = [g['post_max_gain_pct'] for g in group]
        returns = [g['post_7d_return_pct'] for g in group]
        profitable = len([r for r in returns if r > 0])
        print(f'  {etype} (n={len(group)}):')
        print(f'    7日内最大涨幅: 均值={sum(gains)/len(gains):.0f}% 中位={sorted(gains)[len(gains)//2]:.0f}%')
        print(f'    7日后收益>0: {profitable}/{len(group)} = {profitable/len(group):.0%}')

    # ── TOP暴涨事件 ──────────────────────────────────
    print('\n=== 启动前特征最典型的事件（TIGHT_BOX型）===')
    tight = [f for f in features if f['event_type'] == 'TIGHT_BOX']
    tight_big = sorted(tight, key=lambda x: x['post_max_gain_pct'], reverse=True)[:15]
    for f in tight_big:
        print(f"  {f['symbol']:<16} {f['event_date']}  "
              f"压缩={f['f1_compression_pct']:.0f}%  "
              f"量爆={f['f2_vol_explosion']:.0f}x  "
              f"最大涨={f['post_max_gain_pct']:.0f}%")

    # ── 保存完整矩阵 ─────────────────────────────────
    output = {
        'generated_at': datetime.datetime.utcnow().isoformat(),
        'total_events': len(features),
        'feature_stats': {
            key: stat([f[key] for f in features if f.get(key) is not None])
            for key in ['f1_compression_pct','f2_vol_explosion','f3_trend_7d_pct',
                        'f4_vol_shrink','f5_rsi_before','f6_low_vol_bars',
                        'post_max_gain_pct','post_7d_return_pct']
        },
        'by_type_summary': {
            etype: {
                'n': len(g),
                'avg_max_gain': round(sum(x['post_max_gain_pct'] for x in g)/len(g), 1),
                'win_rate_7d': round(len([x for x in g if x['post_7d_return_pct']>0])/len(g), 3)
            } for etype, g in by_type.items()
        },
        'all_features': features
    }

    with open(OUT_PATH, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f'\n✅ 特征矩阵保存: {OUT_PATH}')
    return output


if __name__ == '__main__':
    run_phase2()
