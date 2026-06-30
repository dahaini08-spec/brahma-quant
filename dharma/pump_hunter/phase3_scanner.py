#!/usr/bin/env python3
"""
梵天暴涨猎手 - Phase3: 实时预警引擎
基于Phase1/2提炼的规律，实时扫描全市场，推送预警
"""
import requests, json, datetime, time, os
from collections import defaultdict

API = 'https://fapi.binance.com'
SCAN_INTERVAL_SEC = 900  # 15分钟扫描一次

EXCLUDE = {'BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT'}

# 预警评分权重（基于Phase2铁证）
SCORE_WEIGHTS = {
    'oi_surge_30pct':    25,   # OI单棒 >30%
    'oi_surge_50pct':    35,   # OI单棒 >50% (额外加分)
    'neg_fr_extreme':    30,   # 资金费率 < -0.05%
    'neg_fr_moderate':   15,   # 资金费率 < -0.02%
    'short_crowded':     20,   # 空头占比 > 58%
    'vol_shrink_3d':     10,   # 3日量能萎缩 < 0.5x均值
    'price_compression': 10,   # 48H振幅 < 15%
    'smart_money_div':   15,   # 大户多 + 散户空（情绪背离）
}


def get_all_symbols():
    info = requests.get(f'{API}/fapi/v1/exchangeInfo', timeout=10).json()
    return [s['symbol'] for s in info['symbols']
            if s['status'] == 'TRADING' and s['symbol'].endswith('USDT')
            and 'UP' not in s['symbol'] and 'DOWN' not in s['symbol']
            and s['symbol'] not in EXCLUDE]


def scan_once(symbols):
    """单次全市场扫描，返回预警列表"""
    alerts = []

    # 批量获取24H行情
    tickers = {t['symbol']: t for t in
               requests.get(f'{API}/fapi/v1/ticker/24hr', timeout=15).json()
               if t['symbol'].endswith('USDT')}

    # 过滤：成交额$2M~$500M，排除已大幅波动（>30%）
    candidates = [
        s for s in symbols
        if s in tickers
        and 2_000_000 < float(tickers[s].get('quoteVolume', 0)) < 500_000_000
        and abs(float(tickers[s].get('priceChangePercent', 0))) < 30
    ]

    for sym in candidates:
        try:
            score = 0
            reasons = []
            tick = tickers[sym]
            chg  = float(tick['priceChangePercent'])
            vol  = float(tick['quoteVolume'])
            price = float(tick['lastPrice'])

            # ── OI历史（近12H vs 前36H）─────────────────
            oi_hist = requests.get(f'{API}/futures/data/openInterestHist',
                params={'symbol': sym, 'period': '1h', 'limit': 48},
                timeout=6).json()

            if isinstance(oi_hist, list) and len(oi_hist) >= 12:
                oi_early = [float(x['sumOpenInterestValue']) for x in oi_hist[:36]]
                oi_late  = [float(x['sumOpenInterestValue']) for x in oi_hist[-6:]]
                avg_early = sum(oi_early) / len(oi_early) if oi_early else 0
                avg_late  = sum(oi_late)  / len(oi_late)  if oi_late  else 0
                oi_chg = (avg_late - avg_early) / avg_early * 100 if avg_early > 0 else 0

                if oi_chg >= 50:
                    score += SCORE_WEIGHTS['oi_surge_50pct']
                    reasons.append(f'OI暴增+{oi_chg:.0f}%')
                elif oi_chg >= 30:
                    score += SCORE_WEIGHTS['oi_surge_30pct']
                    reasons.append(f'OI大增+{oi_chg:.0f}%')
            else:
                oi_chg = 0

            # ── 资金费率（近3次）─────────────────────────
            fr_hist = requests.get(f'{API}/fapi/v1/fundingRate',
                params={'symbol': sym, 'limit': 6}, timeout=5).json()
            if isinstance(fr_hist, list) and fr_hist:
                fr_vals = [float(x['fundingRate']) * 100 for x in fr_hist]
                avg_fr = sum(fr_vals) / len(fr_vals)
                latest_fr = fr_vals[-1]

                if latest_fr < -0.05:
                    score += SCORE_WEIGHTS['neg_fr_extreme']
                    reasons.append(f'极端负费率{latest_fr:.3f}%')
                elif latest_fr < -0.02:
                    score += SCORE_WEIGHTS['neg_fr_moderate']
                    reasons.append(f'负费率{latest_fr:.3f}%')
                elif latest_fr > 0.04:
                    score += 8
                    reasons.append(f'正费率偏高{latest_fr:.3f}%（多头坚持）')
            else:
                avg_fr = 0; latest_fr = 0

            # ── 多空比（散户情绪）────────────────────────
            lsr = requests.get(f'{API}/futures/data/globalLongShortAccountRatio',
                params={'symbol': sym, 'period': '1h', 'limit': 6},
                timeout=5).json()
            if isinstance(lsr, list) and lsr:
                short_pct = float(lsr[-1].get('shortAccount', 0)) * 100
                long_pct  = float(lsr[-1].get('longAccount',  0)) * 100
                if short_pct > 62:
                    score += SCORE_WEIGHTS['short_crowded']
                    reasons.append(f'散户空头{short_pct:.0f}%（轧空弹药）')
                elif short_pct > 58:
                    score += SCORE_WEIGHTS['short_crowded'] // 2
                    reasons.append(f'空头偏多{short_pct:.0f}%')
            else:
                short_pct = 50

            # ── 大户 vs 散户背离 ──────────────────────────
            try:
                ttr = requests.get(f'{API}/futures/data/topLongShortPositionRatio',
                    params={'symbol': sym, 'period': '1h', 'limit': 3},
                    timeout=5).json()
                if isinstance(ttr, list) and ttr:
                    elite_long = float(ttr[-1].get('longAccount', 0)) * 100
                    # 大户多头 > 55% 且散户空头 > 55% = 最强背离
                    if elite_long > 55 and short_pct > 55:
                        score += SCORE_WEIGHTS['smart_money_div']
                        reasons.append(f'智能钱背离(大户多{elite_long:.0f}% 散户空{short_pct:.0f}%)')
            except:
                pass

            # ── 价格压缩 + 量能萎缩 ──────────────────────
            kl_4h = requests.get(f'{API}/fapi/v1/klines',
                params={'symbol': sym, 'interval': '4h', 'limit': 24},
                timeout=6).json()
            if isinstance(kl_4h, list) and len(kl_4h) >= 12:
                closes = [float(k[4]) for k in kl_4h]
                vols   = [float(k[7]) for k in kl_4h]
                h48 = max(float(k[2]) for k in kl_4h[-12:])
                l48 = min(float(k[3]) for k in kl_4h[-12:])
                center = (h48 + l48) / 2
                compression = (h48 - l48) / center * 100 if center > 0 else 100

                vol_recent = sum(vols[-6:]) / 6 if len(vols) >= 6 else 0
                vol_base   = sum(vols[-24:-6]) / 18 if len(vols) >= 24 else vol_recent
                vol_ratio  = vol_recent / vol_base if vol_base > 0 else 1.0

                if compression < 15:
                    score += SCORE_WEIGHTS['price_compression']
                    reasons.append(f'极度压缩({compression:.0f}%)')

                if vol_ratio < 0.5:
                    score += SCORE_WEIGHTS['vol_shrink_3d']
                    reasons.append(f'量能萎缩({vol_ratio:.2f}x)')
            else:
                compression = 100; vol_ratio = 1.0

            # ── 生成预警 ──────────────────────────────────
            if score >= 30:
                alert_level = ('🚨紧急' if score >= 65 else
                               ('⚠️关注' if score >= 45 else '🔔监控'))
                alerts.append({
                    'symbol': sym,
                    'score': score,
                    'level': alert_level,
                    'price': price,
                    'chg_24h': round(chg, 1),
                    'vol_24h_m': round(vol / 1e6, 1),
                    'oi_chg_pct': round(oi_chg, 1),
                    'funding_rate': round(latest_fr, 4),
                    'short_pct': round(short_pct, 1),
                    'compression_pct': round(compression, 1),
                    'vol_ratio': round(vol_ratio, 2),
                    'reasons': reasons,
                    'scan_time': datetime.datetime.utcnow().isoformat(),
                })

        except Exception as e:
            pass  # 静默跳过单币错误

    # 按评分排序
    alerts.sort(key=lambda x: x['score'], reverse=True)

    # 新增：T-30先行信号扫描
    pre_alerts = scan_pre_launch(symbols[:300])
    if pre_alerts:
        print(f'\n先行信号(T-30预警): {len(pre_alerts)}个候选')
        for a in pre_alerts[:5]:
            print(f'  {a["level"]} {a["symbol"]:<18} pre_score={a["pre_score"]} | {" | ".join(a["pre_reasons"][:2])}')
        # 保存先行预警
        pre_out = os.path.join(os.path.dirname(__file__), 'pre_launch_alerts.json')
        with open(pre_out, 'w') as f:
            json.dump({'scan_time': datetime.datetime.utcnow().isoformat(),
                       'pre_alerts': pre_alerts}, f, indent=2, ensure_ascii=False)

    return alerts


def format_alert_msg(alert):
    """格式化推送消息"""
    sym   = alert['symbol']
    score = alert['score']
    lvl   = alert['level']
    reasons_str = '\n'.join(f'  · {r}' for r in alert['reasons'])

    return (
        f"{lvl} [{sym}] 暴涨预警\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"综合评分: {score}/100\n"
        f"当前价:  ${alert['price']}\n"
        f"24H涨跌: {alert['chg_24h']:+.1f}%\n"
        f"成交额:  ${alert['vol_24h_m']:.0f}M\n"
        f"OI变化:  {alert['oi_chg_pct']:+.0f}%\n"
        f"资金费率: {alert['funding_rate']:+.4f}%\n"
        f"空头占比: {alert['short_pct']:.0f}%\n"
        f"价格压缩: {alert['compression_pct']:.0f}%\n"
        f"触发信号:\n{reasons_str}\n"
        f"建议: 关注，等放量确认后考虑轻仓多单"
    )




# ══════════════════════════════════════════════════════
# 先行信号扫描：登榜前T-30天可发现的候选（达摩院铁证）
# 模式D（先放量后萎缩）: ≥200%占比22% ← 最强
# 模式标准（横盘+萎缩→爆量）: 均涨+173% ← 质量最高
# ══════════════════════════════════════════════════════
def scan_pre_launch(symbols):
    """T-30天先行信号扫描：在登榜前提前发现布局机会"""
    pre_alerts = []

    for sym in symbols:
        try:
            tick = requests.get(f'{API}/fapi/v1/ticker/24hr',
                params={'symbol': sym}, timeout=5).json()
            chg  = float(tick.get('priceChangePercent', 0))
            vol  = float(tick.get('quoteVolume', 0))

            # 过滤：未大幅波动、有流动性
            if abs(chg) > 25 or vol < 1_000_000 or vol > 1_000_000_000:
                continue

            # 拉取4H K线（近35天）
            kl = requests.get(f'{API}/fapi/v1/klines',
                params={'symbol': sym, 'interval': '4h', 'limit': 210}, timeout=8).json()
            if not isinstance(kl, list) or len(kl) < 84: continue

            closes = [float(k[4]) for k in kl]
            highs  = [float(k[2]) for k in kl]
            lows   = [float(k[3]) for k in kl]
            qvols  = [float(k[7]) for k in kl]

            # ── 位置判断：处于低位（距3月高点40%以上）──
            high_3m = max(highs[-180:]) if len(highs) >= 180 else max(highs)
            dist_from_high = (closes[-1] - high_3m) / high_3m * 100
            if dist_from_high > -20:  # 必须低于高点20%以上
                continue

            # ── T-30/T-14/T-7量能结构 ──────────────
            vol_base = sum(qvols[-120:-84]) / 36 if len(qvols) >= 120 else sum(qvols[:36])/36
            vol_d14  = sum(qvols[-56:-28]) / 28 if len(qvols) >= 56 else vol_base
            vol_d7   = sum(qvols[-28:-14]) / 14 if len(qvols) >= 28 else vol_base
            vol_d1   = sum(qvols[-6:])     / 6

            ratio_d14 = vol_d14 / vol_base if vol_base > 0 else 1
            ratio_d7  = vol_d7  / vol_d14  if vol_d14  > 0 else 1
            ratio_d1  = vol_d1  / vol_d7   if vol_d7   > 0 else 1

            # ── 价格压缩度（近48H） ──────────────────
            h48 = max(highs[-12:]); l48 = min(lows[-12:])
            ctr = (h48 + l48) / 2
            compression = (h48 - l48) / ctr * 100 if ctr > 0 else 99

            # ── 先行信号评分 ──────────────────────────
            pre_score = 0
            pre_reasons = []

            # 模式D：T-14放量 + T-7萎缩（最强信号）
            if ratio_d7 > 2.0 and ratio_d1 < 0.7:
                pre_score += 40
                pre_reasons.append(f'模式D:吸筹放量({ratio_d7:.1f}x)→安静({ratio_d1:.1f}x)')

            # 模式A：T-14萎缩 + T-1爆量
            elif ratio_d7 < 0.7 and ratio_d1 > 2.0:
                pre_score += 30
                pre_reasons.append(f'模式A:量能萎缩→爆量({ratio_d1:.1f}x)')

            # 价格压缩
            if compression < 15:
                pre_score += 25
                pre_reasons.append(f'TIGHT压缩({compression:.0f}%)')
            elif compression < 25:
                pre_score += 12
                pre_reasons.append(f'MODERATE压缩({compression:.0f}%)')

            # 深度低位
            if dist_from_high < -60:
                pre_score += 15
                pre_reasons.append(f'深度低位(距高{dist_from_high:.0f}%)')
            elif dist_from_high < -40:
                pre_score += 8
                pre_reasons.append(f'低位(距高{dist_from_high:.0f}%)')

            # 量能持续萎缩14天
            if ratio_d14 < 0.7 and ratio_d7 < 0.7:
                pre_score += 10
                pre_reasons.append(f'14天持续萎缩({ratio_d14:.1f}x→{ratio_d7:.1f}x)')

            if pre_score >= 40:
                level = '💣埋伏' if pre_score >= 65 else ('📡关注' if pre_score >= 50 else '🔍候选')
                pre_alerts.append({
                    'symbol': sym, 'pre_score': pre_score, 'level': level,
                    'price': closes[-1], 'chg_24h': round(chg, 1),
                    'vol_24h_m': round(vol/1e6, 1),
                    'compression_pct': round(compression, 1),
                    'dist_from_high': round(dist_from_high, 1),
                    'vol_ratio_d14': round(ratio_d14, 2),
                    'vol_ratio_d7': round(ratio_d7, 2),
                    'vol_ratio_d1': round(ratio_d1, 2),
                    'pre_reasons': pre_reasons,
                    'scan_time': datetime.datetime.utcnow().isoformat(),
                    'signal_type': 'PRE_LAUNCH_T30',
                })
        except:
            pass

    pre_alerts.sort(key=lambda x: x['pre_score'], reverse=True)
    return pre_alerts

def run_scanner():
    """持续扫描循环（供cron调用）"""
    print(f'[{datetime.datetime.utcnow().strftime("%H:%M UTC")}] 暴涨猎手扫描开始...')
    symbols = get_all_symbols()
    alerts = scan_once(symbols)

    if alerts:
        print(f'发现 {len(alerts)} 个预警候选:')
        for a in alerts[:10]:
            print(f'  {a["level"]} {a["symbol"]:18} score={a["score"]:3d} | {" | ".join(a["reasons"][:2])}')

        # 高分预警保存
        out = os.path.join(os.path.dirname(__file__), 'live_alerts.json')
        with open(out, 'w') as f:
            json.dump({
                'scan_time': datetime.datetime.utcnow().isoformat(),
                'alerts': alerts
            }, f, indent=2, ensure_ascii=False)
    else:
        print('  无预警信号')

    return alerts


if __name__ == '__main__':
    run_scanner()
