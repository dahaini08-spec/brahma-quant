#!/usr/bin/env python3
"""
brahma_health.py — 梵天系统健康检查
[设计院 2026-08-06 重建]

职责：
  检查系统8项核心指标，输出 HEALTHY score/100 或 UNHEALTHY 详情
  供1号工程标准流程 Step1 调用

运行方式：
  python3 scripts/brahma_health.py           # 完整检查
  python3 scripts/brahma_health.py --quick   # 快速检查（跳过引擎测试）
"""

import os, sys, json, time, subprocess
from pathlib import Path

BASE  = Path(__file__).parent.parent
DATA  = BASE / 'data'
BRAIN = BASE / 'brahma_brain'

def check_memory():
    """内存可用量 >= 500MB"""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable'):
                    mb = int(line.split()[1]) // 1024
                    ok = mb >= 500
                    return ok, f'{mb}MB 可用'
    except:
        pass
    return True, '无法读取'

def check_disk():
    """磁盘剩余 >= 2GB"""
    import shutil
    total, used, free = shutil.disk_usage(str(BASE))
    free_gb = free / 1e9
    ok = free_gb >= 2.0
    return ok, f'{free_gb:.1f}GB 剩余'

def check_brahma_state():
    """brahma_state.json 存在且90分钟内更新"""
    p = DATA / 'brahma_state.json'
    if not p.exists():
        return False, '文件不存在'
    age_min = (time.time() - p.stat().st_mtime) / 60
    try:
        state = json.loads(p.read_text())
        regime = state.get('btc_regime', '?')
        ok = age_min < 90
        return ok, f'{age_min:.0f}min前 BTC={regime}'
    except:
        return False, '解析失败'

def check_liq_heatmap():
    """清算热图 BTC+ETH 存在且180分钟内更新"""
    results = []
    for sym in ['BTCUSDT', 'ETHUSDT']:
        p = DATA / f'liq_heatmap_{sym}.json'
        if not p.exists():
            results.append(f'{sym}缺失')
            continue
        age = (time.time() - p.stat().st_mtime) / 60
        if age > 180:
            results.append(f'{sym} {age:.0f}min前(过旧)')
        else:
            results.append(f'{sym} ✅{age:.0f}min')
    ok = all('缺失' not in r and '过旧' not in r for r in results)
    return ok, ' | '.join(results)

def check_signal_log():
    """信号日志存在且非空"""
    p = DATA / 'live_signal_log.jsonl'
    if not p.exists():
        return False, '文件不存在'
    lines = [l for l in p.read_text().strip().split('\n') if l.strip()]
    ok = len(lines) > 0
    return ok, f'{len(lines)}条记录'

def check_dlq():
    """DLQ死信队列为空"""
    p = DATA / 'dlq.jsonl'
    if not p.exists():
        return True, '空(文件不存在)'
    lines = [l for l in p.read_text().strip().split('\n') if l.strip()]
    ok = len(lines) == 0
    return ok, f'{len(lines)}条未处理' if lines else '空 ✅'

def check_cron():
    """核心cron任务均在ok状态"""
    try:
        r = subprocess.run(['openclaw', 'cron', 'list'],
            capture_output=True, text=True, timeout=10)
        ok_count = r.stdout.count(' ok ')
        idle_count = r.stdout.count(' idle ')
        total = ok_count + idle_count
        ok = ok_count >= 10  # 至少10个ok
        return ok, f'{ok_count}ok / {idle_count}idle / {total}总'
    except:
        return False, 'openclaw cron list 失败'

def check_engine(quick=False):
    """brahma_engine可正常导入（quick模式跳过analyze）"""
    if quick:
        p = BRAIN / 'brahma_engine.py'
        ok = p.exists() and p.stat().st_size > 10000
        return ok, f'文件{"存在" if ok else "缺失"} ({p.stat().st_size//1024}KB)' if p.exists() else '文件缺失'
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('brahma_engine', BRAIN / 'brahma_engine.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ok = hasattr(mod, 'analyze')
        return ok, 'analyze函数存在 ✅' if ok else 'analyze函数缺失'
    except Exception as e:
        return False, f'导入失败: {str(e)[:60]}'

def run(quick=False):
    checks = [
        ('内存',          check_memory),
        ('磁盘',          check_disk),
        ('体制状态',       check_brahma_state),
        ('清算热图',       check_liq_heatmap),
        ('信号日志',       check_signal_log),
        ('DLQ队列',        check_dlq),
        ('Cron任务',       check_cron),
        ('引擎完整性',     lambda: check_engine(quick=quick)),
    ]

    results = []
    for name, fn in checks:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f'检查异常: {e}'
        results.append((name, ok, msg))

    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)
    score  = round(passed / total * 100)
    status = 'HEALTHY' if score >= 75 else 'UNHEALTHY'

    print(f'\n{"="*50}')
    print(f'梵天系统健康检查 {"✅ " if status=="HEALTHY" else "❌ "}{status} {score}/{100}')
    print(f'{"="*50}')
    for name, ok, msg in results:
        icon = '✅' if ok else '❌'
        print(f'  {icon} {name:12s} {msg}')
    print(f'{"="*50}\n')

    return score, status, results

if __name__ == '__main__':
    quick = '--quick' in sys.argv
    score, status, _ = run(quick=quick)
    sys.exit(0 if status == 'HEALTHY' else 1)
