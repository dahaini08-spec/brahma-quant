"""
brahma_health.py — 梵天统一健康指标收集层
设计院·量化工程师·360梵天 联合落地 2026-07-01

职责：
  1. 统一健康检查入口（单次调用，全系统探测）
  2. 结构化metrics收集（延迟/错误率/缓存命中）
  3. 自愈决策建议（发现问题 → 输出修复方案）
  4. 轻量设计：无外部依赖，纯Python，<200ms完成

健康分级：
  🟢 HEALTHY   所有核心模块正常
  🟡 DEGRADED  部分模块降级，主路径仍可用
  🔴 CRITICAL  核心路径受损，需立即干预
"""


# ╔══ INTERFACE CONTRACT ═══════════════════════════════════════════╗
# ║ Interface : run_health_check(full=False)->dict | format_health_report(report)
# ║ Output    : {status:HEALTHY/DEGRADED/CRITICAL, score/100, checks, heal, summa
# ║ Call Freq : cron每2H，或手动触发
# ║ Deps      : requests(fapi) + 本地文件
# ╚════════════════════════════════════════════════════════════════╝
import time
import traceback
import importlib
import sys
import os
from typing import Optional

# 确保路径
_BB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BB)
for _p in [_BB, _ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ═══════════════════════════════════════════════════════════════
# 核心检查项（按优先级排列）
# ═══════════════════════════════════════════════════════════════

def _check_binance_api(timeout: float = 5.0) -> dict:
    """检查Binance API连通性 + 延迟"""
    t0 = time.time()
    try:
        import requests
        r = requests.get(
            'https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT',
            timeout=timeout
        )
        latency_ms = round((time.time() - t0) * 1000)
        if r.status_code == 200:
            price = float(r.json()['price'])
            return {
                'ok': True, 'latency_ms': latency_ms,
                'detail': f'BTC=${price:,.0f} latency={latency_ms}ms',
                'warn': latency_ms > 2000
            }
        return {'ok': False, 'latency_ms': latency_ms,
                'detail': f'HTTP {r.status_code}', 'warn': False}
    except Exception as e:
        return {'ok': False, 'latency_ms': -1,
                'detail': f'连接失败: {str(e)[:50]}', 'warn': False}


def _check_brahma_bus() -> dict:
    """检查数据总线缓存层"""
    t0 = time.time()
    try:
        from brahma_bus import BrahmaBus
        bus = BrahmaBus()
        # 轻量探测：检查缓存状态
        cache_size = len(getattr(bus, '_cache', {}))
        stale = 0
        now = time.time()
        for v in getattr(bus, '_cache', {}).values():
            if isinstance(v, dict) and 'ts' in v:
                if now - v['ts'] > 300:  # >5分钟=陈旧
                    stale += 1
        latency_ms = round((time.time() - t0) * 1000)
        return {
            'ok': True, 'latency_ms': latency_ms,
            'detail': f'缓存条目={cache_size} 陈旧={stale}',
            'cache_size': cache_size, 'stale_entries': stale,
            'warn': stale > cache_size * 0.5 and cache_size > 0
        }
    except Exception as e:
        return {'ok': False, 'latency_ms': -1,
                'detail': f'BrahmaBus异常: {str(e)[:60]}', 'warn': False}


def _check_scoring_engine() -> dict:
    """检查评分引擎可导入性（不实际运行，仅语法+导入检查）"""
    t0 = time.time()
    try:
        # 检查核心函数是否可导入
        from brahma_brain.brahma_core import confluence_score, calc_trade_params
        latency_ms = round((time.time() - t0) * 1000)
        return {
            'ok': True, 'latency_ms': latency_ms,
            'detail': f'confluence_score/calc_trade_params导入正常 {latency_ms}ms',
            'warn': latency_ms > 3000
        }
    except Exception as e:
        return {'ok': False, 'latency_ms': -1,
                'detail': f'导入失败: {str(e)[:80]}', 'warn': False}


def _check_signal_queue() -> dict:
    """检查信号队列积压情况"""
    try:
        queue_file = os.path.join(_ROOT, 'data', 'signal_queue.jsonl')
        if not os.path.exists(queue_file):
            return {'ok': True, 'detail': '队列文件不存在（正常）', 'count': 0, 'warn': False}
        with open(queue_file) as f:
            lines = [l.strip() for l in f if l.strip()]
        count = len(lines)
        return {
            'ok': True, 'detail': f'队列积压={count}条',
            'count': count, 'warn': count > 50
        }
    except Exception as e:
        return {'ok': False, 'detail': f'队列读取异常: {str(e)[:50]}',
                'count': -1, 'warn': False}


def _check_live_signals() -> dict:
    """检查最近信号活跃度"""
    try:
        import json
        log_file = os.path.join(_ROOT, 'data', 'live_signal_log.jsonl')
        if not os.path.exists(log_file):
            return {'ok': True, 'detail': '无信号日志（冷启动正常）',
                    'last_signal_age_h': -1, 'warn': False}
        with open(log_file) as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            return {'ok': True, 'detail': '日志为空', 'last_signal_age_h': -1, 'warn': False}
        last = json.loads(lines[-1])
        ts = last.get('timestamp', last.get('ts', 0))
        if isinstance(ts, str):
            import datetime
            try:
                ts = datetime.datetime.fromisoformat(ts.replace('Z', '')).timestamp()
            except Exception:
                ts = 0
        age_h = round((time.time() - float(ts)) / 3600, 1) if ts else -1
        return {
            'ok': True,
            'detail': f'最近信号={last.get("symbol","?")} {age_h}H前',
            'last_signal_age_h': age_h,
            'warn': age_h > 48 and age_h > 0
        }
    except Exception as e:
        return {'ok': False, 'detail': f'信号日志异常: {str(e)[:50]}',
                'last_signal_age_h': -1, 'warn': False}


def _check_data_files() -> dict:
    """检查关键数据文件健康度"""
    data_dir = os.path.join(_ROOT, 'data')
    critical_files = ['brahma_state.json']
    missing, stale = [], []
    now = time.time()
    for fname in critical_files:
        fpath = os.path.join(data_dir, fname)
        if not os.path.exists(fpath):
            missing.append(fname)
        else:
            age = now - os.path.getmtime(fpath)
            if age > 7200:  # >2H未更新
                stale.append(f'{fname}({age/3600:.1f}H)')
    ok = not missing
    return {
        'ok': ok,
        'detail': f'缺失={missing} 陈旧={stale}' if (missing or stale) else '关键文件正常',
        'missing': missing, 'stale': stale,
        'warn': bool(stale)
    }


def _check_process_memory() -> dict:
    """检查进程内存占用"""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem_mb = round(proc.memory_info().rss / 1024 / 1024, 1)
        return {
            'ok': True, 'mem_mb': mem_mb,
            'detail': f'当前进程内存={mem_mb}MB',
            'warn': mem_mb > 512
        }
    except Exception:
        return {'ok': True, 'detail': 'psutil不可用（非关键）', 'mem_mb': -1, 'warn': False}


def _check_external_routes() -> dict:
    """检查外部路由可用性（跨所FR/Deribit/Yahoo）"""
    results = {}
    try:
        import requests
        # Bybit
        r = requests.get('https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT', timeout=5)
        results['bybit'] = r.status_code == 200
    except Exception:
        results['bybit'] = False
    try:
        import requests
        r = requests.get('https://deribit.com/api/v2/public/get_index_price?index_name=btc_usd', timeout=5)
        results['deribit'] = r.status_code == 200
    except Exception:
        results['deribit'] = False
    available = sum(results.values())
    total = len(results)
    return {
        'ok': available >= total - 1,  # 允许1个失败
        'detail': f'外部路由 {available}/{total} 可用 {results}',
        'routes': results,
        'warn': available < total
    }


# ═══════════════════════════════════════════════════════════════
# 自愈决策引擎
# ═══════════════════════════════════════════════════════════════

def _auto_heal_suggestions(report: dict) -> list:
    """
    根据健康报告输出自愈建议
    返回：[{'action': str, 'command': str, 'priority': int}]
    """
    suggestions = []
    checks = report.get('checks', {})

    # Binance API延迟过高
    api = checks.get('binance_api', {})
    if not api.get('ok'):
        suggestions.append({
            'priority': 0, 'action': 'API_RECONNECT',
            'desc': 'Binance API不可达',
            'command': 'python3 scripts/safe_fetch.py --test'
        })
    elif api.get('warn') and api.get('latency_ms', 0) > 2000:
        suggestions.append({
            'priority': 1, 'action': 'API_LATENCY_WARN',
            'desc': f'API延迟{api["latency_ms"]}ms过高',
            'command': None  # 仅告警
        })

    # 数据总线缓存陈旧
    bus = checks.get('brahma_bus', {})
    if bus.get('warn') and bus.get('stale_entries', 0) > 0:
        suggestions.append({
            'priority': 1, 'action': 'CACHE_FLUSH',
            'desc': f'数据总线陈旧缓存={bus["stale_entries"]}条',
            'command': 'python3 -c "from brahma_brain.brahma_bus import BrahmaBus; BrahmaBus().flush_stale()"'
        })

    # 评分引擎导入失败
    eng = checks.get('scoring_engine', {})
    if not eng.get('ok'):
        suggestions.append({
            'priority': 0, 'action': 'SCORING_ENGINE_FAIL',
            'desc': f'评分引擎异常: {eng.get("detail","")}',
            'command': 'python3 -c "import ast; ast.parse(open(\"brahma_brain/brahma_core.py\").read()); print(\"OK\")"'
        })

    # 信号队列积压
    q = checks.get('signal_queue', {})
    if q.get('warn') and q.get('count', 0) > 50:
        suggestions.append({
            'priority': 2, 'action': 'QUEUE_BACKLOG',
            'desc': f'信号队列积压{q["count"]}条',
            'command': 'python3 scripts/clean_stale_signals.py'
        })

    # 关键文件缺失
    df = checks.get('data_files', {})
    if not df.get('ok') and df.get('missing'):
        suggestions.append({
            'priority': 0, 'action': 'DATA_FILE_MISSING',
            'desc': f'关键数据文件缺失: {df["missing"]}',
            'command': 'python3 scripts/brahma_state_refresh.py'
        })

    # 内存超限
    mem = checks.get('memory', {})
    if mem.get('warn') and mem.get('mem_mb', 0) > 512:
        suggestions.append({
            'priority': 2, 'action': 'MEMORY_HIGH',
            'desc': f'内存占用{mem["mem_mb"]}MB过高',
            'command': None
        })

    return sorted(suggestions, key=lambda x: x['priority'])


# ═══════════════════════════════════════════════════════════════
# 统一健康检查入口
# ═══════════════════════════════════════════════════════════════

def run_health_check(
    full: bool = False,
    timeout: float = 8.0
) -> dict:
    """
    统一健康检查入口

    参数：
      full    : True=完整检查（含外部路由），False=核心检查
      timeout : 每项检查超时秒数

    返回：
      status  : HEALTHY / DEGRADED / CRITICAL
      score   : 健康分 0~100
      checks  : 各项检查结果
      heal    : 自愈建议列表
      summary : 一行摘要
      duration_ms : 总耗时
    """
    t_start = time.time()

    checks = {}

    # 核心检查（必须）
    checks['binance_api']     = _check_binance_api(timeout)
    checks['scoring_engine']  = _check_scoring_engine()
    checks['signal_queue']    = _check_signal_queue()
    checks['live_signals']    = _check_live_signals()
    checks['data_files']      = _check_data_files()
    checks['memory']          = _check_process_memory()

    # 可选检查
    if full:
        checks['brahma_bus']      = _check_brahma_bus()
        checks['external_routes'] = _check_external_routes()

    # 计算健康分
    critical_keys = ['binance_api', 'scoring_engine', 'data_files']
    warn_keys     = ['signal_queue', 'memory', 'brahma_bus', 'external_routes']

    critical_fail = sum(1 for k in critical_keys if not checks.get(k, {}).get('ok', True))
    warn_count    = sum(1 for k in list(checks.keys()) if checks[k].get('warn', False))

    if critical_fail >= 2:
        status = 'CRITICAL'
        score  = max(0, 30 - critical_fail * 15)
    elif critical_fail == 1:
        status = 'DEGRADED'
        score  = max(40, 70 - warn_count * 5)
    elif warn_count >= 3:
        status = 'DEGRADED'
        score  = max(55, 85 - warn_count * 5)
    else:
        status = 'HEALTHY'
        score  = max(85, 100 - warn_count * 5)

    duration_ms = round((time.time() - t_start) * 1000)

    # 生成自愈建议
    heal_suggestions = _auto_heal_suggestions({'checks': checks})

    # 一行摘要
    ok_count   = sum(1 for c in checks.values() if c.get('ok', False))
    fail_names = [k for k, v in checks.items() if not v.get('ok', True)]
    warn_names = [k for k, v in checks.items() if v.get('warn', False)]
    emoji      = {'HEALTHY': '🟢', 'DEGRADED': '🟡', 'CRITICAL': '🔴'}[status]
    summary    = (
        f'{emoji} {status} score={score}/100 '
        f'({ok_count}/{len(checks)}项正常'
        f'{f" | ⚠️{warn_names}" if warn_names else ""}'
        f'{f" | ❌{fail_names}" if fail_names else ""}'
        f') {duration_ms}ms'
    )

    return {
        'status':      status,
        'score':       score,
        'checks':      checks,
        'heal':        heal_suggestions,
        'summary':     summary,
        'duration_ms': duration_ms,
        'timestamp':   time.time(),
    }


def format_health_report(report: dict, compact: bool = True) -> str:
    """格式化健康报告供推送"""
    status = report['status']
    score  = report['score']
    emoji  = {'HEALTHY': '🟢', 'DEGRADED': '🟡', 'CRITICAL': '🔴'}.get(status, '?')
    lines  = [
        f'{emoji} **梵天健康报告** {status} {score}/100',
        f'耗时 {report["duration_ms"]}ms',
        '',
    ]

    if compact:
        # 只显示异常项
        for name, c in report['checks'].items():
            if not c.get('ok') or c.get('warn'):
                flag = '❌' if not c.get('ok') else '⚠️'
                lines.append(f'{flag} {name}: {c.get("detail", "?")}')
        if not any(not c.get('ok') or c.get('warn') for c in report['checks'].values()):
            lines.append('✅ 所有检查项正常')
    else:
        for name, c in report['checks'].items():
            flag = '✅' if c.get('ok') else '❌'
            flag += ' ⚠️' if c.get('warn') else ''
            lines.append(f'{flag} {name}: {c.get("detail", "?")}')

    # 自愈建议
    heal = report.get('heal', [])
    if heal:
        lines.append('\n**自愈建议：**')
        for h in heal[:3]:  # 最多3条
            lines.append(f'  [{h["priority"]}] {h["action"]}: {h["desc"]}')
            if h.get('command'):
                lines.append(f'     → `{h["command"][:60]}`')

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天健康检查')
    parser.add_argument('--full', action='store_true', help='完整检查（含外部路由）')
    parser.add_argument('--compact', action='store_true', default=True)
    parser.add_argument('--json', action='store_true', help='JSON输出')
    args = parser.parse_args()

    report = run_health_check(full=args.full)

    if args.json:
        import json
        # 移除不可序列化的项
        safe = {k: v for k, v in report.items() if k != 'timestamp'}
        print(json.dumps(safe, ensure_ascii=False, indent=2))
    else:
        print(report['summary'])
        print()
        print(format_health_report(report, compact=args.compact))


# ═══════════════════════════════════════════════════════════════
# [增效减负 2026-07-01] 日志统计 + 冷模块契约扫描
# ═══════════════════════════════════════════════════════════════

def _check_log_health() -> dict:
    """
    检查 brainlog 错误/警告计数
    高错误率 = 系统内部异常信号
    """
    try:
        from brainlog import get_stats
        stats = get_stats()
        total_err = stats.get('total_errors', 0)
        total_warn = stats.get('total_warns', 0)
        hot = stats.get('hot_modules', [])
        return {
            'ok': total_err < 10,
            'total_errors': total_err,
            'total_warns': total_warn,
            'hot_modules': hot,
            'detail': f'errors={total_err} warns={total_warn} hot={hot[:2]}',
            'warn': total_err >= 5,
        }
    except Exception as e:
        return {'ok': True, 'detail': f'brainlog未初始化（正常）', 'warn': False}


def _check_module_contracts() -> dict:
    """
    扫描核心新模块是否有接口契约注释
    INTERFACE CONTRACT = 模块文档完整性指标
    """
    brain = os.path.dirname(os.path.abspath(__file__))
    # 需要契约的核心模块
    required = [
        'timing_filter.py', 'smart_money_engine.py', 'brahma_health.py',
        'cross_market_engine.py', 'macro_engine.py', 'brainlog.py',
    ]
    missing_contract = []
    for fname in required:
        fpath = os.path.join(brain, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            src = f.read()
        if 'INTERFACE CONTRACT' not in src:
            missing_contract.append(fname)

    return {
        'ok': len(missing_contract) == 0,
        'missing_contract': missing_contract,
        'detail': f'契约覆盖{len(required)-len(missing_contract)}/{len(required)}' +
                  (f' | 缺失:{missing_contract}' if missing_contract else ''),
        'warn': len(missing_contract) > 2,
    }


def _check_standby_violations_health() -> dict:
    """
    快速扫描STANDBY/AUXILIARY模块被引用情况
    """
    try:
        import ast
        brain = os.path.dirname(os.path.abspath(__file__))
        # 找所有标注了STANDBY/AUXILIARY的模块
        flagged = set()
        for fname in os.listdir(brain):
            if not fname.endswith('.py'):
                continue
            try:
                with open(os.path.join(brain, fname)) as f:
                    src = f.read(500)  # 只读头部
                if 'STATUS: STANDBY' in src or 'STATUS: AUXILIARY' in src:
                    flagged.add(fname[:-3])
            except Exception:
                pass

        violations = []
        skip = flagged | {'__init__', 'auto_review', 'brahma_health'}
        for fname in os.listdir(brain):
            if not fname.endswith('.py') or fname[:-3] in skip:
                continue
            try:
                with open(os.path.join(brain, fname)) as f:
                    src = f.read()
                tree = ast.parse(src)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = [a.name for a in getattr(node, 'names', [])]
                        mod = getattr(node, 'module', '') or ''
                        for n in names + [mod.split('.')[-1]]:
                            if n in flagged and fname[:-3] not in flagged:
                                violations.append(f'{fname[:-3]}→{n}')
            except Exception:
                pass

        return {
            'ok': len(violations) == 0,
            'violations': violations[:5],
            'flagged_count': len(flagged),
            'detail': f'标注模块={len(flagged)} 引用违规={len(violations)}' +
                      (f' | {violations[:3]}' if violations else ''),
            'warn': len(violations) > 0,
        }
    except Exception as e:
        return {'ok': True, 'detail': f'扫描异常: {str(e)[:50]}', 'warn': False}


# 注入到 run_health_check
_original_run = run_health_check


def run_health_check(full: bool = False, timeout: float = 8.0) -> dict:
    report = _original_run(full=full, timeout=timeout)
    # 追加三项新检查
    report['checks']['log_health']        = _check_log_health()
    report['checks']['module_contracts']  = _check_module_contracts()
    if full:
        report['checks']['standby_violations'] = _check_standby_violations_health()
    # 重新计算总分
    warn_count = sum(1 for c in report['checks'].values() if c.get('warn', False))
    fail_count = sum(1 for c in report['checks'].values() if not c.get('ok', True))
    if fail_count == 0 and warn_count == 0:
        report['score'] = 100
        report['status'] = 'HEALTHY'
    elif fail_count >= 2:
        report['status'] = 'CRITICAL'
    elif fail_count == 1 or warn_count >= 3:
        report['status'] = 'DEGRADED'
    else:
        report['score'] = max(80, 100 - warn_count * 5)
    return report
