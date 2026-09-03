#!/usr/bin/env python3
"""
paper_promotion_check.py — 纸面→实盘晋升门控
设计院封印 2026-09-03 苏摩111

接入位置：
  - scripts/paper_promotion_check.py（本文件）
  - openclaw cron: daily（每天由 paper-daily-report 调用）
  - 手动: python3 scripts/paper_promotion_check.py

考核标准（两档）：
  纸面验证档：WR≥55% + n≥10 + avg_pnl>0  →  推送结果报告
  实盘晋升档：WR≥60% + n≥30 + avg_pnl≥0.5%  →  推送「可申请实盘」

数据源：data/paper_positions.json（paper_executor + paper_tp_monitor写入）
"""
import json, time, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

PAPER_POS_FILE  = BASE / 'data' / 'paper_positions.json'
PROMOTION_STATE = BASE / 'data' / 'paper_promotion_state.json'

# ── 晋升门槛 ──────────────────────────────────────────────────────
STAGE1_WR  = 55.0   # 纸面验证通过线
STAGE1_N   = 10     # 最少笔数
STAGE2_WR  = 60.0   # 实盘晋升线
STAGE2_N   = 30     # 最少笔数
STAGE2_PNL = 0.5    # 平均盈亏% 下限

def load_paper_positions() -> dict:
    if PAPER_POS_FILE.exists():
        try:
            return json.loads(PAPER_POS_FILE.read_text())
        except Exception:
            pass
    return {'positions': [], 'closed': [], 'stats': {'total': 0, 'win': 0, 'pnl': 0.0}}

def load_promotion_state() -> dict:
    if PROMOTION_STATE.exists():
        try:
            return json.loads(PROMOTION_STATE.read_text())
        except Exception:
            pass
    return {'stage': 0, 'last_push_ts': 0, 'last_push_n': 0}

def save_promotion_state(state: dict):
    PROMOTION_STATE.write_text(json.dumps(state, indent=2))

def calc_stats(closed: list) -> dict:
    """计算胜率、平均盈亏等核心指标"""
    n = len(closed)
    if n == 0:
        return {'n': 0, 'wr': 0.0, 'avg_pnl': 0.0, 'total_pnl': 0.0,
                'win': 0, 'loss': 0, 'max_win': 0.0, 'max_loss': 0.0}

    wins  = [t for t in closed if t.get('pnl_pct', 0) > 0]
    pnls  = [t.get('pnl_pct', 0) for t in closed]
    wr    = len(wins) / n * 100
    avg   = sum(pnls) / n
    total = sum(pnls)

    return {
        'n':         n,
        'wr':        round(wr, 1),
        'avg_pnl':   round(avg, 3),
        'total_pnl': round(total, 3),
        'win':       len(wins),
        'loss':      n - len(wins),
        'max_win':   round(max(pnls), 3),
        'max_loss':  round(min(pnls), 3),
    }

def regime_breakdown(closed: list) -> dict:
    """按体制分组胜率"""
    regimes = {}
    for t in closed:
        r = t.get('regime', 'UNKNOWN')
        if r not in regimes:
            regimes[r] = {'n': 0, 'win': 0, 'pnl': 0.0}
        regimes[r]['n'] += 1
        p = t.get('pnl_pct', 0)
        regimes[r]['pnl'] += p
        if p > 0:
            regimes[r]['win'] += 1
    return {
        r: {
            'n': v['n'],
            'wr': round(v['win']/v['n']*100, 1),
            'avg_pnl': round(v['pnl']/v['n'], 3),
        }
        for r, v in regimes.items()
    }

def build_report(stats: dict, breakdown: dict, open_count: int) -> str:
    """构建推送报告"""
    n   = stats['n']
    wr  = stats['wr']
    avg = stats['avg_pnl']

    # 晋升判断
    stage2_ok  = wr >= STAGE2_WR and n >= STAGE2_N and avg >= STAGE2_PNL
    stage1_ok  = wr >= STAGE1_WR and n >= STAGE1_N and avg > 0

    if stage2_ok:
        badge = '🟢 实盘晋升资格已达标'
        action = f'WR={wr:.1f}% n={n} avg={avg:+.2f}%\n⚡ 发送 111 确认开启实盘'
    elif stage1_ok:
        badge = '🟡 纸面验证通过'
        need_n   = max(0, STAGE2_N - n)
        need_wr  = STAGE2_WR
        action = f'WR={wr:.1f}% n={n}\n继续积累: 还需{need_n}笔 且WR≥{need_wr}%'
    else:
        badge = '🔵 纸面验证中'
        need_n = max(0, STAGE1_N - n)
        action = f'WR={wr:.1f}% n={n}\n还需: {need_n}笔 且WR≥{STAGE1_WR}%'

    lines = [
        f'📊 梵天纸面系统日报 | {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        f'{badge}',
        '',
        f'📈 总体统计 (n={n})',
        f'  胜率: {wr:.1f}%  ({stats["win"]}胜/{stats["loss"]}负)',
        f'  平均盈亏: {avg:+.2f}%  总计: {stats["total_pnl"]:+.2f}%',
        f'  最大盈: {stats["max_win"]:+.2f}%  最大亏: {stats["max_loss"]:+.2f}%',
        f'  当前开仓: {open_count} 个',
        '',
    ]

    if breakdown:
        lines.append('🔍 按体制分组:')
        for r, v in sorted(breakdown.items(), key=lambda x: -x[1]['wr']):
            lines.append(f'  {r}: WR={v["wr"]:.0f}% n={v["n"]} avg={v["avg_pnl"]:+.2f}%')
        lines.append('')

    lines.append(f'🎯 {action}')
    return '\n'.join(lines)

def push_to_jarvis(msg: str):
    """推送到Jarvis"""
    try:
        import subprocess
        result = subprocess.run(
            ['openclaw', 'message', 'send',
             '--to', '73295708:thread:01a03e25-a459-733e-a2ba-a56083050f26',
             '--channel', 'jarvis',
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            print('[promotion_check] ✅ 推送成功')
        else:
            print(f'[promotion_check] ⚠️  推送失败: {result.stderr[:80]}')
    except Exception as e:
        print(f'[promotion_check] ⚠️  推送异常: {e}')

def main():
    data     = load_paper_positions()
    closed   = data.get('closed', [])
    open_pos = data.get('positions', [])
    stats    = calc_stats(closed)
    breakdown = regime_breakdown(closed)
    pstate   = load_promotion_state()

    print(f'[promotion_check] 已结算: {stats["n"]}笔 WR={stats["wr"]:.1f}% avg={stats["avg_pnl"]:+.2f}%')
    print(f'[promotion_check] 当前开仓: {len(open_pos)}个')

    # 判断是否需要推送
    now_ts = time.time()
    last_ts = pstate.get('last_push_ts', 0)
    last_n  = pstate.get('last_push_n', 0)
    new_closed = stats['n'] - last_n

    # 每天至少推送一次，或每新增5笔结算时推送
    should_push = (
        now_ts - last_ts > 86400  or   # 超过24h
        new_closed >= 5                 # 新增5笔结算
    )

    report = build_report(stats, breakdown, len(open_pos))
    print(report)

    if should_push:
        push_to_jarvis(report)
        pstate['last_push_ts'] = now_ts
        pstate['last_push_n']  = stats['n']

        # 判断晋升阶段
        if stats['wr'] >= STAGE2_WR and stats['n'] >= STAGE2_N and stats['avg_pnl'] >= STAGE2_PNL:
            pstate['stage'] = 2
        elif stats['wr'] >= STAGE1_WR and stats['n'] >= STAGE1_N:
            pstate['stage'] = 1

        save_promotion_state(pstate)

    return stats

if __name__ == '__main__':
    main()
