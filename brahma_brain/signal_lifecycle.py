"""
P3 信号生命周期管理 + P4 TP动态计算 + P5 评分数据实时性
梵天设计院封印 2026-07-26 · 苏摩授权自主执行
"""
import json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

_SIGNAL_LOG = Path(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
_LIFECYCLE_STATE = Path(__file__).parent.parent / 'data' / '_signal_lifecycle.json'


# ── P3: 信号生命周期管理 ────────────────────────────────

SIGNAL_TTL_BARS = 8        # 默认8根1H K线后过期
SIGNAL_TTL_SECS = 8 * 3600

def tick_signal_lifecycle(symbol: str, current_price: float) -> list:
    """
    检查所有OPEN信号的生命周期状态
    返回需要推送的告警列表
    """
    alerts = []
    if not _SIGNAL_LOG.exists():
        return alerts

    lines = _SIGNAL_LOG.read_text().strip().split('\n')
    updated_lines = []
    now_ts = time.time()

    for line in lines:
        if not line.strip():
            continue
        try:
            sig = json.loads(line)
        except Exception:
            updated_lines.append(line)
            continue

        # 只处理当前标的的OPEN信号
        if sig.get('symbol', '').upper() != symbol.upper():
            updated_lines.append(line)
            continue
        if sig.get('status') != 'OPEN':
            updated_lines.append(line)
            continue

        direction = sig.get('direction', 'LONG')
        entry_price = float(sig.get('entry_price', 0) or 0)
        sl_price = float(sig.get('sl_price', 0) or 0)
        tp1_price = float(sig.get('tp1_price', 0) or 0)
        sig_ts = float(sig.get('ts', now_ts) or now_ts)
        sig_id = sig.get('id', sig.get('sha8', '?'))

        # TTL检查
        age_secs = now_ts - sig_ts
        if age_secs > SIGNAL_TTL_SECS:
            sig['status'] = 'EXPIRED'
            sig['result'] = 'EXPIRED'
            sig['settled_price'] = current_price
            sig['settled_ts'] = now_ts
            pnl = ((current_price - entry_price) / entry_price * 100
                   if direction == 'LONG'
                   else (entry_price - current_price) / entry_price * 100)
            sig['pnl_pct'] = round(pnl, 3)
            alerts.append({
                'level': 'INFO',
                'msg': f'⏰ [{symbol}] 信号{sig_id}已超过{SIGNAL_TTL_BARS}H TTL → EXPIRED，PnL={pnl:+.2f}%'
            })
            updated_lines.append(json.dumps(sig, ensure_ascii=False))
            continue

        # SL触发检查
        if sl_price > 0 and entry_price > 0:
            sl_hit = (direction == 'LONG' and current_price <= sl_price) or \
                     (direction == 'SHORT' and current_price >= sl_price)
            if sl_hit:
                sig['status'] = 'STOP_LOSS'
                sig['result'] = 'STOP_LOSS'
                sig['settled_price'] = current_price
                sig['settled_ts'] = now_ts
                pnl = ((current_price - entry_price) / entry_price * 100
                       if direction == 'LONG'
                       else (entry_price - current_price) / entry_price * 100)
                sig['pnl_pct'] = round(pnl, 3)
                alerts.append({
                    'level': 'CRITICAL',
                    'msg': (f'🚨 [{symbol}] 信号{sig_id} STOP_LOSS触发！\n'
                            f'  入场: ${entry_price:.4f} → 现价: ${current_price:.4f}\n'
                            f'  SL: ${sl_price:.4f}  PnL: {pnl:+.2f}%')
                })
                updated_lines.append(json.dumps(sig, ensure_ascii=False))
                continue

        # TP1触发检查
        if tp1_price > 0 and entry_price > 0:
            tp1_hit = (direction == 'LONG' and current_price >= tp1_price) or \
                      (direction == 'SHORT' and current_price <= tp1_price)
            if tp1_hit and sig.get('tp1_hit') != True:
                sig['tp1_hit'] = True
                sig['tp1_ts'] = now_ts
                sig['result'] = 'TP1'          # [fix 2026-07-27 闭环TP1写入]
                sig['settled_price'] = current_price
                sig['settled_ts'] = now_ts
                pnl = ((current_price - entry_price) / entry_price * 100
                       if direction == 'LONG'
                       else (entry_price - current_price) / entry_price * 100)
                sig['pnl_pct'] = round(pnl, 3)
                alerts.append({
                    'level': 'SUCCESS',
                    'msg': (f'✅ [{symbol}] 信号{sig_id} TP1触达！\n'
                            f'  入场: ${entry_price:.4f} → TP1: ${tp1_price:.4f}\n'
                            f'  PnL: {pnl:+.2f}%  建议：移动止损至保本位')
                })

        # TP2触发检查（TP1已触达后才检查TP2）[fix 2026-07-28 TP2闭环]
        tp2_price = float(sig.get('tp2') or sig.get('tp2_price') or 0)
        if tp2_price > 0 and sig.get('tp1_hit') and not sig.get('tp2_hit'):
            tp2_hit = (direction == 'LONG' and current_price >= tp2_price) or \
                      (direction == 'SHORT' and current_price <= tp2_price)
            if tp2_hit:
                sig['tp2_hit'] = True
                sig['tp2_ts'] = now_ts
                sig['result'] = 'TP2'
                sig['settled_price'] = current_price
                sig['settled_ts'] = now_ts
                pnl2 = ((current_price - entry_price) / entry_price * 100
                        if direction == 'LONG'
                        else (entry_price - current_price) / entry_price * 100)
                sig['pnl_pct'] = round(pnl2, 3)
                alerts.append({
                    'level': 'SUCCESS',
                    'msg': (f'🎯 [{symbol}] 信号{sig_id} TP2触达！\n'
                            f'  入场: ${entry_price:.4f} → TP2: ${tp2_price:.4f}\n'
                            f'  PnL: {pnl2:+.2f}%  满仓出场')
                })

        updated_lines.append(json.dumps(sig, ensure_ascii=False))

    # 写回
    _SIGNAL_LOG.write_text('\n'.join(updated_lines) + '\n')
    return alerts


# ── P4: TP基于清算集群密度动态计算 ────────────────────────

def calc_dynamic_tp(
    direction: str,
    entry_price: float,
    liq_heatmap: dict,
    equal_highs: list,
    equal_lows: list,
    fallback_rr: float = 2.0
) -> tuple:
    """
    P4: 基于清算集群密度动态计算TP1/TP2
    Returns (tp1, tp2, method)
    """
    candidates = []

    if direction == 'LONG':
        # TP候选：上方等高止损池（空头踩踏区）
        for pool in equal_highs[:5]:
            price = float(pool.get('level', 0) or 0)
            count = int(pool.get('count', 1) or 1)
            if price > entry_price:
                candidates.append((price, count))

        # 也考虑清算热力图上方密集区
        liq_clusters = liq_heatmap.get('long_clusters', []) if isinstance(liq_heatmap, dict) else []
        for c in liq_clusters[:3]:
            price = float(c.get('price', 0) or 0)
            density = float(c.get('density', 1) or 1)
            if price > entry_price:
                candidates.append((price, int(density * 2)))

    else:  # SHORT
        for pool in equal_lows[:5]:
            price = float(pool.get('level', 0) or 0)
            count = int(pool.get('count', 1) or 1)
            if price < entry_price:
                candidates.append((price, count))

    if not candidates:
        # fallback: RR倍数
        sl_dist = entry_price * 0.02
        if direction == 'LONG':
            tp1 = round(entry_price + sl_dist * fallback_rr, 4)
            tp2 = round(entry_price + sl_dist * fallback_rr * 2, 4)
        else:
            tp1 = round(entry_price - sl_dist * fallback_rr, 4)
            tp2 = round(entry_price - sl_dist * fallback_rr * 2, 4)
        return tp1, tp2, 'FALLBACK_RR'

    # 按密度排序，取前两个
    candidates.sort(key=lambda x: x[1], reverse=True)
    prices_sorted = sorted([c[0] for c in candidates],
                           key=lambda p: abs(p - entry_price))
    tp1 = prices_sorted[0] if len(prices_sorted) >= 1 else None
    tp2 = prices_sorted[1] if len(prices_sorted) >= 2 else None

    if tp1 is None:
        return None, None, 'NO_TARGET'

    return round(tp1, 4), round(tp2, 4) if tp2 else None, 'LIQUIDITY_CLUSTER'


# ── P5: 评分数据实时性审计 ─────────────────────────────────

def audit_score_with_realtime(symbol: str, score_breakdown: dict) -> dict:
    """
    P5: 对关键评分维度附上实时原始数据
    返回增强的breakdown字典，每个维度附上2~3个原始指标值
    """
    enhanced = dict(score_breakdown)

    try:
        # 拉取1H K线
        try:
            from brahma_brain.data_cache import get_klines as _dc
            klines = _dc(symbol, '1h', 20) or []
        except Exception:
            url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=20'
            klines = json.loads(urllib.request.urlopen(url, timeout=6).read())
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        lows = [float(k[3]) for k in klines]

        cur_vol = volumes[-1]
        ma5_vol = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else cur_vol
        vol_decay_pct = round((cur_vol - ma5_vol) / ma5_vol * 100, 1) if ma5_vol > 0 else 0

        # OBV
        obv = 0
        for i in range(1, len(klines)):
            c, pc = float(klines[i][4]), float(klines[i-1][4])
            v = float(klines[i][5])
            obv += v if c > pc else (-v if c < pc else 0)
        obv_prev = obv - (volumes[-1] if closes[-1] > closes[-2] else -volumes[-1])
        obv_dir = 'UP' if obv > obv_prev else 'DOWN'

        # RSI
        gains = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        rsi_cur = round(100 - 100 / (1 + ag/al), 1) if al > 0 else 100

        # 价格低点比较（底背离检测）
        price_low_cur = min(lows[-6:]) if len(lows) >= 6 else lows[-1]
        price_low_prev = min(lows[-14:-6]) if len(lows) >= 14 else price_low_cur
        rsi_arr = []
        for i in range(14, len(closes)):
            g = [max(closes[j]-closes[j-1],0) for j in range(i-13,i+1)]
            l = [max(closes[j-1]-closes[j],0) for j in range(i-13,i+1)]
            ag2 = sum(g)/14; al2 = sum(l)/14
            rsi_arr.append(round(100-100/(1+(ag2/al2 if al2 else 999)),1))
        rsi_low_cur = min(rsi_arr[-6:]) if len(rsi_arr) >= 6 else rsi_cur
        rsi_low_prev = min(rsi_arr[-14:-6]) if len(rsi_arr) >= 14 else rsi_low_cur
        div_valid = price_low_cur < price_low_prev and rsi_low_cur > rsi_low_prev

        ts_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
        enhanced['_P5_realtime'] = {
            'ts': ts_str,
            '量能衰竭_实测': {
                '当前1H量': f'{cur_vol:,.0f}张',
                'MA5均量': f'{ma5_vol:,.0f}张',
                '衰减率': f'{vol_decay_pct:+.1f}%(相对MA5)',
                'OBV方向': obv_dir,
                '评分是否合理': '⚠️存疑' if (vol_decay_pct > -30 and '衰竭' in str(score_breakdown.get('vol_exhaustion',''))) else '✅基本符合'
            },
            '底背离_实测': {
                '当前RSI_1H': rsi_cur,
                '价格低点_当前': round(price_low_cur, 4),
                '价格低点_前期': round(price_low_prev, 4),
                '底背离_是否成立': '✅成立(价低RSI高)' if div_valid else '❌不成立(实测数据)',
            }
        }

    except Exception as e:
        enhanced['_P5_realtime'] = {'error': str(e)}

    return enhanced
