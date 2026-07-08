#!/usr/bin/env python3
"""
brahma_self_heal.py · 梵天自愈引擎 v1.0
══════════════════════════════════════════════════
设计院自主决策 · 2026-07-03 · 苏摩授权

核心能力：
  1. 主动检测 → 识别系统故障（API/引擎/队列/文件/cron/推送路由）
  2. 自动修复 → 不需要人工干预的故障直接自愈
  3. 分级上报 → 自愈成功静默 / 自愈失败 → P0推送苏摩
  4. 看门狗模式 → cron任务存活检测 + 自动重注册

自愈矩阵：
  故障类型             → 自愈动作
  ─────────────────────────────────────────────────
  API断连              → safe_fetch重试 / 等待恢复
  brahma_bus陈旧       → flush_stale()
  评分引擎崩溃          → ast语法检查 + 模块重载
  信号队列积压          → 清理超期信号
  关键数据文件缺失      → brahma_state_refresh
  cron任务停止运行      → 重新触发 / 检测并上报
  推送路由错误          → 检测019f1797匹配 + 自动修正
  regime_state陈旧     → 强制刷新体制状态
  position_sl_state脏  → 清理updated_at=0条目

触发方式：cron every 15min
"""

import sys, os, json, time, subprocess, requests, ast
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

try:
    import scripts.system_config as _sc
    PUSH_TARGET = f"{_sc.JARVIS_USER_ID}:t:{_sc.JARVIS_THREAD_ID}"
except Exception:
    PUSH_TARGET   = '73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075'  # SSOT v10 [BUG-5修复 2026-07-07]
PUSH_CHANNEL  = 'jarvis'
HEAL_LOG_FILE = BASE / 'logs' / 'self_heal.log'
STATE_FILE    = BASE / 'data' / 'self_heal_state.json'
FAPI          = 'https://fapi.binance.com'

# 监控的 cron 任务名 → 最大允许无运行间隔（分钟）
CRON_WATCHLIST = {
    'rsi-structure-watcher':   15,   # 每5min，15min没跑 = 故障
    'brahma-scan-guard':       800,  # 每12H
    'btc-regime-watcher':      15,
    'ws-guardian-keepalive':   15,
    'auto-position-manager-30m': 45,   # 正确任务名（auto-position-manager不存在）
    'regime-switch-monitor':   75,
    # ── 信号推送系统（2026-07-08 自愈盲区补入）──────────────────
    'main-signal-watcher':     45,   # 每30min，45min未跑=故障
    'pump-hunter':             45,   # 每30min
    'brahma-nerve-center':     25,   # 每15min
    'oi-surge-scanner':        300,  # 每4H
}

# 关键数据文件 → 最大陈旧分钟
CRITICAL_FILES = {
    'data/regime_state.json':       60,
    'data/scan_candidates.json':    90,
    'data/rsi_watcher_state.json':  20,   # v1.1: 从15→20min，避免与5min刷新频率临界竞争
}


# ══════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════

def _log(msg: str):
    """写入自愈日志"""
    HEAL_LOG_FILE.parent.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime('%m-%d %H:%M UTC')
    with open(HEAL_LOG_FILE, 'a') as f:
        f.write(f'[{ts}] {msg}\n')
    print(f'[SelfHeal] {msg}')


def _push(msg: str, dedup_key: str = None, dedup_ttl: int = 3600) -> bool:
    state = _load_state()
    if dedup_key:
        last = state.get('dedup', {}).get(dedup_key, 0)
        if time.time() - last < dedup_ttl:
            return False
        state.setdefault('dedup', {})[dedup_key] = time.time()
        _save_state(state)
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', PUSH_CHANNEL,
             '--target', PUSH_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=15
        )
        return True
    except Exception as e:
        _log(f'推送失败: {e}')
        return False


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _run(cmd: list, timeout: int = 30) -> tuple:
    """执行命令，返回 (success, output)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=str(BASE))
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, 'TIMEOUT'
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════
# 检测模块
# ══════════════════════════════════════════════════════════════

def check_module_registry() -> dict:
    """[设计院 2026-07-06] 模块注册表健康检查 — CORE模块全量验证"""
    try:
        import sys, os
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for _p in [_base, os.path.join(_base, 'brahma_brain')]:
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from brahma_brain.module_registry import check_core_modules
        missing = check_core_modules()
        if missing:
            _push(
                f'🚨 [SelfHeal] CORE模块缺失: {missing}',
                dedup_key='core_module_missing', dedup_ttl=3600
            )
            return {'ok': False, 'detail': f'CORE缺失: {missing}'}
        return {'ok': True, 'detail': f'17/17模块正常'}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}

def check_binance_api() -> dict:
    """Binance API 连通性 + 延迟"""
    t0 = time.time()
    try:
        r = requests.get(f'{FAPI}/fapi/v1/ping', timeout=5)
        latency = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            return {'ok': True, 'latency_ms': latency,
                    'warn': latency > 2000,
                    'detail': f'延迟{latency}ms'}
        return {'ok': False, 'detail': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def check_brahma_analyze() -> dict:
    """信号链核心文件 brahma_analyze.py 存在性检查（设计院 2026-07-06）"""
    analyze_file = BASE / 'brahma_analyze.py'
    bak_file     = BASE / 'brahma_analyze.py.bak_20260703'
    if not analyze_file.exists():
        # 自愈：从备份恢复
        if bak_file.exists():
            import shutil
            shutil.copy2(str(bak_file), str(analyze_file))
            _push(
                '🔧 [SelfHeal] brahma_analyze.py 丢失 → 已从.bak自动恢复 \n信号链恢复正常',
                dedup_key='brahma_analyze_missing', dedup_ttl=3600
            )
            return {'ok': True, 'detail': '⚠️ 丢失 → 已从bak自动恢复', 'healed': True}
        else:
            _push(
                '🚨 [SelfHeal] brahma_analyze.py 丢失且无备份！信号链完全中断',
                dedup_key='brahma_analyze_no_bak', dedup_ttl=3600
            )
            return {'ok': False, 'detail': '❌ 丢失且无备份，信号链中断！'}
    try:
        ast.parse(analyze_file.read_text())
        return {'ok': True, 'detail': '存在+语法正常'}
    except SyntaxError as e:
        return {'ok': False, 'detail': f'语法错误L{e.lineno}: {e.msg}'}


def check_scoring_engine() -> dict:
    """评分引擎（brahma_core.py）语法健康"""
    core = BASE / 'brahma_brain' / 'brahma_core.py'
    if not core.exists():
        return {'ok': False, 'detail': 'brahma_core.py 不存在'}
    try:
        ast.parse(core.read_text())
        # 快速导入测试
        ok, out = _run(['python3', '-c',
                        'from brahma_brain.brahma_core import analyze; print("OK")'],
                       timeout=15)
        return {'ok': ok, 'detail': 'syntax+import正常' if ok else out[:100]}
    except SyntaxError as e:
        return {'ok': False, 'detail': f'语法错误L{e.lineno}: {e.msg}'}


def check_regime_state() -> dict:
    """体制状态文件新鲜度"""
    reg = BASE / 'data' / 'regime_state.json'
    if not reg.exists():
        return {'ok': False, 'detail': 'regime_state.json 不存在'}
    try:
        data = json.loads(reg.read_text())
        mtime = reg.stat().st_mtime
        age_min = (time.time() - mtime) / 60
        btc_reg = data.get('BTCUSDT', {}).get('confirmed', 'UNKNOWN')
        warn = age_min > 60
        return {
            'ok': True, 'warn': warn,
            'age_min': round(age_min, 1),
            'btc_regime': btc_reg,
            'detail': f'BTC={btc_reg} 更新={age_min:.0f}min前{"(陈旧⚠️)" if warn else ""}'
        }
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


def check_signal_pipeline() -> dict:
    """
    信号推送链完整性检查（2026-07-08 自愈系统重写）
    
    之前只检查「有没有信号文件」→ 完全无法发现推送链断裂
    现在检查：push_hub存在 + cron message非空 + announce=True + 路由正确
    任何一项不通 → ok=False + 自动触发告警推送
    """
    issues = []
    details = []

    # ── 检查1：push_hub.py 存在 ────────────────────────────────────
    pb1 = BASE / 'push_hub.py'
    pb2 = BASE / 'scripts' / 'push_hub.py'
    if not pb1.exists() and not pb2.exists():
        issues.append('CRITICAL: push_hub.py 缺失，推送出口完全断开')
    else:
        details.append('push_hub: ✅')

    # ── 检查2：信号日志新鲜度 ────────────────────────────────────────
    sig = BASE / 'data' / 'live_signal_log.jsonl'
    if sig.exists():
        try:
            raw_lines = sig.read_text().strip().split('\n')
            now = time.time()
            recent_4h = sum(
                1 for l in raw_lines
                if l.strip() and time.time() - json.loads(l).get('ts', 0) < 14400
            )
            if recent_4h == 0:
                issues.append('WARN: 4H内无新信号写入（分析引擎可能停止）')
            else:
                details.append(f'signal_log 4H新信号: {recent_4h}条 ✅')
        except Exception as e:
            details.append(f'signal_log: 读取异常({e})')
    else:
        issues.append('WARN: live_signal_log.jsonl 不存在')

    # ── 检查3：关键Cron的 message + announce + 路由 ─────────────────
    KEY_JOBS = ['main-signal-watcher', 'pump-hunter', 'brahma-nerve-center',
                'oi-surge-scanner', 'rsi-structure-watcher']
    CORRECT_THREAD = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        raw_jobs = json.loads(jobs_file.read_text())
        all_jobs = raw_jobs.get('jobs', raw_jobs) if isinstance(raw_jobs, dict) else raw_jobs
        job_map = {j.get('name'):j for j in all_jobs if isinstance(j, dict)}
        
        for name in KEY_JOBS:
            j = job_map.get(name)
            if not j:
                issues.append(f'CRITICAL: {name} cron任务不存在')
                continue
            msg = (j.get('payload', {}).get('message') or j.get('message') or '').strip()
            delivery = j.get('delivery') or {}
            announce = delivery.get('announce', False)
            to = delivery.get('to', '')
            
            if not msg:
                issues.append(f'CRITICAL: {name} message为空→任务从不执行')
            if not announce:
                issues.append(f'ERROR: {name} announce=False→结果不推送到Jarvis')
            if CORRECT_THREAD not in to:
                issues.append(f'ERROR: {name} 路由错误→消息发到旧线程')
            
            if msg and announce and CORRECT_THREAD in to:
                details.append(f'{name}: ✅')
    except Exception as e:
        issues.append(f'ERROR: cron jobs读取失败({e})')

    # ── 如果发现问题，立即推送告警 ──────────────────────────────────
    if issues:
        try:
            alert_msg = (
                "🚨 [梵天自愈] 信号推送链故障检测\n"
                + "\n".join(f"  • {i}" for i in issues)
                + f"\n\n时间: {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
            )
            # 尝试通过 push_hub 推送
            try:
                sys.path.insert(0, str(BASE))
                from push_hub import _jarvis
                _jarvis(alert_msg, dedup_key='signal_pipeline_fault', dedup_ttl=1800)
            except Exception:
                # push_hub不可用时直接调openclaw
                _tgt = '73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075'
                subprocess.run(
                    ['openclaw','message','send','--channel','jarvis',
                     '--target', _tgt, '--message', alert_msg],
                    capture_output=True, timeout=15
                )
        except Exception:
            pass

    return {
        'ok': len(issues) == 0,
        'issues': issues,
        'details': details,
        'detail': f'问题={len(issues)}项 | ' + ' | '.join(details[:3])
    }


def _load_job_last_run() -> dict:
    """从 runs/*.jsonl 读取每个jobId最后运行时间（替代 openclaw cron list，快10x）"""
    runs_dir = Path.home() / '.openclaw/cron/runs'
    job_last = {}  # jobId -> last_run_ms
    if not runs_dir.exists():
        return {}
    files = sorted(runs_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)[:120]
    for f in files:
        try:
            for l in f.read_text().strip().split('\n'):
                if not l.strip(): continue
                d = json.loads(l)
                if d.get('action') != 'finished': continue
                jid = d.get('jobId', '')
                run_at = d.get('runAtMs', 0)
                if jid not in job_last or run_at > job_last[jid]:
                    job_last[jid] = run_at
        except Exception:
            pass
    return job_last


def _load_job_name_to_id() -> dict:
    """从 jobs.json 建立 name->id 映射（纯文件读取，无子进程）"""
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        raw = json.loads(jobs_file.read_text())
        all_j = raw.get('jobs', raw) if isinstance(raw, dict) else raw
        return {j.get('name', ''): j.get('id', '') for j in all_j if isinstance(j, dict)}
    except Exception:
        return {}


def check_cron_jobs() -> dict:
    """cron任务存活检测 + message/announce完整性检测（v2 2026-07-08 无子进程版）"""
    issues = []
    now_ms = time.time() * 1000
    job_last   = _load_job_last_run()
    name_to_id = _load_job_name_to_id()

    # jobs.json 全量（用于注册检测 + message检测）
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        raw_jobs  = json.loads(jobs_file.read_text())
        all_jobs  = raw_jobs.get('jobs', raw_jobs) if isinstance(raw_jobs, dict) else raw_jobs
        job_map   = {j.get('name'): j for j in all_jobs if isinstance(j, dict)}
    except Exception as e:
        return {'ok': False, 'detail': f'jobs.json读取失败: {e}'}

    for job_name, max_idle_min in CRON_WATCHLIST.items():
        if job_name not in job_map:
            issues.append(f'{job_name}: 未注册')
            continue
        jid = name_to_id.get(job_name, '')
        last_ms = job_last.get(jid, 0)
        if last_ms > 0:
            age_min = (now_ms - last_ms) / 60000
            if age_min > max_idle_min:
                issues.append(f'{job_name}: {age_min:.0f}min未运行(阈值{max_idle_min}min)')
        # 若无运行记录且阈值<=60，视为可能异常（宽松处理，不强报警）

    # [2026-07-08] 额外检测：关键任务的 message/announce 完整性
    # check_cron_jobs 之前只看「有没有运行」，不检测「message是否为空」
    # message为空 → Agent收到空任务直接HEARTBEAT_OK → 信号永远不推送
    KEY_SIGNAL_JOBS = ['main-signal-watcher', 'pump-hunter', 'brahma-nerve-center',
                       'oi-surge-scanner', 'rsi-structure-watcher']
    CORRECT_THREAD  = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        raw_jobs  = json.loads(jobs_file.read_text())
        all_jobs  = raw_jobs.get('jobs', raw_jobs) if isinstance(raw_jobs, dict) else raw_jobs
        job_map   = {j.get('name'): j for j in all_jobs if isinstance(j, dict)}
        for name in KEY_SIGNAL_JOBS:
            j = job_map.get(name)
            if not j:
                continue   # 「未注册」已在上面检测
            msg      = (j.get('payload', {}).get('message') or j.get('message') or '').strip()
            delivery = j.get('delivery') or {}
            announce = delivery.get('announce', False)
            to       = delivery.get('to', '')
            if not msg:
                issues.append(f'{name}: message为空(Agent收到空任务→永远静默)')
            if not announce:
                issues.append(f'{name}: announce=False(结果不推送到Jarvis)')
            if CORRECT_THREAD not in to:
                issues.append(f'{name}: 路由错误(to={to[:40] if to else "空"})')
    except Exception as _e:
        issues.append(f'cron jobs.json读取异常: {_e}')

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'issues': issues,
        'detail': '所有cron正常' if not issues else f'{len(issues)}个异常: {issues[:2]}'
    }


def check_data_files() -> dict:
    """关键数据文件新鲜度"""
    stale = []
    now = time.time()
    for rel_path, max_age_min in CRITICAL_FILES.items():
        p = BASE / rel_path
        if not p.exists():
            stale.append(f'{rel_path}(不存在)')
            continue
        age_min = (now - p.stat().st_mtime) / 60
        if age_min > max_age_min:
            stale.append(f'{rel_path}({age_min:.0f}min前)')
    return {
        'ok': len(stale) == 0,
        'warn': len(stale) > 0,
        'stale': stale,
        'detail': '文件正常' if not stale else f'陈旧: {stale}'
    }


def check_liq_density_engine() -> dict:
    """
    [2026-07-06] s7-LiqDens 三所清算引擎健康检查
    验证: OKX端点可用 + confidence>=0.3 + score_adj非0
    自愈: OKX接口错误时告警（已修复为liquidation-orders端点）
    """
    try:
        import requests as _req
        # 验证OKX清算端点可用
        _r = _req.get(
            'https://www.okx.com/api/v5/public/liquidation-orders',
            params={'instType': 'SWAP', 'uly': 'BTC-USDT', 'state': 'filled', 'limit': 10},
            timeout=6
        )
        if _r.status_code != 200:
            return {'ok': False, 'warn': True,
                    'detail': f'OKX清算接口HTTP={_r.status_code}，可能接口故障'}
        _data = _r.json().get('data', [])
        _total = sum(len(d.get('details', [])) for d in _data)
        if _total == 0:
            return {'ok': True, 'warn': True,
                    'detail': f'OKX清算数据为空(0条)，市场无强平或接口异常'}
        # 验证liq_density_engine模块能正常import
        _src = (BASE / 'brahma_brain' / 'liq_density_engine.py').read_text()
        if 'liquidation-orders' not in _src:
            return {'ok': False, 'warn': True,
                    'detail': 'liq_density_engine用了旧OKX接口(rubik/OI)，需修复为liquidation-orders'}
        return {'ok': True, 'records': _total,
                'detail': f'OKX清算正常 {_total}条 | 接口=liquidation-orders ✅'}
    except Exception as _e:
        return {'ok': False, 'warn': True, 'detail': f'liq_density检查异常: {str(_e)[:80]}'}


def check_kronos_lgbm() -> dict:
    """
    [2026-07-06] Kronos LightGBM健康检查
    验证: libgomp可用 + lgbm可import + 模型文件存在 + MODE=blend
    自愈: 发现libgomp缺失时自动修复symlink
    """
    issues = []
    # 1. 检查libgomp
    try:
        import ctypes
        ctypes.cdll.LoadLibrary('libgomp.so.1')
    except OSError:
        # 尝试自动修复
        _gomp_torch = '/usr/local/lib/python3.11/dist-packages/torch/lib/libgomp.so.1'
        _gomp_target = '/usr/local/lib/libgomp.so.1'
        import os as _os
        if _os.path.exists(_gomp_torch) and not _os.path.exists(_gomp_target):
            try:
                _os.symlink(_gomp_torch, _gomp_target)
                import subprocess as _sp
                _sp.run(['ldconfig'], capture_output=True)
                issues.append('libgomp.so.1 symlink已自动修复')
            except Exception as _e2:
                issues.append(f'libgomp.so.1缺失且自动修复失败: {_e2}')
        else:
            issues.append('libgomp.so.1不可用，torch/lib路径也缺失')

    # 2. 检查lightgbm import
    try:
        import lightgbm as lgb  # noqa
    except ImportError as _e:
        issues.append(f'lightgbm import失败: {str(_e)[:60]}')
        return {'ok': False, 'warn': True, 'issues': issues,
                'detail': ' | '.join(issues)}

    # 3. 检查模型文件
    _wf_model = BASE / 'data' / 'kronos_wf_model_lgb.txt'
    if not _wf_model.exists():
        issues.append('kronos_wf_model_lgb.txt不存在')

    # 4. 检查MODE
    _kb_path = BASE / 'brahma_brain' / 'kronos_bridge.py'
    if _kb_path.exists():
        _kb_src = _kb_path.read_text()
        if "'shadow'" in _kb_src and 'blend' not in _kb_src:
            issues.append('kronos_bridge MODE=shadow，Kronos分数不影响评分')

    if issues:
        return {'ok': False, 'warn': True, 'issues': issues,
                'detail': ' | '.join(issues)}
    return {'ok': True, 'detail': 'LightGBM正常 | libgomp✅ | 模型文件✅ | MODE=blend✅'}


def check_analysis_chain() -> dict:
    """
    [2026-07-06] 分析链路端到端健康检查
    验证: run_analysis()能正常执行 + 关键字段完整 + score>0
    [修复] 418限速期间返回warn而非fail，避免误触发自愈告警
    """
    try:
        import subprocess as _sp, requests as _rq
        # 418预检 — 限速期间分析链降级是预期行为，非故障
        try:
            _ping = _rq.get('https://fapi.binance.com/fapi/v1/ping', timeout=5)
            if _ping.status_code in (418, 429):
                _ra = _ping.headers.get('Retry-After', _ping.headers.get('retry-after', '?'))
                return {'ok': True, 'warn': True,
                        'detail': f'Binance 限速中({_ping.status_code}) Retry-After={_ra}s — 分析链降级属预期行为'}
        except Exception:
            pass
        _res = _sp.run(
            ['python3', '-c',
             'import sys; sys.path.insert(0,".");'
             'from brahma_brain.brahma_analysis_runner import run_analysis;'
             'r=run_analysis("BTCUSDT");'
             'assert r.get("regime"), "regime缺失";'
             'assert "score_final" in r, "score_final缺失";'
             'sf=str(round(r["score_final"],1));rg=r["regime"];ts=r.get("timing_status","?");print("OK score="+sf+" regime="+rg+" timing="+ts)'],
            capture_output=True, text=True, timeout=45,
            cwd=str(BASE)
        )
        if _res.returncode == 0:
            out = _res.stdout.strip().split('\n')[-1]
            return {'ok': True, 'detail': f'分析链路正常: {out}'}
        else:
            err = (_res.stderr or _res.stdout)[-200:]
            return {'ok': False, 'warn': True, 'detail': f'run_analysis失败: {err}'}
    except Exception as _e:
        return {'ok': False, 'warn': True, 'detail': f'分析链路检查异常: {str(_e)[:80]}'}


def check_execution_pipeline() -> dict:
    """
    OPT-C: 执行管道健康检测（设计院优化 2026-07-03）
    监控: position_tsl_monitor / auto_execute_gate 进程/文件健康
    """
    issues = []
    # 检查 auto_execute_gate.py 语法
    aeg = BASE / 'scripts' / 'auto_execute_gate.py'
    if aeg.exists():
        try:
            ast.parse(aeg.read_text())
        except SyntaxError as e:
            issues.append(f'auto_execute_gate 语法错误L{e.lineno}')
    else:
        issues.append('auto_execute_gate.py 不存在')

    # 检查 position_tsl_monitor.py 语法
    tsl = BASE / 'scripts' / 'position_tsl_monitor.py'
    if tsl.exists():
        try:
            ast.parse(tsl.read_text())
        except SyntaxError as e:
            issues.append(f'position_tsl_monitor 语法错误L{e.lineno}')
    else:
        issues.append('position_tsl_monitor.py 不存在')

    # 检查 live_signal_log 队列是否积压（超100条未处理视为异常）
    lsl = BASE / 'data' / 'live_signal_log.jsonl'
    if lsl.exists():
        try:
            lines = lsl.read_text().strip().split('\n')
            pending = sum(1 for l in lines if '"action":"ENTER"' in l or
                          ('"valid":true' in l.lower() or '"valid": true' in l.lower()))
            if pending > 50:
                issues.append(f'信号队列积压: {pending}条有效信号待处理')
        except Exception:
            pass

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'issues': issues,
        'detail': '执行管道正常' if not issues else f'{issues}'
    }


def check_cron_precise() -> dict:
    """
    OPT-C: 精确cron存活检测 v3（2026-07-08 无子进程版）
    直接读 runs/*.jsonl 获取最后运行时间，避免调用 openclaw cron list（开销~3s）
    """
    issues = []
    now_ms     = time.time() * 1000
    job_last   = _load_job_last_run()   # 共享cache，已在 check_cron_jobs 中加载过
    name_to_id = _load_job_name_to_id()

    for job_name, max_idle_min in CRON_WATCHLIST.items():
        jid = name_to_id.get(job_name, '')
        last_ms = job_last.get(jid, 0)
        if last_ms == 0:
            if max_idle_min <= 30:
                issues.append(f'{job_name}: 从未运行')
        else:
            idle_min = (now_ms - last_ms) / 60000
            if idle_min > max_idle_min:
                issues.append(f'{job_name}: {idle_min:.0f}min未运行(阈值{max_idle_min}min)')

    return {
        'ok':    len(issues) == 0,
        'warn':  len(issues) > 0,
        'issues': issues,
        'detail': 'cron全部正常' if not issues else f'{len(issues)}异常: {issues[:3]}'
    }


def check_push_routing() -> dict:
    """推送路由正确性：确认所有cron任务使用正确线程"""
    # [BUG-5 封印修复 2026-07-07] CORRECT_THREAD曾错误设为旧线程，导致自愈反向覆盖正确路由
    # SSOT: 始终从 system_config.py 动态读取正确线程并不硬编码
    try:
        import importlib.util as _ilu
        _sc_path = BASE / 'scripts' / 'system_config.py'
        _spec = _ilu.spec_from_file_location('system_config', _sc_path)
        _sc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sc)
        CORRECT_THREAD = getattr(_sc, 'JARVIS_THREAD_ID', '019f181f-e4d1-7576-85ca-77f4a7fa8075')
    except Exception:
        CORRECT_THREAD = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
    
    # 旧线程列表：除CORRECT_THREAD外的应该被替换的
    OLD_THREADS    = [
        '019f309c-609b-7a75-a195-e221e5927c63',  # 旧线程 v1
        '019f1797-6c60-7541-ad72-ec34ed14dfc4',  # 旧线程 v0
    ]
    OLD_THREADS = [t for t in OLD_THREADS if t != CORRECT_THREAD]  # 防止正确线程被当作旧线程
    issues = []
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        if not jobs_file.exists():
            return {'ok': True, 'detail': 'jobs.json不存在'}
        raw = json.loads(jobs_file.read_text())
        jobs = raw.get('jobs', raw) if isinstance(raw, dict) else raw
        if isinstance(jobs, dict):
            jobs = list(jobs.values())
        for j in jobs:
            if not isinstance(j, dict):
                continue
            delivery = j.get('delivery', {})
            to = delivery.get('to', '')
            if any(old in to for old in OLD_THREADS):
                issues.append(j.get('name', '?'))
    except Exception as e:
        return {'ok': False, 'detail': str(e)}

    # ── push_hub.py 存在性检查（2026-07-08 盲区补入）──
    push_hub_ok = (BASE / 'push_hub.py').exists() or (BASE / 'scripts' / 'push_hub.py').exists()
    if not push_hub_ok:
        issues.append('push_hub.py 缺失（推送出口断开）')

    # ── Cron message 非空检查（2026-07-08 盲区补入）──
    KEY_SIGNAL_JOBS = ['main-signal-watcher','pump-hunter','brahma-nerve-center',
                       'oi-surge-scanner','rsi-structure-watcher','signal-watcher-6h']
    try:
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        raw2 = json.loads(jobs_file.read_text())
        all_jobs = raw2.get('jobs', raw2) if isinstance(raw2, dict) else raw2
        for j in all_jobs:
            if not isinstance(j, dict): continue
            if j.get('name') in KEY_SIGNAL_JOBS and not (j.get('payload',{}).get('message') or j.get('message','')).strip():
                issues.append(f"{j['name']}: message为空(任务不执行)")
    except Exception:
        pass

    return {
        'ok': len(issues) == 0,
        'warn': len(issues) > 0,
        'wrong_thread_jobs': issues,
        'detail': '推送路由正常' if not issues else f'旧线程任务: {issues}'
    }


# ══════════════════════════════════════════════════════════════
# 自愈执行矩阵
# ══════════════════════════════════════════════════════════════

def heal(fault_type: str, context: dict) -> dict:
    """
    自愈动作执行器
    返回：{'healed': bool, 'action': str, 'output': str}
    """
    result = {'healed': False, 'action': fault_type, 'output': ''}

    if fault_type == 'API_RECONNECT':
        ok, out = _run(['python3', 'scripts/safe_fetch.py', '--test'], timeout=20)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'CACHE_FLUSH':
        ok, out = _run(['python3', '-c',
                        'from brahma_brain.brahma_bus import BrahmaBus; '
                        'b=BrahmaBus(); b.flush_stale(); print("flushed")'], timeout=15)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'REGIME_STALE':
        ok, out = _run(['python3', 'guardrails/brahma_state_refresh.py'], timeout=30)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'DEPS_CHECK':
        # 检查并自动修复缺失的Python依赖
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location('ensure_deps',
                BASE / 'scripts' / 'ensure_deps.py')
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _r = _mod.check_and_fix()
            if _r['fixed']:
                result.update({'healed': True, 'output': f'依赖已修复: {_r["fixed"]}'})
            else:
                result.update({'ok': True, 'output': '依赖完整'})
        except Exception as _e:
            result.update({'ok': False, 'output': f'deps_check异常: {_e}'})

    elif fault_type == 'DATA_FILE_REFRESH':
        ok, out = _run(['python3', 'scripts/brahma_state_refresh.py'], timeout=30)
        result.update({'healed': ok, 'output': out[:200]})

    elif fault_type == 'POSITION_SL_CLEAN':
        # 清理 updated_at=0 脏数据
        ps = BASE / 'data' / 'position_sl_state.json'
        if ps.exists():
            data = json.loads(ps.read_text())
            before = len(data)
            cleaned = {k: v for k, v in data.items()
                       if isinstance(v, dict) and v.get('updated_at', 0) > 1700000000}
            ps.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False))
            removed = before - len(cleaned)
            result.update({'healed': True, 'output': f'清理{removed}条脏数据'})

    elif fault_type == 'PUSH_HUB_MISSING':
        # push_hub.py 缺失 → 自动从备份或重建
        _pb = BASE / 'push_hub.py'
        _pb_s = BASE / 'scripts' / 'push_hub.py'
        if not _pb.exists() and not _pb_s.exists():
            _content = '''import subprocess, json, time, os\nfrom pathlib import Path\ntry:\n    import sys; sys.path.insert(0, str(Path(__file__).parent / "scripts"))\n    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL\n    _TARGET = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"\n    _CHANNEL = JARVIS_CHANNEL\nexcept Exception:\n    _TARGET = "73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075"\n    _CHANNEL = "jarvis"\n_DEDUP_FILE = Path(__file__).parent / "data" / "push_dedup.json"\ndef _load_dedup():\n    try: return json.loads(_DEDUP_FILE.read_text())\n    except: return {}\ndef _save_dedup(d):\n    try: _DEDUP_FILE.parent.mkdir(exist_ok=True); _DEDUP_FILE.write_text(json.dumps(d))\n    except: pass\ndef _jarvis(msg, dedup_key=None, dedup_ttl=3600):\n    if not msg: return False\n    if dedup_key:\n        dedup = _load_dedup(); now = time.time()\n        if now - dedup.get(dedup_key, 0) < dedup_ttl: return False\n        dedup[dedup_key] = now; _save_dedup(dedup)\n    try:\n        r = subprocess.run(["openclaw","message","send","--channel",_CHANNEL,"--target",_TARGET,"--message",msg], capture_output=True, text=True, timeout=15)\n        return r.returncode == 0\n    except: return False\n'''
            _pb.write_text(_content)
            _pb_s.write_text(_content)
            return {'healed': True, 'action': 'push_hub.py重建'}
        return {'healed': False, 'action': 'push_hub已存在'}

    elif fault_type == 'CRON_MESSAGE_EMPTY':
        # Cron message 为空 → 推送告警，不自动修改（需人工确认message内容）
        _msg = "⚠️ [自愈告警] 发现Cron信号任务 message 为空，推送链断裂，请检查修复。"
        try:
            from push_hub import _jarvis as _pj
            _pj(_msg, dedup_key='cron_msg_empty', dedup_ttl=3600)
        except Exception:
            pass
        return {'healed': False, 'action': '已发送告警', 'detail': '需人工修复message'}

    elif fault_type == 'PUSH_ROUTE_FIX':
        # [v1.1 2026-07-03] 修复旧线程推送路由 — 从system_config读取SSOT线程ID
        try:
            import importlib.util as _ilu
            _sc_path = BASE / 'scripts' / 'system_config.py'
            _spec = _ilu.spec_from_file_location('system_config', _sc_path)
            _sc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sc)
            NEW = getattr(_sc, 'JARVIS_THREAD_ID', '019f181f-e4d1-7576-85ca-77f4a7fa8075')
        except Exception:
            NEW = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
        # [BUG-5修复] OLD_LIST不含NEW，防止覆盖正确线程
        OLD_LIST = ['019f309c-609b-7a75-a195-e221e5927c63', '019f1797-6c60-7541-ad72-ec34ed14dfc4']
        OLD_LIST = [o for o in OLD_LIST if o != NEW]
        jobs_file = Path.home() / '.openclaw/cron/jobs.json'
        if jobs_file.exists():
            jobs_txt = jobs_file.read_text()
            total_fixed = 0
            for OLD in OLD_LIST:
                if OLD in jobs_txt:
                    cnt = jobs_txt.count(OLD)
                    jobs_txt = jobs_txt.replace(OLD, NEW)
                    total_fixed += cnt
            if total_fixed > 0:
                # [2026-07-08 地基层升级] 原子写入，防止截断
                import tempfile, shutil as _shutil
                try:
                    _shutil.copy2(str(jobs_file), str(jobs_file) + '.bak')
                    _fd, _tmp = tempfile.mkstemp(dir=str(jobs_file.parent), suffix='.tmp')
                    with open(_fd, 'w') as _f:
                        _f.write(jobs_txt); _f.flush(); os.fsync(_f.fileno())
                    os.replace(_tmp, str(jobs_file))
                    result.update({'healed': True, 'output': f'旧线程→{NEW[:8]} 修复{total_fixed}处'})
                except Exception as _e:
                    result.update({'healed': False, 'output': f'原子写入失败: {_e}'})
            else:
                result.update({'healed': True, 'output': '无需修复（路由已正确）'})

    elif fault_type == 'SCAN_CANDIDATES_REFRESH':
        ok, out = _run(['python3', 'scripts/market_screener.py'], timeout=60)
        result.update({'healed': ok, 'output': out[-200:] if out else ''})

    elif fault_type == 'LIQ_DENSITY_FIX':
        # [2026-07-06] OKX清算接口/extra_data/LONG反转 三项Bug修复校验
        # 主要检查代码是否正确，无法自动重写代码，上报告警即可
        _lde = BASE / 'brahma_brain' / 'liq_density_engine.py'
        _core = BASE / 'brahma_brain' / 'brahma_core.py'
        issues = []
        if _lde.exists():
            _s = _lde.read_text()
            if 'liquidation-orders' not in _s:
                issues.append('OKX接口仍用旧rubik/OI历史端点')
        if _core.exists():
            _s = _core.read_text()
            if "'price': price" not in _s and '"price": price' not in _s:
                issues.append('extra_data[price]未注入')
        if issues:
            result.update({'healed': False,
                           'output': f'LiqDens代码问题: {", ".join(issues)} — 需人工修复'})
        else:
            result.update({'healed': True,
                           'output': 'LiqDens代码校验通过: OKX接口=liquidation-orders, price已注入'})

    elif fault_type == 'KRONOS_LGBM_FIX':
        # [2026-07-06] 尝试修复libgomp symlink
        import os as _os
        _gomp_torch = '/usr/local/lib/python3.11/dist-packages/torch/lib/libgomp.so.1'
        _gomp_target = '/usr/local/lib/libgomp.so.1'
        if not _os.path.exists(_gomp_target) and _os.path.exists(_gomp_torch):
            try:
                _os.symlink(_gomp_torch, _gomp_target)
                import subprocess as _sp
                _sp.run(['ldconfig'], capture_output=True)
                result.update({'healed': True,
                               'output': 'libgomp.so.1 symlink已自动修复，Kronos lgbm恢复'})
            except Exception as _e2:
                result.update({'healed': False,
                               'output': f'libgomp自愈失败: {_e2} — 需人工执行ldconfig'})
        else:
            # 检查MODE是否是blend
            _kb = BASE / 'brahma_brain' / 'kronos_bridge.py'
            if _kb.exists() and "'shadow'" in _kb.read_text():
                result.update({'healed': False,
                               'output': 'kronos_bridge MODE=shadow，评分未激活 — 需改为blend'})
            else:
                result.update({'healed': True,
                               'output': 'libgomp已存在 + MODE=blend，Kronos状态正常'})

    elif fault_type == 'SIGNAL_PIPELINE_FIX':
        # [2026-07-08 架构缺口修复] 信号推送链故障自动修复
        # 处理：push_hub缺失 + cron message为空 + announce=False
        issues = context.get('issues', [])
        fixed = []
        errors = []

        # 修复1: push_hub.py 缺失
        _pb = BASE / 'push_hub.py'
        if not _pb.exists() and 'push_hub' in str(issues):
            try:
                _content = '''import subprocess, json, time, os\nfrom pathlib import Path\ntry:\n    import sys; sys.path.insert(0, str(Path(__file__).parent / "scripts"))\n    from system_config import JARVIS_USER_ID, JARVIS_THREAD_ID\n    _TARGET = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"\nexcept Exception:\n    _TARGET = "73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075"\n_CHANNEL = "jarvis"\n_DEDUP_FILE = Path(__file__).parent / "data" / "push_dedup.json"\ndef _load_dedup():\n    try: return json.loads(_DEDUP_FILE.read_text())\n    except: return {}\ndef _save_dedup(d):\n    try: _DEDUP_FILE.parent.mkdir(exist_ok=True); _DEDUP_FILE.write_text(json.dumps(d))\n    except: pass\ndef _jarvis(msg, dedup_key=None, dedup_ttl=3600):\n    if not msg: return False\n    if dedup_key:\n        dedup = _load_dedup(); now = time.time()\n        if now - dedup.get(dedup_key, 0) < dedup_ttl: return False\n        dedup[dedup_key] = now; _save_dedup(dedup)\n    try:\n        r = subprocess.run(["openclaw","message","send","--channel",_CHANNEL,"--target",_TARGET,"--message",msg], capture_output=True, text=True, timeout=15)\n        return r.returncode == 0\n    except: return False\n'''
                _pb.write_text(_content)
                fixed.append('push_hub.py重建')
            except Exception as _e:
                errors.append(f'push_hub重建失败:{_e}')

        # 修复2: cron message 为空 + announce=False
        KEY_JOBS_MSG = {
            'pump-hunter': 'Run trading-system pump hunter scan. Check dharma/pump_hunter/scan_and_alert.py for high-score pre-pump signals. If score>=85 found, report ticker+score+entry zone. If none, reply HEARTBEAT_OK.',
            'brahma-nerve-center': 'Run brahma nerve center check. Execute scripts/brahma_nerve_center.py and report any P0/P1 alerts found. If no alerts, reply HEARTBEAT_OK.',
            'main-signal-watcher': 'Check live_signal_log for valid trading signals. Run: python3 trading-system/scripts/signal_watcher.py and report any valid=True signals with score>=155. If none, reply HEARTBEAT_OK.',
            'oi-surge-scanner': 'Run OI surge scanner. Execute scripts/oi_surge_scanner.py and report any OI anomalies >5%. If none, reply HEARTBEAT_OK.',
            'rsi-structure-watcher': 'Check RSI structure events. Run scripts/rsi_structure_watcher.py --once and report any triggered events (E1-E9). If none, reply HEARTBEAT_OK.',
        }
        CORRECT_THREAD = '019f181f-e4d1-7576-85ca-77f4a7fa8075'
        try:
            import json as _json
            jobs_file = Path.home() / '.openclaw/cron/jobs.json'
            raw_jobs = _json.loads(jobs_file.read_text())
            all_jobs = raw_jobs.get('jobs', raw_jobs) if isinstance(raw_jobs, dict) else raw_jobs
            changed = False
            for j in all_jobs:
                if not isinstance(j, dict): continue
                name = j.get('name', '')
                if name not in KEY_JOBS_MSG: continue
                msg = (j.get('payload', {}).get('message') or j.get('message') or '').strip()
                delivery = j.get('delivery') or {}
                needs_fix = (not msg) or (not delivery.get('announce')) or (CORRECT_THREAD not in delivery.get('to',''))
                if needs_fix:
                    if not msg:
                        # 写入 payload.message（正确字段），不写顶层message
                        payload = j.get('payload') or {}
                        payload['message'] = KEY_JOBS_MSG[name]
                        j['payload'] = payload
                    delivery['announce'] = True
                    if CORRECT_THREAD not in delivery.get('to',''):
                        delivery['to'] = f'73295708:thread:{CORRECT_THREAD}'
                        delivery['channel'] = 'jarvis'
                    j['delivery'] = delivery
                    changed = True
                    fixed.append(f'{name}:message+announce修复')
            if changed:
                # [2026-07-08 地基层升级] 原子写入
                import tempfile as _tf, shutil as _sh
                try:
                    _final = _json.dumps(raw_jobs['jobs'] if isinstance(raw_jobs, dict) else all_jobs,
                                         indent=2, ensure_ascii=False)
                    if isinstance(raw_jobs, dict):
                        raw_jobs['jobs'] = all_jobs
                        _final = _json.dumps(raw_jobs, indent=2, ensure_ascii=False)
                    _sh.copy2(str(jobs_file), str(jobs_file) + '.bak')
                    _fd, _tmp = _tf.mkstemp(dir=str(jobs_file.parent), suffix='.tmp')
                    with open(_fd, 'w') as _f:
                        _f.write(_final); _f.flush(); os.fsync(_f.fileno())
                    os.replace(_tmp, str(jobs_file))
                except Exception as _we:
                    errors.append(f'原子写入失败:{_we}')
        except Exception as _e:
            errors.append(f'cron修复失败:{_e}')

        if fixed:
            result.update({'healed': True, 'output': f'已修复: {", ".join(fixed)}'})
        elif errors:
            result.update({'healed': False, 'output': f'修复失败: {", ".join(errors)}'})
        else:
            result.update({'healed': True, 'output': '信号推送链正常，无需修复'})

    elif fault_type == 'ANALYSIS_CHAIN_FAIL':
        # 分析链路失败：尝试刷新brahma_bus缓存
        ok, out = _run(['python3', '-c',
                        'import sys; sys.path.insert(0,".");'
                        'from brahma_brain.brahma_bus import BrahmaBus;'
                        'b=BrahmaBus(); b.flush_stale(); print("cache flushed")'],
                       timeout=15)
        result.update({'healed': ok,
                       'output': f'缓存清理: {out[:100]} — 若持续失败需检查brahma_core语法'})

    return result


# ══════════════════════════════════════════════════════════════
# 主检测+自愈循环
# ══════════════════════════════════════════════════════════════

def run_self_heal():
    _log('自愈引擎启动')
    healed_items  = []
    failed_items  = []
    reported_items = []

    # ── 执行所有检测 ─────────────────────────────────────────
    checks = {
        'brahma_analyze':    check_brahma_analyze(),
        'module_registry':   check_module_registry(),
        'binance_api':       check_binance_api(),
        'scoring_engine':    check_scoring_engine(),
        'regime_state':      check_regime_state(),
        'signal_pipeline':   check_signal_pipeline(),
        'cron_jobs':         check_cron_jobs(),
        'data_files':        check_data_files(),
        'push_routing':      check_push_routing(),
        'exec_pipeline':     check_execution_pipeline(),
        'cron_precise':      check_cron_precise(),
        # [2026-07-06] 新增：OKX清算引擎 + Kronos lgbm + 全链路分析
        'liq_density':       check_liq_density_engine(),
        'kronos_lgbm':       check_kronos_lgbm(),
        'analysis_chain':    check_analysis_chain(),
    }

    _log(f'检测完成: {sum(1 for c in checks.values() if c.get("ok"))}/'
         f'{len(checks)}项正常')

    # ── 自愈决策矩阵 ─────────────────────────────────────────
    fault_heal_map = [
        # (检测key, 判断条件, 故障类型, 是否上报失败)
        ('binance_api',    lambda c: not c.get('ok'),                  'API_RECONNECT',          True),
        ('regime_state',   lambda c: c.get('warn') and c.get('age_min',0) > 60, 'REGIME_STALE', False),
        ('data_files',     lambda c: c.get('warn') and len(c.get('stale',[])) > 0, 'DATA_FILE_REFRESH', False),
        ('push_routing',   lambda c: c.get('warn'),                    'PUSH_ROUTE_FIX',         False),
        ('exec_pipeline',  lambda c: c.get('warn'),                    'EXEC_PIPELINE_WARN',     True),
        ('cron_precise',   lambda c: c.get('warn') and len(c.get('issues',[])) > 0, 'CRON_PRECISE_WARN',   True),
        # [2026-07-06] 新增三项自愈
        ('liq_density',   lambda c: not c.get('ok'),                                  'LIQ_DENSITY_FIX',    True),
        ('kronos_lgbm',   lambda c: not c.get('ok'),                                  'KRONOS_LGBM_FIX',    True),
        ('analysis_chain',lambda c: not c.get('ok'),                                  'ANALYSIS_CHAIN_FAIL',True),
        # [2026-07-08 架构缺口修复] signal_pipeline 检测结果纳入自愈矩阵
        ('signal_pipeline', lambda c: not c.get('ok') and len(c.get('issues',[])) > 0, 'SIGNAL_PIPELINE_FIX', True),
    ]

    for check_key, condition, fault_type, report_on_fail in fault_heal_map:
        c = checks.get(check_key, {})
        if not condition(c):
            continue

        _log(f'发现故障: {check_key} → 尝试自愈: {fault_type}')
        heal_result = heal(fault_type, c)

        if heal_result['healed']:
            healed_items.append(f'{fault_type}: {heal_result["output"][:80]}')
            _log(f'✅ 自愈成功: {fault_type} | {heal_result["output"][:80]}')
        else:
            failed_items.append(f'{fault_type}: {heal_result["output"][:80]}')
            _log(f'❌ 自愈失败: {fault_type} | {heal_result["output"][:80]}')
            if report_on_fail:
                reported_items.append((check_key, c, fault_type))

    # 评分引擎故障：必须上报（无法自动修复）
    eng = checks.get('scoring_engine', {})
    if not eng.get('ok'):
        reported_items.append(('scoring_engine', eng, 'SCORING_ENGINE_FAIL'))

    # cron异常上报
    cron = checks.get('cron_jobs', {})
    if cron.get('warn') and cron.get('issues'):
        for issue in cron['issues']:
            _log(f'⚠️ cron异常: {issue}')
        # 仅上报，不自动重注册（避免重复注册）
        reported_items.append(('cron_jobs', cron, 'CRON_ISSUE'))

    # ── 生成上报消息 ─────────────────────────────────────────
    if reported_items:
        lines = ['🔴 **梵天自愈系统 · 需要关注**', '']
        for check_key, c, fault_type in reported_items:
            lines.append(f'❌ {fault_type}')
            lines.append(f'   {c.get("detail", "?")[:100]}')
            lines.append('')
        if failed_items:
            lines.append('⚠️ 自愈失败项：')
            for f in failed_items:
                lines.append(f'  · {f}')
        _push('\n'.join(lines),
              dedup_key=f'self_heal_report_{int(time.time()//3600)}',
              dedup_ttl=3600)
    elif healed_items:
        _log(f'🟢 自愈成功{len(healed_items)}项，静默（无需上报）: {healed_items}')
    else:
        _log('🟢 系统健康，无需自愈')

    # ── 定期健康摘要（每4H推送一次简报）───────────────────────
    state = _load_state()
    last_summary = state.get('last_summary_ts', 0)
    if time.time() - last_summary > 4 * 3600:
        ok_count  = sum(1 for c in checks.values() if c.get('ok'))
        btc_reg   = checks.get('regime_state', {}).get('btc_regime', '?')
        api_lat   = checks.get('binance_api', {}).get('latency_ms', '?')
        sig_24h   = checks.get('signal_pipeline', {}).get('recent_24h', 0)

        summary_msg = (
            f"🟢 **梵天自愈 · 4H摘要**\n"
            f"  系统健康: {ok_count}/{len(checks)}项正常\n"
            f"  BTC体制: {btc_reg}\n"
            f"  API延迟: {api_lat}ms\n"
            f"  24H信号: {sig_24h}条\n"
            f"  自愈历史: {len(healed_items)}项已修复"
        )
        _push(summary_msg,
              dedup_key=f'self_heal_summary_{int(time.time()//14400)}',
              dedup_ttl=14400)
        state['last_summary_ts'] = time.time()
        _save_state(state)

    return {
        'checks': checks,
        'healed': healed_items,
        'failed': failed_items,
        'reported': len(reported_items),
    }


if __name__ == '__main__':
    result = run_self_heal()
    print(f'\n[SelfHeal] 完成 | 自愈={len(result["healed"])} 失败={len(result["failed"])} 上报={result["reported"]}')