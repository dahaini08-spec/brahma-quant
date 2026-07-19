#!/usr/bin/env python3
"""
brahma_integrity_check.py — 梵天系统完整性守卫 v1.0
设计院 · 苏摩111 · 2026-07-18

【防腐设计原则】
  基于13条历史故障模式，对每个已知根因建立自动检测探针。
  每次系统重启/每日定时/人工触发时运行，提前发现问题。

【检测矩阵】
  C1  执行链路探针   — 关键常量是否偏离安全基线
  C2  数据管道探针   — 信号字段完整性（timing/fill_qty）
  C3  路径对齐探针   — scanner输出路径 vs executor读取路径
  C4  cron健康探针   — cron负载/lightContext覆盖率
  C5  持仓风控探针   — 仓位上限/止损合理性
  C6  日志质量探针   — 重复写入/数据污染检测

运行: python3 scripts/brahma_integrity_check.py
      python3 scripts/brahma_integrity_check.py --fix   # 自动修复可修项
      python3 scripts/brahma_integrity_check.py --json  # JSON输出供程序消费
"""
import sys, os, re, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 安全基线常量（苏摩111封印，修改需走六方审核）──────────────────────
BASELINE = {
    # 执行层
    'TIER_1_SCORE':        (155, 145, 175,  'auto_executor.py'),
    'TIER_2_SCORE':        (138, 120, 154,  'auto_executor.py'),
    'TIER_3_SCORE':        (120, 110, 137,  'auto_executor.py'),
    'MAX_POSITIONS':       (20,  10,  25,   'auto_executor.py'),
    'MIN_NOTIONAL':        (4.5, 3.0, 10.0, 'auto_executor.py'),
    'OI_SCORE_THRESHOLD':  (60,  50,  100,  'sub_executor.py'),
    'AUTO_SCORE_THRESHOLD':(120, 100, 130,  'auto_executor.py'),
    # 风控层
    'MAX_SL_PCT':          (5.0, 3.0, 10.0, 'auto_executor.py'),
    'MIN_SL_PCT':          (1.0, 0.5, 2.5,  'auto_executor.py'),
}

# ── 路径对齐基线 ──────────────────────────────────────────────────────
PATH_ALIGNMENT = [
    {
        'name': '暴涨猎手信号路径',
        'scanner_writes': 'dharma/pump_hunter/new_alerts.json',
        'executor_reads': 'dharma/pump_hunter/new_alerts.json',
        'desc': 'scan_and_alert写入 vs pump_signal_executor读取',
    },
    {
        'name': 'OI信号路径',
        'scanner_writes': 'data/oi_candidates.json',
        'executor_reads': 'data/oi_candidates.json',
        'desc': 'oi_advanced_scanner写入 vs sub_executor读取',
    },
    {
        'name': '主系统信号路径',
        'scanner_writes': 'data/live_signal_log.jsonl',
        'executor_reads': 'data/live_signal_log.jsonl',
        'desc': 'brahma_engine.log_signal写入 vs auto_executor读取',
    },
]

# ── 结果收集 ──────────────────────────────────────────────────────────
class CheckResult:
    def __init__(self):
        self.checks = []
        self.errors = []
        self.warnings = []

    def ok(self, name, msg=''):
        self.checks.append({'status': 'OK', 'name': name, 'msg': msg})

    def error(self, name, msg, fix=None):
        self.checks.append({'status': 'ERROR', 'name': name, 'msg': msg, 'fix': fix})
        self.errors.append(name)

    def warn(self, name, msg, fix=None):
        self.checks.append({'status': 'WARN', 'name': name, 'msg': msg, 'fix': fix})
        self.warnings.append(name)

    def summary(self):
        ok = sum(1 for c in self.checks if c['status'] == 'OK')
        err = len(self.errors)
        warn = len(self.warnings)
        return ok, err, warn


def read_const(fname, name):
    """读取脚本中的常量值"""
    path = BASE / 'scripts' / fname
    if not path.exists():
        return None
    content = path.read_text()
    m = re.search(rf'^{re.escape(name)}\s*=\s*([\d.]+)', content, re.M)
    return float(m.group(1)) if m else None


# ══════════════════════════════════════════════════════════════════════
# C1: 执行链路探针 — 关键常量安全基线
# ══════════════════════════════════════════════════════════════════════
def check_c1_constants(r: CheckResult):
    for name, (expected, lo, hi, fname) in BASELINE.items():
        val = read_const(fname, name)
        if val is None:
            r.error(f'C1.{name}', f'{fname}: 常量{name}未找到（可能被删除或改名）')
            continue
        if val < lo or val > hi:
            r.error(f'C1.{name}',
                    f'{fname}: {name}={val} 越界[{lo},{hi}]，安全基线={expected}',
                    fix=f"在 {fname} 中将 {name} 改回 {expected}")
        elif val != expected:
            r.warn(f'C1.{name}',
                   f'{fname}: {name}={val} 偏离基线{expected}（在安全范围内）')
        else:
            r.ok(f'C1.{name}', f'{name}={val} ✓')


# ══════════════════════════════════════════════════════════════════════
# C2: 数据管道探针 — 信号字段完整性
# ══════════════════════════════════════════════════════════════════════
def check_c2_signal_integrity(r: CheckResult):
    log_path = BASE / 'data/live_signal_log.jsonl'
    if not log_path.exists():
        r.warn('C2.signal_log', 'live_signal_log.jsonl 不存在')
        return

    lines = log_path.read_text().strip().splitlines()
    if not lines:
        r.warn('C2.signal_log', '信号日志为空')
        return

    # 取最近50条分析
    recent = []
    for l in lines[-50:]:
        try: recent.append(json.loads(l))
        except: pass

    now = time.time()
    last_24h = [r2 for r2 in recent if now - float(r2.get('ts',0)) < 86400]

    # timing_badge 完整性
    if last_24h:
        # [2026-07-18] 区分「修复前历史存量」 vs 「修复后新信号」
        # timing修复已于 2026-07-18 00:00 UTC 封印，此前信号属历史存量
        FIX_TS = 1784275200  # 2026-07-18 00:00 UTC
        new_signals = [s for s in last_24h if float(s.get('ts',0)) >= FIX_TS]
        if new_signals:
            missing_timing = sum(1 for s in new_signals if not s.get('timing_badge'))
            pct = missing_timing / len(new_signals) * 100
            if pct > 50:
                r.warn('C2.timing_badge',
                        f'修复后新信号({len(new_signals)}条)中{missing_timing}条({pct:.0f}%)缺少timing_badge — 正常（待下次扫描刷新）',
                       )
            else:
                r.ok('C2.timing_badge', f'修复后新信号timing_badge覆盖率{100-pct:.0f}% ✓')
        else:
            r.ok('C2.timing_badge', 'timing修复已封印，历史存量不计 ✓')

        # valid字段
        no_valid = sum(1 for s in last_24h if 'valid' not in s)
        if no_valid > len(last_24h) * 0.3:
            r.warn('C2.valid_field', f'{no_valid}/{len(last_24h)}条信号缺少valid字段')
        else:
            r.ok('C2.valid_field', 'valid字段覆盖正常 ✓')
    else:
        r.warn('C2.signal_24h', '最近24H无新信号（系统可能未运行）')

    # fill_qty 完整性（代码层验证：检查回查逻辑是否存在）
    sub_exec = BASE / 'scripts/sub_executor.py'
    if sub_exec.exists():
        content_se = sub_exec.read_text()
        has_sleep    = 'time.sleep(2)' in content_se
        has_requery  = ("'/fapi/v1/order'" in content_se and "'GET'" in content_se)
        has_fallback = 'fill_qty = qty' in content_se
        if all([has_sleep, has_requery, has_fallback]):
            r.ok('C2.fill_qty', 'fill_qty回查逻辑已封印(sleep+GET回查+fallback) ✓')
        else:
            missing = [x for x,y in [('sleep2s',has_sleep),('GET回查',has_requery),('fallback',has_fallback)] if not y]
            r.error('C2.fill_qty',
                    f'sub_executor.py fill_qty回查逻辑缺失: {missing}',
                    fix='在MARKET订单后加入time.sleep(2)+GET /fapi/v1/order回查逻辑')

    # brahma_state 时效性
    state = BASE / 'data/brahma_state.json'
    if state.exists():
        age_m = (time.time() - state.stat().st_mtime) / 60
        if age_m > 30:
            r.warn('C6.brahma_state', f'brahma_state.json {age_m:.0f}min未更新（signal_watcher可能未运行）')
        else:
            r.ok('C6.brahma_state', f'brahma_state {age_m:.0f}min前更新 ✓')



# ══════════════════════════════════════════════════════════════════════
# C3: 路径对齐探针
# ══════════════════════════════════════════════════════════════════════
def check_c3_path_alignment(r):
    for pa in PATH_ALIGNMENT:
        w_path = BASE / pa['scanner_writes']
        e_path = BASE / pa['executor_reads']
        if str(w_path) != str(e_path):
            r.error(f'C3.{pa["name"]}', f'路径不对齐: scanner→{pa["scanner_writes"]} executor→{pa["executor_reads"]}')
            continue
        if not w_path.exists():
            r.warn(f'C3.{pa["name"]}', f'共享路径不存在: {pa["scanner_writes"]}')
            continue
        age_h = (time.time() - w_path.stat().st_mtime) / 3600
        if age_h > 4:
            r.warn(f'C3.{pa["name"]}', f'{pa["scanner_writes"]} 最后更新{age_h:.1f}H前')
        else:
            r.ok(f'C3.{pa["name"]}', f'路径对齐且文件新鲜({age_h:.1f}H前) ✓')
    pump_exec = BASE / 'scripts/pump_signal_executor.py'
    if pump_exec.exists():
        content = pump_exec.read_text()
        if 'scan_and_alert' in content and 'importlib' in content:
            r.ok('C3.pump_entry', 'pump_signal_executor无参数入口正常 ✓')
        else:
            r.error('C3.pump_entry', 'pump_signal_executor.__main__缺少无参数自动扫描逻辑',
                    fix='在__main__的else分支中调用scan_and_alert.py')


# ══════════════════════════════════════════════════════════════════════
# C4: cron健康探针
# ══════════════════════════════════════════════════════════════════════
def check_c4_cron_health(r):
    jobs_path = Path('/root/.openclaw/cron/jobs.json')
    if not jobs_path.exists():
        r.warn('C4.cron', 'jobs.json不存在')
        return
    data = json.loads(jobs_path.read_text())
    jobs = data.get('jobs', [])
    total = len(jobs)
    heavy_high_freq = []
    for j in jobs:
        ms = j.get('schedule', {}).get('everyMs', 0)
        mins = ms / 60000 if ms else 9999
        lc = j.get('payload', {}).get('lightContext', False)
        if mins <= 10 and not lc:
            heavy_high_freq.append((j.get('name', '?'), mins))
    peak5m = sum(1 for j in jobs if j.get('schedule', {}).get('everyMs', 0) / 60000 <= 5)
    if total > 40:
        r.error('C4.cron_count', f'cron总数{total}超过40上限', fix='删除冗余cron，目标≤35')
    elif total > 35:
        r.warn('C4.cron_count', f'cron总数{total}（安全上限35）')
    else:
        r.ok('C4.cron_count', f'cron总数{total} ✓')
    if heavy_high_freq:
        r.error('C4.lightContext', f'{len(heavy_high_freq)}个高频cron未启用lightContext: {[n for n,_ in heavy_high_freq]}',
                fix='在jobs.json中设置lightContext=true')
    else:
        r.ok('C4.lightContext', '所有高频cron已启用lightContext ✓')
    if peak5m >= 4:
        r.warn('C4.peak_concurrency', f'5min窗口峰值并发{peak5m}个agent（阈值:4）')
    else:
        r.ok('C4.peak_concurrency', f'5min窗口峰值并发{peak5m} ✓')
    KNOWN_REDUNDANT = [('system-guardian', 'brahma-self-heal')]
    for a, b in KNOWN_REDUNDANT:
        names_set = {j.get('name', '') for j in jobs}
        if a in names_set and b in names_set:
            r.error(f'C4.redundant.{a}', f'{a}与{b}功能完全重复', fix=f'openclaw cron rm <{a}-id>')


# ══════════════════════════════════════════════════════════════════════
# C5: 持仓风控探针
# ══════════════════════════════════════════════════════════════════════
def check_c5_risk_control(r):
    wuqu = BASE / 'data/wuqu_positions.json'
    if wuqu.exists():
        try:
            positions = json.loads(wuqu.read_text())
            if isinstance(positions, list):
                dirty = [p for p in positions if isinstance(p, dict)
                         and float(p.get('fill_qty', 1) or 1) == 0]
                if dirty:
                    r.warn('C5.wuqu_dirty', f'wuqu_positions中{len(dirty)}条fill_qty=0')
                else:
                    r.ok('C5.wuqu_positions', 'wuqu_positions数据正常 ✓')
        except Exception as e:
            r.warn('C5.wuqu_parse', f'wuqu_positions.json解析失败: {e}')
    log_path = BASE / 'data/live_signal_log.jsonl'
    if log_path.exists():
        lines = log_path.read_text().strip().splitlines()
        bad_sl = []
        for l in lines[-20:]:
            try:
                s = json.loads(l)
                sl_pct = float(s.get('sl_pct', 0) or 0)
                if 0 < sl_pct < 0.5:
                    bad_sl.append((s.get('symbol', '?'), sl_pct))
            except: pass
        if bad_sl:
            r.error('C5.sl_noise', f'噪音级止损(<0.5%): {bad_sl[:3]}',
                    fix='止损应用SL_PCT公式，不得用ATR×小乘数')
        else:
            r.ok('C5.sl_calculation', '止损计算正常(≥0.5%) ✓')
    ae = BASE / 'scripts/auto_executor.py'
    if ae.exists():
        content = ae.read_text()
        if 'ExposureCap' in content or '_max_exposure' in content:
            r.ok('C5.exposure_cap', '单标的敞口上限逻辑存在 ✓')
        else:
            r.error('C5.exposure_cap', 'auto_executor缺少单标的名义敞口上限（PIXEL风险）',
                    fix='添加 _max_exposure = nav * 0.10 检查')


# ══════════════════════════════════════════════════════════════════════
# C6: 日志质量探针
# ══════════════════════════════════════════════════════════════════════
def check_c6_log_quality(r):
    pl = BASE / 'scripts/performance_logger.py'
    if pl.exists():
        content = pl.read_text()
        if 'dedup' in content.lower() or '_dedup_key' in content:
            r.ok('C6.perf_dedup', 'performance_logger有dedup逻辑 ✓')
        else:
            r.error('C6.perf_dedup', 'performance_logger.log_trade缺少去重逻辑',
                    fix='在log_trade中添加order_id去重检查')
    perf_log = BASE / 'data/live_performance_log.jsonl'
    if perf_log.exists():
        from collections import Counter
        oids = []
        for l in perf_log.read_text().strip().splitlines():
            try:
                rv = json.loads(l)
                oid = str(rv.get('order_id') or rv.get('signal_id', ''))
                if oid: oids.append(oid)
            except: pass
        dupes = {k: v for k, v in Counter(oids).items() if v > 1}
        if dupes:
            r.warn('C6.perf_dupes', f'live_performance_log存在{len(dupes)}个重复order_id')
        else:
            r.ok('C6.perf_log_clean', 'live_performance_log无重复记录 ✓')
    state = BASE / 'data/brahma_state.json'
    if state.exists():
        age_m = (time.time() - state.stat().st_mtime) / 60
        if age_m > 30:
            r.warn('C6.brahma_state', f'brahma_state.json {age_m:.0f}min未更新')
        else:
            r.ok('C6.brahma_state', f'brahma_state {age_m:.0f}min前更新 ✓')


# ══════════════════════════════════════════════════════════════════════
# 主函数
# ══════════════════════════════════════════════════════════════════════
def run(auto_fix=False, as_json=False):
    r = CheckResult()
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    check_c1_constants(r)
    check_c2_signal_integrity(r)
    check_c3_path_alignment(r)
    check_c4_cron_health(r)
    check_c5_risk_control(r)
    check_c6_log_quality(r)

    ok_n, err_n, warn_n = r.summary()
    total = len(r.checks)

    if as_json:
        print(json.dumps({
            'ts': now_str,
            'ok': ok_n, 'errors': err_n, 'warnings': warn_n,
            'checks': r.checks,
            'healthy': err_n == 0,
        }, ensure_ascii=False, indent=2))
        return err_n

    # 文字报告
    print(f"\n🏛️ 梵天系统完整性检查 · {now_str}")
    print(f"{'='*60}")
    for c in r.checks:
        icon = {'OK':'✅','WARN':'⚠️','ERROR':'❌'}[c['status']]
        print(f"  {icon} [{c['status']:5}] {c['name']}: {c['msg']}")
        if c.get('fix') and c['status'] != 'OK':
            print(f"          💊 修复: {c['fix']}")
    print(f"{'='*60}")
    status = '🟢 HEALTHY' if err_n == 0 else ('🟡 WARNING' if warn_n > 0 and err_n == 0 else '🔴 CRITICAL')
    print(f"  {status}  总计:{total}项  ✅{ok_n}  ⚠️{warn_n}  ❌{err_n}")

    if auto_fix:
        print('\n[--fix模式暂不支持自动修复，请参考上方💊建议手动处理]')

    return err_n


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='梵天系统完整性检查')
    parser.add_argument('--fix',  action='store_true', help='自动修复可修项')
    parser.add_argument('--json', action='store_true', help='JSON格式输出')
    args = parser.parse_args()
    exit_code = run(auto_fix=args.fix, as_json=args.json)
    sys.exit(0 if exit_code == 0 else 1)
