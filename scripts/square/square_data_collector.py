#!/usr/bin/env python3
"""
square_data_collector.py — 发帖数据采集层
设计院封印 2026-07-21 | 苏摩111批准

功能：
  1. 拉取BTC/ETH实时价格、OI、FR、RSI
  2. 读取brahma_state.json当前体制
  3. 读取live_signal_log今日最高分信号
  4. 从SMC引擎获取真实结构位（Bull OB / Bear OB / FVG / 流动性）
  5. 输出 data/square_context.json

调用方式：
  python3 scripts/square/square_data_collector.py
  → 生成 data/square_context.json
"""
import os, sys, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

API = 'https://fapi.binance.com'
OUT = BASE / 'data' / 'square_context.json'

REGIME_CN = {
    'BULL_TREND':    '牛市上行',
    'BULL_EARLY':    '牛市初期',
    'BULL_CORRECTION': '牛市回调',
    'BEAR_TREND':    '熊市下行',
    'BEAR_EARLY':    '熊市初期',
    'BEAR_RECOVERY': '熊市反弹',
    'CHOP_MID':      '震荡整理',
    'CHOP_HIGH':     '高位震荡',
    'CHOP_LOW':      '低位震荡',
    'UNKNOWN':       '待判断',
}

REGIME_BIAS = {
    'BULL_TREND':    '偏多，顺势做多为主',
    'BULL_EARLY':    '偏多，轻仓布局',
    'BULL_CORRECTION': '回调蓄力，等企稳再多',
    'BEAR_TREND':    '偏空，顺势做空为主',
    'BEAR_EARLY':    '顶部反转，轻仓试空',
    'BEAR_RECOVERY': '筑底反弹，极轻仓做多，严控止损',
    'CHOP_MID':      '震荡区间，双向机会，优先区间极端位操作',
    'CHOP_HIGH':     '高位震荡，危险体制，优先观望',
    'CHOP_LOW':      '低位震荡，等突破方向确认',
    'UNKNOWN':       '体制未明，建议观望',
}


def get_ticker(symbol):
    try:
        t = requests.get(f'{API}/fapi/v1/ticker/24hr', params={'symbol': symbol}, timeout=5).json()
        fr = requests.get(f'{API}/fapi/v1/premiumIndex', params={'symbol': symbol}, timeout=5).json()
        oi = requests.get(f'{API}/fapi/v1/openInterest', params={'symbol': symbol}, timeout=5).json()
        return {
            'price': float(t.get('lastPrice', 0)),
            'chg24h': float(t.get('priceChangePercent', 0)),
            'fr': float(fr.get('lastFundingRate', 0)) * 100,
            'oi': float(oi.get('openInterest', 0)),
        }
    except Exception as e:
        print(f'[ticker] {symbol} 失败: {e}')
        return {'price': 0, 'chg24h': 0, 'fr': 0, 'oi': 0}


def get_rsi_1h(symbol, period=14):
    """计算RSI_1H"""
    try:
        kl = requests.get(f'{API}/fapi/v1/klines',
            params={'symbol': symbol, 'interval': '1h', 'limit': period + 5},
            timeout=5).json()
        closes = [float(k[4]) for k in kl]
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0: return 100
        return round(100 - 100 / (1 + ag / al), 1)
    except:
        return 50


def get_regime():
    # [regime_bus 2026-08-05] 统一体制总线优先
    try:
        import sys as _rbs_m, os as _rbo_m
        _rbs_m.path.insert(0, _rbo_m.path.join(_rbo_m.path.dirname(_rbo_m.path.abspath(__file__)), '..', 'scripts'))
        from regime_bus import get as _rb_get_m
        _rb_r_m = _rb_get_m('BTCUSDT', layer='MONITOR')
        if _rb_r_m and _rb_r_m != 'UNKNOWN':
            return _rb_r_m
    except Exception:
        pass
    """读取当前体制（BTC为主）"""
    # 1. regime_state.json 是按symbol分层的，需读 BTCUSDT.confirmed
    f1 = BASE / 'data' / 'regime_state.json'
    try:
        if f1.exists():
            d = json.loads(f1.read_text())
            # 按symbol分层格式
            btc_d = d.get('BTCUSDT', {})
            r = btc_d.get('confirmed') or btc_d.get('last_raw') or btc_d.get('regime')
            if r and r != 'UNKNOWN':
                return r
            # 旧版扁平格式兜底
            r2 = d.get('regime') or d.get('btc_regime')
            if r2 and r2 != 'UNKNOWN':
                return r2
    except:
        pass
    # 2. fallback brahma_state.json
    f2 = BASE / 'brahma_brain' / 'brahma_state.json'
    try:
        if f2.exists():
            d = json.loads(f2.read_text())
            r = d.get('regime') or d.get('btc_regime')
            if r and r != 'UNKNOWN':
                return r
    except:
        pass
    return 'UNKNOWN'


def get_top_signal():
    """读取今日最高分有效信号"""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    log_file = BASE / 'data' / 'live_signal_log.jsonl'
    if not log_file.exists():
        return None
    try:
        signals = []
        with open(log_file) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if today in str(d.get('ts_iso', d.get('ts', ''))):
                        if d.get('valid') and d.get('score', 0) >= 100:
                            signals.append(d)
                except:
                    pass
        if not signals:
            return None
        return max(signals, key=lambda x: x.get('score', 0))
    except:
        return None


def get_smc_levels(symbol):
    """
    从梵天SMC引擎获取真实结构位。
    轻量版：读取最近缓存的1号工程结果。
    """
    # 尝试读已有分析缓存
    cache_files = [
        BASE / 'data' / f'smc_cache_{symbol}.json',
        BASE / 'data' / 'brahma_last_analysis.json',
    ]
    for cf in cache_files:
        try:
            if cf.exists():
                d = json.loads(cf.read_text())
                if d.get('symbol') == symbol:
                    return {
                        'bull_ob': d.get('bull_ob', ''),
                        'bear_ob': d.get('bear_ob', ''),
                        'fvg_target': d.get('fvg_target', ''),
                        'liq_above': d.get('liq_above', ''),
                        'liq_below': d.get('liq_below', ''),
                        'source': 'cache',
                    }
        except:
            pass

    # Fallback：用价格结构估算（标注来源为"价格区间估算"）
    try:
        kl = requests.get(f'{API}/fapi/v1/klines',
            params={'symbol': symbol, 'interval': '4h', 'limit': 20},
            timeout=5).json()
        highs = [float(k[2]) for k in kl]
        lows = [float(k[3]) for k in kl]
        closes = [float(k[4]) for k in kl]
        now = closes[-1]

        # 近期高低点（流动性猎杀区）
        high_5 = max(highs[-5:])
        low_5 = min(lows[-5:])
        pivot_high = max(highs[-10:])
        pivot_low = min(lows[-10:])

        # 支撑 = 近期低点区(不与阻力重叠)
        support_lo = min(lows[-10:])
        support_hi = sorted(lows[-10:])[2]  # 第3低点
        # 阻力 = 近期高点区
        resist_lo = sorted(highs[-10:], reverse=True)[2]  # 第3高点
        resist_hi = max(highs[-10:])

        return {
            'bull_ob': f'{support_lo:.1f}~{support_hi:.1f}（4H低点支撑区）',
            'bear_ob': f'{resist_lo:.1f}~{resist_hi:.1f}（4H高点阻力区）',
            'fvg_target': '',
            'liq_above': f'{pivot_high:.1f}（10日高点流动性）',
            'liq_below': f'{pivot_low:.1f}（10日低点流动性）',
            'source': 'price_structure',
        }
    except:
        return {'bull_ob': '', 'bear_ob': '', 'fvg_target': '', 'liq_above': '', 'liq_below': '', 'source': 'none'}


def collect():
    print(f'[square_data_collector] 开始采集 {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    # 基础数据
    btc = get_ticker('BTCUSDT')
    eth = get_ticker('ETHUSDT')
    btc_rsi = get_rsi_1h('BTCUSDT')
    eth_rsi = get_rsi_1h('ETHUSDT')
    regime = get_regime()
    top_signal = get_top_signal()
    btc_smc = get_smc_levels('BTCUSDT')
    eth_smc = get_smc_levels('ETHUSDT')

    # 当前时间（北京时间）
    cst_now = datetime.now(timezone(timedelta(hours=8)))
    hour_cst = cst_now.hour
    if 6 <= hour_cst < 12:
        post_type = '早间综合'
    elif 12 <= hour_cst < 18:
        post_type = '午盘快讯'
    else:
        post_type = '晚盘深度'

    ctx = {
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'post_type': post_type,
        'regime': regime,
        'regime_cn': REGIME_CN.get(regime, regime),
        'regime_bias': REGIME_BIAS.get(regime, '建议观望'),
        'btc': {
            'price': btc['price'],
            'chg24h': f'{btc["chg24h"]:+.1f}%',
            'fr': f'{btc["fr"]:.4f}%',
            'rsi_1h': btc_rsi,
            'support': btc_smc['bull_ob'] or btc_smc['liq_below'],
            'resist': btc_smc['bear_ob'] or btc_smc['liq_above'],
            'liq_above': btc_smc['liq_above'],
            'liq_below': btc_smc['liq_below'],
            'smc_source': btc_smc['source'],
        },
        'eth': {
            'price': eth['price'],
            'chg24h': f'{eth["chg24h"]:+.1f}%',
            'fr': f'{eth["fr"]:.4f}%',
            'rsi_1h': eth_rsi,
            'support': eth_smc['bull_ob'] or eth_smc['liq_below'],
            'resist': eth_smc['bear_ob'] or eth_smc['liq_above'],
            'liq_above': eth_smc['liq_above'],
            'smc_source': eth_smc['source'],
        },
        'top_signal': {
            'symbol': top_signal.get('symbol', '') if top_signal else '',
            'direction': top_signal.get('direction', '') if top_signal else '',
            'score': top_signal.get('score', 0) if top_signal else 0,
            'entry_lo': top_signal.get('entry_lo', '') if top_signal else '',
            'entry_hi': top_signal.get('entry_hi', '') if top_signal else '',
            'sl': top_signal.get('sl', '') if top_signal else '',
            'tp1': top_signal.get('tp1', '') if top_signal else '',
            'rr': top_signal.get('rr1', '') if top_signal else '',
        } if top_signal else None,
    }

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)

    print(f'✅ square_context.json 已生成')
    print(f'   体制: {regime} ({REGIME_CN.get(regime, "")})')
    print(f'   BTC: ${btc["price"]:,.0f} {btc["chg24h"]:+.1f}%  RSI={btc_rsi}')
    print(f'   ETH: ${eth["price"]:,.2f} {eth["chg24h"]:+.1f}%  RSI={eth_rsi}')
    print(f'   今日最高分信号: {top_signal.get("symbol","无") if top_signal else "无"} {top_signal.get("score",0) if top_signal else 0}分')
    print(f'   帖型: {post_type}')
    return ctx


if __name__ == '__main__':
    collect()
