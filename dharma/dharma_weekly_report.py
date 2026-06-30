#!/usr/bin/env python3
"""
dharma_weekly_report.py v2.0 — 达摩院内部健康监测
设计院裁决 2026-05-31

职责：每周一运行，内部统计，不发广场帖
输出三项核心指标：
  1. WR（grade≥60门控效果）
  2. 触发率变化
  3. 武曲校准建议

原则：数据监测 > 广场宣传
"""
import os, sys, json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DHARMA_DIR  = Path(__file__).parent
TRADING_DIR = DHARMA_DIR.parent
sys.path.insert(0, str(TRADING_DIR))
sys.path.insert(0, str(TRADING_DIR / 'scripts'))

BJ = timezone(timedelta(hours=8))

def now_bj(): return datetime.now(BJ).strftime('%Y-%m-%d %H:%M')

def load_signals(days=7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sig_log = TRADING_DIR / 'data' / 'live_signal_log.jsonl'
    if not sig_log.exists():
        return [], []
    all_sigs, recent = [], []
    for line in sig_log.read_text().strip().split('\n'):
        if not line.strip(): continue
        try:
            d = json.loads(line)
            if d.get('_data_quality'): continue
            all_sigs.append(d)
            ts = datetime.fromisoformat(str(d.get('ts','')).replace('Z','+00:00'))
            if ts >= cutoff:
                recent.append(d)
        except: pass
    return all_sigs, recent

def calc_stats(signals):
    triggered = [s for s in signals if s.get('outcome') in ('TP1','TP2','SL')]
    wins      = [s for s in triggered if 'TP' in str(s.get('outcome',''))]
    timeouts  = [s for s in signals if s.get('outcome') in ('TIMEOUT','UNTRIGGERED')]

    wr       = len(wins)/len(triggered) if triggered else 0
    trig_r   = len(triggered)/len(signals) if signals else 0

    # grade 分组 WR
    grade_wr = {}
    for band, lo, hi in [('<60',0,60),('60-79',60,80),('80+',80,999)]:
        g_trig = [s for s in triggered if lo <= int(s.get('structure_grade') or 0) < hi]
        g_wins = [s for s in g_trig if 'TP' in str(s.get('outcome',''))]
        if g_trig:
            grade_wr[band] = {'n': len(g_trig), 'wr': len(g_wins)/len(g_trig)}

    return {
        'total': len(signals),
        'triggered': len(triggered),
        'wins': len(wins),
        'timeouts': len(timeouts),
        'wr': wr,
        'trig_r': trig_r,
        'grade_wr': grade_wr,
    }

def load_wuqu():
    state_path = TRADING_DIR / 'data' / 'wuqu_paper_state.json'
    try:
        s = json.loads(state_path.read_text())
        n = s.get('total_paper', 0)
        wins = s.get('wins', 0)
        wr = wins/n if n > 0 else 0
        cal = s.get('last_calibration')
        return {'n': n, 'wr': wr, 'calibration': cal}
    except:
        return {'n': 0, 'wr': 0, 'calibration': None}

def health_status(stats):
    """系统健康状态：绿/黄/红"""
    wr = stats['wr']
    trig_r = stats['trig_r']
    if wr >= 0.70 and trig_r >= 0.45:
        return '🟢 健康', '核心指标正常'
    elif wr >= 0.55 and trig_r >= 0.30:
        return '🟡 观察', '部分指标偏弱'
    else:
        return '🔴 警告', '核心指标异常，需要诊断'

def main():
    all_sigs, recent = load_signals(days=7)
    all_stats     = calc_stats(all_sigs)
    recent_stats  = calc_stats(recent)
    wuqu          = load_wuqu()

    status, status_desc = health_status(all_stats)

    print(f'╔══════════════════════════════════════════╗')
    print(f'  达摩院周报 v2.0 · {now_bj()}')
    print(f'  系统状态: {status} {status_desc}')
    print(f'╠══════════════════════════════════════════╣')
    print(f'')
    print(f'【全量统计（{len(all_sigs)}条干净信号）】')
    print(f'  整体WR:   {all_stats["wr"]:.1%} ({all_stats["wins"]}/{all_stats["triggered"]})')
    print(f'  触发率:   {all_stats["trig_r"]:.1%} ({all_stats["triggered"]}/{all_stats["total"]})')
    print(f'  未触发:   {all_stats["timeouts"]}条 ({all_stats["timeouts"]/all_stats["total"]:.0%})')
    print(f'')
    print(f'  grade门控效果:')
    for band, v in all_stats['grade_wr'].items():
        bar = '█'*int(v['wr']*10)+'░'*(10-int(v['wr']*10))
        print(f'    grade {band:<8} WR={v["wr"]:.0%} n={v["n"]:>3}  {bar}')
    print(f'')

    if recent:
        print(f'【本周新增（{len(recent)}条）】')
        print(f'  WR:     {recent_stats["wr"]:.1%} ({recent_stats["wins"]}/{recent_stats["triggered"]})')
        print(f'  触发率: {recent_stats["trig_r"]:.1%}')
        print(f'')

    print(f'【武曲校准器状态】')
    print(f'  Paper记录: {wuqu["n"]}/100条')
    if wuqu["n"] > 0:
        print(f'  Paper WR:  {wuqu["wr"]:.1%}')
    cal = wuqu.get('calibration')
    if cal:
        print(f'  校准建议: 门槛={cal.get("suggested_threshold")}  ({cal.get("reason","")})')
    else:
        print(f'  校准建议: 等待积累30条后校准（当前{wuqu["n"]}条）')
    print(f'')
    print(f'╚══════════════════════════════════════════╝')

    # 写入日志
    report = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'all_wr': all_stats['wr'],
        'all_trig_r': all_stats['trig_r'],
        'recent_n': len(recent),
        'recent_wr': recent_stats['wr'],
        'wuqu_n': wuqu['n'],
        'wuqu_wr': wuqu['wr'],
        'threshold_suggestion': cal.get('suggested_threshold') if cal else 145,
    }
    log_path = TRADING_DIR / 'data' / 'dharma_weekly_log.jsonl'
    with open(log_path, 'a') as f:
        f.write(json.dumps(report, ensure_ascii=False, default=str) + '\n')

if __name__ == '__main__':
    main()
