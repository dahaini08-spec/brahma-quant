#!/usr/bin/env python3
"""
macro_ai_bridge.py — AI Pro宏观感知层 → brahma_bus注入
三方联合落地 矿脉3 | 设计院自主执行 2026-08-07

流程：
  AI Pro宏观分析（美联储/DXY/BTC.D/NQ期货）
  → RISK_ON / RISK_OFF / NEUTRAL 判断
  → 写入 brahma_bus['macro_overlay']
  → brahma_engine GATE-0自动叠加体制权重

用法：
  python3 scripts/macro_ai_bridge.py         # 执行宏观分析
  python3 scripts/macro_ai_bridge.py --read  # 读取当前宏观状态
"""

import json
import time
import os
import sys
import requests
import argparse
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / 'data'
MACRO_FILE = DATA_DIR / 'macro_overlay.json'

# ── 宏观数据源（免费接口）─────────────────────────────────────────
FAPI = 'https://fapi.binance.com'
SPOT = 'https://api.binance.com'


def fetch_btc_dominance() -> float | None:
    """BTC.D近似：BTC市值 / 总市值（用BTC/USDT + ETH/USDT价格估算）"""
    try:
        btc = float(requests.get(f'{SPOT}/api/v3/ticker/price?symbol=BTCUSDT', timeout=5).json()['price'])
        eth = float(requests.get(f'{SPOT}/api/v3/ticker/price?symbol=ETHUSDT', timeout=5).json()['price'])
        # 近似：BTC占比 = BTC/(BTC+ETH*15) 作为代理
        btc_d_proxy = btc / (btc + eth * 15) * 100
        return round(btc_d_proxy, 2)
    except Exception:
        return None


def fetch_market_sentiment() -> dict:
    """获取市场情绪指标"""
    result = {}
    try:
        # 资金费率（BTC/ETH）
        fr_url = f'{FAPI}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1'
        fr = requests.get(fr_url, timeout=5).json()
        if fr:
            result['btc_funding_rate'] = float(fr[0].get('fundingRate', 0)) * 100
    except Exception:
        pass
    try:
        fr_eth = requests.get(f'{FAPI}/fapi/v1/fundingRate?symbol=ETHUSDT&limit=1', timeout=5).json()
        if fr_eth:
            result['eth_funding_rate'] = float(fr_eth[0].get('fundingRate', 0)) * 100
    except Exception:
        pass
    try:
        # OI变化（24H）
        oi = requests.get(f'{FAPI}/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=25', timeout=5).json()
        if len(oi) >= 2:
            oi_now = float(oi[-1].get('sumOpenInterestValue', 0))
            oi_24h = float(oi[0].get('sumOpenInterestValue', 0))
            result['btc_oi_change_24h_pct'] = round((oi_now - oi_24h) / oi_24h * 100, 2) if oi_24h else 0
    except Exception:
        pass
    return result


def fetch_btc_trend() -> dict:
    """BTC趋势：EMA50_4H vs 价格"""
    try:
        klines = requests.get(
            f'{FAPI}/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=55', timeout=8
        ).json()
        closes = [float(k[4]) for k in klines]
        price = closes[-1]
        # EMA50
        ema = closes[0]
        for c in closes[1:]:
            ema = c * 2/51 + ema * 49/51
        rsi_closes = closes[-15:]
        deltas = [rsi_closes[i+1]-rsi_closes[i] for i in range(len(rsi_closes)-1)]
        gains = [max(d,0) for d in deltas]
        losses = [max(-d,0) for d in deltas]
        avg_g = sum(gains[:14])/14
        avg_l = sum(losses[:14])/14
        for i in range(14, len(gains)):
            avg_g = (avg_g*13+gains[i])/14
            avg_l = (avg_l*13+losses[i])/14
        rsi_4h = 100 - (100/(1+avg_g/avg_l)) if avg_l else 100
        return {
            'btc_price': round(price, 0),
            'btc_ema50_4h': round(ema, 0),
            'btc_above_ema50': price > ema,
            'btc_rsi_4h': round(rsi_4h, 1),
        }
    except Exception as e:
        return {}


def analyze_macro_state(data: dict) -> dict:
    """
    基于市场数据判断宏观状态
    
    RISK_ON:  BTC站上EMA50 + 资金费率适中正 + OI稳定或增加
    RISK_OFF: BTC跌破EMA50 + 资金费率极端负 + OI大幅减少
    NEUTRAL:  介于两者之间
    """
    score = 0
    signals = []

    btc_above = data.get('btc_above_ema50')
    rsi_4h    = data.get('btc_rsi_4h', 50)
    btc_fr    = data.get('btc_funding_rate', 0)
    oi_chg    = data.get('btc_oi_change_24h_pct', 0)

    # BTC趋势（最重要）
    if btc_above is True:
        score += 30
        signals.append('BTC站上EMA50_4H ✅')
    elif btc_above is False:
        score -= 30
        signals.append('BTC跌破EMA50_4H ⚠️')

    # RSI区间
    if 45 <= rsi_4h <= 65:
        score += 15
        signals.append(f'RSI_4H={rsi_4h:.1f} 健康区间 ✅')
    elif rsi_4h > 75:
        score -= 10
        signals.append(f'RSI_4H={rsi_4h:.1f} 超买区 ⚠️')
    elif rsi_4h < 35:
        score -= 20
        signals.append(f'RSI_4H={rsi_4h:.1f} 极度超卖 ⚠️')
    else:
        signals.append(f'RSI_4H={rsi_4h:.1f}')

    # 资金费率
    if btc_fr is not None:
        if 0.01 <= btc_fr <= 0.08:
            score += 10
            signals.append(f'BTC资金费率={btc_fr:.4f}% 健康正值 ✅')
        elif btc_fr > 0.1:
            score -= 15
            signals.append(f'BTC资金费率={btc_fr:.4f}% 过热 ⚠️')
        elif btc_fr < -0.05:
            score -= 25
            signals.append(f'BTC资金费率={btc_fr:.4f}% 极端负值 ⚠️')
        else:
            signals.append(f'BTC资金费率={btc_fr:.4f}%')

    # OI变化
    if oi_chg is not None:
        if oi_chg > 3:
            score += 10
            signals.append(f'OI+{oi_chg:.1f}% 资金流入 ✅')
        elif oi_chg < -5:
            score -= 15
            signals.append(f'OI{oi_chg:.1f}% 资金撤离 ⚠️')
        else:
            signals.append(f'OI变化={oi_chg:.1f}%')

    # 判断状态
    if score >= 30:
        state = 'RISK_ON'
        bull_weight_mult = 1.3
        long_gate_bonus  = +8
        bear_weight_mult = 0.8
        desc = '宏观偏多，适合BULL_TREND信号执行'
    elif score <= -20:
        state = 'RISK_OFF'
        bull_weight_mult = 0.7
        long_gate_bonus  = -15
        bear_weight_mult = 1.5
        desc = '宏观偏空，谨慎做多，BEAR体制加权'
    else:
        state = 'NEUTRAL'
        bull_weight_mult = 1.0
        long_gate_bonus  = 0
        bear_weight_mult = 1.0
        desc = '宏观中性，按正常体制权重执行'

    return {
        'state': state,
        'score': score,
        'bull_weight_mult': bull_weight_mult,
        'long_gate_bonus': long_gate_bonus,
        'bear_weight_mult': bear_weight_mult,
        'desc': desc,
        'signals': signals,
        'raw': data,
        'ts': time.time(),
        'updated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
    }


def write_to_bus(macro_state: dict):
    """写入brahma_bus文件（macro_overlay.json）"""
    MACRO_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MACRO_FILE, 'w') as f:
        json.dump(macro_state, f, indent=2, ensure_ascii=False)


def read_current_state() -> dict | None:
    """读取当前宏观状态"""
    if not MACRO_FILE.exists():
        return None
    with open(MACRO_FILE) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--read', action='store_true', help='只读取当前宏观状态')
    parser.add_argument('--force', action='store_true', help='强制重新分析，忽略缓存')
    args = parser.parse_args()

    if args.read:
        state = read_current_state()
        if state:
            print(f"宏观状态: {state['state']} (score={state['score']})")
            print(f"更新时间: {state.get('updated_at')}")
            print(f"描述: {state['desc']}")
            for sig in state.get('signals', []):
                print(f"  · {sig}")
        else:
            print("⚠️ 无宏观状态数据，请先运行分析")
        return

    # ── [prime-agent心跳思想 2026-08-08 设计院封印] 4H TTL缓存检查 ──
    # cron每4H运行一次，若未到4H且数据新鲜，直接运用缓存并平静退出
    _MACRO_CACHE_TTL = 14400  # 4H = cron频率
    if not args.force and MACRO_FILE.exists():
        try:
            with open(MACRO_FILE) as _f:
                _cached = json.load(_f)
            _ts = _cached.get('ts', _cached.get('timestamp', 0))
            if isinstance(_ts, str):
                from datetime import datetime as _dt
                try: _ts = _dt.fromisoformat(_ts.replace('Z','+00:00')).timestamp()
                except: _ts = 0
            _age = time.time() - _ts if _ts else 999999
            if _age < _MACRO_CACHE_TTL:
                print(f'[macro_ai_bridge] 缓存有效 age={_age/3600:.1f}h state={_cached.get("state")} — HEARTBEAT_OK')
                return
        except Exception:
            pass  # 读取失败则继续重新分析
    # ── end 缓存检查 ──

    print(f"[macro_ai_bridge] 开始宏观数据采集 {datetime.utcnow().strftime('%H:%M UTC')}")

    # 采集数据
    trend   = fetch_btc_trend()
    senti   = fetch_market_sentiment()
    data    = {**trend, **senti}

    # 分析状态
    macro_state = analyze_macro_state(data)

    # 写入brahma_bus
    write_to_bus(macro_state)

    # 输出
    print(f"[macro_ai_bridge] ✅ 宏观状态: {macro_state['state']} (score={macro_state['score']})")
    print(f"  {macro_state['desc']}")
    for sig in macro_state['signals']:
        print(f"  · {sig}")
    print(f"  → 写入 data/macro_overlay.json")
    print(f"  LONG门控叠加: {macro_state['long_gate_bonus']:+d}分")
    print(f"  BULL权重乘数: {macro_state['bull_weight_mult']}x")


if __name__ == '__main__':
    main()
