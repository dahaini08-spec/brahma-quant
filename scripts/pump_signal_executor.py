"""
pump_signal_executor.py — 暴涨猎手独立信号通道
设计院架构重构 2026-06-30 | 苏摩授权

架构原则（铁律）：
  - 暴涨猎手信号 = PUMP_SIGNAL，独立于梵天TREND_SIGNAL
  - 不共享评分框架，不受梵天死穴约束
  - 独立参数体系：SL=ATR×2.0 TP=ATR×3.0 仓位1%
  - 独立执行队列：data/pump_signal_queue.jsonl

铁证参数（达摩院1600样本验证）：
  TIGHT<15% 7日胜率 97.5%
  RSI<30+TIGHT 93%胜率
  连续缩量13H+ 100%胜率（n=19）
  OOS 2026: 80.6% ✅

体制路由（独立于梵天死穴）：
  BEAR_TREND:     仓位1%  TP×0.8  （逆势妖，轻仓保守）
  BEAR_RECOVERY:  仓位3%  TP×2.0  （顺势，重仓激进）
  CHOP_MID:       仓位2%  TP×1.2
  BULL_TREND:     仓位2%  TP×1.5
"""

import sys, os, json, time, requests, hmac, hashlib
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from brahma_brain.brahma_bus import bus

# ── 路径 ──
QUEUE_PATH   = Path(__file__).parent.parent / 'data/pump_signal_queue.jsonl'
LOG_PATH     = Path(__file__).parent.parent / 'data/pump_signal_log.jsonl'
STATE_PATH   = Path(__file__).parent.parent / 'data/brahma_state.json'
RUNTIME_PATH = Path(__file__).parent.parent / 'data/dharma_runtime.json'

# ── 体制路由表（铁证封印）──
REGIME_PUMP_PARAMS = {
    'BEAR_TREND':     {'size_pct': 0.01, 'tp_mult': 0.8,  'sl_atr': 2.0, 'lev': 3},
    'BEAR_EARLY':     {'size_pct': 0.015,'tp_mult': 1.2,  'sl_atr': 2.0, 'lev': 3},
    'BEAR_RECOVERY':  {'size_pct': 0.03, 'tp_mult': 2.0,  'sl_atr': 2.0, 'lev': 5},
    'CHOP_MID':       {'size_pct': 0.02, 'tp_mult': 1.2,  'sl_atr': 2.5, 'lev': 3},
    'CHOP_HIGH_VOL':  {'size_pct': 0.015,'tp_mult': 1.0,  'sl_atr': 2.5, 'lev': 3},
    'BULL_TREND':     {'size_pct': 0.02, 'tp_mult': 1.5,  'sl_atr': 2.0, 'lev': 5},
    'BULL_EARLY':     {'size_pct': 0.02, 'tp_mult': 1.5,  'sl_atr': 2.0, 'lev': 5},
}
DEFAULT_PUMP_PARAMS = {'size_pct': 0.01, 'tp_mult': 1.0, 'sl_atr': 2.0, 'lev': 3}

# ── API ──
try:
    from scripts.system_config import API_KEY, API_SECRET, FAPI_BASE
except Exception:
    import importlib.util
    cfg_path = Path(__file__).parent / 'system_config.py'
    spec = importlib.util.spec_from_file_location('system_config', cfg_path)
    _cfg = importlib.util.module_from_spec(spec); spec.loader.exec_module(_cfg)
    API_KEY = getattr(_cfg, 'API_KEY', '')
    API_SECRET = getattr(_cfg, 'API_SECRET', '')
    FAPI_BASE = getattr(_cfg, 'FAPI_BASE', 'https://fapi.binance.com')


# ════════════════════════════════════════════════════════
# 一、暴涨猎手评分（独立评分系统，不调用brahma_core）
# ════════════════════════════════════════════════════════

def pump_hunter_score(sym: str) -> dict:
    """
    计算暴涨猎手评分（独立框架）
    返回：{'score': int, 'valid': bool, 'details': dict}
    评分满分：100分
    预警阈值：≥75分 = 🚨预警 | ≥85分 = 💣三级预警
    """
    try:
        k1h = bus.klines(sym, '1h', 200)  # 尽量取8天数据
        if not k1h or len(k1h) < 24:
            return {'score': 0, 'valid': False, 'reason': 'insufficient_data'}

        c = np.array([float(k[4]) for k in k1h])
        h = np.array([float(k[2]) for k in k1h])
        l = np.array([float(k[3]) for k in k1h])
        v = np.array([float(k[5]) for k in k1h])
        px = c[-1]

        # ── 1. TIGHT压缩（铁证最高权重）──
        n7d = min(168, len(h))
        tight7d = (np.max(h[-n7d:]) - np.min(l[-n7d:])) / np.min(l[-n7d:]) * 100
        tight8h  = (np.max(h[-8:])  - np.min(l[-8:]))  / np.min(l[-8:])  * 100

        # ── 2. RSI（超卖）──
        d = np.diff(c[-15:])
        g = np.where(d > 0, d, 0); lo = np.where(d < 0, -d, 0)
        ag = np.mean(g); al = np.mean(lo)
        rsi = 100 - 100 / (1 + ag / al) if al > 0 else 99

        # ── 3. 连续缩量小时数 ──
        shrink_h = 0
        for i in range(len(v) - 2, max(0, len(v) - 20), -1):
            if v[i] < v[i + 1]: shrink_h += 1
            else: break

        # ── 4. 量比（当前量/24H均量）──
        avg_vol = np.mean(v[-24:])
        vol_ratio = v[-1] / avg_vol if avg_vol > 0 else 1.0

        # ── 5. 价格趋势（泵前微跌更强）──
        trend_3d = (c[-1] / c[-72] - 1) * 100 if len(c) >= 72 else 0
        trend_lean = -5 <= trend_3d <= 0  # 微跌 = 最佳

        # ── 24H涨幅（排除已爆发）──
        chg24 = (c[-1] / c[-24] - 1) * 100 if len(c) >= 24 else 0
        if chg24 > 30:
            return {'score': 0, 'valid': False, 'reason': f'already_pumped_{chg24:.0f}pct'}

        # ── ATR（用于TP/SL计算）──
        tr = np.maximum(h[1:] - l[1:],
             np.maximum(np.abs(h[1:] - c[:-1]),
                        np.abs(l[1:] - c[:-1])))
        atr = np.mean(tr[-14:])

        # ── 评分计算 ──
        score = 0
        breakdown = {}

        # TIGHT7D（+40分，铁证核心）
        if tight7d < 15:
            breakdown['TIGHT_40'] = 40; score += 40
        elif tight7d < 20:
            breakdown['TIGHT_20'] = 20; score += 20
        elif tight7d < 30:
            breakdown['TIGHT_10'] = 10; score += 10

        # RSI超卖（+25分）
        if rsi < 30:
            breakdown['RSI_25'] = 25; score += 25
        elif rsi < 40:
            breakdown['RSI_15'] = 15; score += 15
        elif rsi < 50:
            breakdown['RSI_8'] = 8;   score += 8

        # 连续缩量（+20分）
        if shrink_h >= 13:
            breakdown['SHRINK_20'] = 20; score += 20  # 铁证100%胜率
        elif shrink_h >= 6:
            breakdown['SHRINK_12'] = 12; score += 12
        elif shrink_h >= 3:
            breakdown['SHRINK_6'] = 6;  score += 6

        # 量枯竭（+10分）
        if vol_ratio < 0.15:
            breakdown['VOL_EXHAUST_10'] = 10; score += 10
        elif vol_ratio < 0.3:
            breakdown['VOL_EXHAUST_5'] = 5;  score += 5

        # 微跌趋势（+5分）
        if trend_lean:
            breakdown['TREND_LEAN_5'] = 5; score += 5

        # ── 有效性判断 ──
        valid = score >= 75

        return {
            'score': score,
            'valid': valid,
            'symbol': sym,
            'direction': 'LONG',    # 暴涨猎手永远是LONG
            'signal_type': 'PUMP_SIGNAL',
            'tight7d': round(tight7d, 2),
            'tight8h': round(tight8h, 2),
            'rsi': round(rsi, 1),
            'shrink_h': shrink_h,
            'vol_ratio': round(vol_ratio, 3),
            'chg24': round(chg24, 2),
            'atr': round(atr, 6),
            'atr_pct': round(atr / px * 100, 2),
            'price': round(px, 6),
            'breakdown': breakdown,
            'ts': time.time(),
        }
    except Exception as e:
        return {'score': 0, 'valid': False, 'reason': f'error:{e}'}


# ════════════════════════════════════════════════════════
# 二、TP/SL计算（基于ATR，独立于梵天v4.0参数）
# ════════════════════════════════════════════════════════

def calc_pump_exit(px: float, atr: float, regime: str, direction: str = 'LONG') -> dict:
    """
    暴涨猎手出场参数（ATR动态，不使用梵天固定SL=2%）
    铁证依据：妖币波动率远高于普通趋势标的
    """
    params = REGIME_PUMP_PARAMS.get(regime, DEFAULT_PUMP_PARAMS)
    sl_atr = params['sl_atr']
    tp_mult = params['tp_mult']

    if direction == 'LONG':
        sl_price = round(px * (1 - atr * sl_atr / px), 6)
        sl_pct   = round(atr * sl_atr / px * 100, 2)
        tp1      = round(px * (1 + atr * sl_atr * tp_mult / px), 6)
        tp2      = round(px * (1 + atr * sl_atr * tp_mult * 2 / px), 6)
    else:  # SHORT（暴涨猎手不做空，但保留接口）
        sl_price = round(px * (1 + atr * sl_atr / px), 6)
        sl_pct   = round(atr * sl_atr / px * 100, 2)
        tp1      = round(px * (1 - atr * sl_atr * tp_mult / px), 6)
        tp2      = round(px * (1 - atr * sl_atr * tp_mult * 2 / px), 6)

    return {
        'stop_loss': sl_price,
        'sl_pct':    sl_pct,
        'tp1':       tp1,
        'tp2':       tp2,
        'rr':        round(tp_mult, 2),
        'leverage':  params['lev'],
        'size_pct':  params['size_pct'],
    }


# ════════════════════════════════════════════════════════
# 三、信号写入独立队列
# ════════════════════════════════════════════════════════

def emit_pump_signal(scan_result: dict, regime: str, nav_usdt: float = 130.0) -> dict | None:
    """
    将暴涨猎手扫描结果转化为可执行信号，写入独立队列
    """
    if not scan_result.get('valid'):
        return None

    sym   = scan_result['symbol']
    px    = scan_result['price']
    atr   = scan_result['atr']
    score = scan_result['score']

    exit_params = calc_pump_exit(px, atr, regime, 'LONG')

    # 仓位计算（基于NAV）
    size_pct = exit_params['size_pct']
    notional = round(nav_usdt * size_pct, 2)
    qty_raw  = notional * exit_params['leverage'] / px
    # 精度处理（按Binance合约精度）
    qty = round(qty_raw, 2)

    signal = {
        'signal_id':    f"PH_{sym}_{int(time.time())}",
        'signal_type':  'PUMP_SIGNAL',
        'symbol':       sym,
        'direction':    'LONG',
        'regime':       regime,
        'score':        score,
        'price':        px,
        'entry_lo':     round(px * 0.995, 6),
        'entry_hi':     round(px * 1.005, 6),
        'stop_loss':    exit_params['stop_loss'],
        'sl_pct':       exit_params['sl_pct'],
        'tp1':          exit_params['tp1'],
        'tp2':          exit_params['tp2'],
        'rr':           exit_params['rr'],
        'leverage':     exit_params['leverage'],
        'size_pct':     size_pct,
        'notional':     notional,
        'qty':          qty,
        'tight7d':      scan_result['tight7d'],
        'rsi':          scan_result['rsi'],
        'shrink_h':     scan_result['shrink_h'],
        'vol_ratio':    scan_result['vol_ratio'],
        'atr_pct':      scan_result['atr_pct'],
        'ts':           time.time(),
        'ts_iso':       datetime.now(timezone.utc).isoformat(),
        'status':       'PENDING',
        'result':       None,
    }

    # 写入队列
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, 'a') as f:
        f.write(json.dumps(signal, ensure_ascii=False) + '\n')

    return signal


# ════════════════════════════════════════════════════════
# 四、推送格式（独立于梵天信号推送）
# ════════════════════════════════════════════════════════

def format_pump_alert(signal: dict) -> str:
    """暴涨猎手独立推送格式"""
    score = signal['score']
    level = '💣 三级预警' if score >= 85 else '🚨 二级预警'
    regime = signal.get('regime', '?')
    regime_hint = {
        'BEAR_TREND':    '⚠️ 逆势妖币（轻仓1%）',
        'BEAR_RECOVERY': '🟢 顺势反弹（重仓3%）',
        'CHOP_MID':      '🟡 震荡妖币（标准仓2%）',
        'BULL_TREND':    '🚀 顺势爆发（标准仓2%）',
    }.get(regime, f'体制:{regime}')

    return f"""{level} 暴涨猎手预警
━━━━━━━━━━━━━━━━━━━━
标的：{signal['symbol']}
现价：${signal['price']:.5f}
评分：{score}/100
{regime_hint}

🔬 压缩指标：
  TIGHT7D = {signal['tight7d']:.1f}%（<15%=铁证）
  RSI      = {signal['rsi']:.0f}（<30=超卖）
  缩量持续 = {signal['shrink_h']}H
  量比     = {signal['vol_ratio']:.2f}x

📐 信号参数（独立通道）：
  入场区：${signal['entry_lo']:.5f} ~ ${signal['entry_hi']:.5f}
  止损：  ${signal['stop_loss']:.5f}（SL={signal['sl_pct']:.2f}%）
  TP1：  ${signal['tp1']:.5f}（RR={signal['rr']}x）
  TP2：  ${signal['tp2']:.5f}
  仓位：  NAV×{signal['size_pct']*100:.0f}% = ${signal['notional']:.1f} | {signal['leverage']}x
━━━━━━━━━━━━━━━━━━━━
⚡ PUMP_SIGNAL 独立通道 · 不受梵天死穴约束"""


# ════════════════════════════════════════════════════════
# 五、主扫描入口（供cron调用）
# ════════════════════════════════════════════════════════

def scan_and_emit(symbols: list[str], push: bool = True) -> list[dict]:
    """
    扫描候选标的，生成PUMP_SIGNAL
    供 pump-hunter cron 每15分钟调用
    """
    # 获取当前体制
    regime = 'BEAR_TREND'
    try:
        state = json.loads(STATE_PATH.read_text())
        regime = state.get('BTCUSDT', {}).get('regime', 'BEAR_TREND')
    except Exception:
        pass

    # 获取NAV
    nav = 130.0
    try:
        rt = json.loads(RUNTIME_PATH.read_text())
        nav = float(rt.get('nav_usdt', 130.0))
    except Exception:
        pass

    triggered = []
    for sym in symbols:
        result = pump_hunter_score(sym)
        if not result.get('valid'):
            continue

        signal = emit_pump_signal(result, regime, nav)
        if not signal:
            continue

        triggered.append(signal)
        print(f"[PumpHunter] {sym} score={result['score']} regime={regime} → PUMP_SIGNAL emitted")

        # 推送
        if push:
            try:
                from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
                msg = format_pump_alert(signal)
                import subprocess
                subprocess.run([
                    'openclaw', 'message', 'send',
                    '--channel', 'jarvis',
                    '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
                    '--message', msg
                ], capture_output=True)
            except Exception as e:
                print(f'[PumpHunter] push failed: {e}')

    return triggered


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='暴涨猎手独立信号通道')
    parser.add_argument('--scan', nargs='+', help='扫描指定标的')
    parser.add_argument('--score', help='只计算评分（不执行）')
    parser.add_argument('--no-push', action='store_true', help='不推送消息')
    args = parser.parse_args()

    if args.score:
        result = pump_hunter_score(args.score.upper() + 'USDT'
                                   if not args.score.upper().endswith('USDT')
                                   else args.score.upper())
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.scan:
        syms = [s.upper() + 'USDT' if not s.upper().endswith('USDT') else s.upper()
                for s in args.scan]
        results = scan_and_emit(syms, push=not args.no_push)
        print(f'触发信号数: {len(results)}')
        for r in results:
            print(f"  {r['symbol']} score={r['score']} SL={r['sl_pct']:.2f}% TP1={r['tp1']}")
    else:
        print('用法: python pump_signal_executor.py --score TAC')
        print('      python pump_signal_executor.py --scan TAC SYN HUSDT')
