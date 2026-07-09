#!/usr/bin/env python3
"""
brahma_4h_report.py — 梵天4H综合速报
设计院 2026-07-04 | 三合一推送

合并：market_structure_scanner + oi_surge_scanner + whale/smart_money
输出：单条20行内速报，推送到Jarvis
"""
import sys, os, json, subprocess, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

try:
    from system_config import JARVIS_TARGET, JARVIS_CHANNEL
except:
    JARVIS_TARGET  = os.environ.get('JARVIS_TARGET','73295708:thread:019f443a-b891-70f1-8cb0-ed031a80e68b')
    JARVIS_CHANNEL = 'jarvis'

now_utc = datetime.now(timezone.utc).strftime('%m-%d %H:%M')

# ─── 1. 市场结构 ───────────────────────────────────────────
def get_structure():
    try:
        r = subprocess.run(
            ['python3', str(BASE/'brahma_brain'/'market_structure_scanner.py'), '--both'],
            capture_output=True, text=True, timeout=25, cwd=str(BASE)
        )
        lines = r.stdout.strip().split('\n')
        # 提取OB / FVG / 清算集群关键行
        ob_line = next((l for l in lines if 'OB' in l or 'OrderBlock' in l or '订单块' in l), None)
        fvg_line = next((l for l in lines if 'FVG' in l or 'Fair Value' in l or '缺口' in l), None)
        liq_line = next((l for l in lines if '清算' in l or 'Liq' in l or 'cluster' in l.lower()), None)
        gex_line = next((l for l in lines if 'GEX' in l or 'gamma' in l.lower()), None)
        return {
            'ob': ob_line.strip() if ob_line else '无有效OB',
            'fvg': fvg_line.strip() if fvg_line else '无未填FVG',
            'liq': liq_line.strip() if liq_line else '无密集清算区',
            'gex': gex_line.strip() if gex_line else None,
        }
    except Exception as e:
        return {'ob': f'获取失败({e})', 'fvg': '-', 'liq': '-', 'gex': None}

# ─── 2. OI猎手信号 ─────────────────────────────────────────
def get_oi_signals():
    try:
        r = subprocess.run(
            ['python3', '-c',
             'import sys; sys.path.insert(0,"brahma_brain"); '
             'from oi_surge_scanner import scan_oi_surge; '
             'import json; results=scan_oi_surge(); '
             'print(json.dumps(results, ensure_ascii=False))'],
            capture_output=True, text=True, timeout=60, cwd=str(BASE)
        )
        data = json.loads(r.stdout.strip()) if r.stdout.strip() else {}
        return data
    except:
        # fallback: 读signal_bus
        try:
            bus = BASE / 'data' / 'signal_bus.jsonl'
            if not bus.exists(): return {}
            now = time.time()
            sigs = []
            for l in bus.read_text().strip().split('\n'):
                try:
                    s = json.loads(l)
                    if (s.get('source') == 'oi' and
                        s.get('status') == 'pending' and
                        s.get('valid') and
                        now - s.get('ts',0) < 14400):
                        sigs.append(s)
                except: pass
            return {'signals': sigs}
        except:
            return {}

# ─── 3. 大户背离 ────────────────────────────────────────────
def get_whale():
    try:
        from whale_engine import get_whale_activity
        from smart_money_engine import get_smart_money_signal
        btc_w = get_whale_activity('BTCUSDT')
        eth_w = get_whale_activity('ETHUSDT')
        btc_s = get_smart_money_signal('BTCUSDT')
        eth_s = get_smart_money_signal('ETHUSDT')
        return {
            'btc_dir':  btc_w.get('whale_dir', 'NEUTRAL'),
            'eth_dir':  eth_w.get('whale_dir', 'NEUTRAL'),
            'btc_gap':  btc_s.get('whale_retail_gap', 0),
            'eth_gap':  eth_s.get('whale_retail_gap', 0),
        }
    except Exception as e:
        return {'btc_dir': '?', 'eth_dir': '?', 'btc_gap': 0.0, 'eth_gap': 0.0}

# ─── 4. 当前体制 ─────────────────────────────────────────────
def get_regime():
    try:
        state = BASE / 'data' / 'regime_state.json'
        if state.exists():
            d = json.loads(state.read_text())
            return d.get('regime', d.get('btc_regime', 'UNKNOWN'))
    except: pass
    try:
        import requests
        k = requests.get('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=20',timeout=5).json()
        c = [float(x[4]) for x in k]
        def ema(c,n):
            k2=2/(n+1);r=[c[0]]
            for i in range(1,len(c)):r.append(c[i]*k2+r[-1]*(1-k2))
            return r
        def rsi(c,n=14):
            g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
            l=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
            ag=sum(g[-n:])/n;al=sum(l[-n:])/n
            return 100-100/(1+ag/al) if al else 100
        e20=ema(c,20)[-1];r=rsi(c)
        if c[-1]>e20 and r>55: return 'BULL_TREND'
        if c[-1]<e20 and r<45: return 'BEAR_TREND'
        return 'CHOP_MID'
    except:
        return 'UNKNOWN'

# ─── 组装推送 ────────────────────────────────────────────────
def build_report():
    structure = get_structure()
    whale     = get_whale()
    regime    = get_regime()

    # OI信号（快速读signal_bus）
    oi_lines = []
    try:
        bus = BASE / 'data' / 'signal_bus.jsonl'
        if bus.exists():
            now = time.time()
            for l in bus.read_text().strip().split('\n'):
                try:
                    s = json.loads(l)
                    if (s.get('source') == 'oi' and s.get('status') == 'pending'
                        and s.get('valid') and now - s.get('ts',0) < 14400):
                        oi_lines.append(
                            f"  🔥 {s['symbol']} {s['direction']} "
                            f"score={s['score']:.0f} entry=${s['entry_lo']:.4f} "
                            f"sl=${s['sl']:.4f} rr={s['rr1']}"
                        )
                except: pass
    except: pass

    # score>=155 主系统信号
    sig_lines = []
    try:
        bus = BASE / 'data' / 'signal_bus.jsonl'
        if bus.exists():
            now = time.time()
            for l in bus.read_text().strip().split('\n'):
                try:
                    s = json.loads(l)
                    if (s.get('source') == 'main' and s.get('status') == 'pending'
                        and s.get('valid') and float(s.get('score',0)) >= 155
                        and now - s.get('ts',0) < 14400):
                        sig_lines.append(
                            f"  🎯 {s['symbol']} {s['direction']} "
                            f"score={s['score']:.0f} entry=${s['entry_lo']:.4f} "
                            f"sl=${s['sl']:.4f} tp1=${s['tp1']:.4f} rr={s['rr1']}"
                        )
                except: pass
    except: pass

    # 大户背离判断
    btc_gap = whale['btc_gap']
    eth_gap = whale['eth_gap']
    whale_alert = abs(btc_gap) > 0.05 or abs(eth_gap) > 0.05

    # 判断是否有内容推送（无信号+无大户背离 → 静默，不推同质化内容）
    has_content = bool(oi_lines or sig_lines or whale_alert)

    if not has_content:
        # 无有效信号+无异动 → 静默，避免同质化播报
        return None

    # 构建消息
    lines = [f"📡 梵天速报 {now_utc} UTC"]
    lines.append(f"体制: {regime}")
    lines.append("")

    # OI异动
    lines.append("━━ OI异动 ━━")
    if oi_lines:
        lines.extend(oi_lines)
    else:
        lines.append("  无异动")

    # 结构
    lines.append("━━ 结构 ━━")
    lines.append(f"  OB:   {structure['ob'][:60]}")
    lines.append(f"  FVG:  {structure['fvg'][:60]}")
    lines.append(f"  清算: {structure['liq'][:60]}")
    if structure['gex']:
        lines.append(f"  GEX:  {structure['gex'][:60]}")

    # 大户
    lines.append("━━ 大户 ━━")
    btc_mark = '⚠️' if abs(btc_gap) > 0.05 else ''
    eth_mark = '⚠️' if abs(eth_gap) > 0.05 else ''
    lines.append(f"  BTC: {whale['btc_dir']} gap={btc_gap:+.3f}{btc_mark}  "
                 f"ETH: {whale['eth_dir']} gap={eth_gap:+.3f}{eth_mark}")
    if whale_alert:
        lines.append("  ⚠️ 大户背离预警：大户与散户方向出现显著分歧")

    # 信号
    if sig_lines:
        lines.append("━━ 信号 ━━")
        lines.extend(sig_lines)

    return '\n'.join(lines)

# ─── 主执行 ──────────────────────────────────────────────────
if __name__ == '__main__':
    report = build_report()
    if report is None:
        pass  # [静默]
    else:
        print(report)
        # 推送
        try:
            subprocess.run([
                'openclaw', 'message', 'send',
                '--channel', JARVIS_CHANNEL,
                '--to', JARVIS_TARGET,
                '--message', report
            ], timeout=10, cwd=str(BASE))
            print(f'\n✅ 推送完成')
        except Exception as e:
            print(f'推送失败: {e}')
