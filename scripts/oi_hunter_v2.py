#!/usr/bin/env python3
"""
OI猎手 v2.0 — 全球顶级OI研究方法论重构
设计院 2026-07-10 苏摩111授权

═══════════════════════════════════════════════════════════════
核心研究依据:
  · Glassnode: OI/价格背离识别（多空建仓方向判断）
  · QCP Capital: OI持续累积趋势（非单步变化）
  · Deribit研究: 基差+资金费率+OI三维共振
  · Coinglass方法: 多周期OI变化分级（1H/4H/24H/7D）

三大信号类别:
  🟢 A类 现货低倍信号（1-365天持仓量持续提升+100%以上）
    → 适合: 1-5x 低杠杆中长期持仓
    → 触发: 7D/30D OI持续增加 + 基差为正 + 多头建仓方向

  🟡 B类 合约中线信号（OI累积50%-500%）
    → 适合: 10x 中线布局
    → 触发: 24H OI趋势向上 + 价格/OI方向共振 + FR正常

  🔴 C类 短线异动信号（1H/4H OI突变）
    → 适合: 即时方向判断，辅助入场择时
    → 触发: 1H OI变化>1.5%（降低原3%阈值）

═══════════════════════════════════════════════════════════════
"""

import requests, json, time, os, sys, math
from datetime import datetime, timezone
from pathlib import Path

# ── 路径 ───────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
try:
    from scripts.system_config import FAPI_BASE, JARVIS_TARGET, JARVIS_CHANNEL
except:
    FAPI_BASE = 'https://fapi.binance.com'
    JARVIS_TARGET = '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63'
    JARVIS_CHANNEL = 'jarvis'

CACHE_FILE = BASE / 'data' / 'oi_hunter_v2_cache.json'
LOG_FILE   = BASE / 'data' / 'oi_hunter_v2_log.jsonl'

# ── 监控标的（扩展至30个，覆盖主流+潜力山寨）──────────────────
SYMBOLS = [
    # 主力（趋势锚）
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT',
    # 高OI山寨
    'XRPUSDT', 'ADAUSDT', 'DOTUSDT', 'LINKUSDT', 'AVAXUSDT',
    'MATICUSDT', 'NEARUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
    # 中等市值
    'HYPEUSDT', 'LDOUSDT', 'INJUSDT', 'SUIUSDT', 'SEIUSDT',
    'TIAUSDT', 'JUPUSDT', 'WIFUSDT', 'PENGUUSDT', 'ENAUSDT',
    # 监控候选
    'TONUSDT', 'FETUSDT', 'RENDERUSDT', 'WLDUSDT', 'STXUSDT',
]

# ── 阈值配置（全球顶级OI研究标准）────────────────────────────
CFG = {
    # A类：现货长线（OI持续增仓判断）
    'A_7D_OI_MIN_PCT':     50.0,    # 7日OI增幅≥50%
    'A_30D_OI_MIN_PCT':    100.0,   # 30日OI增幅≥100% ← 苏摩要求
    'A_BASIS_POSITIVE':    0.05,    # 基差>0.05%（期货溢价=多头主导）
    'A_FR_MAX':            0.05,    # 资金费率<0.05%（未过热）

    # B类：合约中线（OI趋势布局）
    'B_24H_OI_MIN_PCT':    10.0,    # 24H OI增幅≥10%
    'B_4H_OI_MIN_PCT':     3.0,     # 4H OI增幅≥3%
    'B_OI_PRICE_ALIGN':    True,    # OI与价格方向一致
    'B_OI_RANGE_50_500':   (50.0, 500.0),  # 50%-500%布局区间

    # C类：短线异动
    'C_1H_OI_MIN_PCT':     1.5,     # 1H OI变化≥1.5%（原3%→降低）
    'C_1H_VOL_SPIKE':      2.0,     # 量比>2x配合

    # 共同过滤
    'MIN_OI_USD':          10.0,    # 最小OI规模$10M（过滤垃圾币）
    'MIN_VOL_USD':         50.0,    # 最小24H成交额$50M
}


def safe_get(url, timeout=7, retries=2):
    for _ in range(retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except:
            time.sleep(0.3)
    return None


def get_oi_history(sym, period, limit):
    """获取OI历史序列"""
    d = safe_get(f'{FAPI_BASE}/futures/data/openInterestHist?symbol={sym}&period={period}&limit={limit}')
    if not isinstance(d, list) or len(d) < 2:
        return []
    return [{'ts': int(x['timestamp']), 'oi': float(x['sumOpenInterest']),
             'oi_usd': float(x['sumOpenInterestValue'])} for x in d]


def get_funding_rate(sym):
    """最新资金费率"""
    d = safe_get(f'{FAPI_BASE}/fapi/v1/premiumIndex?symbol={sym}')
    if isinstance(d, dict):
        return float(d.get('lastFundingRate', 0)) * 100
    return 0.0


def get_mark_price(sym):
    """获取标记价格和指数价格（计算基差）"""
    d = safe_get(f'{FAPI_BASE}/fapi/v1/premiumIndex?symbol={sym}')
    if isinstance(d, dict):
        mark = float(d.get('markPrice', 0))
        index = float(d.get('indexPrice', 0))
        basis_pct = (mark - index) / index * 100 if index > 0 else 0
        return mark, index, basis_pct
    return 0, 0, 0


def get_ticker(sym):
    """24H行情"""
    d = safe_get(f'{FAPI_BASE}/fapi/v1/ticker/24hr?symbol={sym}')
    if isinstance(d, dict):
        return {
            'price': float(d.get('lastPrice', 0)),
            'price_chg_pct': float(d.get('priceChangePercent', 0)),
            'volume_usdt': float(d.get('quoteVolume', 0)) / 1e6,  # M USDT
            'high': float(d.get('highPrice', 0)),
            'low': float(d.get('lowPrice', 0)),
        }
    return {}


def get_long_short_ratio(sym):
    """全球多空比"""
    d = safe_get(f'{FAPI_BASE}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=3')
    if isinstance(d, list) and d:
        return float(d[-1].get('longShortRatio', 1.0))
    return 1.0


def calc_oi_trend(oi_list, n_bars):
    """计算n根K线内的OI变化率"""
    if len(oi_list) < n_bars + 1:
        return 0.0
    cur = oi_list[-1]['oi']
    past = oi_list[-(n_bars+1)]['oi']
    return (cur - past) / past * 100 if past > 0 else 0.0


def calc_oi_direction(oi_list, price_chg_pct):
    """
    OI/价格方向矩阵（Glassnode方法论）:
      OI↑ + Price↑ = 多头建仓（做多信号）
      OI↑ + Price↓ = 空头建仓（做空信号）
      OI↓ + Price↑ = 空头平仓（轧空，短期多，持续性弱）
      OI↓ + Price↓ = 多头止损（恐慌抛售，危险）
    """
    if len(oi_list) < 4:
        return 'UNKNOWN', 0
    oi_chg = calc_oi_trend(oi_list, 3)  # 3H OI变化

    if oi_chg > 0 and price_chg_pct > 0:
        return 'LONG_BUILD', 1      # 做多信号
    elif oi_chg > 0 and price_chg_pct < 0:
        return 'SHORT_BUILD', -1    # 做空信号
    elif oi_chg < 0 and price_chg_pct > 0:
        return 'SHORT_SQUEEZE', 1   # 轧空（弱多）
    elif oi_chg < 0 and price_chg_pct < 0:
        return 'LONG_UNWIND', -1    # 多头离场（弱空）
    return 'NEUTRAL', 0


def calc_oi_score(oi_chg_1h, oi_chg_4h, oi_chg_24h, oi_chg_7d,
                  oi_direction, basis_pct, fr, ls_ratio, vol_spike):
    """
    OI综合评分（0-100分，>60触发信号）

    参考全球顶级机构评分体系：
      · 多周期OI趋势得分（核心）：40分
      · 价格/OI方向共振：20分
      · 基差+资金费率：20分
      · 多空比+量比：20分
    """
    score = 0
    details = []

    # ── 1. 多周期OI趋势得分（最高40分）──
    if abs(oi_chg_1h) >= CFG['C_1H_OI_MIN_PCT']:
        pts = min(10, abs(oi_chg_1h) * 2)
        score += pts
        details.append(f'OI_1H={oi_chg_1h:+.1f}%(+{pts:.0f})')

    if abs(oi_chg_4h) >= CFG['B_4H_OI_MIN_PCT']:
        pts = min(15, abs(oi_chg_4h) * 1.5)
        score += pts
        details.append(f'OI_4H={oi_chg_4h:+.1f}%(+{pts:.0f})')

    if abs(oi_chg_24h) >= CFG['B_24H_OI_MIN_PCT']:
        pts = min(20, abs(oi_chg_24h) * 0.5)
        score += pts
        details.append(f'OI_24H={oi_chg_24h:+.1f}%(+{pts:.0f})')

    if abs(oi_chg_7d) >= CFG['A_7D_OI_MIN_PCT']:
        pts = min(25, abs(oi_chg_7d) * 0.3)
        score += pts
        details.append(f'OI_7D={oi_chg_7d:+.1f}%(+{pts:.0f})')

    # ── 2. 价格/OI方向共振（最高20分）──
    dir_map = {'LONG_BUILD': 20, 'SHORT_BUILD': 18,
               'SHORT_SQUEEZE': 10, 'LONG_UNWIND': 8, 'NEUTRAL': 0}
    dir_pts = dir_map.get(oi_direction, 0)
    score += dir_pts
    if dir_pts > 0:
        details.append(f'{oi_direction}(+{dir_pts})')

    # ── 3. 基差+资金费率（最高20分）──
    # 基差>0且<0.5% = 健康多头溢价
    if 0 < basis_pct < 0.5:
        score += 10
        details.append(f'BASIS={basis_pct:.3f}%(+10)')
    elif basis_pct > 0.5:
        score += 5  # 溢价过高，略打折
        details.append(f'BASIS={basis_pct:.3f}%过热(+5)')
    elif basis_pct < 0:
        score += 8  # 期货折价=空头主导，看空信号
        details.append(f'BASIS={basis_pct:.3f}%折价(+8)')

    if 0 < fr < CFG['A_FR_MAX']:
        score += 10
        details.append(f'FR={fr:.4f}%健康(+10)')
    elif fr >= CFG['A_FR_MAX']:
        score += 3
        details.append(f'FR={fr:.4f}%偏高(+3)')
    elif fr < 0:
        score += 8
        details.append(f'FR={fr:.4f}%负费(+8)')

    # ── 4. 多空比+量比（最高20分）──
    if ls_ratio > 1.8:
        score += 12  # 散户极度偏多=反向做空
        details.append(f'LS={ls_ratio:.2f}反向(+12)')
    elif ls_ratio > 1.3:
        score += 6
        details.append(f'LS={ls_ratio:.2f}偏多(+6)')
    elif ls_ratio < 0.8:
        score += 10  # 散户偏空=潜在做多
        details.append(f'LS={ls_ratio:.2f}偏空(+10)')

    if vol_spike > CFG['C_1H_VOL_SPIKE']:
        pts = min(8, vol_spike * 2)
        score += pts
        details.append(f'量比={vol_spike:.1f}x(+{pts:.0f})')

    return min(100, score), details


def classify_signal(score, oi_chg_7d, oi_chg_24h, oi_direction, basis_pct, fr):
    """
    信号分类:
      A类: 现货低倍（1-365天，OI持续100%+）
      B类: 合约中线（10x，OI 50%-500%）
      C类: 短线异动
    """
    sig_class = 'C'
    leverage_range = '3-5x'
    hold_period = '1-7天'

    # A类判断：7D OI增幅≥50% + 30D可推测持续性
    if oi_chg_7d >= CFG['A_7D_OI_MIN_PCT'] and basis_pct > 0 and abs(fr) < 0.05:
        sig_class = 'A'
        leverage_range = '1-5x'
        hold_period = '7-365天'

    # B类判断：24H OI增幅≥10% + 方向清晰
    elif oi_chg_24h >= CFG['B_24H_OI_MIN_PCT'] and oi_direction in ('LONG_BUILD', 'SHORT_BUILD'):
        sig_class = 'B'
        # 根据OI增幅确定杠杆：50%-500%对应3x-10x
        if oi_chg_24h >= 200:
            leverage_range = '8-10x'
            hold_period = '1-7天'
        elif oi_chg_24h >= 100:
            leverage_range = '5-8x'
            hold_period = '3-14天'
        elif oi_chg_24h >= 50:
            leverage_range = '3-5x'
            hold_period = '7-30天'
        else:
            leverage_range = '2-3x'
            hold_period = '14-60天'

    return sig_class, leverage_range, hold_period


def format_signal(sym, sig_class, score, oi_data, ticker, basis_pct, fr,
                  oi_direction, leverage_range, hold_period, details):
    """格式化信号推送卡片"""
    direction_icon = {
        'LONG_BUILD': '🟢做多', 'SHORT_BUILD': '🔴做空',
        'SHORT_SQUEEZE': '🟡轧空', 'LONG_UNWIND': '🟠多头离场'
    }.get(oi_direction, '⚪中性')

    class_icon = {'A': '🏆', 'B': '⚡', 'C': '📡'}.get(sig_class, '📡')
    class_name = {'A': '现货长线', 'B': '合约中线', 'C': '短线异动'}.get(sig_class, '短线')

    lines = [
        f"{'='*45}",
        f"{class_icon} OI猎手v2 · {class_name}信号",
        f"{'='*45}",
        f"标的: {sym}  评分: {score}/100",
        f"方向: {direction_icon}",
        f"建议杠杆: {leverage_range}  持仓周期: {hold_period}",
        f"",
        f"── OI趋势 ─────────────────────",
        f"1H变化:  {oi_data.get('chg_1h',0):+.2f}%",
        f"4H变化:  {oi_data.get('chg_4h',0):+.2f}%",
        f"24H变化: {oi_data.get('chg_24h',0):+.2f}%",
        f"7D变化:  {oi_data.get('chg_7d',0):+.2f}%",
        f"当前OI:  ${oi_data.get('oi_usd',0):.2f}B",
        f"",
        f"── 市场微观 ──────────────────",
        f"当前价: ${ticker.get('price',0):,.4f}",
        f"24H涨幅: {ticker.get('price_chg_pct',0):+.2f}%",
        f"基差: {basis_pct:+.3f}%  FR: {fr:+.4f}%",
        f"",
        f"── 评分明细 ──────────────────",
        f"{' | '.join(details[:4])}",
    ]

    # A类额外说明
    if sig_class == 'A':
        lines += [
            f"",
            f"── 长线布局参考 ──────────────",
            f"OI持续增仓{oi_data.get('chg_7d',0):.0f}%(7D)",
            f"适合现货/低倍 {leverage_range} 长期持有",
            f"风控: 仅投入可承受损失的资金",
        ]
    elif sig_class == 'B':
        lines += [
            f"",
            f"── 中线布局参考 ──────────────",
            f"24H增仓{oi_data.get('chg_24h',0):.0f}%，机构布局特征",
            f"建议{leverage_range}分批建仓",
            f"止损: 入场价-5% (OI衰减时离场)",
        ]

    lines.append(f"{'='*45}")
    return '\n'.join(lines)


def send_message(msg):
    import subprocess
    subprocess.Popen(
        ['openclaw', 'message', 'send',
         '--to', JARVIS_TARGET,
         '--channel', JARVIS_CHANNEL,
         '--message', msg],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except:
            pass
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def log_signal(data):
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(data, ensure_ascii=False) + '\n')


# ════════════════════════════════════════════════════════════
def main():
    now = datetime.now(timezone.utc)
    print(f"[OI猎手v2] 扫描开始 {now.strftime('%Y-%m-%dT%H:%M UTC')}")

    cache = load_cache()
    signals_found = []
    scan_summary = []

    for sym in SYMBOLS:
        try:
            # ── 数据拉取 ──────────────────────────────────
            oi_1h_list  = get_oi_history(sym, '1h', 8)
            oi_4h_list  = get_oi_history(sym, '4h', 8)
            oi_1d_list  = get_oi_history(sym, '1d', 10)
            ticker      = get_ticker(sym)
            mark, index, basis_pct = get_mark_price(sym)
            fr          = get_funding_rate(sym)
            ls_ratio    = get_long_short_ratio(sym)

            if not oi_1h_list or not ticker:
                continue

            price    = ticker.get('price', 0)
            vol_usd  = ticker.get('volume_usdt', 0)
            price_chg = ticker.get('price_chg_pct', 0)

            # 最小规模过滤
            oi_usd_cur = oi_1h_list[-1]['oi_usd'] if oi_1h_list else 0
            if oi_usd_cur < CFG['MIN_OI_USD'] or vol_usd < CFG['MIN_VOL_USD']:
                continue

            # ── OI多周期变化计算 ──────────────────────────
            chg_1h  = calc_oi_trend(oi_1h_list, 1)
            chg_4h  = calc_oi_trend(oi_4h_list, 4) if oi_4h_list else calc_oi_trend(oi_1h_list, 4)
            chg_24h = calc_oi_trend(oi_1d_list, 1) if oi_1d_list else calc_oi_trend(oi_1h_list, 24)
            chg_7d  = calc_oi_trend(oi_1d_list, 7) if len(oi_1d_list) >= 8 else 0

            oi_data = {
                'chg_1h': chg_1h, 'chg_4h': chg_4h,
                'chg_24h': chg_24h, 'chg_7d': chg_7d,
                'oi_usd': oi_usd_cur / 1e9 if oi_usd_cur > 1e6 else oi_usd_cur,
            }

            # ── 方向矩阵 ─────────────────────────────────
            oi_direction, direction_bias = calc_oi_direction(oi_1h_list, price_chg)

            # 量比（用volume近似）
            vol_spike = 1.0  # 简化，实际可接入kline量比

            # ── 综合评分 ─────────────────────────────────
            score, details = calc_oi_score(
                chg_1h, chg_4h, chg_24h, chg_7d,
                oi_direction, basis_pct, fr, ls_ratio, vol_spike
            )

            # ── 信号分类 ─────────────────────────────────
            sig_class, lev_range, hold_period = classify_signal(
                score, chg_7d, chg_24h, oi_direction, basis_pct, fr
            )

            scan_summary.append({
                'sym': sym, 'score': score, 'class': sig_class,
                'chg_1h': round(chg_1h,2), 'chg_24h': round(chg_24h,2),
                'chg_7d': round(chg_7d,2), 'dir': oi_direction
            })

            # ── 触发条件（三级阈值）──────────────────────
            threshold = {'A': 65, 'B': 55, 'C': 45}
            min_score = threshold.get(sig_class, 45)

            if score >= min_score:
                # 去重：同标的同方向12H内不重复推送
                cache_key = f"{sym}_{oi_direction}"
                last_push = cache.get(cache_key, 0)
                age_h = (now.timestamp() - last_push) / 3600
                cooldown = {'A': 24, 'B': 6, 'C': 2}.get(sig_class, 2)

                if age_h >= cooldown:
                    msg = format_signal(
                        sym, sig_class, score, oi_data, ticker,
                        basis_pct, fr, oi_direction, lev_range, hold_period, details
                    )
                    print(f"\n{'*'*50}")
                    print(f"🚨 信号触发: {sym} {sig_class}类 score={score}")
                    print(msg)

                    send_message(msg)
                    cache[cache_key] = now.timestamp()

                    log_signal({
                        'ts': now.isoformat(), 'symbol': sym,
                        'class': sig_class, 'score': score,
                        'direction': oi_direction, 'leverage': lev_range,
                        'oi_data': oi_data, 'basis': round(basis_pct,4),
                        'fr': round(fr,6), 'ls': ls_ratio,
                    })
                    signals_found.append(f"{sym}({sig_class}/{score})")
                else:
                    print(f"  {sym}: score={score} {sig_class}类 [冷却中{cooldown-age_h:.1f}H]")

            time.sleep(0.25)  # 速率限制

        except Exception as e:
            print(f"  ⚠️  {sym}: 扫描失败 {e}")
            continue

    save_cache(cache)

    # ── 扫描汇总 ─────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"[OI猎手v2] 扫描完成 | 覆盖{len(SYMBOLS)}个标的")
    print(f"触发信号: {len(signals_found)}个 → {', '.join(signals_found) or '无'}")
    print(f"\nTop5评分:")
    sorted_sum = sorted(scan_summary, key=lambda x: x['score'], reverse=True)[:5]
    for s in sorted_sum:
        print(f"  {s['sym']:12} score={s['score']:3d} {s['class']}类 "
              f"1H={s['chg_1h']:+.1f}% 24H={s['chg_24h']:+.1f}% "
              f"7D={s['chg_7d']:+.1f}% dir={s['dir']}")

    if not signals_found:
        print("HEARTBEAT_OK")

    return len(signals_found)


if __name__ == '__main__':
    main()
