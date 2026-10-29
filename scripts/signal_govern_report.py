#!/usr/bin/env python3
"""
signal_govern_report.py — 每日拦截统计推送
[封印 2026-10-29 苏摩111]
每天 UTC 09:00 推送前24H的仲裁层战绩
"""
import re, time, sys
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).resolve().parent.parent
LOG     = BASE / 'logs' / 'signal_govern.log'
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(SCRIPTS))

def main():
    if not LOG.exists():
        print('[report] 日志文件不存在，跳过')
        return

    cutoff = time.time() - 86400
    lines  = LOG.read_text(encoding='utf-8', errors='ignore').splitlines()

    block_lines  = [l for l in lines if 'ARB-BLOCK' in l and _ts(l) > cutoff]
    pass_lines   = [l for l in lines if 'ARB-PASS'  in l and _ts(l) > cutoff]
    warn_lines   = [l for l in lines if 'ARB-WARN'  in l and _ts(l) > cutoff]

    # 按规则统计
    reasons = {}
    for l in block_lines:
        m = re.search(r'(L[0-3]:[^)]+|L\d[^→]+)', l)
        key = m.group(0)[:25] if m else 'other'
        reasons[key] = reasons.get(key, 0) + 1

    total = len(block_lines) + len(pass_lines)
    rate  = len(block_lines) * 100 // total if total > 0 else 0

    msg = (
        f'📊 梵天信号治理 v3.0 · 日报\n'
        f'━━━━━━━━━━━━━━━━\n'
        f'过去24H：{total}条信号\n'
        f'✅ 通过：{len(pass_lines)}条\n'
        f'🚫 拦截：{len(block_lines)}条（{rate}%）\n'
        f'⚠️ 软警告：{len(warn_lines)}条\n\n'
        f'拦截原因分布：\n'
    )
    for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1])[:5]:
        msg += f'  {reason}: {cnt}次\n'
    msg += f'\n🌿 姓赵不宣 | 系统自动报告'

    from push_hub import push_jarvis
    push_jarvis(msg, priority='P2', source='report', direction='neutral',
                dedup_key='signal_govern_daily', dedup_ttl=86400)
    print(f'[report] 推送完成 拦截{len(block_lines)} 通过{len(pass_lines)}')

def _ts(line):
    try:
        t = re.search(r'\[(\d{2}:\d{2}:\d{2})\]', line)
        if not t: return 0
        h,m,s = map(int, t.group(1).split(':'))
        now = time.gmtime()
        epoch = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, m, s, 0, 0, 0))
        return epoch - time.timezone
    except Exception:
        return 0

if __name__ == '__main__':
    main()
