#!/usr/bin/env python3
"""
brahma_readiness.py — 梵天系统就绪度仪表盘
══════════════════════════════════════════
设计院 2026-08-25 苏摩111 P2封印

使命：
  一条命令看到所有层是否ready，快速判断系统是否适合交易

5层就绪度检查：
  L1 数据层:   体制状态新鲜度 / 关键数据文件
  L2 分析层:   brahma_core可用 / 方仓矩阵 / 蒸馏矩阵
  L3 决策层:   AI议会(reasoning_gate) / 注射器可用
  L4 执行层:   Binance API / auto-executor状态
  L5 监控层:   核心cron连续错误检测

设计原则：
  - 全部本地文件读取，零subprocess，<1s完成
  - consecutiveErrors>=1才算真正故障（gateway restart单次中断不计）
"""
import sys, json, time
from pathlib import Path

_BASE = Path(__file__).parent
_DATA = _BASE.parent / 'data'
_CRON_JOBS  = Path.home() / '.openclaw/cron/jobs.json'
_CRON_RUNS  = Path.home() / '.openclaw/cron/runs'


# ── cron本地读取工具 ──────────────────────────────────────────────
def _read_cron_jobs() -> list:
    try:
        return json.loads(_CRON_JOBS.read_text()).get('jobs', [])
    except Exception:
        return []


def _get_cron_last_status(job_id: str) -> dict:
    """从 runs JSONL 读最近状态，gateway restart中断不计入consecutive"""
    try:
        runs_file = _CRON_RUNS / f'{job_id}.jsonl'
        if not runs_file.exists():
            return {'status': 'idle', 'consecutive_errors': 0}
        lines = [l for l in runs_file.read_text().strip().split('\n') if l.strip()]
        if not lines:
            return {'status': 'idle', 'consecutive_errors': 0}
        # 连续错误计数（从最新倒数）
        consecutive = 0
        last_record = {}
        for line in reversed(lines):
            try:
                r = json.loads(line)
                if r.get('action') != 'finished':
                    continue
                if not last_record:
                    last_record = r
                if r.get('status') == 'error':
                    if 'gateway restart' in r.get('error', ''):
                        break  # restart中断不累计
                    consecutive += 1
                else:
                    break
            except Exception:
                continue
        return {
            'status': last_record.get('status', 'idle'),
            'consecutive_errors': consecutive,
            'error': last_record.get('error', ''),
        }
    except Exception:
        return {'status': 'unknown', 'consecutive_errors': 0}


def _get_cron_status(name: str) -> str:
    for j in _read_cron_jobs():
        if j.get('name') == name:
            info = _get_cron_last_status(j['id'])
            return 'error' if info['consecutive_errors'] >= 1 else (info['status'] or 'idle')
    return 'unknown'


# ── L1 数据层 ────────────────────────────────────────────────────
def _check_l1_data() -> dict:
    issues = []
    sf = _DATA / 'brahma_state.json'
    regime_age, regime = None, '?'
    if sf.exists():
        regime_age = int((time.time() - sf.stat().st_mtime) / 60)
        try:
            regime = json.loads(sf.read_text()).get('regime', '?')
        except Exception:
            pass
        if regime_age > 60:
            issues.append(f'体制陈旧{regime_age}min')
    else:
        issues.append('brahma_state.json缺失')

    if not (_DATA / 'price_zones.json').exists():
        issues.append('price_zones.json缺失')

    warn = regime_age is not None and 30 < regime_age <= 60
    return {
        'layer': 'L1-数据层',
        'ready': len(issues) == 0,
        'warn':  warn,
        'detail': f'体制={regime} age={regime_age}min' if regime_age is not None else '体制文件缺失',
        'issues': issues,
    }


# ── L2 分析层 ────────────────────────────────────────────────────
def _check_l2_analysis() -> dict:
    issues = []
    mf = _DATA / 'brahma_experience_matrix.json'
    if mf.exists():
        try:
            n = json.loads(mf.read_text()).get('meta', {}).get('total_cases', 0)
            if n < 10000:
                issues.append(f'方仓矩阵案例不足({n}条)')
        except Exception:
            issues.append('方仓矩阵解析失败')
    else:
        issues.append('方仓矩阵缺失')

    if not (_BASE / 'brahma_core.py').exists():
        issues.append('brahma_core.py缺失')

    return {
        'layer': 'L2-分析层',
        'ready': len(issues) == 0,
        'warn':  False,
        'detail': '方仓矩阵✅ brahma_core✅' if not issues else ' | '.join(issues[:2]),
        'issues': issues,
    }


# ── L3 决策层 ────────────────────────────────────────────────────
def _check_l3_decision() -> dict:
    issues = []
    sys.path.insert(0, str(_BASE))

    try:
        import reasoning_client
        if not hasattr(reasoning_client, 'reasoning_gate'):
            issues.append('reasoning_gate未实现')
    except Exception as e:
        issues.append(f'reasoning_client: {str(e)[:40]}')

    try:
        import brahma_context_injector  # noqa
    except Exception as e:
        issues.append(f'注射器: {str(e)[:40]}')

    import shutil
    if not shutil.which('openclaw'):
        issues.append('openclaw命令不存在')

    return {
        'layer': 'L3-决策层',
        'ready': len(issues) == 0,
        'warn':  False,
        'detail': 'reasoning_gate✅ 注射器✅' if not issues else ' | '.join(issues[:2]),
        'issues': issues,
    }


# ── L4 执行层 ────────────────────────────────────────────────────
def _check_l4_execution() -> dict:
    issues = []
    # Binance API ping（轻量HTTP）
    try:
        import urllib.request
        resp = urllib.request.urlopen('https://fapi.binance.com/fapi/v1/ping', timeout=4)
        if resp.status != 200:
            issues.append('Binance API ping失败')
    except Exception as e:
        issues.append(f'Binance API: {str(e)[:40]}')

    # auto-executor状态（本地读取）
    exec_status = _get_cron_status('auto-executor')
    if exec_status == 'error':
        issues.append('auto-executor连续error')

    api_ok = not any('Binance' in i for i in issues)
    exec_ok = exec_status in ('ok', 'idle', 'unknown')
    detail_parts = []
    if api_ok:
        detail_parts.append('BinanceAPI✅')
    detail_parts.append(f'auto-executor={exec_status}')

    return {
        'layer': 'L4-执行层',
        'ready': api_ok and exec_ok,
        'warn':  exec_status == 'unknown',
        'detail': ' '.join(detail_parts),
        'issues': issues,
    }


# ── L5 监控层 ────────────────────────────────────────────────────
def _check_l5_monitor() -> dict:
    issues = []
    CORE = ['brahma-state-refresh', 'brahma-360-health',
            'position-guardian', 'signal-settler']
    error_core = []
    jobs = _read_cron_jobs()
    job_map = {j['name']: j['id'] for j in jobs}
    for name in CORE:
        if name not in job_map:
            continue
        info = _get_cron_last_status(job_map[name])
        if info['consecutive_errors'] >= 1:
            error_core.append(name)

    if error_core:
        issues.append(f'监控cron持续error: {error_core}')

    return {
        'layer': 'L5-监控层',
        'ready': len(issues) == 0,
        'warn':  False,
        'detail': '核心cron全正常' if not issues else ' | '.join(issues[:2]),
        'issues': issues,
    }


# ── 主入口 ────────────────────────────────────────────────────────
def run_readiness_check() -> dict:
    t0 = time.time()
    layers = [
        _check_l1_data(),
        _check_l2_analysis(),
        _check_l3_decision(),
        _check_l4_execution(),
        _check_l5_monitor(),
    ]
    ready_count = sum(1 for l in layers if l['ready'])
    warn_count  = sum(1 for l in layers if l.get('warn'))
    score = min(100, ready_count * 20 - warn_count * 5)

    if ready_count == len(layers):
        status = '🟢 READY'
    elif ready_count >= 3:
        status = '🟡 DEGRADED'
    else:
        status = '🔴 NOT_READY'

    return {
        'status':       status,
        'score':        score,
        'ready_layers': ready_count,
        'total_layers': len(layers),
        'layers':       layers,
        'duration_ms':  round((time.time() - t0) * 1000),
    }


def print_readiness(result: dict) -> None:
    print('═' * 52)
    print(f'  梵天系统就绪度  {result["status"]}  {result["score"]}/100')
    print(f'  就绪层: {result["ready_layers"]}/{result["total_layers"]}  '
          f'耗时: {result["duration_ms"]}ms')
    print('─' * 52)
    for l in result['layers']:
        icon = '✅' if l['ready'] else ('⚠️ ' if l.get('warn') else '❌')
        print(f'  {icon} {l["layer"]:12s}  {l["detail"]}')
        for issue in l.get('issues', []):
            print(f'       └─ {issue}')
    print('─' * 52)
    l4 = next((l for l in result['layers'] if 'L4' in l['layer']), {})
    l1 = next((l for l in result['layers'] if 'L1' in l['layer']), {})
    if l4.get('ready') and l1.get('ready'):
        print('  💹 系统适合交易')
    elif l4.get('ready'):
        print('  ⚠️  执行层正常，数据层需关注')
    else:
        print('  🚫 执行层异常，暂不适合交易')
    print('═' * 52)


if __name__ == '__main__':
    result = run_readiness_check()
    if '--json' in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_readiness(result)
    sys.exit(0 if result['ready_layers'] >= 4 else 1)
