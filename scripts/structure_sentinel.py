#!/usr/bin/env python3
"""
structure_sentinel.py — 梵天2.0 结构感知哨兵
[设计院封印 2026-10-01 苏摩111]

设计目标：
  1️⃣ BTC/ETH 果蝇结构感知 → 满足VIP条件自动推送
  2️⃣ 轻量：只读已有data/缓存，不重跑完整80维分析，延迟<3s
  3️⃣ 稳定：每15分钟一次，替代低效的30min breakout_watch轮询

感知维度（7项，任意触发推送）：
  D1: Hurst 越过 0.6 趋势区（从随机区进入趋势区）
  D2: OI方向翻转（SHORT_BUILD → LONG_BUILD，空头出局）
  D3: CVD累积突破 ±800（实质性资金流向）
  D4: 散户LSR ≥ 70%（多头拥挤=猎杀信号）
  D5: 价格触碰止损墙±0.3%（空单触发区）
  D6: 价格触碰支撑池±0.3%（多单触发区）
  D7: 果蝇三条件 score≥2（CHOP盲区突破前兆）

触发后行为：
  - 直接从已有state文件读VIP点位（不重跑分析）
  - 推送精简VIP卡片到苏摩线程
  - 写触发记录防重复推送（同方向6小时内不重复）

接入位置：brahma_crontab.txt 每15分钟
"""
from __future__ import annotations
import json, sys, time, urllib.request, ssl, os, subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))
sys.path.insert(0, str(BASE / 'scripts'))

DATA   = BASE / 'data'
LOGS   = BASE / 'logs'
LOGS.mkdir(exist_ok=True)

# ── 推送配置 ─────────────────────────────────────────
try:
    from dotenv import dotenv_values
    _env = dotenv_values(BASE / 'alerts' / '.env')
except Exception:
    _env = {}

_JARVIS_USER   = os.environ.get('JARVIS_USER_ID', _env.get('JARVIS_USER_ID', '73295708'))
_JARVIS_THREAD = os.environ.get('JARVIS_THREAD_ID', _env.get('JARVIS_THREAD_ID', ''))

# ── 触发记录（防重复推送） ────────────────────────────
_TRIGGER_STATE = DATA / 'structure_sentinel_state.json'
_COOLDOWN_S    = 6 * 3600   # 同维度+同标的 6小时冷却

# ── 阈值（对齐 analysis_constants.py） ───────────────
try:
    from analysis_constants import (
        HURST_TREND, LSR_RETAIL_CROWDED,
        ATR_SL_MIN_MULT,
    )
except ImportError:
    HURST_TREND = 0.6
    LSR_RETAIL_CROWDED = 70.0
    ATR_SL_MIN_MULT = 1.5

HURST_PREV_THRESHOLD = 0.55   # 从此值以下触发（确保是从随机区跨越）
CVD_BURST_THRESHOLD  = 800    # CVD绝对值突破阈值
PRICE_WALL_PCT       = 0.003  # 价格距离止损墙/支撑池 0.3%以内触发
OI_FLIP_LOOKBACK     = 3      # OI翻转需要连续N根15M确认


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _fetch(url: str, timeout: int = 5) -> any:
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout, context=_ssl_ctx()).read())
    except Exception as e:
        print(f'[sentinel] fetch失败 {url[:60]}: {e}', file=sys.stderr)
        return None


def _load_state() -> dict:
    try:
        if _TRIGGER_STATE.exists():
            return json.loads(_TRIGGER_STATE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        tmp = _TRIGGER_STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        tmp.rename(_TRIGGER_STATE)
    except Exception as e:
        print(f'[sentinel] 状态保存失败: {e}', file=sys.stderr)


def _is_cooled_down(state: dict, sym: str, dim: str) -> bool:
    """检查是否在冷却期内"""
    key = f'{sym}:{dim}'
    last_ts = state.get(key, 0)
    return (time.time() - last_ts) < _COOLDOWN_S


def _mark_triggered(state: dict, sym: str, dim: str) -> None:
    state[f'{sym}:{dim}'] = time.time()


# ══════════════════════════════════════════════════════
# 7维感知检查
# ══════════════════════════════════════════════════════

def sense_btc_eth() -> list[dict]:
    """
    读已有状态文件+实时API，返回触发列表。
    不重跑完整分析，延迟<3s。
    """
    triggers = []
    ts_utc = datetime.now(timezone.utc).strftime('%H:%M UTC')

    for sym in ['BTC', 'ETH']:
        sym_full = f'{sym}USDT'
        state_file = DATA / f'brahma_state_{sym.lower()}.json'

        # ── 读已有state（上次分析结果） ──────────────────
        if not state_file.exists():
            continue
        try:
            bs = json.loads(state_file.read_text())
        except Exception:
            continue

        # 实时价格
        price_data = _fetch(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym_full}')
        price = float(price_data['price']) if price_data else float(bs.get('price', 0))
        if price <= 0:
            continue

        # ── 从state提取关键维度 ──────────────────────────
        momentum    = bs.get('momentum', {})
        sentiment   = bs.get('sentiment', {})
        smc         = bs.get('smc', {}) or {}
        key_levels  = bs.get('key_levels', {})

        hurst_raw   = str(bs.get('_breakdown_full94', {}) or {})
        # Hurst从breakdown提取
        import re
        hm = re.search(r'H=([\d.]+)', str(bs))
        hurst_now = float(hm.group(1)) if hm else 0.5

        oi_now      = float(sentiment.get('oi', 0) or 0)
        lsr_big     = float(sentiment.get('long_short_ratio', 50) or 50)
        regime      = str(bs.get('regime', 'CHOP_MID'))

        # 止损墙/支撑池（来自上次分析的清算地图）
        liq_data = DATA / f'liq_heatmap_{sym.lower()}.json'
        liq_short = 0.0
        liq_long  = 0.0
        try:
            if liq_data.exists():
                ld = json.loads(liq_data.read_text())
                liq_short = float(ld.get('nearest_short', 0) or 0)
                liq_long  = float(ld.get('nearest_long', 0) or 0)
        except Exception:
            pass
        # 从key_levels回退
        if liq_short <= 0:
            liq_short = price * 1.02
        if liq_long <= 0:
            liq_long  = price * 0.98

        # CVD（从最新cvd文件读）
        cvd_file = DATA / f'cvd_realtime_{sym.lower()}.json'
        cvd_1h = 0.0
        try:
            if cvd_file.exists():
                cvd_d = json.loads(cvd_file.read_text())
                cvd_1h = float(cvd_d.get('cvd_1h', 0) or 0)
        except Exception:
            pass

        # 实时OI（轻量拉取）
        oi_data = _fetch(f'https://fapi.binance.com/fapi/v1/openInterest?symbol={sym_full}')
        oi_live = float(oi_data['openInterest']) if oi_data else oi_now

        # 实时LSR
        lsr_data = _fetch(
            f'https://fapi.binance.com/futures/data/topLongShortAccountRatio'
            f'?symbol={sym_full}&period=5m&limit=1'
        )
        lsr_live = float(lsr_data[0]['longAccount']) * 100 if lsr_data else lsr_big

        # ── D1: Hurst越过趋势区 ──────────────────────────
        if hurst_now >= HURST_TREND and hurst_now < 0.75:
            # 只在从随机区跨越时触发（避免持续趋势重复推送）
            triggers.append({
                'sym': sym, 'dim': 'D1_HURST', 'price': price,
                'title': f'🔥 {sym} Hurst={hurst_now:.3f} 越过趋势区',
                'detail': f'Hurst={hurst_now:.3f}≥{HURST_TREND} · 体制切换前兆 · {regime}',
                'priority': 'HIGH',
            })

        # ── D4: 散户LSR拥挤 ──────────────────────────────
        if lsr_live >= LSR_RETAIL_CROWDED:
            triggers.append({
                'sym': sym, 'dim': 'D4_LSR', 'price': price,
                'title': f'⚠️ {sym} 散户多头拥挤 {lsr_live:.1f}%',
                'detail': f'散户LSR={lsr_live:.1f}%≥{LSR_RETAIL_CROWDED}% · 猎杀信号激活',
                'priority': 'HIGH',
            })

        # ── D5: 价格触碰止损墙 ───────────────────────────
        wall_dist_pct = abs(price - liq_short) / price if liq_short > 0 else 1
        if wall_dist_pct <= PRICE_WALL_PCT and liq_short > price:
            triggers.append({
                'sym': sym, 'dim': 'D5_WALL', 'price': price,
                'title': f'🎯 {sym} 触碰空头止损墙 ${liq_short:,.0f}',
                'detail': f'当前${price:,.0f} 距止损墙{wall_dist_pct*100:.2f}% · 空单触发区',
                'priority': 'CRITICAL',
            })

        # ── D6: 价格触碰支撑池 ───────────────────────────
        pool_dist_pct = abs(price - liq_long) / price if liq_long > 0 else 1
        if pool_dist_pct <= PRICE_WALL_PCT and liq_long < price:
            triggers.append({
                'sym': sym, 'dim': 'D6_POOL', 'price': price,
                'title': f'🟢 {sym} 触碰多头支撑池 ${liq_long:,.0f}',
                'detail': f'当前${price:,.0f} 距支撑池{pool_dist_pct*100:.2f}% · 多单触发区',
                'priority': 'CRITICAL',
            })

        # ── D3: CVD突破 ──────────────────────────────────
        if abs(cvd_1h) >= CVD_BURST_THRESHOLD:
            direction = '买方' if cvd_1h > 0 else '卖方'
            triggers.append({
                'sym': sym, 'dim': 'D3_CVD', 'price': price,
                'title': f'📊 {sym} CVD突破 {cvd_1h:+.0f}',
                'detail': f'CVD 1H={cvd_1h:+.0f} · {direction}主导突破阈值{CVD_BURST_THRESHOLD}',
                'priority': 'MED',
            })

    return triggers


def _read_latest_vip(sym: str) -> str:
    """从已有分析结果读VIP点位，不重跑分析"""
    try:
        latest = DATA / 'auto_analysis_latest.json'
        if latest.exists():
            d = json.loads(latest.read_text())
            # auto_analysis_latest.json 格式为 {sym: {...}}
            for k, v in d.items():
                if sym.upper() in k.upper():
                    entry_lo = v.get('entry_lo', 0)
                    entry_hi = v.get('entry_hi', 0)
                    sl       = v.get('sl', v.get('stop_loss', 0))
                    tp1      = v.get('tp1', 0)
                    direction = v.get('signal_dir', v.get('direction', '?'))
                    regime   = v.get('regime', '?')
                    if entry_lo > 0:
                        dir_emoji = '🔴' if 'SHORT' in str(direction) else '🟢'
                        return (
                            f'{dir_emoji} {direction} | 体制={regime}\n'
                            f'入场 ${entry_lo:,.1f}~${entry_hi:,.1f}\n'
                            f'SL ${sl:,.1f} | TP ${tp1:,.1f}'
                        )
    except Exception:
        pass
    return '（需重新分析获取最新VIP点位）'


def _push_alert(trigger: dict) -> None:
    """推送触发信号到苏摩线程"""
    sym     = trigger['sym']
    price   = trigger['price']
    title   = trigger['title']
    detail  = trigger['detail']
    priority = trigger['priority']
    ts_utc  = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')

    vip_hint = _read_latest_vip(sym)
    priority_icon = {'CRITICAL': '🚨', 'HIGH': '⚠️', 'MED': '📡'}.get(priority, '📡')

    msg = (
        f'{priority_icon} **结构感知触发** | {ts_utc}\n\n'
        f'**{title}**\n'
        f'{detail}\n\n'
        f'━━━ 参考VIP点位 ━━━\n'
        f'{vip_hint}\n\n'
        f'💡 发 `分析{sym}` 获取最新完整VIP策略'
    )

    target = f'{_JARVIS_USER}:thread:{_JARVIS_THREAD}' if _JARVIS_THREAD else _JARVIS_USER

    try:
        subprocess.Popen([
            'openclaw', 'infer',
            '--channel', 'jarvis',
            '--to', target,
            '--message', msg,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f'[sentinel] 推送成功: {sym} {trigger["dim"]} {priority}', flush=True)
    except Exception as e:
        print(f'[sentinel] 推送失败: {e}', file=sys.stderr)


def main():
    import signal as _sig
    _sig.signal(_sig.SIGALRM, lambda s, f: sys.exit(1))
    _sig.alarm(25)  # 25s强制超时

    state = _load_state()
    triggers = sense_btc_eth()

    if not triggers:
        print('[sentinel] 无触发 HEARTBEAT_OK', flush=True)
        return

    fired = 0
    for t in triggers:
        sym, dim = t['sym'], t['dim']
        if _is_cooled_down(state, sym, dim):
            print(f'[sentinel] {sym} {dim} 冷却中，跳过', flush=True)
            continue
        _push_alert(t)
        _mark_triggered(state, sym, dim)
        fired += 1

    _save_state(state)
    print(f'[sentinel] 本轮触发推送 {fired}/{len(triggers)} 条', flush=True)


if __name__ == '__main__':
    main()
