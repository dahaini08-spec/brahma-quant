#!/usr/bin/env python3
"""
brahma_module_health_check.py — 梵天模块健康检查
2026-08-29 苏摩111封印

功能：扫描所有模块接入状态，发现静默模块立即推送
每日cron自动运行，确保"建了必须用，升级了必须生效"

用法：
  python3 scripts/brahma_module_health_check.py
  python3 scripts/brahma_module_health_check.py --json
"""
import sys, os, subprocess, json, time, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ══ 模块清单：每个模块的期望接入状态 ══════════════════════════════════════
MODULES = [
    # (模块文件名, 中文名, 期望core引用>0, 期望runner引用>0, 是否关键)
    ('weekly_monthly_anchor',    'HTF周月线锚定',    True,  False, True),
    ('tradfi_dump_detector',     'TradFi抛售检测',   True,  False, True),
    ('market_quadrant',          '市场象限',         True,  False, False),
    ('liq_density_engine',       '清算密度引擎',      False, True,  True),
    ('smc_engine',               'SMC结构引擎',      True,  False, True),
    ('hcme_matcher',             'HCME历史匹配',     True,  False, True),
    ('kronos_bridge',            'Kronos ML',       True,  True,  True),
    ('signal_quality_engine',    '信号质量SQE',      False, True,  True),
    ('anti_manipulation_engine', '操控防御',         False, True,  True),
    ('gex_engine',               'GEX Gamma墙',     True,  False, False),
    ('divergence_engine',        '背离引擎',         True,  False, True),
    ('fangcang_hcme_bridge',     '方仓HCME桥接',     True,  False, False),
    ('brahma_longmem',           '长期记忆',         True,  False, False),
    ('signal_trace',             '信号追踪',         False, True,  False),
    ('price_zone_engine',        '战场预判',         False, False, True,  True),  # 通过full_report接入
]

# ══ 动态维度验证：从最新full_report提取各模块实际输出 ════════════════════
DIMENSION_CHECKS = [
    # (字段路径, 描述, 是否允许0)
    ('score_final',              '最终评分',         False),
    ('grade',                    '结构grade',        False),
    ('regime',                   '体制识别',         False),
    ('htf_score_addon',          'HTF周月线评分贡献', True),  # 允许0（NEUTRAL时）
    ('kronos_score',             'Kronos评分',       True),
    ('hcme_adj',                 'HCME调整分',       True),
    ('_price_zones',             '战场预判区间',      False),
]

def count_references(module_name: str, file_path: str) -> int:
    r = subprocess.run(['grep', '-c', module_name, file_path],
                       capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except:
        return 0

def check_file_exists(module_name: str) -> bool:
    return os.path.exists(os.path.join(ROOT, 'brahma_brain', f'{module_name}.py'))

def run_health_check(verbose: bool = True) -> dict:
    results = {
        'ts': time.strftime('%Y-%m-%d %H:%M CST', time.gmtime(time.time() + 8*3600)),
        'modules': [],
        'critical_issues': [],
        'warnings': [],
        'summary': '',
    }

    core_path   = os.path.join(ROOT, 'brahma_brain', 'brahma_core.py')
    runner_path = os.path.join(ROOT, 'brahma_brain', 'brahma_analysis_runner.py')

    full_report_path = os.path.join(ROOT, 'brahma_brain', 'brahma_full_report.py')

    # ── 模块接入检查 ──────────────────────────────────────────────────────
    for mod, cn, need_core, need_runner, critical, *extra in MODULES:
        allow_full_report = extra[0] if extra else False
        exists   = check_file_exists(mod)
        cnt_core = count_references(mod, core_path) if exists else 0
        cnt_run  = count_references(mod, runner_path) if exists else 0
        cnt_full = count_references(mod, full_report_path) if (exists and allow_full_report) else 0

        if not exists:
            status = 'MISSING'
            issue  = f'❌ 文件缺失: brahma_brain/{mod}.py'
        elif cnt_core == 0 and cnt_run == 0 and cnt_full == 0:
            status = 'DEAD'
            issue  = f'🚨 建了未接入: {mod}'
        elif need_core and cnt_core == 0 and cnt_full == 0:
            status = 'PARTIAL'
            issue  = f'⚠️ 仅runner引用，未进core评分: {mod}'
        elif need_runner and cnt_run == 0 and cnt_full == 0:
            status = 'PARTIAL'
            issue  = f'⚠️ 仅core引用，无runner验证: {mod}'
        else:
            status = 'OK'
            issue  = None

        entry = {
            'module': mod, 'cn': cn, 'status': status,
            'core_refs': cnt_core, 'runner_refs': cnt_run,
            'critical': critical,
        }
        results['modules'].append(entry)

        if issue and critical:
            results['critical_issues'].append(issue)
        elif issue:
            results['warnings'].append(issue)

    # ── 运行时维度验证（静态扫描，不调用run_full_analysis避免超时）────────
    try:
        # 检查关键字段是否在runner的返回结构里有定义
        import ast
        runner_src = open(runner_path).read()
        for field, desc, allow_zero in DIMENSION_CHECKS:
            if field not in runner_src and field not in open(os.path.join(ROOT,'brahma_brain','brahma_core.py')).read():
                results['warnings'].append(f'⚠️ 字段可能缺失: {desc}({field})')
        results['runtime_score'] = 'static_scan'
        results['runtime_regime'] = 'static_scan'
        # HTF共振专项：检查brahma_full_report.py是否读了实时共振
        full_src = open(os.path.join(ROOT,'brahma_brain','brahma_full_report.py')).read()
        if 'get_features' in full_src and 'htf_resonance' in full_src:
            results['htf_resonance'] = 'fixed(get_features)'
        else:
            results['warnings'].append('⚠️ HTF周月线full_report未读实时共振')
            results['htf_resonance'] = 'unknown'
    except Exception as e:
        results['warnings'].append(f'静态扫描失败: {e}')

    # ── 汇总 ─────────────────────────────────────────────────────────────
    n_critical = len(results['critical_issues'])
    n_warn     = len(results['warnings'])
    n_ok       = sum(1 for m in results['modules'] if m['status'] == 'OK')
    n_total    = len(results['modules'])

    results['summary'] = (
        f"梵天模块健康 {results['ts']} | "
        f"✅{n_ok}/{n_total}正常 | "
        f"🚨{n_critical}严重 | ⚠️{n_warn}警告"
    )
    results['status'] = 'CRITICAL' if n_critical > 0 else ('WARNING' if n_warn > 0 else 'HEALTHY')

    if verbose:
        print(f"\n{'='*60}")
        print(f"  梵天模块健康检查 | {results['ts']}")
        print(f"{'='*60}")
        for m in results['modules']:
            icon = '✅' if m['status'] == 'OK' else ('🚨' if m['status'] == 'DEAD' else ('❌' if m['status'] == 'MISSING' else '⚠️'))
            print(f"  {icon} {m['module']:<35} core={m['core_refs']} runner={m['runner_refs']}")
        if results['critical_issues']:
            print(f"\n🚨 严重问题({n_critical}个):")
            for i in results['critical_issues']: print(f"  {i}")
        if results['warnings']:
            print(f"\n⚠️ 警告({n_warn}个):")
            for w in results['warnings']: print(f"  {w}")
        runtime_score = results.get('runtime_score', '?')
        runtime_regime = results.get('runtime_regime', '?')
        print(f"\n运行时验证: score={runtime_score} regime={runtime_regime} HTF共振={results.get('htf_resonance','?')}")
        print(f"\n{results['summary']}")

    return results

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true', help='JSON输出')
    args = parser.parse_args()

    results = run_health_check(verbose=not args.json)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    sys.exit(0 if results['status'] == 'HEALTHY' else 1)
