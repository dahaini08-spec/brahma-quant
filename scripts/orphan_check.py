#!/usr/bin/env python3
"""
orphan_check.py — 孤岛模块可达性检查 v4.0（pre-commit第2层后端）
[2026-09-26 苏摩111] 根因修复：v2.0 hook只grep 4个入口文件直接文本，
导致37个有真实caller的模块被误判孤岛（间接import链/动态加载不可见）。

判定逻辑（运行时可达性）：
  入口 = brahma_crontab.txt里的*.py + scripts/*.sh里的*.py
       + tests/*.py（unittest入口） + 主链4文件（runner/core/full_report/auto_executor）
  BFS沿import边遍历 → 不在可达集且不在豁免名单的brahma_brain/*.py = 孤岛

豁免名单（EXEMPT）= 合理孤岛（独立进程/测试夹具/服务器/冻结实验）。
豁免不等于无罪：列入即承认「主链不可达」事实，须在模块docstring写明理由。

用法：
  python3 scripts/orphan_check.py          # exit 0=通过 / 1=有孤岛 / 2=检查器自身错误
  python3 scripts/orphan_check.py --list   # 仅打印可达性分类，不设退出码
"""
import re
import sys
from pathlib import Path

BRAIN = Path(__file__).parent.parent / 'brahma_brain'
ROOT = Path(__file__).parent.parent

# ── 豁免名单 ──────────────────────────────────────────────────────
# [2026-09-26] 新增8项（真死模块审计处置）：
#   core_data/core_extra/core_scorer  9.7拆块封印的独立测试适配层（已合并回brahma_core，tests/引用）
#   data_contract_validator           契约验证工具（9.17封印，独立工具非运行时模块）
#   dim_ic_audit                      94维IC诊断工具（一次性审计已出结论，jesse依赖缺失）
#   brahma_brain_prompt               人格prompt库（唯一caller在_deprecated/，冻结）
#   brahma_engine_v5                  v5回测引擎（jesse依赖缺失冻结，dim_ic_audit同链）
#   brahma_mcp_server                 MCP服务器（独立启动进程，非import消费）
# 以下为v2.0 hook遗留豁免（历史登记，含已删文件名，保留无害）：
_EXEMPT = frozenset('''
    error_ledger risk_gate
    brahma_360 brahma_health brahma_wiring_check brahma_smoke_test_v2 math_utils safe_fetch
    state_store brahma_bus regime_config brahma_ci brahma_cpu brahma_pipeline brahma_gateway
    brahma_readiness market_quadrant brahma_context_injector multi_tf_context_builder
    failure_pattern_db macro_calendar causal_regime_verifier regime_scorer brahma_coordinator
    kronos_lite tradfi_dump_detector brahma_learning_loop brahma_experience_distiller llm_council
    llm_council_bridge position_sizer formatter signal_quality_engine brahma_smoke_test trader_brain
    core_data core_extra core_scorer data_contract_validator dim_ic_audit
    brahma_brain_prompt brahma_engine_v5 brahma_mcp_server
'''.split())

# 与stdlib/third-party撞名的模块名（resolve时优先本仓文件，无同名冲突即可用）
_EXTERNAL = frozenset('''
    sys os json re time math datetime logging pathlib collections typing functools itertools
    asyncio threading subprocess argparse traceback warnings dataclasses enum abc hashlib random
    glob shutil tempfile uuid socket statistics textwrap importlib contextlib copy io csv sqlite3
    signal queue sched urllib requests numpy pandas sklearn scipy talib pytz dateutil yaml dotenv
    aiohttp websockets ccxt binance redis psutil matplotlib PIL torch tqdm colorama zoneinfo venv
    tests concurrent ast codecs zlib gzip bz2 base64 struct ctypes multiprocessing filelock hmac
    http ssl select platform operator decimal fractions numbers calendar inspect gc weakref types
    jesse mcp openai binance_common binance_futures binance_spot
'''.split())


def imports_of(path: Path) -> list:
    """提取一个py文件引用的import名（静态+import_module动态）"""
    try:
        txt = path.read_text(errors='ignore')
    except Exception:
        return []
    out = []
    for m in re.finditer(r'^\s*from\s+([\w\.]+)\s+import', txt, re.M):
        out.append(m.group(1))
    for m in re.finditer(r'^\s*import\s+([\w\.,\s]+)', txt, re.M):
        for part in m.group(1).split(','):
            part = part.strip().split(' as ')[0].strip()
            if part:
                out.append(part)
    for m in re.finditer(r'(?:import_module|__import__)\(\s*["\']([\w\.]+)["\']', txt):
        out.append(m.group(1))
    return out


def resolve(imp: str, importer_dir: str):
    """把import名解析为本仓文件（绝对路径）；解析不到返回None"""
    parts = imp.split('.')
    base = parts[0] if parts else ''
    if not base or base in _EXTERNAL:
        return None
    cands = []
    if base == 'brahma_brain' and len(parts) >= 2:
        cands = [BRAIN / f'{parts[1]}.py']
    else:
        if importer_dir == 'scripts':
            cands.append(ROOT / 'scripts' / f'{base}.py')
        cands += [BRAIN / f'{base}.py', ROOT / 'scripts' / f'{base}.py', ROOT / f'{base}.py']
    for c in cands:
        if c.exists():
            return c
    return None


def collect_entries() -> set:
    """运行时入口集：cron py + sh py + tests py + 主链4文件（全部绝对路径）"""
    entries = set()
    py_by_name = {}
    for d in (ROOT / 'scripts', ROOT / 'scripts' / 'square', ROOT / 'brahma_brain', ROOT / 'tests'):
        if not d.exists():
            continue
        for p in d.glob('*.py'):
            py_by_name[p.name] = p
    # 1) crontab里的py（含scripts/square/子目录）
    ct = ROOT / 'brahma_crontab.txt'
    if ct.exists():
        for m in re.findall(r'[\w/]+\.py', ct.read_text(errors='ignore')):
            nm = m.split('/')[-1]
            if nm in py_by_name:
                entries.add(py_by_name[nm])
    # 2) 所有sh里的py（watchdog/resurrect/autostart全量）
    for sh in (ROOT / 'scripts').glob('*.sh'):
        for m in re.findall(r'[\w/]+\.py', sh.read_text(errors='ignore')):
            nm = m.split('/')[-1]
            if nm in py_by_name:
                entries.add(py_by_name[nm])
    # 3) tests入口（unittest消费的适配层）
    for p in (ROOT / 'tests').glob('*.py'):
        if p.stem.startswith('test_'):
            entries.add(p)
    # 4) 主链4文件（hook v2.0原口径，始终是入口）
    for e in ('brahma_brain/brahma_analysis_runner.py', 'brahma_brain/brahma_core.py',
              'brahma_brain/brahma_full_report.py', 'scripts/auto_executor.py'):
        if (ROOT / e).exists():
            entries.add(ROOT / e)
    return entries


def reachable_set() -> set:
    """从入口BFS遍历import边，返回可达文件路径集合（绝对路径）"""
    visited = set()
    queue = sorted(collect_entries())
    while queue:
        p = queue.pop(0)
        sp = str(p)
        if sp in visited:
            continue
        visited.add(sp)
        d = 'scripts' if sp.startswith(str(ROOT / 'scripts')) else 'brain'
        for imp in imports_of(p):
            r = resolve(imp, d)
            if r and str(r) not in visited:
                queue.append(r)
    return visited


def run(list_only=False):
    reach = reachable_set()
    all_stems = sorted(p.stem for p in BRAIN.glob('*.py')
                       if not p.stem.startswith('_') and p.stem != '__init__'
                       and '__pycache__' not in str(p) and '_archived' not in str(p))
    reach_stems = {Path(p).stem for p in reach
                   if Path(p).is_absolute() and Path(p).is_relative_to(BRAIN)}
    unreachable = sorted(set(all_stems) - reach_stems)
    orphans = [n for n in unreachable if n not in _EXEMPT]
    exempt_hit = sorted(n for n in unreachable if n in _EXEMPT)
    print(f'[orphan_check] 可达模块={len(reach_stems)} 豁免={len(exempt_hit)} '
          f'(用到: {", ".join(exempt_hit) if exempt_hit else "-"}) 孤岛={len(orphans)}')
    if orphans:
        print(f'[orphan_check] ❌ 孤岛模块未接通: {", ".join(orphans)}')
        print('[orphan_check]    接入主链或加入豁免名单(须写明理由+docstring)')
    else:
        print('[orphan_check] ✅ 0孤岛')
    if not list_only:
        sys.exit(1 if orphans else 0)


if __name__ == '__main__':
    try:
        run(list_only='--list' in sys.argv)
    except SystemExit:
        raise
    except Exception as e:  # 检查器自身崩溃 = fail-closed（与arch_gate口径一致）
        print(f'[orphan_check] ERROR: {e}')
        sys.exit(2)
