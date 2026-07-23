#!/usr/bin/env python3
"""
brahma_config_audit.py — 梵天系统配置契约审计 v1.0
设计院 · 2026-07-23 · 苏摩111封印

发现类型: L2配置漂移 + L3逻辑缺陷 + L4集成断裂
触发方式: cron每日凌晨 / 手动 / post-commit hook

检测项:
  A1  依赖包完整性 (yaml/requests/pandas等)
  A2  .env KEY名 vs 各脚本读取名 一致性
  A3  PUSH_TARGET占位符扫描 (YOUR_USER_ID)
  A4  cron message语言检测 (应含中文)
  A5  cron delivery.to vs SSOT 一致性
  A6  safety.yaml有效性 (live_trading/yaml语法)
  A7  关键脚本 sys.path 完整性
  A8  dict裸访问['status']防御性扫描
  A9  Binance MIN_NOTIONAL vs 系统配置
  A10 executed_set写入逻辑 (FAILED不写入)
"""

import sys, os, json, subprocess, re, time, importlib
from pathlib import Path
from datetime import datetime, timezone

BASE   = Path(__file__).parent.parent
DATA   = BASE / 'data'
SCRIPTS = BASE / 'scripts'

sys.path.insert(0, str(BASE))

SSOT_THREAD = None
SSOT_USER   = None
try:
    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_TARGET
    SSOT_USER   = JARVIS_USER_ID
    SSOT_THREAD = JARVIS_THREAD_ID
except Exception:
    pass

PUSH_TARGET  = f"{SSOT_USER}:thread:{SSOT_THREAD}" if SSOT_USER else '73295708:thread:019f8768-6731-777d-8924-2426a5abd10f'
PUSH_CHANNEL = 'jarvis'

# ─── 结果收集 ─────────────────────────────────────────────────────

class AuditResult:
    def __init__(self):
        self.checks = []  # [(id, name, status, detail)]
        self.errors   = 0
        self.warnings = 0
        self.passed   = 0

    def ok(self, id_, name, detail=''):
        self.checks.append((id_, name, 'OK', detail))
        self.passed += 1

    def warn(self, id_, name, detail):
        self.checks.append((id_, name, 'WARN', detail))
        self.warnings += 1

    def fail(self, id_, name, detail):
        self.checks.append((id_, name, 'FAIL', detail))
        self.errors += 1

    def score(self):
        total = len(self.checks) or 1
        return int((self.passed / total) * 100)

    def summary(self):
        return f"通过={self.passed} 警告={self.warnings} 失败={self.errors} 总分={self.score()}/100"


# ─── 检测项实现 ───────────────────────────────────────────────────

def check_a1_deps(r: AuditResult):
    """A1 依赖包完整性"""
    REQUIRED = ['yaml', 'requests', 'hmac', 'pandas', 'numpy', 'sklearn', 'statsmodels']
    OPTIONAL  = ['lgbm', 'lightgbm', 'psutil']
    for pkg in REQUIRED:
        try:
            importlib.import_module(pkg)
            r.ok('A1', f'依赖/{pkg}', '已安装')
        except ImportError:
            r.fail('A1', f'依赖/{pkg}', f'❌ {pkg} 未安装 → 可能导致运行时崩溃')
    for pkg in OPTIONAL:
        try:
            importlib.import_module(pkg)
            r.ok('A1', f'依赖可选/{pkg}', '已安装')
        except ImportError:
            r.warn('A1', f'依赖可选/{pkg}', f'⚠️ {pkg} 未安装 (可选)')


def check_a2_env_keys(r: AuditResult):
    """A2 .env KEY名 vs 脚本读取名一致性"""
    env_file = BASE / '.env'
    if not env_file.exists():
        r.fail('A2', '.env文件', '❌ .env文件不存在')
        return

    # 读取.env中的KEY名
    env_keys = set()
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k = line.split('=', 1)[0].strip()
                env_keys.add(k)
    except Exception as e:
        r.fail('A2', '.env读取', f'❌ 读取失败: {e}')
        return

    r.ok('A2', '.env文件', f'存在，含{len(env_keys)}个KEY: {sorted(env_keys)}')

    # 检查关键KEY
    KEY_PAIRS = [
        ('BINANCE_API_KEY',    ['BINANCE_API_KEY'],     '必须'),
        ('BINANCE_API_SECRET', ['BINANCE_API_SECRET', 'BINANCE_SECRET'], '必须'),
    ]
    for expected, alternatives, level in KEY_PAIRS:
        found = [k for k in alternatives if k in env_keys]
        if found:
            r.ok('A2', f'.env/{expected}', f'存在为 {found[0]}')
        else:
            if level == '必须':
                r.fail('A2', f'.env/{expected}', f'❌ .env中未找到 {alternatives}')

    # 检查system_config读取的KEY名是否与.env一致
    sc_file = SCRIPTS / 'system_config.py'
    if sc_file.exists():
        sc_src = sc_file.read_text()
        # 找 os.environ.get('KEY_NAME') 模式
        gets = re.findall(r"os\.environ\.get\(['\"]([^'\"]+)['\"]", sc_src)
        # 可选ENV变量（有代码默认值，不在.env里是正常的）
    OPTIONAL_KEYS = {'BINANCE_FAPI_BASE', 'BINANCE_TESTNET', 'BINANCE_RECV_WINDOW'}
    for key in gets:
            if key.startswith('BINANCE_'):
                if key in env_keys:
                    r.ok('A2', f'system_config读取/{key}', '与.env一致')
                elif key in OPTIONAL_KEYS:
                    r.ok('A2', f'system_config读取/{key}', '可选KEY，有代码默认值')
                else:
                    # 检查是否有兼容名
                    compat = {'BINANCE_API_SECRET': 'BINANCE_SECRET', 'BINANCE_SECRET': 'BINANCE_API_SECRET'}
                    alt = compat.get(key)
                    if alt and alt in env_keys:
                        r.warn('A2', f'system_config读取/{key}', f'⚠️ system_config读{key}但.env存的是{alt}')
                    else:
                        r.fail('A2', f'system_config读取/{key}', f'❌ .env中无此KEY')


def check_a3_placeholders(r: AuditResult):
    """A3 PUSH_TARGET占位符扫描"""
    PLACEHOLDERS = ['YOUR_USER_ID', 'YOUR_THREAD_ID', 'YOUR_JARVIS']
    script_files = list(SCRIPTS.glob('*.py'))
    found_any = False
    for f in script_files:
        try:
            src = f.read_text()
            for ph in PLACEHOLDERS:
                # 排除注释行、工具脚本、审计脚本自身
                if ph in src and f.name not in ('update_jarvis_thread.py', 'brahma_config_audit.py'):
                    lines = [i+1 for i, l in enumerate(src.splitlines())
                             if ph in l
                             and not l.strip().startswith('#')
                             and not l.strip().startswith('"""')
                             and "# SSOT" not in l
                             and "fallback" not in l.lower()]
                    if lines:
                        r.fail('A3', f'占位符/{f.name}', f'❌ 含 {ph} 于行 {lines[:3]}')
                        found_any = True
        except Exception:
            pass
    if not found_any:
        r.ok('A3', '占位符扫描', f'✅ {len(script_files)}个脚本无占位符')


def check_a4_cron_language(r: AuditResult):
    """A4 cron message语言检测（应含中文）"""
    jobs_file = Path.home() / '.openclaw/cron/jobs.json'
    if not jobs_file.exists():
        r.warn('A4', 'cron语言', '⚠️ jobs.json不存在')
        return

    data = json.load(open(jobs_file))
    jobs = data.get('jobs', [])
    issues = []
    for j in jobs:
        if not j.get('enabled'): continue
        name = j.get('name', '')
        msg  = j.get('payload', {}).get('message', '')
        if not msg: continue
        has_zh = any('\u4e00' <= c <= '\u9fff' for c in msg)
        if not has_zh:
            issues.append(name)
            r.warn('A4', f'cron语言/{name}', f'⚠️ message无中文 → AI可能用英文回复')

    if not issues:
        enabled = sum(1 for j in jobs if j.get('enabled'))
        r.ok('A4', 'cron语言', f'✅ {enabled}个活跃任务全部含中文')


def check_a5_cron_routing(r: AuditResult):
    """A5 cron delivery目标 vs SSOT一致性"""
    if not SSOT_THREAD:
        r.warn('A5', 'cron路由', '⚠️ 无法读取SSOT，跳过检测')
        return

    jobs_file = Path.home() / '.openclaw/cron/jobs.json'
    if not jobs_file.exists():
        r.warn('A5', 'cron路由', '⚠️ jobs.json不存在')
        return

    data = json.load(open(jobs_file))
    jobs = data.get('jobs', [])
    misrouted = []
    for j in jobs:
        if not j.get('enabled'): continue
        delivery = j.get('delivery', {})
        to = delivery.get('to', '')
        # Square帖子有独立线程，不算错误路由
        ALLOWED_THREADS = [t for t in [SSOT_THREAD, '019f879c-9249-7044-a232-8783ae496198'] if t]
        if to and not any(t in to for t in ALLOWED_THREADS):
            misrouted.append((j['name'], to))
            r.fail('A5', f'cron路由/{j["name"]}', f'❌ 推送到 {to[:50]} 非SSOT线程')

    if not misrouted:
        enabled = sum(1 for j in jobs if j.get('enabled'))
        r.ok('A5', 'cron路由', f'✅ {enabled}个任务全部路由到SSOT')


def check_a6_safety_yaml(r: AuditResult):
    """A6 safety.yaml有效性"""
    safety_file = BASE / 'config' / 'safety.yaml'
    if not safety_file.exists():
        r.fail('A6', 'safety.yaml', '❌ 文件不存在')
        return

    try:
        import yaml
        cfg = yaml.safe_load(safety_file.read_text()) or {}
        live = cfg.get('global', {}).get('live_trading_enabled', False)
        if live:
            r.ok('A6', 'safety.yaml', f'✅ live_trading_enabled=true')
        else:
            r.fail('A6', 'safety.yaml', '❌ live_trading_enabled=false → 自动开单被全局封禁')
        paper = cfg.get('global', {}).get('paper_only', True)
        if paper:
            r.warn('A6', 'safety.yaml/paper_only', '⚠️ paper_only=true')
        else:
            r.ok('A6', 'safety.yaml/paper_only', 'paper_only=false ✅')
    except ImportError:
        r.fail('A6', 'pyyaml', '❌ pyyaml未安装 → safety.yaml无法读取 → live_trading默认false')
    except Exception as e:
        r.fail('A6', 'safety.yaml语法', f'❌ YAML解析失败: {e}')


def check_a7_syspath(r: AuditResult):
    """A7 关键脚本sys.path完整性 (能从任意目录import brahma_brain)"""
    KEY_SCRIPTS = [
        'market_screener.py',
        'auto_executor.py',
        'brahma_dashboard.py',
        'signal_watcher.py',
        'btc_regime_watcher.py',
    ]
    for fname in KEY_SCRIPTS:
        fpath = SCRIPTS / fname
        if not fpath.exists():
            r.warn('A7', f'sys.path/{fname}', f'⚠️ 脚本不存在')
            continue
        src = fpath.read_text()
        # 检查是否有sys.path insert在brahma_brain import之前
        has_path_fix = 'sys.path.insert' in src or 'sys.path.append' in src
        has_brahma_import = 'from brahma_brain' in src or 'import brahma_brain' in src
        if has_brahma_import and not has_path_fix:
            r.fail('A7', f'sys.path/{fname}', f'❌ 有brahma_brain import但无sys.path修复')
        elif has_brahma_import:
            # 确认path fix在import之前
            path_pos = src.find('sys.path.insert')
            import_pos = src.find('from brahma_brain')
            if import_pos == -1:
                import_pos = src.find('import brahma_brain')
            if path_pos < import_pos:
                r.ok('A7', f'sys.path/{fname}', 'sys.path在import前 ✅')
            else:
                r.warn('A7', f'sys.path/{fname}', '⚠️ sys.path在import后，可能有问题')
        else:
            r.ok('A7', f'sys.path/{fname}', '无brahma_brain直接import')


def check_a8_dict_access(r: AuditResult):
    """A8 dict裸访问['status']防御性扫描（应用.get）"""
    PATTERNS = [
        (r"exec_result\['status'\]",      "exec_result裸访问"),
        (r"result\['status'\](?!\s*=)",   "result裸访问"),
        (r"response\['code'\]",            "response裸访问"),
    ]
    issues = []
    for fpath in SCRIPTS.glob('*.py'):
        try:
            src = fpath.read_text()
            for pattern, label in PATTERNS:
                matches = [(i+1, l.strip()) for i, l in enumerate(src.splitlines())
                           if re.search(pattern, l) and not l.strip().startswith('#')]
                if matches:
                    for lineno, line in matches[:2]:
                        issues.append(f'{fpath.name}:{lineno} {label}')
                        r.warn('A8', f'裸访问/{fpath.name}:{lineno}', f'⚠️ {label}: {line[:60]}')
        except Exception:
            pass
    if not issues:
        r.ok('A8', '防御性访问', f'✅ 扫描{len(list(SCRIPTS.glob("*.py")))}个脚本无裸访问')


def check_a9_min_notional(r: AuditResult):
    """A9 系统MIN_NOTIONAL vs Binance实际"""
    import requests as req
    # 读取auto_executor的MIN_NOTIONAL
    ae_file = SCRIPTS / 'auto_executor.py'
    if not ae_file.exists():
        r.warn('A9', 'MIN_NOTIONAL', '⚠️ auto_executor.py不存在')
        return

    src = ae_file.read_text()
    m = re.search(r'MIN_NOTIONAL\s*=\s*([\d.]+)', src)
    if not m:
        r.warn('A9', 'MIN_NOTIONAL', '⚠️ 找不到MIN_NOTIONAL定义')
        return

    system_min = float(m.group(1))
    r.ok('A9', f'系统MIN_NOTIONAL={system_min}', '已读取')

    # 读取SYMBOL_MIN_NOTIONAL映射（per-symbol覆盖）
    sym_min_map = {}
    m2 = re.findall(r"[\'\"]([A-Z]+USDT)[\'\"]\s*:\s*([0-9.]+)", src)
    for sym_k, val in m2:
        sym_min_map[sym_k] = float(val)

    try:
        resp = req.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=8).json()
        symbols_to_check = ['SNDKUSDT', 'BTCUSDT', 'ETHUSDT', 'HYPEUSDT']
        for sym in symbols_to_check:
            # 该symbol的系统最低值
            effective_min = sym_min_map.get(sym, system_min)
            for s in resp.get('symbols', []):
                if s['symbol'] == sym:
                    for f in s.get('filters', []):
                        if f['filterType'] == 'MIN_NOTIONAL':
                            binance_min = float(f.get('notional', 0))
                            if effective_min < binance_min:
                                r.fail('A9', f'MIN_NOTIONAL/{sym}',
                                       f'❌ 系统配置{effective_min} < Binance实际{binance_min}（需在SYMBOL_MIN_NOTIONAL中配置）')
                            else:
                                r.ok('A9', f'MIN_NOTIONAL/{sym}',
                                     f'✅ 系统{effective_min} >= Binance{binance_min}')
                            break
    except Exception as e:
        r.warn('A9', 'Binance API', f'⚠️ 无法获取exchangeInfo: {e}')


def check_a10_executed_set_logic(r: AuditResult):
    """A10 executed_set逻辑：FAILED不应写入持久化"""
    ae_file = SCRIPTS / 'auto_executor.py'
    if not ae_file.exists():
        r.warn('A10', 'executed_set', '⚠️ auto_executor.py不存在')
        return

    src = ae_file.read_text()
    # 检查是否有正确的条件判断
    # 正确: if exec_result.get('status') == 'EXECUTED': ... _save_executed
    # 错误: 无条件调用 _save_executed
    lines = src.splitlines()
    # 只检查调用点（不是函数定义体内的write行）
    save_calls = [(i+1, l) for i, l in enumerate(lines)
                  if '_save_executed(' in l
                  and not l.strip().startswith('#')
                  and 'def _save_executed' not in l
                  and '.write_text(' not in l]

    for lineno, line in save_calls:
        # 向上找最近的if语句（8行上文）
        context = '\n'.join(lines[max(0, lineno-8):lineno])
        # 防重复挂单分支（existing_open/already_open）是合理的无条件写入
        if 'existing_open' in context or 'already_open' in context:
            r.ok('A10', f'executed_set/L{lineno}', '防重复挂单分支，合理写入✅')
        elif "get('status') == 'EXECUTED'" in context or "get('status') ==" in context:
            r.ok('A10', f'executed_set/L{lineno}', '✅ _save_executed有EXECUTED条件保护')
        else:
            r.fail('A10', f'executed_set/L{lineno}', f'❌ _save_executed无条件执行 → FAILED也会写入去重集')

    if not save_calls:
        r.warn('A10', 'executed_set', '⚠️ 找不到_save_executed调用')


# ─── 主流程 ───────────────────────────────────────────────────────

def run_audit(push=False, verbose=False):
    r = AuditResult()

    print(f"🔍 梵天配置契约审计 · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("─" * 55)

    checks = [
        ("A1 依赖包",        check_a1_deps),
        ("A2 ENV KEY名",     check_a2_env_keys),
        ("A3 占位符",        check_a3_placeholders),
        ("A4 cron语言",      check_a4_cron_language),
        ("A5 cron路由",      check_a5_cron_routing),
        ("A6 safety.yaml",   check_a6_safety_yaml),
        ("A7 sys.path",      check_a7_syspath),
        ("A8 防御性访问",    check_a8_dict_access),
        ("A9 MIN_NOTIONAL",  check_a9_min_notional),
        ("A10 executed_set", check_a10_executed_set_logic),
    ]

    for label, fn in checks:
        try:
            fn(r)
        except Exception as e:
            r.fail(label, label, f'❌ 检测异常: {e}')

    # 打印结果
    status_icon = {'OK': '✅', 'WARN': '⚠️', 'FAIL': '❌'}
    if verbose:
        for id_, name, status, detail in r.checks:
            icon = status_icon.get(status, '?')
            print(f"  {icon} [{id_}] {name}")
            if status != 'OK' and detail:
                print(f"       {detail}")
    else:
        # 只打印非OK项
        non_ok = [(id_, name, status, detail) for id_, name, status, detail in r.checks if status != 'OK']
        for id_, name, status, detail in non_ok:
            icon = status_icon.get(status, '?')
            print(f"  {icon} [{id_}] {name}: {detail}")

    print("─" * 55)
    score = r.score()
    status = "🟢 HEALTHY" if score >= 90 else "🟡 DEGRADED" if score >= 70 else "🔴 CRITICAL"
    print(f"结果: {status} | {r.summary()}")

    # 推送到Jarvis
    if push and (r.errors > 0 or r.warnings > 2):
        _push_alert(r)

    return r


def _push_alert(r: AuditResult):
    """发现问题时推送到苏摩"""
    score = r.score()
    status = "🔴 CRITICAL" if score < 70 else "🟡 DEGRADED"
    fails = [(id_, name, detail) for id_, name, status_, detail in r.checks if status_ == 'FAIL']
    warns = [(id_, name, detail) for id_, name, status_, detail in r.checks if status_ == 'WARN']

    lines = [
        f"🔍 **梵天配置审计告警** · {datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')}",
        f"状态: {status} | 总分={score}/100 | 失败={r.errors} 警告={r.warnings}",
    ]
    if fails:
        lines.append(f"\n**❌ 失败项 ({len(fails)})**")
        for id_, name, detail in fails[:5]:
            lines.append(f"  [{id_}] {name}")
            lines.append(f"    {detail}")
    if warns:
        lines.append(f"\n**⚠️ 警告项 ({len(warns)})**")
        for id_, name, detail in warns[:3]:
            lines.append(f"  [{id_}] {name}")

    msg = '\n'.join(lines)
    subprocess.run(
        ['openclaw', 'message', 'send', '--channel', PUSH_CHANNEL,
         '--target', PUSH_TARGET, '--message', msg],
        capture_output=True, timeout=15
    )
    print(f"[audit] 已推送告警到 {PUSH_TARGET}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天配置契约审计')
    parser.add_argument('--push',    action='store_true', help='发现问题时推送到Jarvis')
    parser.add_argument('--verbose', action='store_true', help='显示全部检测结果')
    args = parser.parse_args()
    run_audit(push=args.push, verbose=args.verbose)
