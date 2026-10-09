#!/usr/bin/env python3
"""
🔍 梵天Grim前置门控 brahma_grim_gate.py
2026-10-09 苏摩111封印

Loop Engineer 融合：Grim决策树
分析请求前先明确苏摩意图，针对性执行，节省Token ~40%

触发场景：
  - 苏摩说"分析BTC/ETH"时，先问意图
  - 根据意图路由到最小必要分析链路

意图 → 路由：
  入场   → 完整主链 + 重点VIP
  持仓   → M5仓位反向检查
  宏观   → D8宏观+FR+LSR
  快速   → 只拉价格+RSI+liq（30秒内完成）
  全链   → 完整94维分析
"""
import json, pathlib, sys, os, subprocess, time, ssl, urllib.request

BASE  = pathlib.Path(__file__).parent.parent
DATA  = BASE / 'data'
JARVIS_USER   = os.getenv('JARVIS_USER_ID', '73295708')
JARVIS_THREAD = os.getenv('JARVIS_THREAD_ID', '01a0d79b-fea4-71b1-9f2a-c02a9844b4ed')
CHANNEL = 'jarvis'

ctx = ssl.create_default_context()

def fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())

def push(msg):
    target = f'{JARVIS_USER}:thread:{JARVIS_THREAD}'
    cmd = ['openclaw', 'message', 'send', '--channel', CHANNEL, '-t', target, '-m', msg]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
        print(f'[PUSH OK] {r.stdout.strip()[:60]}')
    except Exception as e:
        print(f'[PUSH ERR] {e}', file=sys.stderr)


def load_positions():
    try:
        return json.loads((DATA / 'active_positions.json').read_text())
    except:
        return {}


def quick_snapshot(symbols):
    """快速模式：30秒内输出价格+RSI+liq"""
    lines = [f'⚡ 梵天快速看盘 {time.strftime("%H:%M UTC")}']
    for sym in symbols:
        try:
            price = float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT')['price'])
            st    = json.loads((DATA / f'brahma_state_{sym.lower()}.json').read_text())
            rsi4h = float(st.get('rsi_4h', 50) or 50)
            rsi1h = float(st.get('rsi_1h', 50) or 50)
            liq_s = float(st.get('liq_short', 0) or 0)
            liq_l = float(st.get('liq_long',  0) or 0)
            atr1h = float(st.get('atr_1h', 0) or 0)
            lines.append(
                f'\n{sym} ${price:,.2f}\n'
                f'  RSI 4H={rsi4h:.1f} 1H={rsi1h:.1f}\n'
                f'  止损墙 ${liq_s:,.0f}(+{(liq_s-price)/price*100:.1f}%) '
                f'支撑池 ${liq_l:,.0f}(-{(price-liq_l)/price*100:.1f}%)\n'
                f'  ATR1H=${atr1h:.0f}'
            )
        except Exception as e:
            lines.append(f'\n{sym}: 数据拉取失败 {e}')

    lines.append('\n🌿 姓赵不宣 | 快速看盘')
    push('\n'.join(lines))


def position_check(symbols):
    """持仓评估模式：只检查现有仓位的风险信号"""
    positions = load_positions()
    if not positions:
        push('📋 当前无活跃仓位记录\n请先更新 data/active_positions.json')
        return

    lines = [f'📊 梵天持仓风险检查 {time.strftime("%H:%M UTC")}']
    for key, pos in positions.items():
        sym = pos.get('symbol', key.split('_')[0])
        if sym not in [s.upper() for s in symbols]:
            continue
        try:
            price = float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT')['price'])
            entry  = float(pos.get('entry', price))
            sl     = float(pos.get('sl', 0))
            direction = pos.get('direction', 'LONG')
            pnl = (price - entry) / entry * 100 if direction == 'LONG' else (entry - price) / entry * 100
            sl_dist = abs(price - sl) / price * 100 if sl else 0
            risk = '🔴 危险' if sl_dist < 1.0 else ('⚠️ 注意' if sl_dist < 2.0 else '✅ 安全')
            lines.append(
                f'\n{sym} {direction} 入场${entry:,.2f}\n'
                f'  当前 ${price:,.2f} | 浮{pnl:+.2f}%\n'
                f'  SL ${sl:,.2f} (距{sl_dist:.2f}%) {risk}'
            )
        except Exception as e:
            lines.append(f'\n{sym}: {e}')

    lines.append('\n🌿 姓赵不宣 | 持仓检查')
    push('\n'.join(lines))


def macro_snapshot(symbols):
    """宏观模式：只看FR/LSR/OI大方向"""
    lines = [f'🌍 梵天宏观快览 {time.strftime("%H:%M UTC")}']
    for sym in symbols:
        try:
            fr   = fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}USDT&limit=1')
            lsr  = fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}USDT&period=1h&limit=1')
            oi   = fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}USDT&period=1h&limit=3')
            price = float(fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}USDT')['price'])
            fr_val  = float(fr[-1]['fundingRate']) * 100
            lsr_val = float(lsr[0]['longAccount']) * 100
            oi_v    = [float(o['sumOpenInterest']) for o in oi]
            oi_chg  = (oi_v[-1] - oi_v[0]) / oi_v[0] * 100 if len(oi_v) > 1 else 0
            fr_sig  = '多头付费⚠️' if fr_val > 0.003 else ('空头付费✅' if fr_val < -0.003 else '中性')
            oi_sig  = 'BUILD✅' if oi_chg > 0.3 else ('UNWIND⚠️' if oi_chg < -0.5 else 'NEUTRAL')
            hunt_dist = (65.0 - lsr_val) if sym == 'BTC' else (77.0 - lsr_val)
            hunt_sig = '🚨触发!' if hunt_dist <= 0 else (f'⚠️差{hunt_dist:.1f}%' if hunt_dist < 1.5 else '安全')
            lines.append(
                f'\n{sym} ${price:,.2f}\n'
                f'  FR={fr_val:+.4f}% {fr_sig}\n'
                f'  散户LSR={lsr_val:.1f}% 猎杀门槛{hunt_sig}\n'
                f'  OI变化={oi_chg:+.2f}% {oi_sig}'
            )
        except Exception as e:
            lines.append(f'\n{sym}: {e}')
    lines.append('\n🌿 姓赵不宣 | 宏观速览')
    push('\n'.join(lines))


def full_analysis(symbols):
    """完整主链分析"""
    sym_str = ' '.join(s.upper() for s in symbols)
    result = subprocess.run(
        ['python3', 'scripts/brahma_manual_analysis.py', '--symbols'] + [s.upper() for s in symbols],
        cwd=str(BASE), timeout=180, capture_output=True, text=True
    )
    if result.returncode != 0:
        push(f'❌ 主链分析失败 RC={result.returncode}\n{result.stderr[:200]}')
        print(f'[ERR] RC={result.returncode}', file=sys.stderr)
    else:
        print(f'[OK] 主链分析完成 {sym_str}')


# ── Grim决策树入口 ─────────────────────────────────
def run_grim(intent, symbols):
    """
    intent: quick / position / macro / entry / full
    """
    syms = [s.upper() for s in symbols]
    print(f'[Grim] intent={intent} symbols={syms}')

    if intent == 'quick':
        quick_snapshot(syms)
    elif intent == 'position':
        position_check(syms)
    elif intent == 'macro':
        macro_snapshot(syms)
    elif intent in ('entry', 'full'):
        full_analysis(syms)
    else:
        # 默认：推送意图选择提示
        msg = (
            f'🔍 梵天Grim门控\n'
            f'分析 {" ".join(syms)} — 请选择分析目的：\n\n'
            f'  1️⃣ quick   — 30秒快速看盘\n'
            f'  2️⃣ macro   — 宏观速览(FR/LSR/OI)\n'
            f'  3️⃣ position — 持仓风险检查\n'
            f'  4️⃣ entry   — 完整入场分析(主链)\n'
            f'  5️⃣ full    — 94维全链路分析\n\n'
            f'回复：分析 BTC quick / 分析 ETH entry 等\n'
            f'🌿 姓赵不宣 | 明确意图，精准输出'
        )
        push(msg)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天Grim前置门控')
    parser.add_argument('--intent',  default='', help='quick/macro/position/entry/full')
    parser.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    args = parser.parse_args()
    run_grim(args.intent or 'prompt', args.symbols)
