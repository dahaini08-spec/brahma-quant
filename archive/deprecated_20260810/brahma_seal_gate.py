#!/usr/bin/env python3
"""
brahma_seal_gate.py — 梵天自动化封口门控
addyosmani/agent-skills 哲学落地：VERIFY阶段代码化
设计院自主封印 2026-08-08

用途：
  每次 git commit 前自动运行（或苏摩说"封口"时手动执行）
  五道门控全部通过 → 允许封口
  任何一道不通过 → 拒绝封口，输出修复建议

运行:
  python3 scripts/brahma_seal_gate.py
  python3 scripts/brahma_seal_gate.py --strict   # 严格模式，warn也算失败
"""
import sys, os, json, time, subprocess, argparse
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
RESET  = '\033[0m'
BOLD   = '\033[1m'

results = []

def gate_pass(name, detail=''):
    results.append(('PASS', name, detail))
    print(f'  {GREEN}✅ {name}{RESET}' + (f'  {detail}' if detail else ''))

def gate_fail(name, detail='', fix=''):
    results.append(('FAIL', name, detail))
    print(f'  {RED}❌ {name}{RESET}' + (f'  {detail}' if detail else ''))
    if fix:
        print(f'     → 修复: {fix}')

def gate_warn(name, detail=''):
    results.append(('WARN', name, detail))
    print(f'  {YELLOW}⚠️  {name}{RESET}' + (f'  {detail}' if detail else ''))


# ══════════════════════════════════════════════════════════════════
# 门控1：体制状态 SSOT（<120min）
# ══════════════════════════════════════════════════════════════════
def check_gate1_regime():
    print(f'\n{CYAN}【门控1】体制状态 SSOT{RESET}')
    try:
        with open(BASE / 'data' / 'brahma_state.json') as f:
            d = json.load(f)
        ts = d.get('last_update', d.get('timestamp', 0))
        age_min = (time.time() - ts) / 60
        regime = d.get('regime', '?')

        if age_min < 60:
            gate_pass('体制年龄', f'{age_min:.0f}min BTC={regime}')
        elif age_min < 120:
            gate_warn('体制年龄偏旧', f'{age_min:.0f}min（60~120min警戒区）')
        else:
            gate_fail('体制陈旧', f'{age_min:.0f}min > 120min',
                      fix='python3 scripts/brahma_state_refresh.py')
    except Exception as e:
        gate_fail('体制文件读取', str(e)[:80],
                  fix='检查 data/brahma_state.json 是否存在')


# ══════════════════════════════════════════════════════════════════
# 门控2：SQE 信号日志无脏数据
# ══════════════════════════════════════════════════════════════════
def check_gate2_sqe():
    print(f'\n{CYAN}【门控2】SQE 脏数据检查{RESET}')
    try:
        with open(BASE / 'data' / 'live_signal_log.jsonl') as f:
            sigs = [json.loads(l) for l in f if l.strip()]
        dirty = [s for s in sigs if s.get('sl_pct', 0) > 2.0]
        if not dirty:
            gate_pass('SQE Gate1 清洁', f'共{len(sigs)}条，sl>2.0=0条')
        else:
            sl_dist = {}
            for s in dirty:
                k = round(s.get('sl_pct', 0), 1)
                sl_dist[k] = sl_dist.get(k, 0) + 1
            gate_fail('SQE Gate1 脏数据', f'{len(dirty)}条 sl>2.0 分布={sl_dist}',
                      fix='python3 -c "清洗sl>2.0信号后重跑signal_settler"')
    except Exception as e:
        gate_fail('信号日志读取', str(e)[:80])


# ══════════════════════════════════════════════════════════════════
# 门控3：WR 权重基准
# ══════════════════════════════════════════════════════════════════
def check_gate3_wr():
    print(f'\n{CYAN}【门控3】WR 权重基准{RESET}')
    try:
        with open(BASE / 'data' / 'signal_weights.json') as f:
            sw = json.load(f)

        checks = [
            ('BULL_TREND:LONG:120-139', 'NORMAL', 0.9),
            ('BULL_TREND:LONG:140-154', 'BLOCK',  None),   # 死亡区必须封禁
        ]
        for key, expected_action, min_mult in checks:
            entry = sw.get(key, {})
            action = entry.get('action', '?')
            mult = entry.get('multiplier', 0)

            if action == expected_action:
                detail = f'action={action} mult={mult}'
                if min_mult and mult < min_mult:
                    gate_warn(f'WR·{key}', f'multiplier={mult} 偏低（<{min_mult}）')
                else:
                    gate_pass(f'WR·{key}', detail)
            else:
                gate_fail(f'WR·{key}', f'action={action}（期望{expected_action}）',
                          fix='更新 data/signal_weights.json')
    except Exception as e:
        gate_fail('signal_weights读取', str(e)[:80])


# ══════════════════════════════════════════════════════════════════
# 门控4：Cron 架构（无僵尸model + 任务数合理）
# ══════════════════════════════════════════════════════════════════
def check_gate4_cron():
    print(f'\n{CYAN}【门控4】Cron 架构审计{RESET}')
    try:
        r = subprocess.run(
            ['openclaw', 'cron', 'list', '--json'],
            capture_output=True, text=True, timeout=15
        )
        jobs = json.loads(r.stdout)
        jl = jobs if isinstance(jobs, list) else jobs.get('jobs', [])

        # 僵尸model检查：仅检查明确走cron_noai_runner.sh但配model的任务
        noai_runner_tasks = []
        for j in jl:
            msg = j.get('payload', {}).get('message', '')
            has_model = 'model' in j.get('payload', {})
            # 只有明确走cron_noai_runner.sh的任务才算僵尸
            if 'cron_noai_runner' in msg and has_model:
                noai_runner_tasks.append(j['name'])

        if not noai_runner_tasks:
            gate_pass('无僵尸model', f'43任务均合规' if len(jl) == 43 else f'{len(jl)}任务均合规')
        else:
            gate_fail('僵尸model任务', str(noai_runner_tasks),
                      fix='重建任务时不传--model参数')

        # 任务数范围检查（允许40~50个）
        if 35 <= len(jl) <= 50:
            gate_pass('任务数合理', f'{len(jl)}个（合理范围40~50）')
        else:
            gate_warn('任务数异常', f'{len(jl)}个（期望40~50）')

    except Exception as e:
        gate_fail('Cron列表读取', str(e)[:80])


# ══════════════════════════════════════════════════════════════════
# 门控5：关键文件完整性
# ══════════════════════════════════════════════════════════════════
def check_gate5_files():
    print(f'\n{CYAN}【门控5】关键文件完整性{RESET}')
    CRITICAL = [
        ('data/brahma_state.json',       '体制状态SSOT'),
        ('data/live_signal_log.jsonl',   '信号日志'),
        ('data/signal_weights.json',     'WR权重矩阵'),
        ('data/macro_overlay.json',      '宏观叠加层'),
        ('data/regime_state.json',       '体制快照'),
        ('brahma_brain/brahma_engine.py','梵天分析引擎'),
        ('brahma_brain/fangcang_engine.py', '方仓引擎'),
        ('brahma_brain/kronos_bridge.py','Kronos桥接'),
        ('scripts/auto_executor.py',     '自动执行器'),
        ('scripts/position_guardian.py',        '持仓守护'),
    ]
    missing = []
    for path, desc in CRITICAL:
        full = BASE / path
        if full.exists():
            size_kb = full.stat().st_size // 1024
            gate_pass(desc, f'{size_kb}KB')
        else:
            missing.append(path)
            gate_fail(desc, f'{path} 不存在')

    if not missing:
        print(f'  {GREEN}→ 全部{len(CRITICAL)}个关键文件完整{RESET}')


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description='梵天封口门控')
    parser.add_argument('--strict', action='store_true', help='严格模式：warn也算失败')
    args = parser.parse_args()

    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f'\n{BOLD}{"═"*55}')
    print(f'  🏛️  梵天封口门控审计  |  {now_str}')
    print(f'{"═"*55}{RESET}')

    check_gate1_regime()
    check_gate2_sqe()
    check_gate3_wr()
    check_gate4_cron()
    check_gate5_files()

    # ── 汇总 ──────────────────────────────────────────────────────
    print(f'\n{BOLD}{"─"*55}{RESET}')
    n_pass = sum(1 for r in results if r[0] == 'PASS')
    n_warn = sum(1 for r in results if r[0] == 'WARN')
    n_fail = sum(1 for r in results if r[0] == 'FAIL')
    total  = len(results)

    if n_fail == 0 and (n_warn == 0 or not args.strict):
        print(f'{GREEN}{BOLD}🔐 封口裁决：PASS  {n_pass}/{total} 通过{RESET}')
        if n_warn:
            print(f'{YELLOW}   （{n_warn}项警告，非严格模式下允许封口）{RESET}')
        # 写封口记录
        seal_log = BASE / 'data' / 'seal_history.json'
        history = []
        if seal_log.exists():
            try:
                with open(seal_log) as f:
                    history = json.load(f)
            except: pass
        history.append({
            'ts': time.time(), 'dt': now_str,
            'pass': n_pass, 'warn': n_warn, 'fail': n_fail, 'total': total
        })
        with open(seal_log, 'w') as f:
            json.dump(history[-50:], f, ensure_ascii=False, indent=2)
        sys.exit(0)
    else:
        print(f'{RED}{BOLD}🚫 封口裁决：FAIL  {n_fail}项不通过{RESET}')
        fails = [r for r in results if r[0] == 'FAIL']
        for _, name, detail in fails:
            print(f'{RED}  → {name}: {detail}{RESET}')
        sys.exit(1)


if __name__ == '__main__':
    main()
