#!/usr/bin/env python3
"""
blacktea_approval.py — 审批确认处理器
2026-07-10 苏摩111批准 · 对标 nmrtn/blacktea

当苏摩回复 "111" / "批准" / "approve" 时调用此脚本
→ 标记 approval_pending.json 中的记录为 approved=True
→ 触发 auto_executor 重新执行该信号（此次跳过审批门直接执行）
"""
import sys, os, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

APPROVAL_RECORD_PATH = BASE / 'data' / 'approval_pending.json'

try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    JARVIS_TARGET = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'
except:
    JARVIS_TARGET  = '73295708:thread:019f309c-609b-7a75-a195-e221e5927c63'
    JARVIS_CHANNEL = 'jarvis'


def send_msg(msg):
    subprocess.run(
        ['openclaw', 'message', 'send',
         '--channel', JARVIS_CHANNEL, '--to', JARVIS_TARGET,
         '--message', msg],
        capture_output=True, timeout=10
    )


def process_approval():
    """处理苏摩审批确认"""
    if not APPROVAL_RECORD_PATH.exists():
        print('无待审批记录')
        return

    pending = json.loads(APPROVAL_RECORD_PATH.read_text())
    now_ts  = time.time()

    if not pending:
        print('无待审批记录')
        return

    approved_list = []
    expired_list  = []

    for key, rec in list(pending.items()):
        age_min = (now_ts - rec.get('requested_at', 0)) / 60

        if age_min > 60:  # 超过60min的记录清理
            expired_list.append(key)
            continue

        if not rec.get('approved'):
            # 标记为已批准
            pending[key]['approved']    = True
            pending[key]['approved_at'] = now_ts
            approved_list.append(rec)
            print(f'✅ 批准: {rec["symbol"]} {rec["direction"]} ${rec["notional"]:.1f}')

    # 清理过期记录
    for k in expired_list:
        del pending[k]
        print(f'🗑️  清理过期: {k}')

    APPROVAL_RECORD_PATH.write_text(json.dumps(pending, indent=2))

    if approved_list:
        # 通知苏摩
        names = ', '.join(f'{r["symbol"]} {r["direction"]}' for r in approved_list)
        send_msg(
            f'✅ [blacktea] 审批已确认: {names}\n'
            f'auto_executor 下次运行时将按原仓位执行（约1-2min内）'
        )
        # 立即触发 auto_executor（不等cron）
        subprocess.Popen(
            ['python3', str(BASE / 'scripts' / 'auto_executor.py')],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f'✅ 已触发auto_executor立即执行')
    else:
        print('无新审批项')


def show_pending():
    """查看当前待审批列表"""
    if not APPROVAL_RECORD_PATH.exists():
        print('无待审批记录')
        return

    pending = json.loads(APPROVAL_RECORD_PATH.read_text())
    now_ts  = time.time()

    if not pending:
        print('无待审批记录')
        return

    print(f'当前待审批 ({len(pending)}条):')
    for key, rec in pending.items():
        age_min = (now_ts - rec.get('requested_at', 0)) / 60
        remaining = max(0, 30 - age_min)
        status = '✅已批准' if rec.get('approved') else f'⏳等待中({remaining:.0f}min剩余)'
        print(f'  {rec["symbol"]} {rec["direction"]} ${rec["notional"]:.1f} score={rec["score"]:.0f} | {status}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--show',    action='store_true', help='查看待审批列表')
    ap.add_argument('--approve', action='store_true', help='批准所有待审批（苏摩111触发）')
    ap.add_argument('--clear',   action='store_true', help='清空所有审批记录')
    args = ap.parse_args()

    if args.show:
        show_pending()
    elif args.approve:
        process_approval()
    elif args.clear:
        APPROVAL_RECORD_PATH.write_text('{}')
        print('✅ 审批记录已清空')
    else:
        # 默认：查看
        show_pending()
