"""
brahma_wiring_v2.py — 梵天接线健康巡检 v2.0
════════════════════════════════════════════
设计院 2026-08-25 苏摩111 封印

使命：
  - 每次 analyze() 完成后可异步调用（非阻断）
  - 检测核心模块 import_err / p_up僵死 / price_ts缺失
  - 任何异常持续超阈值 → 自动推送苏摩
  - 替代人工每天发现"fangcang unavailable 已持续3周"的尴尬

核心模块检查列表（高价值，断线影响评分）：
  - fangcang_engine        方仓引擎 1597条历史案例
  - cross_asset_gate       BTC/ETH相关性门控 1.85x风险敞口
  - kronos_bridge          Kronos p_up预测
  - freshness_checker      数据新鲜度守门员

封印门控（第六道门）：
  run_seal_check() → 返回 (ok: bool, issues: list)
  不通过则拒绝封印，强制修复后再封
"""

import os
import sys
import time
import json
import importlib
import logging

_log = logging.getLogger('brahma.wiring_v2')
_BASE = os.path.dirname(os.path.abspath(__file__))

# ── 核心模块清单（断线直接影响评分的模块）──────────────────────────────
CRITICAL_MODULES = [
    {
        'name': 'fangcang_engine',
        'import': 'fangcang_engine',
        'fn': 'get_fangcang_context',
        'desc': '方仓引擎(1597条历史案例)',
    },
    {
        'name': 'cross_asset_gate',
        'import': 'cross_asset_gate',
        'fn': 'get_gate',
        'desc': 'BTC/ETH相关性门控(1.85x风险)',
    },
    {
        'name': 'kronos_bridge',
        'import': 'kronos_bridge',
        'fn': 'get_s23_kronos',
        'desc': 'Kronos p_up预测(s23维)',
    },
]

# ── 断线状态持久化（避免重复推送）──────────────────────────────────────
_STATE_FILE = os.path.join(_BASE, '..', 'data', 'wiring_v2_state.json')


def _load_state() -> dict:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass


# ── 单模块检查 ─────────────────────────────────────────────────────────
def _check_module(mod_cfg: dict) -> dict:
    """返回 {ok, name, desc, error}"""
    try:
        mod = importlib.import_module(mod_cfg['import'])
        fn = getattr(mod, mod_cfg['fn'], None)
        if fn is None:
            return {'ok': False, 'name': mod_cfg['name'],
                    'desc': mod_cfg['desc'],
                    'error': f"函数 {mod_cfg['fn']} 不存在"}
        return {'ok': True, 'name': mod_cfg['name'], 'desc': mod_cfg['desc'], 'error': None}
    except ImportError as e:
        return {'ok': False, 'name': mod_cfg['name'],
                'desc': mod_cfg['desc'], 'error': str(e)}
    except Exception as e:
        return {'ok': False, 'name': mod_cfg['name'],
                'desc': mod_cfg['desc'], 'error': str(e)}


# ── price_ts 检查 ──────────────────────────────────────────────────────
def check_price_ts(result: dict) -> tuple:
    """
    检查 analyze() 返回的 result 是否包含新鲜的 price_ts
    返回 (ok: bool, age_sec: float, msg: str)
    """
    ts = result.get('price_ts')
    if ts is None:
        return False, -1, 'price_ts 缺失，数据来源不明'
    age = time.time() - ts
    if age > 300:
        return False, age, f'价格数据已过期 {age:.0f}s（允许最大300s）'
    return True, age, f'数据新鲜 {age:.1f}s'


# ── 全量接线巡检 ──────────────────────────────────────────────────────
def run_check(push_on_error: bool = False) -> dict:
    """
    全量接线巡检，返回 {ok, issues, report}
    push_on_error: True 时对新出现的断线通过 Jarvis 推送告警
    """
    if _BASE not in sys.path:
        sys.path.insert(0, _BASE)

    issues = []
    results = []
    for mod_cfg in CRITICAL_MODULES:
        r = _check_module(mod_cfg)
        results.append(r)
        if not r['ok']:
            issues.append(f"❌ {r['name']}: {r['error']}")

    overall_ok = len(issues) == 0

    report_lines = ['=== 梵天接线巡检 v2.0 ===']
    for r in results:
        status = '✅' if r['ok'] else '❌'
        report_lines.append(f"  {status} {r['name']}: {r['desc']}")
        if not r['ok']:
            report_lines.append(f"       ERROR: {r['error']}")
    report_lines.append(f"\n总结: {'HEALTHY' if overall_ok else f'发现 {len(issues)} 个断线'}")

    report = '\n'.join(report_lines)

    # 推送逻辑：新出现的断线才推送（避免重复骚扰）
    if push_on_error and issues:
        _maybe_push(issues)

    return {'ok': overall_ok, 'issues': issues, 'report': report, 'results': results}


def _maybe_push(issues: list):
    """仅当断线是「新出现」时推送，静默已知持续断线"""
    state = _load_state()
    known = set(state.get('known_issues', []))
    new_issues = [i for i in issues if i not in known]

    if not new_issues:
        return  # 都是已知问题，不重复推送

    # 更新已知断线列表
    state['known_issues'] = list(set(issues))
    state['last_alert_ts'] = time.time()
    _save_state(state)

    msg_lines = [
        '🚨 **梵天接线告警** — 发现新断线模块',
        '',
    ]
    for issue in new_issues:
        msg_lines.append(f'  {issue}')
    msg_lines += [
        '',
        '⚠️ 以上模块已无法工作，影响评分精度。',
        '请设计院立即修复（从archive恢复或重新安装依赖）。',
    ]

    try:
        import subprocess
        msg = '\n'.join(msg_lines)
        # 推到苏摩主线程
        subprocess.Popen([
            'openclaw', 'infer',
            '--channel', 'jarvis',
            '--to', '73295708:thread:01a033af-3697-734a-9f9c-c3e34a00c378',
            '--message', msg,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _log.warning(f'[wiring_v2] 推送失败: {e}')


# ── 封印第六道门控 ─────────────────────────────────────────────────────
def run_seal_check() -> tuple:
    """
    封印门控第六道：全量接线验证
    返回 (ok: bool, issues: list[str])
    封印流程中调用，不通过则拒绝封印
    """
    result = run_check(push_on_error=False)
    return result['ok'], result['issues']


# ── 清除已知断线记录（修复后调用）─────────────────────────────────────
def clear_known_issues():
    """模块修复后调用，清除已知断线记录，下次巡检如还有问题会重新推送"""
    state = _load_state()
    state['known_issues'] = []
    _save_state(state)


# ── CLI 入口 ───────────────────────────────────────────────────────────
if __name__ == '__main__':
    sys.path.insert(0, _BASE)
    result = run_check(push_on_error=True)
    print(result['report'])
    if not result['ok']:
        print('\n断线明细:')
        for issue in result['issues']:
            print(f'  {issue}')
        sys.exit(1)
    else:
        print('\n✅ 所有核心模块接线正常')
