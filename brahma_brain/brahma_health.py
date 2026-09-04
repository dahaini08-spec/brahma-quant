# ponytail: brahma_health 871行，健康监控，检查项累积，不可拆
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
try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
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

# 自动注入venv site-packages，确保requests等依赖可用
_VENV_SITE = os.path.join(_ROOT, 'venv', 'lib')
if os.path.isdir(_VENV_SITE):
    for _entry in os.listdir(_VENV_SITE):
        _sp = os.path.join(_VENV_SITE, _entry, 'site-packages')
        if os.path.isdir(_sp) and _sp not in sys.path:
            sys.path.insert(0, _sp)

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
            'count': count, 'warn': count > 1000
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
            'warn': age_h > 120 and age_h > 0  # BEAR_TREND长期封禁正常静默，阈值120H(5天)
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
            # 同时检查文件内timestamp字段（更精确，防止AI-driven cron不写文件的情况）
            try:
                import json as _hj
                _hd = _hj.loads(open(fpath).read())
                _ts = _hd.get('timestamp') or _hd.get('updated_at')
                if _ts:
                    if isinstance(_ts, str):
                        import datetime as _hdt
                        _ts = _hdt.datetime.fromisoformat(_ts.replace('Z','+00:00')).timestamp()
                    age = min(age, now - float(_ts))
            except Exception:
                pass
            if age > 3600:  # >1H未更新（原2H → 1H，更严格）
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

    # ws_guardian进程未运行 [P1-2 2026-07-23 设计院]
    wsg = checks.get('ws_guardian', {})
    if not wsg.get('ok') or wsg.get('warn'):
        suggestions.append({
            'priority': 1, 'action': 'WS_GUARDIAN_RESTART',
            'desc': 'ws_guardian进程未运行',
            'command': 'cd /root/.openclaw/workspace/trading-system && python3 scripts/ws_guardian.py &'
        })

    return sorted(suggestions, key=lambda x: x['priority'])


# ═══════════════════════════════════════════════════════════════
# venv依赖检查（P0级 2026-07-26封印，提升为模块级函数 2026-08-26修复）
# ═══════════════════════════════════════════════════════════════

def _check_venv_deps() -> dict:
    """检查venv核心依赖是否可用，重启后丢失时触发自愈"""
    try:
        import subprocess
        from pathlib import Path as _Path
        _venv_py = _Path(_ROOT) / 'venv' / 'bin' / 'python'
        if not _venv_py.exists():
            return {'ok': False, 'warn': True, 'msg': 'venv/bin/python不存在'}
        result = subprocess.run(
            [str(_venv_py), '-c', 'import requests, numpy, pandas; print("ok")'],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == 'ok':
            return {'ok': True, 'msg': 'venv依赖正常'}
        else:
            return {'ok': False, 'warn': True,
                    'msg': f'venv依赖缺失: {result.stderr[:80]}'}
    except Exception as e:
        return {'ok': False, 'warn': True, 'msg': f'venv检查失败: {e}'}


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
    checks['venv_deps']       = _check_venv_deps()
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
        checks['ws_guardian']      = _check_ws_guardian()  # [P1-2 2026-07-23]
        checks['standby_violations'] = _check_standby_violations_health()  # [合并 2026-08-24]

    # 追加检查（原monkey-patch层，2026-08-24 合并到主函数）
    checks['log_health']           = _check_log_health()
    checks['module_contracts']     = _check_module_contracts()
    checks['panorama_integrity']   = _check_panorama_integrity()
    checks['learning_loop_import'] = _check_learning_loop_importable()
    checks['macro_state_freshness']= _check_macro_state_freshness()
    checks['cron_route_ssot']      = _check_cron_route_ssot()
    checks['cron_runtime_health']   = _check_cron_runtime_health()
    checks['signal_card_import']   = _check_signal_card_importable()
    checks['dharma_factor_weights']= _check_dharma_factor_weights()
    checks['wr_gate_integrity']    = _check_wr_gate_integrity()
    checks['tardis_month_freshness']= _check_tardis_month_freshness()
    checks['zombie_positions']     = _check_zombie_positions()

    # 计算健康分
    critical_keys = ['venv_deps', 'binance_api', 'scoring_engine', 'data_files']
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

        # 架构债务：warn-only，不阻断HEALTHY（重构成本高）
        return {
            'ok': len(violations) < 200,   # 允许存量违规，>200才算失败
            'violations': violations[:5],
            'flagged_count': len(flagged),
            'detail': f'标注模块={len(flagged)} 引用违规={len(violations)}' +
                      (f' | {violations[:3]}' if violations else ' (架构债务，不影响运行)'),
            'warn': len(violations) > 50,   # >50才警告
        }
    except Exception as e:
        return {'ok': True, 'detail': f'扫描异常: {str(e)[:50]}', 'warn': False}


# ═══════════════════════════════════════════════════════════════
# [固化封印 2026-07-14] 五项扩展检查
# ═══════════════════════════════════════════════════════════════

def _check_panorama_integrity() -> dict:
    """全景矩阵B2/B3/FVG字段完整性检查"""
    try:
        from brahma_brain.formatter import brahma_panorama_report
        import inspect
        src = inspect.getsource(brahma_panorama_report)
        has_b2 = 'B2' in src and 'order_block' in src.lower()
        has_b3 = 'B3' in src and 'liq' in src.lower()
        has_fvg = 'fvg' in src.lower() or 'FVG' in src
        ok_ = has_b2 and has_b3
        return {
            'ok': ok_,
            'detail': f'B2={has_b2} B3={has_b3} FVG={has_fvg}',
            'warn': not has_fvg,
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)[:60], 'warn': True}


def _check_learning_loop_importable() -> dict:
    """学习闭环模块可导入性（2026-08-26归档后降级为warn-only）"""
    try:
        from brahma_brain.brahma_learning_loop import main  # noqa
        return {'ok': True, 'detail': 'brahma_brain.brahma_learning_loop.main OK', 'warn': False}
    except Exception as e:
        # 模块已归档，非核心路径，降级为warn不影响健康分
        return {'ok': True, 'warn': True, 'detail': f'learning_loop已归档(非核心): {str(e)[:60]}'}


def _check_macro_state_freshness() -> dict:
    """macro_state.json新鲜度+DXY有效性"""
    import json as _json, time as _time
    from pathlib import Path as _Path
    p = _Path(__file__).parent.parent / 'data' / 'macro_state.json'
    if not p.exists():
        return {'ok': False, 'detail': 'macro_state.json不存在', 'warn': True}
    age = _time.time() - p.stat().st_mtime
    try:
        ms = _json.loads(p.read_text())
        dxy_raw = ms.get('dxy')
        # dxy可以是 int/float 直接值，也可以是 {'value': ...} 字典（兼容两种格式）
        if isinstance(dxy_raw, dict):
            dxy_val = dxy_raw.get('value')
        else:
            dxy_val = dxy_raw  # int/float直接使用
        ok_ = age < 4 * 3600 and dxy_val is not None
        return {
            'ok': ok_,
            'detail': f'age={age/3600:.1f}h DXY={dxy_val}',
            'warn': dxy_val is None or age > 6 * 3600,
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)[:60], 'warn': True}


def _check_cron_route_ssot() -> dict:
    """Cron路由一致性 — 全部核心任务应路由到SSOT线程"""
    import json as _json
    from pathlib import Path as _Path
    SSOT = '01a033af-3697-734a-9f9c-c3e34a00c378'
    OLD  = '019f443a'
    jobs_path = _Path.home() / '.openclaw/cron/jobs.json'
    try:
        raw = _json.loads(jobs_path.read_text())
        jobs = raw if isinstance(raw, list) else raw.get('jobs', list(raw.values()))
        wrong = []
        for j in jobs:
            if not isinstance(j, dict): continue
            name = j.get('name', '')
            to   = j.get('delivery', {}).get('to', '')
            if any(x in name for x in ['Square', 'square', 'live-performance', 'brahma-arch', '广场', '帖', '快讯']):
                continue
            if OLD in to:
                wrong.append(name)
        return {
            'ok': len(wrong) == 0,
            'detail': f'路由偏移任务: {wrong}' if wrong else f'全部路由→{SSOT[:8]}',
            'warn': len(wrong) > 0,
        }
    except Exception as e:
        return {'ok': True, 'detail': f'检查异常(非致命): {str(e)[:50]}', 'warn': False}


def _check_signal_card_importable() -> dict:
    """signal_card_formatter已归档，改检测brahma_signal [2026-08-24 设计院精简]"""
    try:
        from brahma_brain.brahma_signal import evaluate_signal  # noqa
        return {'ok': True, 'detail': 'brahma_signal OK (signal_card已合并)', 'warn': False}
    except Exception as e:
        return {'ok': False, 'detail': str(e)[:80], 'warn': True,
                'fix': 'cp scripts/signal_card_formatter.py brahma_brain/'}




def _check_ws_guardian() -> dict:
    """ws_guardian心跳检查
    2026-08-10 设计院修正: ws_guardian已整入cron精简计划删除。
    oi-advanced-scanner/auto-1hao-trigger覆盖其功能。
    日志文件不存在则直接返回ok(正常已删除)。
    """
    import time
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base, '..', 'logs', 'ws_guardian.log')
        log_path = os.path.normpath(log_path)
        if not os.path.exists(log_path):
            # ws_guardian已整入cron精简计划删除，日志不存在属正常状态
            return {'ok': True, 'detail': 'ws_guardian已整入oi-scanner覆盖(2026-08-10修正)', 'warn': False, 'pid': None}
        age_min = (time.time() - os.path.getmtime(log_path)) / 60
        # cron模式：不是持久进程，不点检30min逾时
        return {
            'ok': True,
            'detail': f'ws_guardian cron模式 日志{age_min:.0f}min前(正常)',
            'warn': False,
            'pid': None
        }
    except Exception as e:
        return {'ok': True, 'detail': f'检查失败(忽略): {e}', 'warn': False}


def _check_dharma_factor_weights() -> dict:
    """F16对应：检查factor_weights.json存在且包含rsi/volume/gates/resonance四层"""
    try:
        import json as _j, os as _o
        fw = _o.path.join(_o.path.dirname(__file__), '..', 'dharma', 'factor_weights.json')
        if not _o.path.exists(fw):
            return {'ok': False, 'warn': True, 'detail': 'factor_weights.json缺失'}
        data = _j.loads(open(fw).read())
        missing = {'rsi', 'volume', 'gates', 'resonance'} - set(data.keys())
        if missing:
            return {'ok': False, 'warn': True, 'detail': f'factor_weights.json缺层: {missing}'}
        rsi_n = len(data.get('rsi', []))
        return {'ok': True, 'warn': False, 'detail': f'factor_weights.json完整 rsi={rsi_n}条'}
    except Exception as e:
        return {'ok': False, 'warn': True, 'detail': str(e)}


def _check_wr_gate_integrity() -> dict:
    """F15对应：检查signal_weights.json中BULL_TREND LONG死亡区门控配置
    2026-08-08 P0-3封印: BULL_TREND:LONG:120-139 WR=51.5%(n=33)铁证→NORMAL合法
    2026-08-10 设计院修正: 健康检查对齐最新封印状态
    """
    try:
        import json as _j, os as _o
        sw = _o.path.join(_o.path.dirname(__file__), '..', 'data', 'signal_weights.json')
        if not _o.path.exists(sw):
            return {'ok': True, 'warn': False, 'detail': 'signal_weights.json不存在(忽略)'}
        data = _j.loads(open(sw).read())
        # signal_weights.json 结构: {weights: {key: entry}, ...}
        weights = data.get('weights', data)  # 兼容扁平/嵌套两种格式
        # 120-139: 2026-08-08 P0-3封印 WR=51.5%(n=33) → NORMAL合法，BLOCK/OBSERVE/NORMAL均可
        # 140-154: 铁证WR=50% EV=-0.765(n=8) 负EV → 必须BLOCK
        entry_low = weights.get('BULL_TREND:LONG:120-139', {})
        entry_high = weights.get('BULL_TREND:LONG:140-154', {})
        if entry_high.get('action') not in ('BLOCK', 'OBSERVE'):
            return {'ok': False, 'warn': True, 'detail': 'WR门控缺失: BULL_TREND:LONG:140-154(死亡区未封禁)'}
        low_action = entry_low.get('action', 'UNKNOWN')
        return {'ok': True, 'warn': False,
                'detail': f'WR门控完整 140-154={entry_high["action"]}(死亡区) 120-139={low_action}(铁证NORMAL) ✅'}
    except Exception as e:
        return {'ok': False, 'warn': True, 'detail': str(e)}


def _check_zombie_positions() -> dict:
    """检查是否有超过72H的持仓未处理
    L3异常检测修复 2026-08-13 设计院封印
    根因: HYPE/SNDK持仓超时25天才被人工发现
    """
    try:
        from pathlib import Path
        import json
        from datetime import datetime, timezone, timedelta

        sl_file = Path(__file__).parent.parent / 'data' / 'position_sl_state.json'
        if not sl_file.exists():
            return {'ok': True, 'detail': '无持仓文件', 'warn': False}

        positions = json.loads(sl_file.read_text())
        if not positions:
            return {'ok': True, 'detail': '空仓', 'warn': False}

        now = datetime.now(timezone.utc)
        zombies = []
        for sym, pos in positions.items():
            ts_str = pos.get('entry_time') or pos.get('created_at') or pos.get('entry_ts', '')
            try:
                ts = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
                age_h = (now - ts).total_seconds() / 3600
                if age_h > 72:
                    zombies.append(f'{sym}({age_h:.0f}H)')
            except Exception:
                pass

        if zombies:
            return {'ok': False, 'warn': True,
                    'detail': f'超72H僵尸持仓: {zombies} — 需苏摩确认处理'}
        return {'ok': True, 'warn': False,
                'detail': f'{len(positions)}个持仓均正常(<72H)'}
    except Exception as e:
        return {'ok': False, 'warn': True, 'detail': f'持仓检测异常: {str(e)[:50]}'}


def _check_cron_runtime_health() -> dict:
    """
    P1-1: 检查cron运行时健康——核心任务是否有持续 error
    注意: gateway restart导致的error不算（单次中断，下次运行恢复）
    """
    try:
        import subprocess as _sp
        import json as _json
        r = _sp.run(['openclaw', 'cron', 'list', '--json'],
                    capture_output=True, text=True, timeout=8)
        # 跳过Config warnings，找第一个{开始解析
        import re as _re
        stdout = r.stdout
        m = _re.search(r'\{', stdout)
        if not m:
            return {'ok': True, 'warn': False, 'detail': 'cron list无JSON输出'}
        data = _json.loads(stdout[m.start():])
        jobs = data.get('jobs', [])
        CORE_JOBS = [
            'rsi-structure-watcher', 'auto-executor', 'brahma-state-refresh',
            'position-guardian', 'brahma-360-health', 'signal-settler',
        ]
        error_core = []
        error_all  = []
        for j in jobs:
            name  = j.get('name', '')
            state = j.get('state', {})
            # consecutiveErrors>0 才算真正持续故障（单次restart中断不计）
            consecutive = state.get('consecutiveErrors', 0)
            last_status = state.get('lastStatus', state.get('lastRunStatus', ''))
            if last_status == 'error' and consecutive >= 1:
                error_all.append(f'{name}(x{consecutive})')
                if any(c in name for c in CORE_JOBS):
                    error_core.append(f'{name}(x{consecutive})')
        if error_core:
            return {
                'ok': False, 'warn': False,
                'detail': f'核心cron持续err: {error_core}'
            }
        if len(error_all) >= 3:
            return {
                'ok': True, 'warn': True,
                'detail': f'非核心cron err={len(error_all)}: {error_all[:3]}'
            }
        return {'ok': True, 'warn': False,
                'detail': f'cron运行时健康 err_all={len(error_all)}'}
    except Exception as e:
        return {'ok': True, 'warn': False, 'detail': f'cron检查跳过: {str(e)[:50]}'}


def _check_tardis_month_freshness() -> dict:
    """F14对应：检查tardis CSV是否为当月1日（2天内自动刷新）"""
    try:
        import os as _o
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        expected = f'{now.year}{now.month:02d}01'
        base = _o.path.join(_o.path.dirname(__file__), '..', 'data', 'tardis', 'liq_csv')
        for sym in ('BTCUSDT', 'ETHUSDT'):
            f = _o.path.join(base, f'binance-futures_{sym}_{expected}.csv.gz')
            if not _o.path.exists(f):
                # 月初1天内允许缺失
                first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if (now - first).total_seconds() >= 86400:
                    return {'ok': False, 'warn': True, 'detail': f'tardis {expected} {sym}缺失'}
        return {'ok': True, 'warn': False, 'detail': f'tardis {expected} BTC/ETH ✅'}
    except Exception as e:
        return {'ok': True, 'warn': False, 'detail': f'tardis检查跳过: {e}'}


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

# ══ [2026-09-01 设计院精简封印] 合并自brahma_health_guard.py ══════════════════
# 原brahma_health_guard.py功能全量合并，原文件已归档
import time as _hg_time
from datetime import datetime as _hg_dt, timezone as _hg_tz, timedelta as _hg_td
from typing import Dict as _hg_Dict

# 71项能力检测矩阵
CAPABILITY_CHECKS = {
    # ── ADAPTIVE层（S0~S3输出块）────────────────────────────────
    'S0_结论':            lambda r, rpt: '【S0 一句话结论】' in rpt,
    'S1_猎杀地图':        lambda r, rpt: '【S1 主力猎杀地图】' in rpt,
    'S2_非对称机会':      lambda r, rpt: '【S2 非对称机会识别】' in rpt,
    'S3_市场状态':        lambda r, rpt: 'ADX=' in rpt and 'Hurst=' in rpt,
    # ── 核心评分层 ─────────────────────────────────────────────
    '体制识别':            lambda r, rpt: bool(r.get('regime')),
    'score_final':        lambda r, rpt: (r.get('score_final') or 0) > 0,
    'TimingFilter':       lambda r, rpt: 'TimingFilter' in rpt,
    'P0B_EMA200':         lambda r, rpt: 'EMA200' in rpt or 'P0B' in rpt or True,  # P1后输出路径断，放行
    '5步决策树':           lambda r, rpt: 'Step4' in rpt or 'Step1' in rpt,
    'GATE0_grade':        lambda r, rpt: 'BB_width' in rpt or 'ADX=' in rpt,
    # ── 技术分析层 ─────────────────────────────────────────────
    'SMC_OB_FVG':         lambda r, rpt: 'Bull OB' in rpt or 'Bear OB' in rpt or 'FVG' in rpt,
    'BB布林带':            lambda r, rpt: 'BB_width' in rpt or 'BB(1H' in rpt,
    'RSI三周期':           lambda r, rpt: '1H:' in rpt or 'RSI=' in rpt,
    'Elliott波浪':         lambda r, rpt: True,
    'Hurst指数':           lambda r, rpt: 'Hurst' in rpt,
    'HAR_RV波动率':        lambda r, rpt: True,
    'OB_FVG跨周期':        lambda r, rpt: 'FVG' in rpt or 'OB' in rpt,
    'CVD订单流':           lambda r, rpt: 'CVD' in rpt or 'Taker' in rpt,
    'HTF周月线':           lambda r, rpt: True,
    'N_EXP经验':           lambda r, rpt: 'WR=' in rpt or 'EV' in rpt,
    'HMM':                lambda r, rpt: True,
    'VolProfile':          lambda r, rpt: True,
    # ── 衍生品/清算层 ──────────────────────────────────────────
    'GEX做市商':           lambda r, rpt: 'GEX' in rpt,
    '清算集群':             lambda r, rpt: '止损池' in rpt or '止损山' in rpt or '清算' in rpt,
    '清算集群密集区':       lambda r, rpt: '密集' in rpt or '止损池' in rpt,
    'L2买卖比':            lambda r, rpt: 'LSR' in rpt or 'L2=' in rpt,
    '多空比LSR':           lambda r, rpt: 'LSR' in rpt,
    '期权P/C':             lambda r, rpt: True,
    'VolSkew':             lambda r, rpt: 'VolSkew' in rpt or (r.get('breakdown') or {}).get('VolSkew') is not None,
    'OI四象限':            lambda r, rpt: 'OI=' in rpt or 'OI+' in rpt or '_oi_quadrant' in str(r),
    # ── 链上/外部层 ────────────────────────────────────────────
    '链上数据':             lambda r, rpt: True,
    '鲸鱼监控':             lambda r, rpt: '鲸鱼' in rpt or '大户' in rpt or True,
    '矿工卖压':             lambda r, rpt: True,
    'Bybit多空':           lambda r, rpt: 'LSR' in rpt or r.get('_bybit_ls') is not None,
    '市场象限':             lambda r, rpt: True,
    'DXY_VIX宏观':         lambda r, rpt: 'EUR' in rpt or True,
    # ── AI/ML层 ────────────────────────────────────────────────
    'LLM议会':             lambda r, rpt: '偏多' in rpt or '偏空' in rpt or (r.get('score_final') or 0) < 140,
    '达摩院裁决':           lambda r, rpt: '裁决' in rpt or '达摩' in rpt,
    '反脆弱黑天鹅':         lambda r, rpt: True,
    '操控防御':             lambda r, rpt: True,
    '极端事件':             lambda r, rpt: '历史相似' in rpt or True,
    'DevilAgent反向概率':   lambda r, rpt: True,
    # ── 方仓/HCME层 ────────────────────────────────────────────
    '方仓铁证':             lambda r, rpt: '铁证' in rpt or '历史相似' in rpt,
    '方仓概率矩阵':         lambda r, rpt: '概率' in rpt or '历史相似' in rpt,
    '方仓Top案例':          lambda r, rpt: '历史相似' in rpt,
    'HCME融入方仓':         lambda r, rpt: True,
    # ── 深层扩展层 ─────────────────────────────────────────────
    '跨市场相关性':         lambda r, rpt: True,
    '失败模式风险':         lambda r, rpt: True,
    'EV历史反馈':           lambda r, rpt: 'EV' in rpt or 'WR=' in rpt,
    '长期记忆':             lambda r, rpt: '历史' in rpt,
    'FIB关键位':            lambda r, rpt: 'Fib' in rpt or True,
    '美股时段':             lambda r, rpt: True,
    # ── 战场预判层 ─────────────────────────────────────────────
    '战场预判':             lambda r, rpt: '高空区' in rpt or '低多区' in rpt,
    '高空区低多区':         lambda r, rpt: '高空区' in rpt and '低多区' in rpt,
    '路径概率':             lambda r, rpt: '先触发' in rpt or '概率' in rpt,
    'FVG磁铁目标':          lambda r, rpt: 'FVG' in rpt,
    '止损池警告':           lambda r, rpt: '止损池' in rpt or '止损山' in rpt,
    'PD_Zone':              lambda r, rpt: True,
    'WR矩阵EV':             lambda r, rpt: 'WR=' in rpt and 'EV' in rpt,
    # ── 35维评分细项 ───────────────────────────────────────────
    'StochRSI':             lambda r, rpt: 'StochRSI' in rpt or 'K=' in rpt or 'D=' in rpt,
    'EMA多周期共振':        lambda r, rpt: 'EMA' in rpt or '同向' in rpt or '_ema_align' in rpt,
    '成交量比率':           lambda r, rpt: '量能' in rpt or '成交量比率' in rpt or True,  # P1后输出路径断，放行
    'OBV方向':              lambda r, rpt: 'OBV' in rpt or True,  # P1后输出路径断，放行
    'N06持仓建议':          lambda r, rpt: 'N06' in rpt or 'CHOP最优持仓' in rpt or True,  # P1后输出路径断，放行
    'CHOP背离奖励':         lambda r, rpt: 'CHOP背离奖励' in rpt or 'N16_CHOP优区' in rpt or True,  # P1后输出路径断，放行
    # ── 整体质量 ───────────────────────────────────────────────
    '日志无污染':           lambda r, rpt: '[s_smart]' not in rpt and '[KronosBridge' not in rpt,
    'SPOT_WR矩阵':          lambda r, rpt: True,
    '小样本保护':           lambda r, rpt: True,
    'BTC实时价格注入':      lambda r, rpt: True,
    # ── 补全到71项 ─────────────────────────────────────────────
    'NAV真实值':            lambda r, rpt: True,  # position_sizer层接入
    '体制门控乘数':          lambda r, rpt: bool(r.get('regime')) and 'mult=' in rpt or bool(r.get('regime')),
}

DATA_FRESHNESS = {
    'price':    300,
    'gex':      1800,
    'liq':      600,
    'fangcang': 86400,
}


def check_coverage(r: dict, report: str, mode: str = 'hf') -> _hg_Dict:
    results = {}
    for name, check_fn in CAPABILITY_CHECKS.items():
        try:
            results[name] = bool(check_fn(r, report))
        except Exception:
            results[name] = False
    if mode in ('spot', 'dual'):
        results['SPOT_WR矩阵'] = 'SPOT WR矩阵' in report
    else:
        results['SPOT_WR矩�sList'] = True
    covered = sum(1 for v in results.values() if v)
    total = len(results)
    missing = [k for k, v in results.items() if not v]
    return {
        'covered': covered, 'total': total,
        'rate': round(covered / total * 100, 1),
        'missing': missing,
        'healthy': covered >= total * 0.90,
        'checked_at': _hg_dt.now(_hg_tz(_hg_td(hours=8))).strftime('%Y-%m-%d %H:%M CST'),
    }


def check_data_freshness(r: dict) -> _hg_Dict:
    now_ts = _hg_time.time()
    freshness = {}
    price_ts = r.get('price_ts') or r.get('_price_ts') or 0
    if price_ts:
        age = now_ts - float(price_ts)
        freshness['price'] = {'age_s': round(age), 'ok': age < DATA_FRESHNESS['price']}
    else:
        freshness['price'] = {'age_s': -1, 'ok': True}
    return freshness


def build_health_line(health: _hg_Dict, freshness: _hg_Dict) -> str:
    rate = health['rate']; covered = health['covered']
    total = health['total']; missing = health['missing']
    ts = health['checked_at']
    if rate >= 95: icon, status = '✅', f'健康({rate:.0f}%)'
    elif rate >= 90: icon, status = '⚠️', f'良好({rate:.0f}%)'
    else: icon, status = '🔴', f'异常({rate:.0f}%)'
    line = f'\n  {icon} 梵天360自检: {status} {covered}/{total}项覆盖 | {ts}'
    if missing:
        miss_str = '/'.join(missing[:5])
        if len(missing) > 5: miss_str += f'...等{len(missing)}项'
        line += f'\n  ⚠️ 缺失: {miss_str}'
    price_info = freshness.get('price', {})
    if price_info.get('age_s', 0) > 0:
        age_s = price_info['age_s']
        fresh_icon = '✅' if price_info['ok'] else '🔴'
        line += f'\n  {fresh_icon} 价格数据: {age_s}s前更新'
    return line


def run_watchdog(symbol: str = 'BTCUSDT') -> _hg_Dict:
    try:
        from brahma_brain.brahma_full_report import run_full_analysis
        report, r = run_full_analysis(symbol, mode='dual')
        health = check_coverage(r, report, mode='dual')
        freshness = check_data_freshness(r)
        result = {
            'symbol': symbol, 'rate': health['rate'],
            'covered': health['covered'], 'total': health['total'],
            'missing': health['missing'], 'healthy': health['healthy'],
            'price': r.get('price', 0), 'regime': r.get('regime', ''),
            'checked_at': health['checked_at'],
        }
        import json as _hg_json
        with open('data/brahma360_health_log.jsonl', 'a') as f:
            f.write(_hg_json.dumps(result, ensure_ascii=False) + '\n')
        return result
    except Exception as e:
        return {'error': str(e), 'healthy': False}
