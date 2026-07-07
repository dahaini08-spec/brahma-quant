#!/usr/bin/env python3
"""
🏛️ 梵天信号仪表盘 v1.0 — 三系统统一信号追踪
设计院封印 2026-07-07

三大信号系统：
  A. 主系统 (brahma_analysis_runner) → signal_bus.jsonl
  B. OI猎手 (oi_surge_scanner)       → data/oi_candidates.json
  C. 暴涨猎手 (pump_hunter)          → dharma/pump_hunter/new_alerts.json

功能：
  1. 实时聚合三系统信号
  2. 计算今日/近7日信号统计
  3. 推送日报仪表盘（含新增/过期/实时信号）
  4. 写入 data/signal_dashboard_v2.json 供外部查询
"""
import os, sys, json, time, datetime
import subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'scripts'))

# ── 路由 SSOT ──────────────────────────────────────────────────
try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except Exception:
    JARVIS_TARGET = os.environ.get('JARVIS_TARGET', '73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075')

PUSH_CHANNEL = 'jarvis'

# ── 文件路径 ───────────────────────────────────────────────────
SIGNAL_BUS_FILE    = BASE / 'data' / 'signal_bus.jsonl'
OI_CANDIDATES_FILE = BASE / 'data' / 'oi_candidates.json'
PUMP_ALERTS_FILE   = BASE / 'dharma' / 'pump_hunter' / 'new_alerts.json'
PUMP_LOG_FILE      = BASE / 'dharma' / 'pump_hunter' / 'scan_log.jsonl'
DASHBOARD_STATE    = BASE / 'data' / 'signal_dashboard_v2.json'

NOW = time.time()
NOW_DT = datetime.datetime.utcnow()

# ────────────────────────────────────────────────────────────────
# 数据采集
# ────────────────────────────────────────────────────────────────

def load_main_signals(hours=24):
    """读取主系统signal_bus，返回最近N小时的信号"""
    signals = []
    cutoff = NOW - hours * 3600
    if not SIGNAL_BUS_FILE.exists():
        return signals
    try:
        for line in SIGNAL_BUS_FILE.read_text().strip().split('\n'):
            if not line.strip():
                continue
            try:
                s = json.loads(line)
                ts = s.get('ts', 0)
                if ts > cutoff:
                    signals.append(s)
            except:
                pass
    except Exception:
        pass
    return signals

def load_oi_signals():
    """读取OI猎手缓存"""
    if not OI_CANDIDATES_FILE.exists():
        return [], 0
    try:
        d = json.loads(OI_CANDIDATES_FILE.read_text())
        age = NOW - d.get('updated_at', 0)
        cands = list(d.get('candidates', {}).values())
        return cands, age
    except:
        return [], 9999

def load_pump_signals(hours=24):
    """读取暴涨猎手近期信号"""
    signals = []
    cutoff = NOW - hours * 3600
    if not PUMP_LOG_FILE.exists():
        return signals
    try:
        for line in PUMP_LOG_FILE.read_text().strip().split('\n'):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get('ts', '')
                try:
                    ts = datetime.datetime.fromisoformat(ts_str).timestamp()
                except:
                    ts = NOW
                if ts > cutoff:
                    entry['_ts'] = ts
                    signals.append(entry)
            except:
                pass
    except:
        pass
    return signals

def load_pump_latest():
    """读取暴涨猎手最新扫描结果"""
    if not PUMP_ALERTS_FILE.exists():
        return {}, 9999
    try:
        d = json.loads(PUMP_ALERTS_FILE.read_text())
        age = NOW - d.get('scan_ts', 0)
        return d, age
    except:
        return {}, 9999

# ────────────────────────────────────────────────────────────────
# 统计分析
# ────────────────────────────────────────────────────────────────

def analyze_main_signals(signals):
    """分析主系统信号质量"""
    if not signals:
        return {'total': 0, 'valid': 0, 'active': 0, 'expired': 0, 'by_symbol': {}}

    valid = [s for s in signals if s.get('valid', False)]
    expired_cutoff = NOW
    active = []
    expired = []
    for s in valid:
        exp_str = s.get('expires_at')
        if exp_str:
            try:
                from datetime import timezone
                exp_ts = datetime.datetime.fromisoformat(str(exp_str).replace('Z', '+00:00'))
                if exp_ts.tzinfo:
                    exp_epoch = exp_ts.timestamp()
                else:
                    exp_epoch = exp_ts.timestamp()
                if exp_epoch > expired_cutoff:
                    active.append(s)
                else:
                    expired.append(s)
            except:
                expired.append(s)
        else:
            expired.append(s)

    by_sym = {}
    for s in signals:
        sym = s.get('symbol', '?')
        by_sym.setdefault(sym, 0)
        by_sym[sym] += 1

    # 最新有效信号（按时间排序）
    latest = sorted(valid, key=lambda x: x.get('ts', 0), reverse=True)[:3]

    return {
        'total': len(signals),
        'valid': len(valid),
        'active': len(active),
        'expired': len(expired),
        'by_symbol': by_sym,
        'latest': latest,
        'active_signals': active[:5],
    }

def analyze_oi_signals(candidates, age_sec):
    """分析OI猎手信号"""
    if not candidates:
        return {'total': 0, 'high_quality': 0, 'watchlist': 0, 'buy_ready': 0,
                'data_age_min': age_sec/60, 'stale': age_sec > 3600}

    high = [c for c in candidates if c.get('layers_pass', 0) >= 4]
    watch = [c for c in candidates if c.get('layers_pass', 0) >= 3]
    buy_ready = [c for c in candidates if c.get('action') in ('buy_full', 'buy_light')]

    return {
        'total': len(candidates),
        'high_quality': len(high),
        'watchlist': len(watch),
        'buy_ready': len(buy_ready),
        'data_age_min': round(age_sec / 60, 1),
        'stale': age_sec > 7200,
        'top': sorted(candidates, key=lambda x: x.get('layers_pass', 0) * 100 + x.get('oi_score', 0), reverse=True)[:5],
    }

def analyze_pump_signals(log_entries, latest_data, latest_age):
    """分析暴涨猎手信号"""
    today_start = NOW - 86400
    today_entries = [e for e in log_entries if e.get('_ts', 0) > today_start]
    total_today = sum(e.get('alerts', 0) for e in today_entries)
    new_today = sum(e.get('new', 0) for e in today_entries)
    scans_today = len(today_entries)

    # 最新告警
    latest_alerts = latest_data.get('new_alerts', [])
    all_alerts = latest_data.get('alerts', [])

    return {
        'scans_today': scans_today,
        'total_alerts_today': total_today,
        'new_alerts_today': new_today,
        'latest_scan_age_min': round(latest_age / 60, 1),
        'current_alerts': len(all_alerts),
        'current_new': len(latest_alerts),
        'stale': latest_age > 1800,  # 30分钟未扫描=陈旧
        'top_alerts': all_alerts[:3],
    }

# ────────────────────────────────────────────────────────────────
# 仪表盘格式化
# ────────────────────────────────────────────────────────────────

def format_dashboard(main_stat, oi_stat, pump_stat, state):
    """生成完整仪表盘报告"""
    ts_str = NOW_DT.strftime('%Y-%m-%d %H:%M UTC')
    today_str = NOW_DT.strftime('%m-%d')

    # 系统健康状态
    main_ok   = main_stat['valid'] > 0 or main_stat['total'] > 0
    oi_ok     = not oi_stat['stale'] and oi_stat['total'] > 0
    pump_ok   = not pump_stat['stale']

    def health(ok): return '🟢' if ok else '🔴'

    lines = [
        f'📊 梵天信号仪表盘 · {ts_str}',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'',
        f'系统健康: 主系统{health(main_ok)} | OI猎手{health(oi_ok)} | 暴涨猎手{health(pump_ok)}',
        f'',
        f'── 📈 主系统信号（近24H）──────────────',
        f'  总信号: {main_stat["total"]} | 有效: {main_stat["valid"]} | 实时有效: {main_stat["active"]} | 已过期: {main_stat["expired"]}',
    ]

    # 主系统实时有效信号
    if main_stat['active_signals']:
        lines.append('  🟢 当前有效信号:')
        for s in main_stat['active_signals'][:3]:
            sym = s.get('symbol', '?')
            dir_ = s.get('direction', '?')
            score = s.get('score', 0)
            regime = s.get('regime', '?')
            src = s.get('source', 'main')
            icon = '🟢' if dir_ == 'LONG' else '🔴'
            lines.append(f'    {icon} {sym} {dir_} score={score:.0f} [{regime}] src={src}')
    elif main_stat['total'] == 0:
        lines.append('  ⚪ 无信号（主系统未产生任何信号）')
    else:
        lines.append('  ⚠️ 无实时有效信号（信号已全部过期）')

    # 按标的统计
    if main_stat['by_symbol']:
        top_syms = sorted(main_stat['by_symbol'].items(), key=lambda x: -x[1])[:5]
        lines.append(f'  信号分布: {" | ".join(f"{sym}×{cnt}" for sym,cnt in top_syms)}')

    lines += [
        f'',
        f'── 🏹 OI猎手（数据更新: {oi_stat["data_age_min"]:.0f}分钟前）──────',
        f'  候选标的: {oi_stat["total"]} | 高质量(4层): {oi_stat["high_quality"]} | 监控池: {oi_stat["watchlist"]} | 可买入: {oi_stat["buy_ready"]}',
    ]

    if oi_stat.get('stale'):
        lines.append('  🔴 数据陈旧！超过2小时未更新')
    elif oi_stat['top']:
        lines.append('  Top信号:')
        for c in oi_stat['top'][:4]:
            act = c.get('action', '?')
            mode = c.get('mode', '?')
            oi_score = c.get('oi_score', 0)
            layers = c.get('layers_pass', 0)
            whale = c.get('whale_l', 0)
            act_icon = '🟢' if act in ('buy_full', 'buy_light') else '👁'
            lines.append(f'    {act_icon} {c["symbol"]:<14} 模式{mode} OI+{oi_score:.1f}% 大户{whale:.0f}% {layers}/4层 [{act}]')

    lines += [
        f'',
        f'── 🚨 暴涨猎手（每15分钟扫描）──────────',
        f'  今日扫描: {pump_stat["scans_today"]}次 | 今日告警: {pump_stat["total_alerts_today"]} | 新信号: {pump_stat["new_alerts_today"]}',
        f'  最近扫描: {pump_stat["latest_scan_age_min"]:.0f}分钟前 | 当前活跃告警: {pump_stat["current_alerts"]}',
    ]

    if pump_stat['stale']:
        lines.append('  🔴 扫描陈旧！超过30分钟未运行')
    elif pump_stat['top_alerts']:
        lines.append('  当前信号:')
        for a in pump_stat['top_alerts'][:3]:
            sym = a.get('symbol', '?')
            score = a.get('score', 0)
            reasons = ' | '.join(a.get('reasons', [])[:2])
            vr = a.get('vol_ratio', 1)
            ok = '✅' if vr < 5 else '⚠️过期'
            lines.append(f'    🔸 {sym} score={score} {ok} | {reasons}')

    # 7日趋势（从state读取）
    hist = state.get('history', [])
    if hist:
        lines += ['', '── 📅 近7日信号趋势 ──────────────────────']
        for entry in hist[-7:]:
            d_str = entry.get('date', '?')
            total = entry.get('main_total', 0)
            valid = entry.get('main_valid', 0)
            oi = entry.get('oi_total', 0)
            pump = entry.get('pump_new', 0)
            lines.append(f'  {d_str}: 主={total}(✓{valid}) OI={oi} 暴涨={pump}')

    lines += [
        f'',
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━',
        f'🏛️ 仪表盘更新: {ts_str} | 梵天设计院',
    ]

    return '\n'.join(lines)

# ────────────────────────────────────────────────────────────────
# 状态持久化
# ────────────────────────────────────────────────────────────────

def load_state():
    if DASHBOARD_STATE.exists():
        try:
            return json.loads(DASHBOARD_STATE.read_text())
        except:
            pass
    return {'history': [], 'last_push': 0}

def save_state(state):
    DASHBOARD_STATE.parent.mkdir(exist_ok=True)
    DASHBOARD_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def update_daily_history(state, main_stat, oi_stat, pump_stat):
    """每日追加一条历史记录"""
    today = NOW_DT.strftime('%m-%d')
    hist = state.get('history', [])
    # 去重（同一天只保留最新）
    hist = [h for h in hist if h.get('date') != today]
    hist.append({
        'date': today,
        'main_total': main_stat['total'],
        'main_valid': main_stat['valid'],
        'main_active': main_stat['active'],
        'oi_total': oi_stat['total'],
        'oi_buy_ready': oi_stat['buy_ready'],
        'pump_scans': pump_stat['scans_today'],
        'pump_new': pump_stat['new_alerts_today'],
    })
    state['history'] = hist[-14:]  # 保留14天
    return state

def push_msg(msg):
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target', JARVIS_TARGET,
             '--message', msg],
            capture_output=True, timeout=15
        )
    except Exception as e:
        print(f'[Dashboard] 推送失败: {e}')

# ────────────────────────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────────────────────────

def run(force_push=False):
    print(f'[Dashboard] 开始生成仪表盘 {NOW_DT.strftime("%H:%M UTC")}')

    # 采集
    main_signals  = load_main_signals(hours=24)
    oi_cands, oi_age = load_oi_signals()
    pump_log      = load_pump_signals(hours=24)
    pump_latest, pump_age = load_pump_latest()

    # 分析
    main_stat  = analyze_main_signals(main_signals)
    oi_stat    = analyze_oi_signals(oi_cands, oi_age)
    pump_stat  = analyze_pump_signals(pump_log, pump_latest, pump_age)

    # 状态
    state = load_state()
    state = update_daily_history(state, main_stat, oi_stat, pump_stat)

    # 格式化
    report = format_dashboard(main_stat, oi_stat, pump_stat, state)
    print(report)

    # 推送逻辑：
    # 1. 有实时有效信号 → 立即推送
    # 2. 强制推送标志 → 推送
    # 3. 距上次推送>4H → 定期汇总
    last_push = state.get('last_push', 0)
    has_active = main_stat['active'] > 0 or oi_stat['buy_ready'] > 0 or pump_stat['current_new'] > 0
    time_elapsed = NOW - last_push

    should_push = force_push or has_active or (time_elapsed > 4 * 3600)

    if should_push:
        push_msg(report)
        state['last_push'] = NOW
        print(f'[Dashboard] ✅ 仪表盘已推送')
    else:
        print(f'[Dashboard] 无新信号，距上次推送{time_elapsed/3600:.1f}H，静默')
        print('HEARTBEAT_OK')

    # 保存完整state（含统计数据）
    state['last_run'] = NOW
    state['last_stats'] = {
        'main': {k: v for k, v in main_stat.items() if k not in ('latest', 'active_signals')},
        'oi': {k: v for k, v in oi_stat.items() if k != 'top'},
        'pump': {k: v for k, v in pump_stat.items() if k != 'top_alerts'},
    }
    save_state(state)

    return {'main': main_stat, 'oi': oi_stat, 'pump': pump_stat}

if __name__ == '__main__':
    import sys
    force = '--force' in sys.argv or '-f' in sys.argv
    run(force_push=force)
