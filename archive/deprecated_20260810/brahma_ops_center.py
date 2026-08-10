#!/usr/bin/env python3
"""
brahma_ops_center.py — 梵天自运营中心 v1.0
设计院封印 2026-08-08 | 苏摩111批准

职责：
  --daily-health   系统体检日报（health+360+冒烟汇总）
  --cron-doctor    cron健康巡检+自动修复error/wrong-thread
  --signal-report  信号质量日报（SQE通过率/EV/拦截分析）
  --stepb-monitor  Step-B触发监控（BULL信号→500条自动触发BrahmaOptimizer）
  --disk-clean     磁盘自动清理（logs/core/archive）
  --full           以上全部（日报用）

所有操作：异常才推苏摩，正常完全静默（HEARTBEAT_OK）
"""

import sys, os, json, time, subprocess, shutil
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
SCRIPTS = BASE / 'scripts'
BRAIN   = BASE / 'brahma_brain'
DATA    = BASE / 'data'
LOGS    = BASE / 'logs'

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(SCRIPTS))

try:
    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
    _TARGET  = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
    _CHANNEL = JARVIS_CHANNEL
except Exception:
    _TARGET  = "73295708:thread:019fd9dd-4b0f-71db-87fb-1e192ccb2291"
    _CHANNEL = "jarvis"

CORRECT_THREAD = '019fd9dd-4b0f-71db-87fb-1e192ccb2291'


def push(msg: str):
    """推送到苏摩新线程"""
    try:
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', _CHANNEL,
            '--to', _TARGET,
            '--message', msg
        ], capture_output=True, text=True, timeout=15)
    except Exception as e:
        print(f"推送失败: {e}", file=sys.stderr)


def _cron_list():
    r = subprocess.run(
        ['openclaw', 'cron', 'list', '--json'],
        capture_output=True, text=True,
        cwd=str(BASE.parent.parent)
    )
    data = json.loads(r.stdout.strip())
    return data.get('jobs', data) if isinstance(data, dict) else data


# ═══════════════════════════════════════════════════════════
# 1. 系统体检日报
# ═══════════════════════════════════════════════════════════
def daily_health():
    lines = []
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    lines.append(f"🏥 梵天系统体检日报 | {ts}")
    lines.append("━" * 40)

    # brahma health
    try:
        r = subprocess.run(
            ['python3', '-W', 'ignore', 'scripts/brahma_health.py', '--quick'],
            capture_output=True, text=True, timeout=30, cwd=str(BASE)
        )
        status = 'HEALTHY' if 'HEALTHY' in r.stdout else 'UNHEALTHY'
        lines.append(f"{'✅' if status=='HEALTHY' else '🔴'} brahma_health: {status}")
    except Exception as e:
        lines.append(f"❌ brahma_health: {e}")

    # SQE状态
    try:
        sys.path.insert(0, str(BRAIN))
        import signal_quality_engine as sqe_mod
        sqe = sqe_mod.SignalQualityEngine()
        g1 = sqe.evaluate({'sl_pct':2.5,'regime':'BULL_TREND','direction':'LONG','score':130,'timing_status':''}).rejected
        g3 = sqe.evaluate({'sl_pct':1.3,'regime':'BULL_TREND','direction':'LONG','score':130,'timing_status':'READY'}).rejected
        ok = sqe.evaluate({'sl_pct':1.5,'regime':'BULL_TREND','direction':'LONG','score':130,'timing_status':''}).passed
        lines.append(f"{'✅' if g1 and g3 and ok else '❌'} SQE v2.0: G1={g1} G3={g3} PASS={ok}")
    except Exception as e:
        lines.append(f"❌ SQE: {e}")

    # 磁盘
    free_gb = shutil.disk_usage('/root').free / 1024**3
    lines.append(f"{'✅' if free_gb > 10 else '🔴'} 磁盘: /root {free_gb:.1f}GB可用")

    # core dump
    core_cnt = len(list(BASE.glob('core.*')))
    lines.append(f"{'✅' if core_cnt==0 else '🔴'} core dump: {core_cnt}个")

    # cron健康
    jobs = _cron_list()
    enabled = [j for j in jobs if j.get('enabled', True)]
    errors  = [j for j in enabled if j.get('state',{}).get('lastStatus') == 'error']
    wrong   = [j for j in enabled if CORRECT_THREAD not in j.get('delivery',{}).get('to','')]
    lines.append(f"{'✅' if not errors and not wrong else '🔴'} cron: {len(enabled)}启用 error={len(errors)} 错误线程={len(wrong)}")

    # 信号积累
    try:
        sl = (DATA / 'live_signal_log.jsonl').read_text().strip().split('\n')
        bull = [l for l in sl if 'BULL_TREND' in l]
        lines.append(f"✅ 信号: 总{len(sl)}条 BULL={len(bull)} Step-B={len(bull)}/500")
    except Exception as e:
        lines.append(f"❌ 信号日志: {e}")

    # 宏观
    try:
        mo = json.loads((DATA / 'macro_overlay.json').read_text())
        age = (time.time() - mo.get('ts',0)) / 60
        lines.append(f"✅ 宏观: {mo['state']} score={mo['score']} age={age:.0f}min")
    except:
        lines.append("⚠️ 宏观: 数据缺失")

    report = '\n'.join(lines)
    print(report)

    # 只有异常时才推苏摩
    has_issue = any(k in report for k in ['🔴','❌','error=0'[1:], 'UNHEALTHY'])
    if has_issue:
        push(report)
        return False
    print("HEARTBEAT_OK")
    return True


# ═══════════════════════════════════════════════════════════
# 2. cron健康巡检+自动修复
# ═══════════════════════════════════════════════════════════
def cron_doctor():
    jobs = _cron_list()
    enabled = [j for j in jobs if j.get('enabled', True)]
    errors  = [j for j in enabled if j.get('state',{}).get('lastStatus') == 'error']
    wrong   = [j for j in enabled if CORRECT_THREAD not in j.get('delivery',{}).get('to','')]

    fixed = []
    failed = []

    # 修复错误线程
    for j in wrong:
        jid  = j.get('id','')
        name = j.get('name','')
        r = subprocess.run([
            'openclaw', 'cron', 'edit', jid,
            '--to', f"73295708:thread:{CORRECT_THREAD}",
            '--channel', 'jarvis'
        ], capture_output=True, text=True, cwd=str(BASE.parent.parent))
        if r.returncode == 0:
            fixed.append(f"线程修复: {name}")
        else:
            failed.append(f"线程修复失败: {name}")

    # error状态：记录（无法自动reset，等下次运行自然恢复）
    persistent_errors = []
    for j in errors:
        errs = j.get('state',{}).get('consecutiveErrors', 0)
        if errs >= 3:
            persistent_errors.append(f"{j['name']}(连续{errs}次)")

    if fixed or failed or persistent_errors:
        msg_parts = ["🔧 [cron-doctor] 巡检发现问题:"]
        if fixed:
            msg_parts.append(f"已修复({len(fixed)}): " + ", ".join(fixed))
        if failed:
            msg_parts.append(f"修复失败: " + ", ".join(failed))
        if persistent_errors:
            msg_parts.append(f"持续error(需人工介入): " + ", ".join(persistent_errors))
        push('\n'.join(msg_parts))
        print('\n'.join(msg_parts))
    else:
        print(f"HEARTBEAT_OK: {len(enabled)}个cron全部健康")


# ═══════════════════════════════════════════════════════════
# 3. 信号质量日报
# ═══════════════════════════════════════════════════════════
def signal_report():
    try:
        sys.path.insert(0, str(BRAIN))
        import signal_quality_engine as sqe_mod
        sqe = sqe_mod.SignalQualityEngine()

        lines_raw = (DATA / 'live_signal_log.jsonl').read_text().strip().split('\n')
        today_ts = time.time() - 86400  # 最近24H

        today_sigs = []
        for l in lines_raw:
            try:
                s = json.loads(l)
                ts = s.get('timestamp', s.get('ts', 0))
                if isinstance(ts, str):
                    from datetime import datetime as dt2
                    ts = dt2.fromisoformat(ts.replace('Z','+00:00')).timestamp()
                if ts > today_ts:
                    today_sigs.append(s)
            except:
                pass

        if not today_sigs:
            print("HEARTBEAT_OK: 今日无新信号")
            return

        # SQE分析
        passed = []
        rejected = []
        for s in today_sigs:
            result = sqe.evaluate({
                'sl_pct': s.get('sl_pct', 0),
                'regime': s.get('regime', ''),
                'direction': s.get('direction', ''),
                'score': s.get('score', 0) or 0,
                'timing_status': s.get('timing_status', '') or ''
            })
            if result.passed:
                passed.append(s)
            else:
                rejected.append((s, result.reason))

        # 统计
        bull = [s for s in passed if 'BULL' in s.get('regime','')]
        total_sig = len(today_sigs)
        pass_rate = len(passed)/total_sig*100 if total_sig else 0

        ts_str = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
        msg = (
            f"📊 信号质量日报 | {ts_str}\n"
            f"今日信号: {total_sig}条\n"
            f"SQE通过: {len(passed)}条 ({pass_rate:.0f}%)\n"
            f"SQE拦截: {len(rejected)}条\n"
            f"BULL通过: {len(bull)}条\n"
            f"Step-B进度: {len([l for l in lines_raw if 'BULL_TREND' in l])}/500"
        )
        print(msg)
        if len(passed) > 0 or len(rejected) > 3:
            push(msg)

    except Exception as e:
        print(f"signal_report error: {e}")


# ═══════════════════════════════════════════════════════════
# 4. Step-B触发监控
# ═══════════════════════════════════════════════════════════
def stepb_monitor():
    try:
        lines = (DATA / 'live_signal_log.jsonl').read_text().strip().split('\n')
        bull_cnt = sum(1 for l in lines if 'BULL_TREND' in l)
        threshold = 500

        # 检查是否已触发过
        state_f = DATA / 'stepb_trigger_state.json'
        state = json.loads(state_f.read_text()) if state_f.exists() else {}

        if state.get('triggered') and bull_cnt >= threshold:
            print(f"HEARTBEAT_OK: Step-B已触发 bull={bull_cnt}")
            return

        if bull_cnt >= threshold and not state.get('triggered'):
            # 触发！
            push(
                f"🚀 [Step-B触发] BULL_TREND信号达到{bull_cnt}条！\n"
                f"BrahmaOptimizer开始自动重跑588组合网格搜索...\n"
                f"结果约30分钟内推送。"
            )
            # 运行BrahmaOptimizer
            r = subprocess.run(
                ['python3', '-W', 'ignore', 'brahma_brain/brahma_optimizer.py', '--auto'],
                capture_output=True, text=True, timeout=300, cwd=str(BASE)
            )
            state_f.write_text(json.dumps({'triggered': True, 'ts': time.time(), 'bull_cnt': bull_cnt}))
            result_summary = r.stdout[-500:] if r.stdout else r.stderr[-200:]
            push(f"✅ BrahmaOptimizer完成\n{result_summary}")
        else:
            remaining = threshold - bull_cnt
            print(f"HEARTBEAT_OK: Step-B进度 {bull_cnt}/{threshold} 差{remaining}条")

            # 每100条里程碑提醒苏摩
            milestone = (bull_cnt // 100) * 100
            state_mile = state.get('last_milestone', 0)
            if milestone > state_mile and milestone > 0:
                push(f"📈 Step-B里程碑: BULL信号={bull_cnt}条 ({bull_cnt/threshold*100:.0f}%)\n差{remaining}条触发Gate4封印")
                state['last_milestone'] = milestone
                state_f.write_text(json.dumps(state))

    except Exception as e:
        print(f"stepb_monitor error: {e}")


# ═══════════════════════════════════════════════════════════
# 5. 磁盘自动清理
# ═══════════════════════════════════════════════════════════
def disk_clean():
    cleaned = []
    now = time.time()

    # core dump
    cores = list(BASE.glob('core.*'))
    if cores:
        for f in cores:
            f.unlink()
        cleaned.append(f"core dump: {len(cores)}个")

    # 7天以上的日志
    log_cleaned = 0
    for f in LOGS.glob('*.log'):
        if (now - f.stat().st_mtime) > 7*86400 and f.stat().st_size > 512*1024:
            f.unlink()
            log_cleaned += 1
    if log_cleaned:
        cleaned.append(f"旧日志: {log_cleaned}个")

    # noai_runner.log 超过5MB截断
    noai_log = LOGS / 'noai_runner.log'
    if noai_log.exists() and noai_log.stat().st_size > 5*1024*1024:
        content = noai_log.read_bytes()
        noai_log.write_bytes(content[-1024*1024:])  # 保留最后1MB
        cleaned.append("noai_runner.log截断(5MB→1MB)")

    free_gb = shutil.disk_usage('/root').free / 1024**3

    if cleaned:
        msg = f"🧹 [disk-clean] 清理完成: {', '.join(cleaned)}\n/root可用: {free_gb:.1f}GB"
        print(msg)
        push(msg)
    else:
        print(f"HEARTBEAT_OK: 磁盘健康 {free_gb:.1f}GB可用")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--daily-health',  action='store_true')
    parser.add_argument('--cron-doctor',   action='store_true')
    parser.add_argument('--signal-report', action='store_true')
    parser.add_argument('--stepb-monitor', action='store_true')
    parser.add_argument('--disk-clean',    action='store_true')
    parser.add_argument('--full',          action='store_true')
    args = parser.parse_args()

    if args.full or args.daily_health:
        daily_health()
    if args.full or args.cron_doctor:
        cron_doctor()
    if args.full or args.signal_report:
        signal_report()
    if args.full or args.stepb_monitor:
        stepb_monitor()
    if args.full or args.disk_clean:
        disk_clean()

    if not any(vars(args).values()):
        parser.print_help()
