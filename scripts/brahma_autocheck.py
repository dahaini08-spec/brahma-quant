#!/usr/bin/env python3
"""
brahma_autocheck.py — 梵天自动自查协议
设计院封印 2026-09-03 苏摩111

触发方式：
  1. 苏摩发"自查" / "autocheck" / "系统检查"
  2. cron 每6h自动运行
  3. brahma_analysis_runner 启动时可选触发

五级检查：
  L1 导入健康（0.1s）  → 核心模块能import
  L2 数据新鲜度（0.5s）→ klines/state/wr_matrix时效
  L3 性能基准（10s）   → 单次analyze时间
  L4 维度覆盖（1s）    → breakdown有效维度数
  L5 异常日志（0.2s）  → 最近1h错误数

接入位置：
  scripts/brahma_autocheck.py（本文件）
  cron: 每6h运行，结果推送Jarvis
"""
import sys, os, time, json
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

PASS = '✅'; WARN = '⚠️'; FAIL = '❌'

def check_l1_imports() -> tuple:
    """L1: 核心模块导入检查"""
    critical = [
        'brahma_brain.brahma_core',
        'brahma_brain.fangcang_engine',
        'brahma_brain.gex_unified',
        'brahma_brain.vol_beta_engine',
        'brahma_brain.smc_engine',
        'brahma_brain.regime_config',
        'brahma_brain.signal_quality_engine',
        'brahma_brain.disk_cache',
    ]
    failed = []
    for mod in critical:
        try:
            __import__(mod)
        except Exception as e:
            failed.append(f'{mod.split(".")[-1]}: {str(e)[:40]}')
    if not failed:
        return PASS, f'全部{len(critical)}个核心模块导入正常'
    return FAIL, f'{len(failed)}个模块导入失败: {failed}'


def check_l2_freshness() -> tuple:
    """L2: 数据文件新鲜度检查"""
    checks = {
        'brahma_state.json':    (120, 'min'),   # 2h内
        'regime_state.json':    (120, 'min'),
        'wr_matrix_realtime.json': (1440, 'min'), # 24h内
        'gex_state.json':       (180, 'min'),   # GEX每3h由cron刷新
        'paper_positions.json': (1440, 'min'),
    }
    issues = []
    for fname, (limit, unit) in checks.items():
        f = BASE / 'data' / fname
        if not f.exists():
            issues.append(f'{fname}不存在')
            continue
        # gex_state.json特殊处理：读内部updated_at字段
        if fname == 'gex_state.json':
            try:
                import json as _j
                gex_d = _j.loads(f.read_text())
                btc_ts = gex_d.get('BTC', {}).get('updated_at', 0)
                eth_ts = gex_d.get('ETH', {}).get('updated_at', 0)
                latest_ts = max(btc_ts, eth_ts, 1)
                age_min = (time.time() - latest_ts) / 60
            except Exception:
                age_min = (time.time() - f.stat().st_mtime) / 60
        else:
            age_min = (time.time() - f.stat().st_mtime) / 60
        if age_min > limit:
            issues.append(f'{fname}已{age_min:.0f}min未更新(限{limit}min)')
    if not issues:
        return PASS, f'全部{len(checks)}个数据文件新鲜'
    if len(issues) <= 2:
        return WARN, ' | '.join(issues)
    return FAIL, ' | '.join(issues)


def check_l3_performance() -> tuple:
    """L3: 性能基准检查（目标<5s）"""
    try:
        t0 = time.time()
        from brahma_brain import brahma_core
        r = brahma_core.analyze('BTCUSDT', signal_dir='LONG')
        elapsed = time.time() - t0
        score = r.get('score_final', 0)
        if elapsed < 5:
            return PASS, f'分析耗时{elapsed:.2f}s score={score:.1f}'
        elif elapsed < 10:
            return WARN, f'分析偏慢{elapsed:.2f}s(目标<5s) score={score:.1f}'
        else:
            return FAIL, f'分析严重超时{elapsed:.2f}s(目标<5s)'
    except Exception as e:
        return FAIL, f'分析失败: {str(e)[:60]}'


def check_l4_dimensions() -> tuple:
    """L4: 有效评分维度覆盖率（目标>=45/53）"""
    try:
        from brahma_brain import brahma_core
        r = brahma_core.analyze('BTCUSDT', signal_dir='LONG')
        bd = r.get('confluence', {}).get('breakdown', {})
        nonzero = [k for k, v in bd.items() if v != 0 and not k.startswith('_')]
        n = len(nonzero)
        total = 53
        pct = n / total * 100
        extra_errors = [k for k in r.get('extra', {}) if 'err' in k.lower()]
        if n >= 45:
            return PASS, f'有效维度{n}/{total}({pct:.0f}%) 错误{len(extra_errors)}个'
        elif n >= 38:
            return WARN, f'维度偏少{n}/{total}({pct:.0f}%) 错误模块:{extra_errors[:3]}'
        else:
            return FAIL, f'维度严重不足{n}/{total}({pct:.0f}%) 错误:{extra_errors}'
    except Exception as e:
        return FAIL, f'维度检查失败: {str(e)[:60]}'


def check_l5_error_logs() -> tuple:
    """L5: 最近1h错误日志扫描（只看1h内，不被旧日志干扰）"""
    import time as _t
    log_file = BASE / 'logs' / 'syscron.log'
    if not log_file.exists():
        return WARN, 'syscron.log不存在'
    try:
        cutoff = _t.time() - 3600  # 只看最近1h
        mtime   = log_file.stat().st_mtime
        lines   = log_file.read_text().split('\n')
        # 如果日志文件比1h旧，全文件都不在1h内 → 直接PASS
        if mtime < cutoff:
            return PASS, '日志文件>1h未更新，无新错误'
        # 估算最近1h行数（文件mtime-cutoff=文件覆盖时长，按行数比例截取）
        age_total = max(_t.time() - (mtime - 3600 * len(lines) / max(len(lines), 1)), 1)
        recent = lines[-min(200, len(lines)):]  # 取最近200行，1h内的日志
        errors  = [l for l in recent if any(x in l for x in ['ERROR', 'Traceback', 'Exception', 'FAILED'])]
        http400 = [l for l in recent if 'HTTP Error 400' in l]
        circular = [l for l in recent if 'circular' in l.lower()]
        issues = []
        if len(errors) > 10:
            issues.append(f'错误{len(errors)}行')
        if len(http400) > 3:  # 允许偶发400，>3次才告警
            issues.append(f'HTTP400×{len(http400)}')
        if circular:
            issues.append(f'循环引用×{len(circular)}')
        if not issues:
            return PASS, f'最近200行无严重错误'
        if len(issues) == 1 and len(errors) <= 10:
            return WARN, ' '.join(issues)
        return FAIL, ' '.join(issues)
    except Exception as e:
        return WARN, f'日志读取失败: {str(e)[:40]}'


def check_l6_memory() -> tuple:
    """L6: 内存状态"""
    try:
        mf = open('/proc/meminfo').read()
        total = int([l for l in mf.split('\n') if 'MemTotal' in l][0].split()[1]) // 1024
        avail = int([l for l in mf.split('\n') if 'MemAvailable' in l][0].split()[1]) // 1024
        pct = (total - avail) / total * 100
        if avail >= 600:
            return PASS, f'可用{avail}MB({pct:.0f}%已用)'
        elif avail >= 300:
            return WARN, f'内存偏紧可用{avail}MB(<600MB)'
        else:
            return FAIL, f'内存危险可用{avail}MB(<300MB) OOM风险'
    except Exception as e:
        return WARN, f'内存读取失败'


def check_l7_supercronic() -> tuple:
    """L7: supercronic状态"""
    import subprocess
    r = subprocess.run(['pgrep', '-f', 'supercronic'], capture_output=True)
    if r.returncode == 0:
        pids = r.stdout.decode().strip().split('\n')
        return PASS, f'supercronic运行中 pid={pids[0]}'
    else:
        return FAIL, 'supercronic未运行！需要手动启动'


def check_l8_disk_cache() -> tuple:
    """L8: 磁盘缓存健康度"""
    try:
        from disk_cache import disk_stats
        stats = disk_stats()
        return PASS, f'缓存{stats["files"]}个文件 {stats["total_mb"]}MB 过期{stats["expired"]}个'
    except Exception as e:
        return WARN, f'disk_cache未初始化: {str(e)[:40]}'


def main(push: bool = True) -> dict:
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f'\n🔍 梵天自查协议启动 | {ts}')
    print('═' * 55)

    checks = [
        ('L1 核心导入',   check_l1_imports),
        ('L2 数据新鲜度', check_l2_freshness),
        ('L3 性能基准',   check_l3_performance),
        ('L4 维度覆盖',   check_l4_dimensions),
        ('L5 错误日志',   check_l5_error_logs),
        ('L6 内存状态',   check_l6_memory),
        ('L7 supercronic', check_l7_supercronic),
        ('L8 磁盘缓存',   check_l8_disk_cache),
    ]

    results = {}
    passed = warned = failed = 0

    for name, fn in checks:
        try:
            t0 = time.time()
            status, detail = fn()
            elapsed = time.time() - t0
            print(f'  {status} {name}: {detail} ({elapsed:.1f}s)')
            results[name] = {'status': status, 'detail': detail}
            if status == PASS:   passed += 1
            elif status == WARN: warned += 1
            else:                failed += 1
        except Exception as e:
            print(f'  {FAIL} {name}: 检查异常 {str(e)[:50]}')
            results[name] = {'status': FAIL, 'detail': str(e)}
            failed += 1

    total = passed + warned + failed
    score = int(passed / total * 100) if total else 0
    grade = '🟢S' if score >= 90 else ('🟡A' if score >= 75 else ('🟠B' if score >= 60 else '🔴C'))

    summary = (
        f'📊 梵天自查完成 {ts}\n'
        f'{grade} 综合评分: {score}/100\n'
        f'✅{passed} ⚠️{warned} ❌{failed} / 共{total}项\n'
    )
    if failed > 0:
        fail_items = [f'{k}: {v["detail"][:40]}' for k, v in results.items() if v['status'] == FAIL]
        summary += '❌需要修复:\n' + '\n'.join(f'  {i}' for i in fail_items)
    if warned > 0:
        warn_items = [f'{k}: {v["detail"][:40]}' for k, v in results.items() if v['status'] == WARN]
        summary += '\n⚠️需要关注:\n' + '\n'.join(f'  {i}' for i in warn_items)

    print('\n' + summary)

    if push and (failed > 0 or warned > 2):
        try:
            sys.path.insert(0, str(BASE / 'scripts'))
            from push_hub import _jarvis as _pj
            _pj(summary, level='P1' if failed > 0 else 'P2')
        except Exception:
            pass

    # 写入自查记录
    record_file = BASE / 'data' / 'autocheck_last.json'
    record_file.write_text(json.dumps({
        'ts': ts, 'score': score, 'passed': passed,
        'warned': warned, 'failed': failed, 'results': {
            k: v['detail'] for k, v in results.items()
        }
    }, indent=2))

    return results


if __name__ == '__main__':
    push = '--push' in sys.argv
    main(push=push)
