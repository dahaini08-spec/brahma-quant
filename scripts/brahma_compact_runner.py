#!/usr/bin/env python3
"""
brahma_compact_runner.py — 梵天分析结果压缩包装器
将brahma_1hao_analysis.py的135行输出压缩为5行结构化摘要
P0-A 上下文压缩落地 [苏摩111 2026-07-19]
"""
import sys, subprocess, re
from pathlib import Path

BASE = Path(__file__).parent.parent

def run_compact(symbol: str) -> str:
    """运行分析并返回压缩摘要（5行以内）"""
    r = subprocess.run(
        ['python3', 'scripts/brahma_1hao_analysis.py', '--symbols', symbol],
        capture_output=True, text=True, timeout=30, cwd=str(BASE)
    )
    out = r.stdout + r.stderr

    # 提取关键字段
    score = re.search(r'score=([0-9.]+)', out)
    grade = re.search(r'grade=([0-9.]+)', out)
    direction = re.search(r'方向:\s*(LONG|SHORT)', out)
    regime = re.search(r'(?:体制|REGIME)[：:]\s*([A-Z_]+)', out)
    timing = re.search(r'(READY|MONITOR|WAIT|STANDBY)', out)
    blocked = 'BLOCKED' in out or 'globally_blocked' in out or '封禁' in out
    entry = re.search(r'入场区[：:]\s*([^\n]+)', out)
    sl = re.search(r'参考止损[：:]\s*([^\n]+)', out)
    tp1 = re.search(r'参考TP1[：:]\s*([^\n]+)', out)

    if r.returncode != 0 and not score:
        return f"ERROR: {out[-100:]}"

    s = float(score.group(1)) if score else 0
    g = float(grade.group(1)) if grade else 0

    # GATE-0封禁 → 立即返回，不注入长文本
    if blocked and s < 138:
        return f"HEARTBEAT_OK  [{symbol} score={s:.0f} BLOCKED/低分]"

    # 低分 → 简短返回
    if s < 138:
        return f"HEARTBEAT_OK  [{symbol} score={s:.0f} < 138]"

    # 有效信号 → 结构化5行摘要
    dir_str = direction.group(1) if direction else '?'
    reg_str = regime.group(1) if regime else '?'
    tim_str = timing.group(1) if timing else '?'
    ent_str = entry.group(1).strip()[:40] if entry else '?'
    sl_str  = sl.group(1).strip()[:30] if sl else '?'
    tp1_str = tp1.group(1).strip()[:30] if tp1 else '?'

    return (
        f"SIGNAL  {symbol} {dir_str}  score={s:.0f}  grade={g:.0f}  regime={reg_str}  timing={tim_str}\n"
        f"  进场区: {ent_str}\n"
        f"  止损:  {sl_str}\n"
        f"  TP1:   {tp1_str}\n"
        f"  → score>={'155✅' if s>=155 else '138⚡'} 推送信号卡"
    )

if __name__ == '__main__':
    sym = sys.argv[1] if len(sys.argv) > 1 else 'BTCUSDT'
    print(run_compact(sym))
