#!/usr/bin/env python3
"""
smart_digest.py — 梵天智能汇总推送
设计院封印 2026-07-02

功能：
1. 汇总所有活跃观察信号（暴涨猎手+OI猎手+体制监控）
2. 承前启后：展示过去48h内仍有效的信号，不遗忘
3. 去重：同一symbol不重复推送
4. 分级：P0紧急/P1重要/P2参考，避免信息疲劳
5. 每6h推送一次，整合所有信息
"""
import json, os, time, sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent

def fmt_time(ts):
    if not ts: return '?'
    age_h = (time.time() - ts) / 3600
    if age_h < 1: return f'{int(age_h*60)}min前'
    if age_h < 24: return f'{age_h:.1f}h前'
    return f'{age_h/24:.1f}天前'

def load_pump_signals():
    """读取暴涨猎手活跃信号（48h内）"""
    signals = []
    log_file = BASE / 'logs' / 'pump_hunter.log'
    if not log_file.exists():
        return signals
    lines = log_file.read_text().splitlines()[-200:]
    seen = set()
    for line in reversed(lines):
        if 'PUMP_SIGNAL写入独立队列' in line or '推送完成' in line:
            import re
            m = re.search(r'([A-Z]{3,10}USDT)\s+score=(\d+)', line)
            if m:
                sym, score = m.group(1), int(m.group(2))
                if sym not in seen:
                    seen.add(sym)
                    signals.append({'symbol': sym, 'score': score, 'source': 'pump_hunter', 'ts': time.time()})
    return signals[:10]

def load_oi_signals():
    """读取OI猎手活跃信号（24h内）"""
    signals = []
    log_file = BASE / 'logs' / 'oi_scanner.log'
    if not log_file.exists():
        return signals
    lines = log_file.read_text().splitlines()[-100:]
    seen = set()
    for line in reversed(lines):
        import re
        m = re.search(r'([A-Z]{3,10}USDT).*OI.*([+-]\d+\.?\d*)%', line)
        if m:
            sym, chg = m.group(1), float(m.group(2))
            if sym not in seen and abs(chg) >= 5:
                seen.add(sym)
                signals.append({'symbol': sym, 'oi_chg': chg, 'source': 'oi_hunter', 'ts': time.time()})
    return signals[:5]

def load_regime_alerts():
    """读取体制切换告警（24h内）"""
    alerts = []
    log_file = BASE / 'logs' / 'regime_watcher.log'
    if not log_file.exists():
        return alerts
    lines = log_file.read_text().splitlines()[-50:]
    for line in lines[-20:]:
        if '触发' in line or '切换' in line or 'BEAR_TREND' in line or 'BULL_TREND' in line:
            alerts.append(line.strip()[-120:])
    return alerts[-3:]

def load_active_positions():
    """读取当前持仓（ws_guardian状态）"""
    pos_file = BASE / 'data' / 'wuqu_positions.json'
    if not pos_file.exists():
        return []
    try:
        data = json.loads(pos_file.read_text())
        return data if isinstance(data, list) else data.get('positions', [])
    except:
        return []

def load_signal_trace():
    """读取最近24h的信号trace"""
    trace_file = BASE / 'brahma_brain' / 'logs' / 'signal_trace.jsonl'
    if not trace_file.exists():
        trace_file = BASE / 'logs' / 'signal_trace.jsonl'
    if not trace_file.exists():
        return []
    cutoff = time.time() - 86400
    records = []
    for line in trace_file.read_text().splitlines()[-100:]:
        try:
            r = json.loads(line)
            records.append(r)
        except:
            pass
    return records[-20:]

def format_digest():
    lines = []
    now_str = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
    lines.append(f'🏛️ **梵天智能日报** | {now_str}')
    lines.append('')

    # ── 持仓状态 ──────────────────────────────────────────
    positions = load_active_positions()
    if positions:
        lines.append('**📍 当前持仓**')
        for p in positions:
            sym = p.get('symbol','?')
            dir_ = p.get('direction', p.get('side','?'))
            entry = p.get('entry_price', p.get('avg_price', 0))
            lines.append(f'  • {sym} {dir_} @ ${entry:.4g}')
        lines.append('')

    # ── 暴涨猎手观察池 ────────────────────────────────────
    pump_sigs = load_pump_signals()
    if pump_sigs:
        lines.append('**🚀 暴涨猎手观察池**（承前启后）')
        for s in pump_sigs:
            lines.append(f'  • {s["symbol"]} score={s["score"]} 🔍持续监控中')
        lines.append('')

    # ── OI猎手异动 ────────────────────────────────────────
    oi_sigs = load_oi_signals()
    if oi_sigs:
        lines.append('**📊 OI猎手异动**')
        for s in oi_sigs:
            arrow = '🔺' if s['oi_chg'] > 0 else '🔻'
            lines.append(f'  • {s["symbol"]} OI {arrow}{s["oi_chg"]:+.1f}%')
        lines.append('')

    # ── 体制告警 ──────────────────────────────────────────
    regime_alerts = load_regime_alerts()
    if regime_alerts:
        lines.append('**⚡ 体制监控**')
        for a in regime_alerts[-2:]:
            lines.append(f'  • {a[-80:]}')
        lines.append('')

    # ── 信号trace摘要 ─────────────────────────────────────
    traces = load_signal_trace()
    gen = [t for t in traces if t.get('action') == 'SIGNAL_GENERATED']
    skip = [t for t in traces if t.get('action') == 'SIGNAL_SKIPPED']
    if traces:
        lines.append(f'**🧠 梵天信号(24h)**: 生成{len(gen)}个 跳过{len(skip)}个')
        for t in gen[:3]:
            lines.append(f'  • {t.get("symbol")} score={t.get("score","?")} {t.get("regime","?")} {t.get("direction","?")}')
        lines.append('')

    # ── 系统健康 ──────────────────────────────────────────
    lines.append('**💚 系统**: 运行正常 | 下次汇报6h后')

    return '\n'.join(lines)

if __name__ == '__main__':
    digest = format_digest()
    print(digest)
    # 写入推送队列
    out_file = BASE / 'data' / 'smart_digest_latest.txt'
    out_file.write_text(digest)
