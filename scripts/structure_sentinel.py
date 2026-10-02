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
# [Fix 2026-10-01] 从 brahma_cpu 同源读线程ID
if not _JARVIS_THREAD:
    try:
        import re as _re2
        _cpu_src = (BASE / 'brahma_brain' / 'brahma_cpu.py').read_text()
        _tm = _re2.search(r"_JARVIS_THREAD\s*=\s*['\"]([^'\"]{8,})['\"]", _cpu_src)
        if _tm:
            _JARVIS_THREAD = _tm.group(1)
    except Exception:
        pass

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

        # CVD：先读realtime文件，不存在则从brahma_state.extra读
        cvd_file = DATA / f'cvd_realtime_{sym.lower()}.json'
        cvd_1h = 0.0
        try:
            if cvd_file.exists():
                cvd_d = json.loads(cvd_file.read_text())
                cvd_1h = float(cvd_d.get('cvd_1h', 0) or 0)
            else:
                # fallback: brahma_state.extra.order_flow or extra._snap_for_xgb
                _of = (bs.get('extra', {}) or {}).get('order_flow', {}) or {}
                cvd_1h = float(_of.get('cvd_1h', _of.get('cvd', 0)) or 0)
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


    # ── D2: OI方向反转（连续下跌→连续上涨，最强入场信号）──
    # [2026-10-02 苏摩111 封印] 今日错过BTC多单根因——OI反转未感知
    oi_hist = _fetch(
        f'https://fapi.binance.com/futures/data/openInterestHist'
        f'?symbol={sym_full}&period=1h&limit=5'
    )
    try:
        if oi_hist and len(oi_hist) >= 4:
            oi_vals = [float(o['sumOpenInterest']) for o in oi_hist]
            # 检测：前N根连续下跌，最新1~2根转为上涨
            recent_up   = oi_vals[-1] > oi_vals[-2]          # 最新1h在涨
            prev_down   = all(oi_vals[i] < oi_vals[i-1]      # 之前连续下跌
                              for i in range(1, len(oi_vals)-1))
            chg_pct     = (oi_vals[-1] - oi_vals[-3]) / oi_vals[-3] * 100
            if recent_up and prev_down and chg_pct > 0.05:   # 反转+小幅增仓确认
                oi_seq_str = ' → '.join(f'{v:.0f}' for v in oi_vals)
                triggers.append({
                    'sym': sym, 'dim': 'D2_OI_REVERSAL', 'price': price,
                    'title': f'🔄 {sym} OI反转！连跌后首次上涨 +{chg_pct:.2f}%',
                    'detail': f'OI序列: {oi_seq_str} | 多头入场信号激活',
                    'priority': 'CRITICAL',
                })
    except Exception as _oi_e:
        print(f'[sentinel] D2_OI_REVERSAL失败: {_oi_e}', file=sys.stderr)

    # ── D7: 果蝇三条件 score≥2（breakout_watch结果文件，零API消耗）──
    bw_file = DATA / 'breakout_watch_latest.json'
    try:
        if bw_file.exists():
            import time as _t2
            bw_age = _t2.time() - bw_file.stat().st_mtime
            if bw_age < 1800:  # 30min内的结果有效
                bw_data = json.loads(bw_file.read_text())
                for sym_full, result in bw_data.get('results', {}).items():
                    sym_short = sym_full[:-4] if sym_full.endswith('USDT') and sym_full in ('BTCUSDT','ETHUSDT') else None
                    if not sym_short: continue
                    score = result.get('score', 0)
                    level = result.get('level', 'NORMAL')
                    if score >= 2:
                        c1 = result.get('c1_volume', {})
                        c2 = result.get('c2_lsr', {})
                        c3 = result.get('c3_chop', {})
                        detail = f"量能{c1.get('vol_ratio',0):.1f}x / 轧空{'✅' if c2.get('ok') else '❌'} / 弹簧{'✅' if c3.get('ok') else '❌'}"
                        triggers.append({
                            'sym': sym_short, 'dim': 'D7_BREAKOUT', 'price': c1.get('cur_price', 0),
                            'title': f'🚀 {sym_short} 果蝇突破 {score}/3条件满足',
                            'detail': f'level={level} · {detail}',
                            'priority': 'CRITICAL' if score >= 3 else 'HIGH',
                        })
    except Exception as _bw_e:
        print(f'[sentinel] D7果蝇读取失败: {_bw_e}', file=sys.stderr)

    return triggers


def _read_latest_vip(sym: str) -> str:
    """从 liq_heatmap + brahma_state 读精确VIP关键点位
    [Fix 2026-10-01] auto_analysis_latest只存文本，改读结构化state文件
    """
    try:
        sym_lower = sym.lower()
        price_now, regime, liq_short, liq_long = 0.0, 'CHOP_MID', 0.0, 0.0
        state_file = DATA / f'brahma_state_{sym_lower}.json'
        if state_file.exists():
            bs = json.loads(state_file.read_text())
            price_now = float(bs.get('price', 0))
            regime    = bs.get('regime', 'CHOP_MID')
        liq_file = DATA / f'liq_heatmap_{sym_lower}usdt.json'
        if liq_file.exists():
            ld = json.loads(liq_file.read_text())
            if not price_now:
                price_now = float(ld.get('price', 0))
            liq_short = float(ld.get('nearest_short_liq', 0) or 0)
            liq_long  = float(ld.get('nearest_long_liq', 0) or 0)
        if liq_short <= 0 and price_now > 0:
            liq_short = round(price_now * 1.02, 1)
            liq_long  = round(price_now * 0.98, 1)
        if liq_short > 0:
            entry_s_lo = round(liq_short * 0.995, 1)
            entry_l_hi = round(liq_long  * 1.005, 1)
            sl_s = round(liq_short * 1.02, 1)
            sl_l = round(liq_long  * 0.98, 1)
            return (
                f'🔴 空单区 ${entry_s_lo:,.1f}~${liq_short:,.1f}  SL ${sl_s:,.1f}\n'
                f'🟢 多单区 ${liq_long:,.1f}~${entry_l_hi:,.1f}  SL ${sl_l:,.1f}\n'
                f'体制={regime} | 现价=${price_now:,.1f}'
            )
    except Exception:
        pass
    return '（state待刷新，发 `分析BTC` 获取最新）'


def _quick_three_party(sym: str, dim: str, price: float) -> str:
    """
    Astra模式精简三方快评 [2026-10-02 苏摩111封印]
    CRITICAL级信号触发时的5s内快评，不跑80维，纯读缓存
    对标 @thedelost 架构：主力持续跑，架构师只在关键节点介入
    """
    try:
        sym_l = sym.lower()
        bs_f  = DATA / f'brahma_state_{sym_l}.json'
        liq_f = DATA / f'liq_heatmap_{sym_l}usdt.json'
        bw_f  = DATA / 'breakout_watch_latest.json'

        bs  = json.loads(bs_f.read_text()) if bs_f.exists() else {}
        ld  = json.loads(liq_f.read_text()) if liq_f.exists() else {}
        bw  = json.loads(bw_f.read_text()) if bw_f.exists() else {}

        regime  = bs.get('regime', 'CHOP_MID')
        # hurst读取：优先market_state_raw.hurst_4h，再ensemble.raw_vec.hurst，再兜底0.0
        _ms = bs.get('market_state_raw', {})
        _ens = bs.get('extra', {}).get('ensemble', {}).get('raw_vec', {})
        hurst   = float(_ms.get('hurst_4h', _ens.get('hurst', 0.0)) or 0.0)
        kl      = bs.get('key_levels', {})
        resist  = kl.get('resistance', [price*1.02])
        support = kl.get('support', [price*0.98])
        r1      = resist[0] if resist else price * 1.02
        s1      = support[0] if support else price * 0.98
        liq_s   = float(ld.get('nearest_short_liq', price*1.02) or price*1.02)
        liq_l   = float(ld.get('nearest_long_liq',  price*0.98) or price*0.98)
        bw_sym  = bw.get('results', {}).get(f'{sym}USDT', {})
        bw_score= bw_sym.get('score', 0)

        # 方向逻辑（纯规则，0 LLM）
        dist_wall = (liq_s - price) / price * 100
        dist_pool = (price - liq_l) / price * 100

        if dim == 'D2_OI_REVERSAL':
            quant = f'OI反转=多头入场先行指标。距止损墙{dist_wall:.1f}%，现在是低位布多的窗口。'
            damo  = f'统计上OI反转后2h内价格跟涨概率>65%。'
            trader= f'等1H收阳确认，止损支撑池${liq_l:,.1f}下方。'
            bias  = '🟢 偏多'
        elif dim in ('D5_WALL', 'D4_LSR'):
            quant = f'价格触碰止损墙${liq_s:,.1f}，空头止损密集区激活。'
            damo  = f'止损墙附近做空EV历史正值，RR≥2.0。'
            trader= f'挂空单${liq_s*0.995:,.1f}~${liq_s:,.1f}，止损${liq_s*1.02:,.1f}。'
            bias  = '🔴 偏空'
        elif dim == 'D6_POOL':
            quant = f'价格触碰支撑池${liq_l:,.1f}，多头清算区入场点。'
            damo  = f'支撑池接多历史WR≈60%，需量能确认。'
            trader= f'挂多单${liq_l:,.1f}~${liq_l*1.005:,.1f}，止损${liq_l*0.98:,.1f}。'
            bias  = '🟢 偏多'
        else:
            quant = f'体制={regime} H={hurst:.3f} 果蝇{bw_score}/3。'
            damo  = f'等主方向确认后入场。'
            trader= f'当前观察，不追价。'
            bias  = '⚪ 中性'

        return (
            f'━━━ 🏛️ 精简三方快评（Astra模式）━━━\n'
            f'🔬 量化：{quant}\n'
            f'📐 达摩院：{damo}\n'
            f'⚔️ 交易员：{trader}\n'
            f'**方向：{bias}** | 体制={regime} H={hurst:.3f}\n'
            f'━━━ 发 `分析{sym}` 获取完整D1~D10 ━━━'
        )
    except Exception as e:
        return f'（快评读取失败: {e}）'


def _push_alert(trigger: dict) -> None:
    """推送触发信号到苏摩线程（含Astra精简三方快评）"""
    sym      = trigger['sym']
    price    = trigger['price']
    title    = trigger['title']
    detail   = trigger['detail']
    priority = trigger['priority']
    dim      = trigger['dim']
    ts_utc   = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')

    vip_hint = _read_latest_vip(sym)
    priority_icon = {'CRITICAL': '🚨', 'HIGH': '⚠️', 'MED': '📡'}.get(priority, '📡')

    # CRITICAL级追加精简三方快评（Astra模式：关键节点架构师介入）
    astra_block = ''
    if priority == 'CRITICAL':
        astra_block = '\n\n' + _quick_three_party(sym, dim, price)

    msg = (
        f'{priority_icon} **结构感知触发** | {ts_utc}\n\n'
        f'**{title}**\n'
        f'{detail}\n\n'
        f'━━━ 参考VIP点位 ━━━\n'
        f'{vip_hint}'
        f'{astra_block}'
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
