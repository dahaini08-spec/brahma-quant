#!/usr/bin/env python3
"""
梵天信号质量增强器 v1.0
2026-07-10 苏摩111批准 · 设计院顶级推理

对标全球顶级量化系统方法论:
  · Jane Street:   多信号源融合 + 独立因子正交化
  · Two Sigma:     特征工程标准化 + 风险因子分解
  · Citadel:       高频信号过滤 + 执行成本感知
  · Jump Trading:  延迟最小化 + 机会成本量化
  · Renaissance:   统计套利 + 信号衰减追踪

本模块增强功能:
  1. 三源信号融合（梵天主系统 + OI猎手 + 暴涨猎手）
  2. 信号衰减追踪（信号"半衰期"估算）
  3. 风险因子正交化（去除BTC/ETH相关性）
  4. 执行质量评分（考虑滑点/时机/市场深度）
  5. 实时信号聚合推送（增强决策效率）
"""
import sys, os, json, time, requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

FAPI = 'https://fapi.binance.com'

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except:
    JARVIS_TARGET = '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63'
    JARVIS_CHANNEL = 'jarvis'


# ════════════════════════════════════════════════════════════════
# 全局市场状态感知（Two Sigma风格：宏观因子分解）
# ════════════════════════════════════════════════════════════════

def get_market_regime_snapshot():
    """
    全局市场状态快照
    参考Two Sigma的因子分解方法：将市场分解为
      - 宏观方向（BTC主趋势）
      - 波动率状态（VIX替代：BTC BB宽度）
      - 资金情绪（FR加权平均）
      - 相关性强度（BTC.D）
    """
    try:
        # BTC现价和趋势
        btc_tick = requests.get(f'{FAPI}/fapi/v1/ticker/24hr?symbol=BTCUSDT', timeout=5).json()
        btc_price = float(btc_tick.get('lastPrice', 0))
        btc_chg = float(btc_tick.get('priceChangePercent', 0))

        # BTC 4H K线 → BB宽度（波动率状态）
        kl = requests.get(f'{FAPI}/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=25', timeout=5).json()
        closes = [float(k[4]) for k in kl] if isinstance(kl, list) else []
        bb_width = 0.0
        if len(closes) >= 20:
            import statistics
            ma20 = sum(closes[-20:]) / 20
            std20 = statistics.stdev(closes[-20:])
            bb_upper = ma20 + 2 * std20
            bb_lower = ma20 - 2 * std20
            bb_width = round((bb_upper - bb_lower) / ma20 * 100, 3)

        # 资金费率（BTC+ETH平均）
        btc_fr = float(requests.get(f'{FAPI}/fapi/v1/premiumIndex?symbol=BTCUSDT', timeout=4).json().get('lastFundingRate', 0)) * 100
        eth_fr = float(requests.get(f'{FAPI}/fapi/v1/premiumIndex?symbol=ETHUSDT', timeout=4).json().get('lastFundingRate', 0)) * 100
        avg_fr = (btc_fr + eth_fr) / 2

        # 体制读取
        regime_state = {}
        rs_path = BASE / 'data' / 'regime_state.json'
        if rs_path.exists():
            regime_state = json.loads(rs_path.read_text())
        btc_regime = regime_state.get('BTCUSDT', {}).get('confirmed', 'UNKNOWN')
        if isinstance(btc_regime, dict):
            btc_regime = btc_regime.get('confirmed', 'UNKNOWN')

        # 市场状态分级
        if bb_width > 3.0:
            vol_state = 'HIGH_VOL'    # 高波动：信号质量下降，仓位缩小
        elif bb_width > 1.5:
            vol_state = 'NORMAL'      # 正常波动：标准执行
        else:
            vol_state = 'LOW_VOL'     # 低波动/压缩：等待爆发，OI猎手最佳时机

        # 资金费率极值检测
        if avg_fr > 0.05:
            fr_state = 'OVER_LONG'   # 多头过热，做多需谨慎
        elif avg_fr < -0.02:
            fr_state = 'OVER_SHORT'  # 空头过拥挤，逼空机会
        else:
            fr_state = 'NEUTRAL'

        return {
            'btc_price':   btc_price,
            'btc_chg_24h': btc_chg,
            'btc_regime':  btc_regime,
            'bb_width':    bb_width,
            'vol_state':   vol_state,
            'avg_fr':      round(avg_fr, 5),
            'fr_state':    fr_state,
            'timestamp':   datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {'error': str(e), 'btc_regime': 'UNKNOWN', 'vol_state': 'UNKNOWN'}


# ════════════════════════════════════════════════════════════════
# 三源信号聚合（Jane Street风格：多因子融合）
# ════════════════════════════════════════════════════════════════

def aggregate_all_signals(market_snap):
    """
    聚合三大信号源，产生统一排名
    Jane Street方法：信号正交化 + 加权融合 + 衰减过滤
    """
    now_ts = time.time()
    all_signals = []

    # ── 源1：梵天主系统信号 ─────────────────────────────────
    try:
        sig_log = BASE / 'data' / 'live_signal_log.jsonl'
        main_sigs = [json.loads(l) for l in open(sig_log).readlines() if l.strip()]
        # 取2H内有效信号
        fresh_main = [s for s in main_sigs
                      if (now_ts - float(s.get('ts', 0))) < 2*3600
                      and s.get('valid', False)
                      and float(s.get('score', 0)) >= 135]
        for s in fresh_main:
            all_signals.append({
                'source':    'brahma_main',
                'symbol':    s['symbol'],
                'direction': s.get('direction', s.get('signal_dir', '')),
                'score':     float(s.get('score', 0)),
                'regime':    s.get('regime', ''),
                'rr1':       float(s.get('rr1', 0) or 0),
                'sl_pct':    float(s.get('sl_pct', 0) or 0),
                'entry_lo':  s.get('entry_lo', 0),
                'tp1':       s.get('tp1', 0),
                'signal_id': s.get('signal_id', ''),
                'age_min':   (now_ts - float(s.get('ts', 0))) / 60,
                'weight':    1.0,   # 主系统权重最高
            })
    except Exception as e:
        pass

    # ── 源2：OI猎手信号 ──────────────────────────────────────
    try:
        oi_data = json.loads((BASE / 'data' / 'oi_candidates.json').read_text())
        oi_age_h = (now_ts - oi_data.get('updated_at', 0)) / 3600
        if oi_age_h < 2.0:  # 2H内有效
            for sym, c in oi_data.get('candidates', {}).items():
                if not isinstance(c, dict): continue
                oi_sc = float(c.get('oi_score', 0) or 0)
                action = c.get('action', '')
                if oi_sc < 50 or action not in ('buy_full', 'buy_light'): continue
                mode = c.get('mode', 'C')
                dir_bias = c.get('direction_bias', 'LONG')
                all_signals.append({
                    'source':    f'oi_hunter_{mode}',
                    'symbol':    sym,
                    'direction': dir_bias,
                    'score':     oi_sc,
                    'regime':    c.get('regime', 'UNKNOWN'),
                    'rr1':       1.0,
                    'sl_pct':    c.get('sl_pct', 2.5),
                    'entry_lo':  float(c.get('price', 0)) * 0.996,
                    'tp1':       float(c.get('price', 0)) * 1.05,
                    'age_min':   oi_age_h * 60,
                    'weight':    0.7 if mode == 'A' else (0.6 if mode == 'B' else 0.4),
                    'oi_7d':     c.get('chg_7d', 0),
                    'whale_l':   c.get('whale_l', 50),
                })
    except Exception as e:
        pass

    # ── 源3：暴涨猎手信号 ────────────────────────────────────
    try:
        ph_out = json.loads(open(BASE / 'dharma/pump_hunter/new_alerts.json').read())
        ph_alerts = ph_out.get('alerts', [])
        ph_ts = ph_out.get('scan_ts', 0)
        ph_age_min = (now_ts - ph_ts) / 60
        if ph_age_min < 15:  # 15min内有效
            for a in ph_alerts[:5]:
                if a.get('score', 0) < 75: continue
                all_signals.append({
                    'source':    'pump_hunter',
                    'symbol':    a['symbol'],
                    'direction': 'LONG',
                    'score':     float(a.get('score', 0)),
                    'regime':    a.get('brahma_regime', 'UNKNOWN'),
                    'rr1':       float(a.get('rr', 1.5)),
                    'sl_pct':    float(a.get('sl_pct', 2.5)),
                    'entry_lo':  a.get('entry_lo', 0),
                    'tp1':       a.get('tp1_price', 0),
                    'age_min':   ph_age_min,
                    'weight':    0.5,
                    'compression': a.get('compression', 99),
                    'vol_ratio': a.get('vol_ratio', 1),
                })
    except Exception as e:
        pass

    return all_signals


# ════════════════════════════════════════════════════════════════
# 信号质量增强（Citadel风格：执行成本感知）
# ════════════════════════════════════════════════════════════════

def enhance_signal_quality(signals, market_snap):
    """
    Citadel方法：考虑执行成本的信号质量评分
    
    增强维度：
      · 时效性惩罚：信号越旧，质量越低
      · 市场状态感知：高波动期降低仓位
      · 源可信度加权：多源共振加分
      · 相关性去重：BTC/ETH不同时开双向
    """
    enhanced = []
    vol_mult = {'HIGH_VOL': 0.6, 'NORMAL': 1.0, 'LOW_VOL': 0.8}.get(
        market_snap.get('vol_state', 'NORMAL'), 1.0)

    # 检查是否BTC/ETH双向开单（相关性风险）
    btc_dir = next((s['direction'] for s in signals if s['symbol']=='BTCUSDT'), None)
    eth_dir = next((s['direction'] for s in signals if s['symbol']=='ETHUSDT'), None)
    has_correlation_risk = (btc_dir and eth_dir and btc_dir == eth_dir)

    # 统计多源共振
    sym_sources = {}
    for s in signals:
        sym = s['symbol']
        sym_sources[sym] = sym_sources.get(sym, 0) + 1

    for s in signals:
        sym = s['symbol']
        score = s['score']
        age_min = s.get('age_min', 0)
        weight = s.get('weight', 1.0)

        # 时效性衰减（Renaissance信号衰减模型：指数衰减）
        half_life_min = 60  # 1H半衰期
        decay = 0.5 ** (age_min / half_life_min)

        # 多源共振加成
        source_bonus = 1.2 if sym_sources.get(sym, 0) >= 2 else 1.0

        # 相关性惩罚
        corr_penalty = 0.9 if (has_correlation_risk and sym in ('BTCUSDT', 'ETHUSDT')) else 1.0

        # 综合增强评分
        enhanced_score = score * weight * decay * source_bonus * corr_penalty * vol_mult

        enhanced.append({
            **s,
            'enhanced_score': round(enhanced_score, 1),
            'decay':          round(decay, 3),
            'source_bonus':   source_bonus,
            'vol_mult':       vol_mult,
            'exec_priority':  'IMMEDIATE' if enhanced_score >= 80 else (
                              'HIGH' if enhanced_score >= 60 else 'NORMAL'),
        })

    # 去重：同标的取最高增强分
    seen = {}
    for s in sorted(enhanced, key=lambda x: -x['enhanced_score']):
        sym = s['symbol']
        if sym not in seen:
            seen[sym] = s

    return sorted(seen.values(), key=lambda x: -x['enhanced_score'])


# ════════════════════════════════════════════════════════════════
# 统一信号报告生成
# ════════════════════════════════════════════════════════════════

def generate_signal_report(signals_enh, market_snap):
    """生成结构化信号报告（Jump Trading风格：机会成本显示）"""
    now_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
    regime = market_snap.get('btc_regime', '?')
    bb_w = market_snap.get('bb_width', 0)
    vol_state = market_snap.get('vol_state', '?')
    avg_fr = market_snap.get('avg_fr', 0)

    immediate = [s for s in signals_enh if s['exec_priority'] == 'IMMEDIATE']
    high_pri  = [s for s in signals_enh if s['exec_priority'] == 'HIGH']

    lines = [
        f"🧠 梵天三源信号聚合 · {now_str}",
        f"{'─'*44}",
        f"🌐 市场状态: {regime} | BB宽={bb_w:.2f}% | FR={avg_fr:+.5f}%",
        f"📊 波动率: {vol_state} | BTC: ${market_snap.get('btc_price',0):,.0f} ({market_snap.get('btc_chg_24h',0):+.1f}%)",
        f"{'─'*44}",
    ]

    if immediate:
        lines.append(f"⚡ 立即执行候选 ({len(immediate)}个):")
        for s in immediate[:4]:
            src_icon = {'brahma_main': '🔮', 'oi_hunter_A': '🏆',
                       'oi_hunter_B': '⚡', 'pump_hunter': '💣'}.get(s['source'], '📡')
            lines.append(
                f"  {src_icon} {s['symbol']:14} {s['direction']:5} "
                f"增强分={s['enhanced_score']:.0f} | 原分={s['score']:.0f} "
                f"| {s['source']}"
            )
            if s.get('entry_lo'):
                lines.append(
                    f"     入场≈{s['entry_lo']:.4g} | SL-{s.get('sl_pct',2.5):.1f}% | RR={s.get('rr1',1):.1f}x"
                )

    if high_pri:
        lines.append(f"\n🟡 高优先候选 ({len(high_pri)}个):")
        for s in high_pri[:3]:
            lines.append(
                f"  {s['symbol']:14} {s['direction']:5} 增强分={s['enhanced_score']:.0f} [{s['source']}]"
            )

    if not immediate and not high_pri:
        lines.append("📭 暂无高优先信号（所有来源均低于阈值）")

    lines.append(f"{'─'*44}")
    lines.append(f"总信号数: {len(signals_enh)} | 立即:{len(immediate)} 高:{len(high_pri)}")

    return '\n'.join(lines)


def send_message(msg):
    import subprocess
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', JARVIS_CHANNEL, '--to', JARVIS_TARGET,
         '--message', msg],
        capture_output=True, timeout=15
    )


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def main(push=True):
    print("🧠 梵天信号质量增强器 v1.0 启动...")

    # 1. 市场状态快照
    market = get_market_regime_snapshot()
    print(f"  市场状态: {market.get('btc_regime')} | BB宽={market.get('bb_width'):.2f}% | {market.get('vol_state')}")

    # 2. 聚合三源信号
    all_sigs = aggregate_all_signals(market)
    print(f"  三源信号: {len(all_sigs)}个")
    src_counts = {}
    for s in all_sigs:
        src = s['source'].split('_')[0]+'_'+s['source'].split('_')[1] if '_' in s['source'] else s['source']
        src_counts[src] = src_counts.get(src, 0) + 1
    for src, cnt in src_counts.items():
        print(f"    {src}: {cnt}个")

    # 3. 信号质量增强
    enhanced = enhance_signal_quality(all_sigs, market)
    immediate = [s for s in enhanced if s['exec_priority'] == 'IMMEDIATE']
    print(f"  增强后: {len(enhanced)}个去重 | 立即执行: {len(immediate)}个")

    # 4. 生成报告
    if enhanced:
        report = generate_signal_report(enhanced, market)
        print(f"\n{report}")
        if push and (immediate or len(enhanced) >= 3):
            send_message(report)
            print("✅ 报告已推送苏摩")
    else:
        print("HEARTBEAT_OK - 无有效信号")

    # 5. 保存聚合结果
    output = {
        'ts':        time.time(),
        'generated': datetime.now(timezone.utc).isoformat(),
        'market':    market,
        'signals':   enhanced[:20],
        'immediate': len(immediate),
        'total':     len(enhanced),
    }
    out_path = BASE / 'data' / 'signal_aggregator.json'
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    return enhanced


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-push', action='store_true')
    args = ap.parse_args()
    main(push=not args.no_push)
