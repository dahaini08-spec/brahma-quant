#!/usr/bin/env python3
"""
暴涨猎手30天信号回溯分析
深度研究过去10天涨幅TOP标的，还原爆发前信号状态
"""
import requests, numpy as np, time, sys, os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 过去10天TOP1/TOP代表标的
TARGETS = [
    ('2026-06-26', 'AGLDUSDT',   75.08,  '成交额269M'),
    ('2026-06-27', 'VELVETUSDT', 129.82, '成交额979M'),
    ('2026-06-28', 'ACTUSDT',    40.31,  '成交额336M'),
    ('2026-06-29', 'TACUSDT',    171.73, '成交额490M'),
    ('2026-06-30', 'MUSDT',      25.34,  '成交额28M'),
    ('2026-07-01', 'TAIKOUSDT',  495.69, '成交额609M'),
    ('2026-07-02', 'BIRBUSDT',   65.80,  '成交额339M'),
    ('2026-07-03', 'TLMUSDT',    55.11,  '成交额393M'),
    ('2026-07-04', 'LABUSDT',    160.86, '成交额1543M'),
    ('2026-07-05', 'VANRYUSDT',  58.10,  '成交额346M(进行中)'),
]

def get_klines(sym, interval='1h', limit=720):
    r = requests.get('https://fapi.binance.com/fapi/v1/klines', params={
        'symbol': sym, 'interval': interval, 'limit': limit
    }, timeout=10)
    return r.json()

def pump_score_snapshot(c, h, l, v, idx):
    end = idx + 1
    if end < 30: return None
    c_s = c[:end]; h_s = h[:end]; l_s = l[:end]; v_s = v[:end]
    px = c_s[-1]

    n7d = min(168, len(h_s))
    tight7d  = (np.max(h_s[-n7d:]) - np.min(l_s[-n7d:])) / max(np.min(l_s[-n7d:]), 1e-9) * 100
    tight24h = (np.max(h_s[-24:])  - np.min(l_s[-24:]))  / max(np.min(l_s[-24:]),  1e-9) * 100
    tight8h  = (np.max(h_s[-8:])   - np.min(l_s[-8:]))   / max(np.min(l_s[-8:]),   1e-9) * 100

    d = np.diff(c_s[-15:]) if len(c_s) >= 15 else np.diff(c_s)
    g = np.where(d > 0, d, 0); lo = np.where(d < 0, -d, 0)
    rsi = 100 - 100 / (1 + np.mean(g) / np.mean(lo)) if np.mean(lo) > 0 else 99

    shrink_h = 0
    for i in range(len(v_s) - 2, max(0, len(v_s) - 20), -1):
        if v_s[i] < v_s[i + 1]: shrink_h += 1
        else: break

    ema20 = np.mean(c_s[-20:]) if len(c_s) >= 20 else np.mean(c_s)
    trend = '略跌' if px < ema20 * 0.995 else ('略涨' if px > ema20 * 1.005 else '横盘')

    score = 0; reasons = []
    if tight7d < 15:
        score += 40; reasons.append('TIGHT7D<15%(%.1f%%)' % tight7d)
    elif tight7d < 30:
        score += 20; reasons.append('TIGHT7D<30%(%.1f%%)' % tight7d)
    elif tight7d < 50:
        score += 8; reasons.append('TIGHT7D<50%(%.1f%%)' % tight7d)

    if tight24h < 5:
        score += 15; reasons.append('TIGHT24H极压缩(%.1f%%)' % tight24h)
    elif tight24h < 10:
        score += 8; reasons.append('TIGHT24H偏低(%.1f%%)' % tight24h)

    if rsi < 25:
        score += 30; reasons.append('RSI深超卖(%.0f)' % rsi)
    elif rsi < 35:
        score += 20; reasons.append('RSI超卖(%.0f)' % rsi)
    elif rsi < 45:
        score += 10; reasons.append('RSI偏低(%.0f)' % rsi)

    if shrink_h >= 13:
        score += 25; reasons.append('连续缩量%dH(铁证)' % shrink_h)
    elif shrink_h >= 8:
        score += 15; reasons.append('连续缩量%dH' % shrink_h)
    elif shrink_h >= 5:
        score += 8; reasons.append('缩量%dH' % shrink_h)

    if trend == '略跌':
        score += 10; reasons.append('趋势略跌')

    return {
        'score': score, 'tight7d': round(tight7d, 1), 'tight8h': round(tight8h, 1),
        'tight24h': round(tight24h, 1), 'rsi': round(rsi, 1),
        'shrink': shrink_h, 'trend': trend, 'reasons': reasons, 'px': px
    }


def analyze(pump_date, sym, pct, vol_info):
    sep = '=' * 68
    print(sep)
    print('[%s] %s  当日收盘+%.2f%%  %s' % (pump_date, sym, pct, vol_info))
    print()

    try:
        klines = get_klines(sym, '1h', 1080)  # 45天
        if not isinstance(klines, list) or len(klines) < 72:
            print('  数据不足，跳过')
            return

        c = np.array([float(k[4]) for k in klines])
        h = np.array([float(k[2]) for k in klines])
        l = np.array([float(k[3]) for k in klines])
        v = np.array([float(k[5]) for k in klines])
        ts = [int(k[0]) for k in klines]

        pump_dt = datetime.strptime(pump_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        pump_ms = int(pump_dt.timestamp() * 1000)

        pump_idx = None
        for i, t in enumerate(ts):
            if t >= pump_ms:
                pump_idx = i
                break
        if pump_idx is None:
            pump_idx = len(ts) - 1

        # 爆发前48H最高分
        pre_scores = []
        for i in range(max(30, pump_idx - 48), pump_idx):
            snap = pump_score_snapshot(c, h, l, v, i)
            if snap:
                pre_scores.append((snap['score'], snap, ts[i]))
        pre_scores.sort(key=lambda x: -x[0])

        max_pre_score = pre_scores[0][0] if pre_scores else 0
        best_snap = pre_scores[0][1] if pre_scores else None
        best_ts = pre_scores[0][2] if pre_scores else None

        captured = max_pre_score >= 75
        print('  爆发前48H最高评分: %d/100  (预警门槛=75分)' % max_pre_score)
        print('  系统应该预判: %s' % ('YES - 应被捕获' if captured else 'NO  - 设计盲区'))
        if best_snap and best_ts:
            best_time = datetime.fromtimestamp(best_ts / 1000, tz=timezone.utc).strftime('%m-%d %H:%M')
            print('  最高分时刻: %s  | %s' % (best_time, ' | '.join(best_snap['reasons'][:3])))
        print()

        # 30天评分变化轨迹（每天一个采样点）
        print('  30天信号轨迹 (每天采样):')
        print('  %-13s %-5s %-8s %-7s %-6s %-5s' % ('日期', '分数', 'TIGHT7D', 'RSI', '缩量H', '趋势'))
        print('  ' + '-' * 60)
        for days_before in [30, 28, 25, 21, 18, 15, 12, 10, 7, 5, 4, 3, 2, 1]:
            idx = pump_idx - days_before * 24
            if idx < 30: continue
            snap = pump_score_snapshot(c, h, l, v, idx)
            if not snap: continue
            dt_str = datetime.fromtimestamp(ts[idx] / 1000, tz=timezone.utc).strftime('%m-%d')
            flag = '[**]' if snap['score'] >= 75 else ('[! ]' if snap['score'] >= 50 else '    ')
            print('  %s T-%2dd %s  %3d/100  %6.1f%%  %5.1f  %4dH  %s' % (
                flag, days_before, dt_str, snap['score'],
                snap['tight7d'], snap['rsi'], snap['shrink'], snap['trend']
            ))

        # 关键：识别最大压缩点
        print()
        min_tight_idx = None
        min_tight = 999
        for i in range(max(30, pump_idx - 30 * 24), pump_idx):
            snap = pump_score_snapshot(c, h, l, v, i)
            if snap and snap['tight7d'] < min_tight:
                min_tight = snap['tight7d']
                min_tight_idx = i

        if min_tight_idx:
            min_snap = pump_score_snapshot(c, h, l, v, min_tight_idx)
            days_before_pump = (pump_ms - ts[min_tight_idx]) / (86400 * 1000)
            mt = datetime.fromtimestamp(ts[min_tight_idx] / 1000, tz=timezone.utc).strftime('%m-%d %H:%M')
            print('  最大压缩点: %s (距爆发%.1f天前)  TIGHT7D=%.1f%%  评分=%d/100' % (
                mt, days_before_pump, min_tight, min_snap['score'] if min_snap else 0
            ))
            min_candidates = (min_tight < 15)
            print('  最大压缩时是否满足候选池(MIN_VOL=5000万): %s' % ('需核实成交额' if not min_candidates else '理论上应入选'))
            print('  最大压缩时TIGHT<15%%: %s' % ('YES' if min_candidates else 'NO(%.1f%%)' % min_tight))

        # 候选池检查：MIN_VOL
        vol_24h = float(klines[-1][7]) if klines else 0
        print()
        print('  当前24H成交额: %.1fM  候选池门槛(MIN_VOL): 5000万' % (vol_24h / 1e6))
        # 历史成交额（爆发前）
        pre_vol = np.mean([float(klines[max(0, pump_idx-48+i)][7]) for i in range(48)]) if pump_idx >= 48 else 0
        print('  爆发前48H平均成交额: %.1fM  %s' % (
            pre_vol / 1e6,
            '✅ 在候选池内' if pre_vol >= 50_000_000 else '❌ 低于5000万，候选池外！这是核心原因'
        ))

        print()

    except Exception as e:
        print('  ERROR: %s' % e)
        import traceback; traceback.print_exc()


if __name__ == '__main__':
    print('梵天设计院 · 暴涨猎手30天信号回溯分析报告')
    print('分析时间: %s UTC' % datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'))
    print()
    for args in TARGETS:
        analyze(*args)
        time.sleep(0.5)
    print()
    print('=== 分析完成 ===')
