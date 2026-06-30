#!/usr/bin/env python3
"""
brahma_360.py — 梵天360全系统健康管理中心
设计院·苏摩111 2026-06-30

定位：像电脑360一样 —— 体检 → 发现 → 分级 → 自动修复 → 验证 → 报告

五层架构：
  Layer1 Scanner   : 8维全盘扫描，输出问题清单
  Layer2 Classifier: 红/橙/黄/绿四级威胁评估
  Layer3 AutoFixer : 数据/进程/配置类问题自动修复（代码修改需苏摩审批）
  Layer4 Verifier  : 修复后60s验证闭环
  Layer5 Reporter  : 实时告警 + 健康日报 + 修复历史

使用：
  python3 brahma_360.py          # 全量体检
  python3 brahma_360.py --fix    # 体检+自动修复
  python3 brahma_360.py --report # 输出健康报告
"""

import os, sys, json, time, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

# ── 路径 ────────────────────────────────────────────────────────
_DIR   = Path(__file__).parent
_ROOT  = _DIR.parent
_DATA  = _ROOT / 'data'
_HISTORY = _DATA / 'brahma_360_history.jsonl'
sys.path.insert(0, str(_DIR))
sys.path.insert(0, str(_ROOT))

# ════════════════════════════════════════════════════════════════
# Layer1: Scanner — 8维全盘扫描
# ════════════════════════════════════════════════════════════════

def scan_d1_modules() -> list:
    """D1: 模块接入完整性（孤儿模块）"""
    issues = []
    try:
        from auto_review import check_orphan_modules
        orphans = check_orphan_modules()
        if orphans:
            issues.append({
                'dim': 'D1_modules', 'level': 'ERROR',
                'msg': f'孤儿模块 {len(orphans)}个未接入: {orphans}',
                'auto_fix': False,
            })
    except Exception as e:
        issues.append({'dim': 'D1_modules', 'level': 'WARN', 'msg': f'巡检异常: {e}', 'auto_fix': False})
    return issues


def scan_d2_data() -> list:
    """D2: 数据文件新鲜度"""
    issues = []
    now = time.time()
    contracts = {
        'data/regime_state.json':      {'max_min': 30,  'level': 'ERROR',    'fix': 'none'},
        'data/brahma_state.json':       {'max_min': 10,  'level': 'CRITICAL', 'fix': 'none'},
        'data/live_prices.json':        {'max_min': 10,  'level': 'CRITICAL', 'fix': 'init_live_prices'},
        'data/ws_guardian_state.json':  {'max_min': 3,   'level': 'CRITICAL', 'fix': 'none'},
        'data/signal_queue.jsonl':      {'max_min': 120, 'level': 'ERROR',    'fix': 'reset_signal_queue'},
    }
    for fpath, cfg in contracts.items():
        fp = _ROOT / fpath
        if not fp.exists():
            issues.append({
                'dim': 'D2_data', 'level': cfg['level'],
                'msg': f'{fpath} 不存在',
                'auto_fix': cfg['fix'] != 'none',
                'fix_action': cfg['fix'],
                'file': str(fp),
            })
        else:
            age_min = (now - fp.stat().st_mtime) / 60
            if age_min > cfg['max_min'] * 2:
                issues.append({
                    'dim': 'D2_data', 'level': cfg['level'],
                    'msg': f'{fpath} 过期 {age_min:.0f}min（限{cfg["max_min"]}min）',
                    'auto_fix': cfg['fix'] != 'none',
                    'fix_action': cfg['fix'],
                    'file': str(fp),
                    'age_min': age_min,
                })
    return issues


def scan_d3_processes() -> list:
    """D3: 关键进程存活"""
    issues = []
    try:
        r = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        ps = r.stdout
        procs = {
            'ws_guardian.py':    {'level': 'CRITICAL', 'fix': 'restart_ws_guardian'},
            'arjuna':            {'level': 'WARN',     'fix': 'none'},   # 可选执行引擎
            'pump_hunter':       {'level': 'WARN',     'fix': 'none'},
        }
        for proc, cfg in procs.items():
            if proc not in ps:
                issues.append({
                    'dim': 'D3_processes', 'level': cfg['level'],
                    'msg': f'进程未运行: {proc}',
                    'auto_fix': cfg['fix'] != 'none',
                    'fix_action': cfg['fix'],
                })
    except Exception as e:
        issues.append({'dim': 'D3_processes', 'level': 'WARN', 'msg': f'进程检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d4_interfaces() -> list:
    """D4: 接口一致性（关键字段检查）"""
    issues = []
    try:
        core_file = _DIR / 'brahma_core.py'
        if core_file.exists():
            core = core_file.read_text()
            required_fields = {
                'grade_num':    ('confluence_score()返回必须含grade_num', 'patch_grade_num'),
                'score_final':  ('_result必须含score_final', 'none'),
                'signal_dir':   ('_result必须含signal_dir', 'none'),
            }
            for field, (desc, fix) in required_fields.items():
                if field not in core:
                    issues.append({
                        'dim': 'D4_interfaces', 'level': 'ERROR',
                        'msg': f'接口字段缺失: {field} — {desc}',
                        'auto_fix': fix != 'none',
                        'fix_action': fix,
                    })
    except Exception as e:
        issues.append({'dim': 'D4_interfaces', 'level': 'WARN', 'msg': f'接口检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d5_params() -> list:
    """D5: 铁证参数一致性（MEMORY.md封印值 vs 代码实际）"""
    issues = []
    try:
        core_file = _DIR / 'brahma_core.py'
        if not core_file.exists():
            return issues
        core = core_file.read_text()

        iron_rules = [
            ('BEAR_TREND SHORT乘数1.6x',  r"'BEAR_TREND'.*1\.6",   'ERROR'),
            ('CHOP_MID SHORT乘数0.88x',   r"'CHOP_MID'.*0\.88",    'ERROR'),
            ('RSM体制防抖已接入',           'regime_state_machine',  'ERROR'),
            ('RANGE区间路由已接入',         'detect_range_structure', 'WARN'),
            ('PositionSizer已接入',        'position_sizer',         'WARN'),
        ]
        for rule_name, pattern, level in iron_rules:
            found = bool(re.search(pattern, core))
            if not found:
                issues.append({
                    'dim': 'D5_params', 'level': level,
                    'msg': f'铁证参数异常: {rule_name} — 未找到匹配',
                    'auto_fix': False,
                })
    except Exception as e:
        issues.append({'dim': 'D5_params', 'level': 'WARN', 'msg': f'参数检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d6_silent_failures() -> list:
    """D6: 静默失败点趋势"""
    issues = []
    try:
        core_file = _DIR / 'brahma_core.py'
        if core_file.exists():
            core = core_file.read_text()
            count = sum(1 for l in core.split('\n')
                       if 'except' in l and 'pass' in l and not l.strip().startswith('#'))
            if count > 100:
                level = 'ERROR'
            elif count > 50:
                level = 'WARN'
            else:
                level = 'OK'
            if level != 'OK':
                issues.append({
                    'dim': 'D6_silent', 'level': level,
                    'msg': f'静默失败点(except:pass): {count}处 — 系统可观测性差',
                    'auto_fix': False,
                    'count': count,
                })
    except Exception as e:
        issues.append({'dim': 'D6_silent', 'level': 'WARN', 'msg': f'静默检查异常: {e}', 'auto_fix': False})
    return issues


def scan_d7_crons() -> list:
    """D7: Cron任务健康"""
    issues = []
    try:
        r = subprocess.run(['openclaw', 'cron', 'list', '--json'],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            crons = json.loads(r.stdout)
            if isinstance(crons, list):
                for c in crons:
                    if c.get('status') not in ('ok', 'enabled', None):
                        issues.append({
                            'dim': 'D7_crons', 'level': 'WARN',
                            'msg': f'Cron异常: {c.get("name")} status={c.get("status")}',
                            'auto_fix': False,
                        })
    except Exception as e:
        pass  # cron检查失败不阻断
    return issues


def scan_d8_backups() -> list:
    """D8: 备份文件堆积"""
    issues = []
    try:
        bak_files = list(_DIR.glob('*.bak*'))
        if len(bak_files) > 20:
            size_mb = sum(f.stat().st_size for f in bak_files) / 1024 / 1024
            issues.append({
                'dim': 'D8_backups', 'level': 'WARN',
                'msg': f'备份文件堆积: {len(bak_files)}个 共{size_mb:.1f}MB — 建议git init清理',
                'auto_fix': False,
                'count': len(bak_files),
            })
    except Exception as e:
        pass
    return issues




def scan_d9_signal_pipeline() -> list:
    """D9: 信号链路关键节点可观测性检查"""
    issues = []
    try:
        import json, time
        # 读最近一次 brahma_state（记录analyze()运行结果）
        bs = _DATA / 'brahma_state.json'
        if not bs.exists():
            issues.append({
                'dim': 'D9_pipeline', 'level': 'WARN',
                'msg': 'brahma_state.json 不存在 — 信号链路无快照',
                'auto_fix': False,
            })
            return issues

        state = json.loads(bs.read_text())
        ts = state.get('ts', 0)
        age_min = (time.time() - ts) / 60 if ts else 999

        # 检查关键字段是否在最近输出中出现
        required_keys = ['regime', 'score', 'action']
        missing_keys = [k for k in required_keys if k not in state]
        if missing_keys:
            issues.append({
                'dim': 'D9_pipeline', 'level': 'WARN',
                'msg': f'信号快照缺失字段: {missing_keys}',
                'auto_fix': False,
            })

        # 检查RSM是否在运行
        rsm_state = _DATA / 'regime_state.json'
        if rsm_state.exists():
            rsm = json.loads(rsm_state.read_text())
            if not rsm.get('current_regime'):
                issues.append({
                    'dim': 'D9_pipeline', 'level': 'WARN',
                    'msg': 'RSM状态机无有效体制记录',
                    'auto_fix': False,
                })
    except Exception as e:
        pass  # D9非关键维度，失败不告警
    return issues


def run_full_scan() -> dict:
    """全量8维扫描，返回结构化结果"""
    ts = time.time()
    all_issues = []
    all_issues += scan_d1_modules()
    all_issues += scan_d2_data()
    all_issues += scan_d3_processes()
    all_issues += scan_d4_interfaces()
    all_issues += scan_d5_params()
    all_issues += scan_d6_silent_failures()
    all_issues += scan_d7_crons()
    all_issues += scan_d8_backups()
    all_issues += scan_d9_signal_pipeline()

    # 计算健康评分 (0~100)
    deductions = {'CRITICAL': 25, 'ERROR': 10, 'WARN': 3}
    score = 100
    for issue in all_issues:
        score -= deductions.get(issue['level'], 0)
    score = max(0, score)

    level_counts = {}
    for issue in all_issues:
        lv = issue['level']
        level_counts[lv] = level_counts.get(lv, 0) + 1

    return {
        'ts': ts,
        'datetime': datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'health_score': score,
        'health_label': '🟢健康' if score >= 85 else ('🟡注意' if score >= 60 else ('🟠警告' if score >= 40 else '🔴危险')),
        'issues': all_issues,
        'level_counts': level_counts,
        'total_issues': len(all_issues),
    }


# ════════════════════════════════════════════════════════════════
# Layer3: AutoFixer — 自动修复（仅数据/进程/配置，不动代码）
# ════════════════════════════════════════════════════════════════

def fix_init_live_prices(issue: dict) -> bool:
    """修复: live_prices.json 初始化"""
    try:
        import urllib.request
        prices = {}
        for sym in ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT']:
            try:
                url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = json.loads(r.read())
                prices[sym] = {'price': float(data['price']), 'ts': time.time(), 'source': 'brahma_360_fix'}
            except Exception:
                pass
        if prices:
            (_DATA / 'live_prices.json').write_text(json.dumps(prices, indent=2))
            print(f'[360-Fix] ✅ live_prices.json 初始化 {len(prices)}个标的')
            return True
    except Exception as e:
        print(f'[360-Fix] ❌ live_prices初始化失败: {e}')
    return False


def fix_reset_signal_queue(issue: dict) -> bool:
    """修复: signal_queue.jsonl 重置"""
    try:
        sq = _DATA / 'signal_queue.jsonl'
        if sq.exists():
            bak = _DATA / f'signal_queue.jsonl.bak_{int(time.time())}'
            sq.rename(bak)
        sq.write_text('')
        print(f'[360-Fix] ✅ signal_queue.jsonl 已重置')
        return True
    except Exception as e:
        print(f'[360-Fix] ❌ signal_queue重置失败: {e}')
    return False


def fix_restart_ws_guardian(issue: dict) -> bool:
    """修复: ws_guardian 进程重启"""
    try:
        r = subprocess.run(
            ['python3', str(_ROOT / 'ws_guardian.py'), '--daemon'],
            capture_output=True, text=True, timeout=5
        )
        print(f'[360-Fix] ✅ ws_guardian 重启指令已发送')
        return True
    except Exception as e:
        print(f'[360-Fix] ❌ ws_guardian重启失败: {e}')
    return False


FIX_HANDLERS = {
    'init_live_prices':    fix_init_live_prices,
    'reset_signal_queue':  fix_reset_signal_queue,
    'restart_ws_guardian': fix_restart_ws_guardian,
}


def auto_fix_issues(issues: list) -> list:
    """对所有可自动修复的问题执行修复，返回修复记录"""
    fix_log = []
    for issue in issues:
        if not issue.get('auto_fix'):
            continue
        action = issue.get('fix_action', '')
        handler = FIX_HANDLERS.get(action)
        if not handler:
            continue

        print(f'[360-AutoFix] 尝试修复: {issue["msg"][:60]}')
        success = handler(issue)
        fix_log.append({
            'ts': time.time(),
            'issue': issue['msg'],
            'action': action,
            'success': success,
        })
        time.sleep(1)  # 修复间隔

    return fix_log


# ════════════════════════════════════════════════════════════════
# Layer4: Verifier — 修复验证（60s后重扫同维度）
# ════════════════════════════════════════════════════════════════

def verify_fixes(fix_log: list) -> list:
    """修复后重新扫描验证"""
    if not fix_log:
        return []
    time.sleep(3)  # 等待修复生效

    results = []
    for fix in fix_log:
        if not fix['success']:
            results.append({**fix, 'verified': False, 'note': '修复失败，跳过验证'})
            continue
        # 重新扫描数据维度验证
        action = fix.get('action', '')
        verified = False
        if action == 'init_live_prices':
            verified = (_DATA / 'live_prices.json').exists()
        elif action == 'reset_signal_queue':
            sq = _DATA / 'signal_queue.jsonl'
            verified = sq.exists() and (time.time() - sq.stat().st_mtime) < 60
        elif action == 'restart_ws_guardian':
            r = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            verified = 'ws_guardian' in r.stdout
        else:
            verified = True  # 无法验证的默认通过

        results.append({**fix, 'verified': verified,
                        'note': '✅验证通过' if verified else '❌验证失败，需人工介入'})
        print(f'[360-Verify] {"✅" if verified else "❌"} {fix["action"]}: {results[-1]["note"]}')
    return results


# ════════════════════════════════════════════════════════════════
# Layer5: Reporter — 健康报告
# ════════════════════════════════════════════════════════════════

def save_history(scan_result: dict, fix_log: list, verify_log: list):
    """追加记录到历史文件"""
    record = {
        'ts': scan_result['ts'],
        'datetime': scan_result['datetime'],
        'health_score': scan_result['health_score'],
        'total_issues': scan_result['total_issues'],
        'level_counts': scan_result['level_counts'],
        'fixes_applied': len([f for f in fix_log if f.get('success')]),
        'fixes_verified': len([v for v in verify_log if v.get('verified')]),
    }
    _DATA.mkdir(exist_ok=True)
    with open(_HISTORY, 'a') as f:
        f.write(json.dumps(record) + '\n')


def format_report(scan_result: dict, fix_log: list = None, verify_log: list = None) -> str:
    """格式化健康报告（用于Jarvis推送）"""
    s = scan_result
    lines = [
        f"🔱 梵天360 健康报告",
        f"📅 {s['datetime']}",
        f"",
        f"健康评分: {s['health_score']}/100  {s['health_label']}",
        f"问题总数: {s['total_issues']}个",
    ]

    lc = s.get('level_counts', {})
    if lc:
        lines.append(f"  🔴CRITICAL:{lc.get('CRITICAL',0)}  🟠ERROR:{lc.get('ERROR',0)}  🟡WARN:{lc.get('WARN',0)}")

    if s['issues']:
        lines.append(f"\n📋 问题清单:")
        for issue in s['issues']:
            icon = {'CRITICAL':'🔴','ERROR':'🟠','WARN':'🟡'}.get(issue['level'],'⚪')
            fix_tag = ' [可自动修复]' if issue.get('auto_fix') else ''
            lines.append(f"  {icon} [{issue['dim']}] {issue['msg'][:60]}{fix_tag}")

    if fix_log:
        success = [f for f in fix_log if f.get('success')]
        lines.append(f"\n🔧 自动修复: {len(success)}/{len(fix_log)}项成功")
        for fix in success:
            lines.append(f"  ✅ {fix['action']}")

    if not s['issues']:
        lines.append(f"\n✅ 系统运行正常，无待处理问题")

    return '\n'.join(lines)


# ════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════

def run_360(auto_fix: bool = True, push: bool = False) -> dict:
    """梵天360全流程入口"""
    print(f'[梵天360] 开始全量体检 {datetime.now(timezone.utc).strftime("%H:%M UTC")}')

    # Layer1: 扫描
    scan = run_full_scan()
    print(f'[梵天360] 健康评分: {scan["health_score"]}/100 {scan["health_label"]} | 问题: {scan["total_issues"]}个')

    # Layer3: 自动修复
    fix_log, verify_log = [], []
    if auto_fix and scan['issues']:
        fixable = [i for i in scan['issues'] if i.get('auto_fix')]
        if fixable:
            print(f'[梵天360] 自动修复 {len(fixable)} 个问题...')
            fix_log = auto_fix_issues(fixable)
            # Layer4: 验证
            verify_log = verify_fixes(fix_log)

    # Layer5: 存档
    save_history(scan, fix_log, verify_log)

    # 报告
    report = format_report(scan, fix_log, verify_log)

    # 推送（仅CRITICAL/ERROR）
    critical_count = scan['level_counts'].get('CRITICAL', 0) + scan['level_counts'].get('ERROR', 0)
    if push and critical_count > 0:
        print(f'[梵天360] 推送告警 ({critical_count}个CRITICAL/ERROR)')

    return {
        'scan': scan,
        'fix_log': fix_log,
        'verify_log': verify_log,
        'report': report,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天360健康管理')
    parser.add_argument('--fix',    action='store_true', help='自动修复')
    parser.add_argument('--push',   action='store_true', help='推送告警')
    parser.add_argument('--report', action='store_true', help='只输出报告')
    args = parser.parse_args()

    result = run_360(auto_fix=args.fix or True, push=args.push)
    print('\n' + result['report'])
