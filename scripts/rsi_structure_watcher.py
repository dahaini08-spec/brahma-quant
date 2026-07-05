#!/usr/bin/env python3
"""
rsi_structure_watcher.py — 梵天信号v5.0 · 零成本守望层
设计院 6方辩论落地 · 2026-07-01 · 苏摩111批准

╔══════════════════════════════════════════════════════════════╗
║  职责：0 tokens纯脚本，监控7个市场结构事件                   ║
║  任一触发 → 写入 data/rsi_trigger_event.json                ║
║         → cron触发 brahma_scan_all BTC ETH（层2）           ║
║  静默条件满足 → 完全不写入，积分消耗=0                       ║
╠══════════════════════════════════════════════════════════════╣
║  触发事件：                                                  ║
║  E1: RSI_1H 从<50 穿越到 ≥62（反弹做空窗口）                ║
║  E2: RSI_1H 从>70 跌破 <65（超买回落）                      ║
║  E3: 价格突破48H高点（EMA确认）                              ║
║  E4: 价格跌破48H低点（破位信号）                             ║
║  E5: BB宽度从<0.8%扩张至>1.2%（压缩释放）                   ║
║  E6: 1H量比突然>2x（异常成交量）                             ║
║  E7: OI 1H变化>3%（资金大幅进出）                           ║
╠══════════════════════════════════════════════════════════════╣
║  静默条件（节省积分）：                                      ║
║  · RSI_1H在45~60区间 且 BB宽度<0.8% → 死水封印，跳过       ║
║  · 距上次触发<2H（冷却期，防重复消耗）                       ║
║  · 当前BB宽度<0.5%（极度压缩，方向未定）                     ║
╚══════════════════════════════════════════════════════════════╝

运行方式：openclaw cron every 5min（与btc_regime_watcher并行）
"""

import sys, os, json, time, requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
STATE_FILE  = BASE / 'data' / 'rsi_watcher_state.json'
TRIGGER_FILE = BASE / 'data' / 'rsi_trigger_event.json'
FAPI = 'https://fapi.binance.com'

SYMBOLS = [
    # 主力（趋势锚点）
    'BTCUSDT', 'ETHUSDT',
    # MacroGate可通过的BULL_TREND标的（price>EMA200_1D）
    'NEARUSDT', 'HYPEUSDT', 'JTOUSDT', 'SYNUSDT',
    'BEATUSDT', 'BASUSDT', 'TACUSDT',
]  # v5.2 2026-07-03: 扩展至7个小币（MacroGate可通过）
COOLDOWN_SECONDS = 7200   # 2H冷却
SILENT_RSI_LOW   = 45.0
SILENT_RSI_HIGH  = 60.0
SILENT_BB_MAX    = 0.80   # BB宽度<0.8%时死水封印
EXTREME_BB_MIN   = 0.50   # BB宽度<0.5%极度压缩，方向未定

# 触发阈值
RSI_CROSS_UP_FROM  = 50.0   # E1: 从<50
RSI_CROSS_UP_TO    = 62.0   # E1: 穿越到≥62
RSI_CROSS_DOWN_FROM= 70.0   # E2: 从>70
RSI_CROSS_DOWN_TO  = 65.0   # E2: 跌破<65
BB_EXPAND_FROM     = 0.80   # E5: 从<0.8%
BB_EXPAND_TO       = 1.20   # E5: 扩张到>1.2%
VOL_SURGE_RATIO    = 2.0    # E6: 量比>2x
OI_CHANGE_PCT      = 3.0    # E7: OI 1H变化>3%


def _fetch(url, timeout=8):
    try:
        r = requests.get(url, timeout=timeout)
        return r.json()
    except Exception:
        return None


def get_market_data(sym):
    """拉取1H K线 + OI，计算所有指标"""
    try:
        # 1H K线 48根
        kl = _fetch(f'{FAPI}/fapi/v1/klines?symbol={sym}&interval=1h&limit=50')
        if not kl or len(kl) < 20:
            return None

        closes = [float(k[4]) for k in kl]
        highs  = [float(k[2]) for k in kl]
        lows   = [float(k[3]) for k in kl]
        vols   = [float(k[5]) for k in kl]

        px = closes[-1]

        # RSI_1H
        n = 14
        c = closes[-(n+2):]
        gains  = [max(c[i]-c[i-1], 0) for i in range(1, len(c))]
        losses = [max(c[i-1]-c[i], 0) for i in range(1, len(c))]
        ag = sum(gains[-n:]) / n
        al = sum(losses[-n:]) / n
        rsi_1h = round(100 - 100/(1+ag/al), 1) if al > 0 else 100.0

        # 布林带宽度%
        import statistics
        ma20  = sum(closes[-20:]) / 20
        std20 = statistics.stdev(closes[-20:])
        bb_width = std20 * 2 / ma20 * 100

        # 量比（当前1H vs 过去24H均量）
        vol_ratio = vols[-1] / (sum(vols[-25:-1]) / 24) if sum(vols[-25:-1]) > 0 else 1.0

        # 48H高低点
        r48h = max(highs[-48:]) if len(highs) >= 48 else max(highs)
        s48h = min(lows[-48:])  if len(lows)  >= 48 else min(lows)

        # EMA20_1H
        ema20 = sum(closes[-20:]) / 20

        # OI 1H变化
        oi_data = _fetch(f'{FAPI}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=3')
        oi_chg_1h = 0.0
        if oi_data and len(oi_data) >= 2:
            v_prev = float(oi_data[-2].get('sumOpenInterestValue', 0))
            v_curr = float(oi_data[-1].get('sumOpenInterestValue', 0))
            if v_prev > 0:
                oi_chg_1h = (v_curr - v_prev) / v_prev * 100

        return dict(
            sym=sym, px=px,
            rsi_1h=rsi_1h,
            bb_width=round(bb_width, 3),
            vol_ratio=round(vol_ratio, 2),
            r48h=r48h, s48h=s48h, ema20=ema20,
            oi_chg_1h=round(oi_chg_1h, 2),
        )
    except Exception as e:
        print(f'[RSI-Watcher] {sym} 数据拉取失败: {e}')
        return None


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def check_cooldown(state, sym):
    """检查冷却期，True=在冷却中，跳过"""
    last_ts = state.get(f'{sym}_last_trigger', 0)
    elapsed = time.time() - last_ts
    return elapsed < COOLDOWN_SECONDS


def detect_events(data, prev_state, sym):
    """
    检测7个触发事件，返回触发的事件列表
    """
    px       = data['px']
    rsi      = data['rsi_1h']
    bb       = data['bb_width']
    vol_r    = data['vol_ratio']
    r48h     = data['r48h']
    s48h     = data['s48h']
    ema20    = data['ema20']
    oi_chg   = data['oi_chg_1h']

    prev_rsi = prev_state.get(f'{sym}_rsi', rsi)
    prev_bb  = prev_state.get(f'{sym}_bb',  bb)

    events = []

    # ── 静默门控（优先判断，节省积分） ──────────────────────────
    # 死水封印：RSI中性区 + BB极度压缩
    if SILENT_RSI_LOW <= rsi <= SILENT_RSI_HIGH and bb < SILENT_BB_MAX:
        return [], 'SILENT_DEAD_WATER'

    # BB极度压缩（方向未定，任何触发都是噪音）
    if bb < EXTREME_BB_MIN:
        return [], 'SILENT_BB_EXTREME_COMPRESS'

    # ── E1: RSI_1H 从<50 穿越到 ≥62（反弹做空窗口） ──────────
    if prev_rsi < RSI_CROSS_UP_FROM and rsi >= RSI_CROSS_UP_TO:
        if px < ema20:  # 价格仍在EMA20下方，结构偏空
            events.append({
                'event': 'E1_RSI_CROSS_UP_SHORT_WINDOW',
                'desc': f'RSI_1H {prev_rsi:.1f}→{rsi:.1f} 突破{RSI_CROSS_UP_TO}，价格仍<EMA20，做空窗口打开',
                'priority': 'HIGH',
            })

    # ── E2: RSI_1H 从>70 跌破 <65（超买回落） ──────────────────
    if prev_rsi > RSI_CROSS_DOWN_FROM and rsi < RSI_CROSS_DOWN_TO:
        events.append({
            'event': 'E2_RSI_OVERBOUGHT_PULLBACK',
            'desc': f'RSI_1H {prev_rsi:.1f}→{rsi:.1f} 超买回落破{RSI_CROSS_DOWN_TO}，做空确认',
            'priority': 'HIGH',
        })

    # ── E3: 价格突破48H高点 ─────────────────────────────────────
    if px > r48h * 1.001:  # 突破0.1%确认
        events.append({
            'event': 'E3_PRICE_BREAK_48H_HIGH',
            'desc': f'价格${px:,.2f}突破48H高点${r48h:,.2f}(+{(px/r48h-1)*100:.2f}%)',
            'priority': 'MEDIUM',
        })

    # ── E4: 价格跌破48H低点 ─────────────────────────────────────
    if px < s48h * 0.999:  # 跌破0.1%确认
        events.append({
            'event': 'E4_PRICE_BREAK_48H_LOW',
            'desc': f'价格${px:,.2f}跌破48H低点${s48h:,.2f}({(px/s48h-1)*100:.2f}%)',
            'priority': 'HIGH',
        })

    # ── E5: BB宽度从<0.8%扩张至>1.2%（压缩释放） ───────────────
    if prev_bb < BB_EXPAND_FROM and bb > BB_EXPAND_TO:
        events.append({
            'event': 'E5_BB_EXPANSION',
            'desc': f'BB宽度 {prev_bb:.2f}%→{bb:.2f}% 压缩释放，方向即将选择',
            'priority': 'MEDIUM',
        })

    # ── E6: 1H量比突然>2x（异常成交量） ─────────────────────────
    if vol_r > VOL_SURGE_RATIO:
        events.append({
            'event': 'E6_VOLUME_SURGE',
            'desc': f'1H量比{vol_r:.1f}x 异常放量，结构可能变化',
            'priority': 'MEDIUM',
        })

    # ── E7: OI 1H变化>3%（资金大幅进出） ────────────────────────
    if abs(oi_chg) > OI_CHANGE_PCT:
        direction = '增仓' if oi_chg > 0 else '减仓'
        events.append({
            'event': 'E7_OI_SURGE',
            'desc': f'OI 1H变化{oi_chg:+.1f}% 资金{direction}，注意方向',
            'priority': 'MEDIUM',
        })

    # ── E8/E9: ETH 价格阈值告警（替代 eth-alert cron，0 tokens） ──
    # 原 eth-alert-1773 / eth-alert-1745 逻辑迁移至此（2026-07-05 苏摩111授权）
    if sym == 'ETHUSDT':
        if px >= 1773:
            events.append({
                'event': 'E8_ETH_BREAK_1773',
                'desc': f'🟢 ETH突破1773 EMA20_1H！当前${px:.2f}，短线反弹确认，WATCH状态',
                'priority': 'HIGH',
            })
        elif px <= 1745:
            events.append({
                'event': 'E9_ETH_BREAK_1745',
                'desc': f'🔴 ETH跌破1745三重支撑告急！当前${px:.2f}，EMA50_1H+BB下轨同时破位，加速下行警报',
                'priority': 'HIGH',
            })

    return events, 'ACTIVE' if events else 'NO_EVENT'


def write_trigger(sym, events, data):
    """写入触发事件文件（供scan_all读取）+ 高优先级事件推送Jarvis"""
    try:
        existing = {}
        if TRIGGER_FILE.exists():
            try:
                existing = json.loads(TRIGGER_FILE.read_text())
            except Exception:
                existing = {}

        existing[sym] = {
            'ts': time.time(),
            'ts_iso': datetime.now(tz=timezone.utc).isoformat(),
            'symbol': sym,
            'px': data['px'],
            'rsi_1h': data['rsi_1h'],
            'bb_width': data['bb_width'],
            'events': events,
            'high_priority': any(e['priority'] == 'HIGH' for e in events),
        }
        TRIGGER_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False))

        # ── [设计院 2026-07-05] 高优先级事件直推Jarvis ──────────────────
        high_events = [e for e in events if e.get('priority') in ('HIGH', 'P0', 'P1')]
        if high_events:
            try:
                import subprocess as _sp
                from scripts.system_config import JARVIS_TARGET
                ev_lines = '\n'.join([f"  [{e['priority']}] {e['event']}: {e['desc']}" for e in high_events])
                msg = (
                    f"🔔 RSI结构事件 · {sym}\n"
                    f"价格: ${data['px']:,.2f} | RSI_1H={data['rsi_1h']:.1f} | BB={data['bb_width']:.2f}%\n"
                    f"{ev_lines}\n"
                    f"→ 梵天扫描链已启动"
                )
                _sp.Popen(
                    ['openclaw', 'message', 'send', '--to', JARVIS_TARGET, '--channel', 'jarvis', '--message', msg],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
                )
                print(f'[RSI-Watcher] 📤 Jarvis推送: {sym} {len(high_events)}个高优先级事件')
            except Exception as _pe:
                print(f'[RSI-Watcher] 推送失败(非致命): {_pe}')
        # ────────────────────────────────────────────────────────────────
        return True
    except Exception as e:
        print(f'[RSI-Watcher] 写入trigger失败: {e}')
        return False


def run():
    now_str = datetime.now(tz=timezone.utc).strftime('%H:%M UTC')
    state = load_state()
    triggered_syms = []
    silent_syms = []

    for sym in SYMBOLS:
        # 冷却期检查
        if check_cooldown(state, sym):
            last_ts = state.get(f'{sym}_last_trigger', 0)
            remaining = int((COOLDOWN_SECONDS - (time.time() - last_ts)) / 60)
            print(f'[RSI-Watcher] {sym} 冷却中 剩余{remaining}分钟')
            continue

        data = get_market_data(sym)
        if not data:
            continue

        events, status = detect_events(data, state, sym)

        # 更新状态（RSI/BB记录）
        state[f'{sym}_rsi'] = data['rsi_1h']
        state[f'{sym}_bb']  = data['bb_width']

        if status.startswith('SILENT'):
            silent_syms.append(f"{sym}({status})")
            print(f'[RSI-Watcher] {sym} 静默 → {status} RSI={data["rsi_1h"]:.1f} BB={data["bb_width"]:.2f}%')
        elif events:
            # 有触发事件
            write_trigger(sym, events, data)
            state[f'{sym}_last_trigger'] = time.time()
            triggered_syms.append(sym)
            for ev in events:
                print(f'[RSI-Watcher] 🔔 {sym} [{ev["priority"]}] {ev["event"]}: {ev["desc"]}')
        else:
            print(f'[RSI-Watcher] {sym} 无事件 RSI={data["rsi_1h"]:.1f} BB={data["bb_width"]:.2f}% Vol={data["vol_ratio"]:.1f}x OI={data["oi_chg_1h"]:+.1f}%')

    save_state(state)

    if triggered_syms:
        print(f'[RSI-Watcher] ✅ 触发事件: {triggered_syms} → 扫描+执行链路已启动')
        import subprocess
        # 层关1事件触发后：扫描完成即触发auto_executor（缩短延迟）
        # ulimit限制单条Python链内存上限（防止OOM）
        scan_cmd = (
            f'cd {BASE} && '
            f'ulimit -v 1048576 2>/dev/null; '
            f'python3 scripts/market_screener.py && '
            f'python3 scripts/brahma_scan_all.py --candidates && '
            f'python3 scripts/auto_executor.py 2>&1 | tail -5'
        )
        try:
            # ── 防积压：检查是否已有扫描链在运行 ──────────────────
            import os, glob
            lock_file = BASE / 'data/.rsi_scan_chain.lock'
            if lock_file.exists():
                lock_age = time.time() - lock_file.stat().st_mtime
                if lock_age < 120:   # v5.2: 2min内认为上一轮还在跑（原4min，gateway重启导致残留）
                    print(f'[RSI-Watcher] ⚠️ 上一轮扫描链仍在运行({lock_age:.0f}s)，跳过')
                    return
                else:
                    lock_file.unlink(missing_ok=True)  # 超时残留锁，强制清除
            lock_file.write_text(str(os.getpid()))
            proc = subprocess.Popen(scan_cmd, shell=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print(f'[RSI-Watcher] 🚀 扫描+执行已启动 PID={proc.pid}')
            # 非阻塞：让进程在后台运行，定时清理锁
            def _cleanup_lock(p, lf):
                try:
                    p.wait(timeout=240)  # 最多等4min
                except Exception:
                    p.kill()
                finally:
                    try: lf.unlink(missing_ok=True)
                    except: pass
            import threading
            threading.Thread(target=_cleanup_lock, args=(proc, lock_file), daemon=True).start()
        except Exception as e:
            try: Path(BASE / 'data/.rsi_scan_chain.lock').unlink(missing_ok=True)
            except: pass
            print(f'[RSI-Watcher] 链路启动失败: {e}')
    elif not silent_syms:
        print(f'[RSI-Watcher] {now_str} 无触发，市场等待中')

    if not triggered_syms and not silent_syms:
        print('HEARTBEAT_OK')


if __name__ == '__main__':
    run()
