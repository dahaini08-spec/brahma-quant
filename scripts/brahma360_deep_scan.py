#!/usr/bin/env python3
"""
brahma360_deep_scan.py — 梵天系统全资产深度扫描 v1.0
设计院 × 量化工程师360 · 2026-06-19
"""
import sys, os, json, time, subprocess, re, math
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

BASE    = Path(__file__).parent.parent
DATA    = BASE / 'data'
SCRIPTS = BASE / 'scripts'
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(SCRIPTS))

issues = []

def P0(code, msg):
    issues.append(('P0', code, msg))
    print(f'  ❌ P0 [{code}] {msg}')

def P1(code, msg):
    issues.append(('P1', code, msg))
    print(f'  ⚠️ P1 [{code}] {msg}')

def OK(msg):
    print(f'  ✅ {msg}')

def section(title):
    print(f'\n{"━"*54}')
    print(f'  {title}')
    print(f'{"━"*54}')

# ── A. 核心模块语法 ───────────────────────────────────────────
section('A. 核心模块语法 & 可导入性')
CORE = [
    'brahma_brain/brahma_core.py',
    'brahma_brain/market_state.py',
    'scripts/auto_execute_gate.py',
    'scripts/hunter_executor.py',
    'scripts/signal_watcher.py',
    'scripts/live_signal_settler.py',
    'scripts/position_sl_monitor.py',
    'scripts/brahma_scan_all.py',
    'scripts/market_screener.py',
    'scripts/regime_switch_monitor.py',
    'scripts/push_hub.py',
    'ws_guardian.py',
    'executor.py',
]
for m in CORE:
    r = subprocess.run(['python3', '-m', 'py_compile', str(BASE / m)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        P0('SYNTAX', f'{m}: {r.stderr.strip()[:100]}')
    else:
        OK(f'syntax OK: {m}')

# ── B. 关键数据文件 ──────────────────────────────────────────
section('B. 关键数据文件完整性')
FILES = {
    'data/brahma_state.json':        ['positions', 'wuqu_positions', 'nav_usdt'],
    'data/live_signal_log.jsonl':    [],
    'data/wuqu_paper_settled.jsonl': [],
    'data/signal_watcher_state.json': ['notified', 'warned'],
    'data/auto_execute_log.jsonl':   [],
    'data/circuit_breaker.json':     [],
    'data/soma_ai_registry.json':    [],
}
for fpath, keys in FILES.items():
    p = BASE / fpath
    if not p.exists():
        P0('FILE_MISSING', fpath)
        continue
    size = p.stat().st_size
    if fpath.endswith('.json'):
        try:
            d = json.loads(p.read_text())
            missing = [k for k in keys if k not in d]
            if missing:
                P1('SCHEMA', f'{fpath} 缺字段: {missing}')
            else:
                OK(f'{fpath} {size}B schema✓')
        except Exception as e:
            P0('JSON_CORRUPT', f'{fpath}: {e}')
    else:
        lines = 0; bad = 0
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    json.loads(line)
                except Exception:
                    bad += 1
        if bad > 0:
            P1('JSONL_CORRUPT', f'{fpath}: {bad}/{lines}行损坏')
        else:
            OK(f'{fpath} lines={lines} corrupt=0')

# ── C. 进程健康 ──────────────────────────────────────────────
section('C. 进程健康')
procs = {
    'ws_guardian': ('P0', 'stop-loss guardian — 止损守护'),
    'brahma_state_refresh': ('P1', 'state刷新 (可选)'),
}
for proc, (level, desc) in procs.items():
    r = subprocess.run(['pgrep', '-f', proc], capture_output=True, text=True)
    pids = r.stdout.strip()
    if pids:
        OK(f'{proc} PID={pids.split()[0]} ({desc})')
    else:
        if level == 'P0':
            P0('PROC_DOWN', f'{proc} 未运行 — {desc}')
        else:
            P1('PROC_DOWN', f'{proc} 未运行 — {desc}')

# ── D. Cron任务 ──────────────────────────────────────────────
section('D. Cron任务健康')
r = subprocess.run(['openclaw', 'cron', 'list'], capture_output=True, text=True)
cron_out = r.stdout
EXPECTED = [
    'signal-watcher-1h',
    'live-signal-settle-2h',
    'brahma-scan-8h',
    'position-sl-monitor',
    'price-entry-monitor',
    'gateway-restart-daily',
]
for name in EXPECTED:
    if name in cron_out:
        for line in cron_out.split('\n'):
            if name in line:
                ok_status = 'ok' in line
                idle_status = 'idle' in line
                status = 'ok' if ok_status else ('idle' if idle_status else 'unknown')
                OK(f'cron/{name} status={status}')
                break
    else:
        P1('CRON_MISSING', f'cron/{name} 不存在')

# ── E. 信号链路全资产 ────────────────────────────────────────
section('E. 信号链路 — 全资产分析')
sigs = []
with open(DATA / 'live_signal_log.jsonl') as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                sigs.append(json.loads(line))
            except Exception:
                pass

now = time.time()
cutoff = now - 86400
today = [s for s in sigs if float(s.get('ts', 0)) >= cutoff]
sym_cnt    = Counter(s.get('symbol') for s in today)
dir_cnt    = Counter(s.get('direction') for s in today)
regime_cnt = Counter(s.get('regime') for s in today)
status_cnt = Counter(s.get('status') for s in today)

print(f'  今日信号: {len(today)}条 | 标的: {dict(sym_cnt)}')
print(f'  方向: {dict(dir_cnt)} | 体制: {dict(regime_cnt)}')
print(f'  状态: {dict(status_cnt)}')

if today:
    best = max(today, key=lambda x: float(x.get('score', 0)))
    age_min = (now - float(best.get('ts', 0))) / 60
    print(f'  最高分: {best["symbol"]} {best.get("direction")} score={best["score"]} age={age_min:.0f}min')

scores = [float(s.get('score', 0)) for s in today]
if scores:
    s200 = sum(1 for s in scores if s >= 200)
    s180 = sum(1 for s in scores if 180 <= s < 200)
    s138 = sum(1 for s in scores if 138 <= s < 180)
    slo  = sum(1 for s in scores if s < 138)
    print(f'  score分布: ≥200={s200}  180-199={s180}  138-179={s138}  <138={slo}')
    if s200 + s180 == 0:
        P1('NO_HIGH_SCORE', '今日无score≥180信号，武曲自动执行条件偏紧')
    else:
        OK(f'高质量信号(≥180): {s200+s180}条')

# 僵尸信号检查
zombie_count = 0
for s in today:
    if s.get('status') == 'OPEN':
        exp = s.get('expires_at', '')
        if exp:
            try:
                exp_ts = datetime.fromisoformat(exp.replace('Z', '+00:00')).timestamp()
                if now > exp_ts:
                    zombie_count += 1
            except Exception:
                pass
if zombie_count > 0:
    P1('ZOMBIE_SIGNALS', f'{zombie_count}个已过期但仍OPEN的僵尸信号')
else:
    OK('无僵尸信号')

# ── F. 执行链路深度 ──────────────────────────────────────────
section('F. 执行链路深度检查')
exec_logs = []
log_path = DATA / 'auto_execute_log.jsonl'
if log_path.exists():
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    exec_logs.append(json.loads(line))
                except Exception:
                    pass

ev_cnt = Counter(e.get('event') for e in exec_logs)
print(f'  执行日志总计: {len(exec_logs)}条  {dict(ev_cnt)}')

blocked = [e for e in exec_logs if e.get('event') == 'BLOCKED']
block_reasons = Counter()
for e in blocked:
    r = e.get('reason', '')
    if '持仓数' in r or '已有持仓' in r:
        block_reasons['持仓满/重复'] += 1
    elif 'HARD_BLOCK' in r:
        block_reasons['死穴拦截'] += 1
    elif 'score' in r:
        block_reasons['score不足'] += 1
    elif 'breaker' in r:
        block_reasons['熔断'] += 1
    elif '异常' in r or 'Error' in r or 'Exception' in r:
        block_reasons['系统异常'] += 1
    else:
        block_reasons['其他'] += 1
print(f'  BLOCKED原因分布: {dict(block_reasons)}')

if block_reasons.get('系统异常', 0) > 0:
    P0('EXEC_EXCEPTION', f'执行层{block_reasons["系统异常"]}次系统异常，需排查')
else:
    OK('执行层无系统异常拦截')

executed = [e for e in exec_logs if e.get('event') == 'EXECUTED']
if executed:
    OK(f'已成功执行 {len(executed)} 笔')
    for e in executed[-3:]:
        print(f'    {e.get("ts","")} {e.get("symbol")} {e.get("direction")} score={e.get("score")}')
else:
    P1('ZERO_EXECUTED', '历史0笔成功执行（修复后首单待触发）')

# dry_run验证
try:
    import importlib, importlib.util
    spec = importlib.util.spec_from_file_location('aeg', str(SCRIPTS/'auto_execute_gate.py'))
    aeg  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aeg)
    test_sig = {
        'symbol': 'ETHUSDT', 'direction': 'SHORT', 'score': 165,
        'regime': 'BEAR_TREND', 'entry_lo': 1700.0, 'entry_hi': 1720.0,
        'stop_loss': 1779.0, 'tp1': 1602.0, 'tp2': 1500.0, 'leverage': 3,
    }
    res = aeg.auto_execute(test_sig, dry_run=True)
    if res['executed']:
        order = res.get('order', {})
        OK(f'dry_run全链路通过 qty={order.get("qty")} entry={order.get("entry_price")}')
    else:
        P0('DRY_RUN_FAIL', f'dry_run执行失败: {res["reason"]}')
except Exception as e:
    P0('DRY_RUN_ERROR', f'dry_run异常: {e}')

# ── G. 持仓一致性 ────────────────────────────────────────────
section('G. 持仓字段完整性')
bs = json.loads((DATA / 'brahma_state.json').read_text())
wuqu = bs.get('wuqu_positions', [])
glob_pos = bs.get('positions', [])
OK(f'wuqu_positions: {len(wuqu)}个')
OK(f'positions(全局): {len(glob_pos)}个')
REQUIRED_POS = ['status', 'sl_price', 'tp1_price', 'direction', 'qty']
for p in glob_pos:
    missing = [k for k in REQUIRED_POS if k not in p]
    sym = p.get('symbol', '?')
    if missing:
        P1('POS_SCHEMA', f'{sym} 缺字段: {missing}')
    else:
        OK(f'{sym} status={p["status"]} sl={p["sl_price"]} dir={p["direction"]}')

# ── H. SSOT & 安全 ──────────────────────────────────────────
section('H. SSOT & 安全检查')
tools_txt = (BASE.parent / 'TOOLS.md').read_text()
real_key_prefix = re.search(r'API Key:\s*(\S+)', tools_txt).group(1)[:10]
py_files = list(SCRIPTS.glob('*.py')) + list(BASE.glob('*.py'))
hardcoded = []
for f in py_files:
    try:
        txt = f.read_text()
        if real_key_prefix in txt and 'system_config' not in str(f):
            hardcoded.append(f.name)
    except Exception:
        pass
if hardcoded:
    P1('HARDCODED_KEY', f'疑似硬编码APIKey: {hardcoded}')
else:
    OK('无硬编码APIKey (SSOT通过)')

# ── I. 风控参数 ──────────────────────────────────────────────
section('I. 风控参数一致性')
gate_code = (SCRIPTS / 'auto_execute_gate.py').read_text()
min_score_m = re.search(r'MIN_SCORE\s*=\s*(\d+)', gate_code)
max_pos_m   = re.search(r'MAX_OPEN_POSITIONS\s*=\s*(\d+)', gate_code)
max_nav_m   = re.search(r'MAX_POS_PCT_NAV\s*=\s*([\d.]+)', gate_code)

ms_val = int(min_score_m.group(1)) if min_score_m else None
mp_val = int(max_pos_m.group(1))   if max_pos_m   else None
mn_val = float(max_nav_m.group(1)) if max_nav_m   else None

if ms_val:
    if ms_val < 130 or ms_val > 160:
        P1('RISK_PARAM', f'MIN_SCORE={ms_val} 偏离建议范围[130-160]')
    else:
        OK(f'MIN_SCORE={ms_val} ✓')
else:
    P0('RISK_PARAM', 'MIN_SCORE未定义')

if mp_val:
    OK(f'MAX_OPEN_POSITIONS={mp_val}')
else:
    P0('RISK_PARAM', 'MAX_OPEN_POSITIONS未定义')

if mn_val:
    pct = mn_val * 100
    if pct > 25:
        P1('RISK_PARAM', f'MAX_POS_PCT_NAV={pct:.0f}% 偏高，建议≤20%')
    else:
        OK(f'MAX_POS_PCT_NAV={pct:.0f}% ✓')
else:
    P0('RISK_PARAM', 'MAX_POS_PCT_NAV未定义')

EXPECTED_BLOCKS = {'BEAR_TREND_LONG', 'BULL_TREND_SHORT', 'BEAR_RECOVERY_SHORT', 'BULL_CORRECTION_LONG'}
hb_m = re.search(r'HARD_BLOCK\s*=\s*\{([^}]+)\}', gate_code, re.DOTALL)
if hb_m:
    found = set(re.findall(r"'([A-Z_]+)'", hb_m.group(1)))
    missing = EXPECTED_BLOCKS - found
    if missing:
        P0('HARD_BLOCK_MISSING', f'死穴缺失: {missing}')
    else:
        OK(f'HARD_BLOCK完整: {found}')
else:
    P0('HARD_BLOCK_MISSING', 'HARD_BLOCK集合未找到')

# ── J. 已知遗留问题 ──────────────────────────────────────────
section('J. 已知遗留问题复查')

# CausalVerifier extra_data bug
try:
    cv_code = (BASE / 'brahma_brain/causal_regime_verifier.py').read_text()
    lines_cv = cv_code.split('\n')
    uninit_lines = []
    for i, line in enumerate(lines_cv):
        if 'extra_data' in line and '=' not in line and line.strip().startswith('extra_data'):
            uninit_lines.append(i + 1)
    if uninit_lines:
        P1('CAUSAL_UNINIT', f'causal_regime_verifier extra_data可能未初始化 行:{uninit_lines[:3]}')
    else:
        OK('causal_regime_verifier extra_data无明显问题')
except Exception as e:
    P1('CAUSAL_CHECK', f'检查异常: {e}')

# signal_watcher 异常吞噬
sw_code = (SCRIPTS / 'signal_watcher.py').read_text()
if 'except Exception as _ex:' in sw_code and 'auto_execute' in sw_code:
    P1('SWALLOW_EXCEPTION', 'signal_watcher: auto_execute异常被except吞噬，建议加告警日志')
else:
    OK('signal_watcher 异常处理正常')

# LOT_SIZE 无缓存（每次执行都调exchangeInfo）
aeg_code = (SCRIPTS / 'auto_execute_gate.py').read_text()
if 'exchangeInfo' in aeg_code and '_lot_size_cache' not in aeg_code:
    P1('NO_LOTSIZECACHE', 'auto_execute_gate每次执行都调exchangeInfo，无缓存，网络故障时执行失败')
else:
    OK('LOT_SIZE缓存正常')

# ── K. Git状态 ───────────────────────────────────────────────
section('K. 代码版本 & 未提交变更')
r = subprocess.run(['git', 'status', '--short'], capture_output=True, text=True, cwd=str(BASE))
dirty = [l for l in r.stdout.strip().split('\n') if l.strip()]
if dirty:
    P1('GIT_DIRTY', f'有{len(dirty)}个未提交文件: {[d.split()[-1] for d in dirty[:5]]}')
else:
    OK('工作区干净，无未提交变更')

r2 = subprocess.run(['git', 'log', '--oneline', '-3'], capture_output=True, text=True, cwd=str(BASE))
for line in r2.stdout.strip().split('\n'):
    print(f'  📌 {line}')

# ────────────────────────────────────────────────────────────
section('全局扫描汇总')
p0_list = [x for x in issues if x[0] == 'P0']
p1_list = [x for x in issues if x[0] == 'P1']
total_issues = len(p0_list) + len(p1_list)
print(f'\n  扫描范围: A-K 共11个维度')
print(f'  发现问题: P0(严重)={len(p0_list)}  P1(关注)={len(p1_list)}  总计={total_issues}')

if p0_list:
    print('\n  ❌ P0 严重（需立即处理）:')
    for _, c, m in p0_list:
        print(f'     [{c}] {m}')
if p1_list:
    print('\n  ⚠️ P1 需关注:')
    for _, c, m in p1_list:
        print(f'     [{c}] {m}')
if not p0_list and not p1_list:
    print('\n  🟢 全系统健康，无问题')
elif not p0_list:
    print('\n  🟡 无严重问题，有待优化项')
else:
    print('\n  🔴 存在严重问题，需立即处理')

print()
verdict = '🟢' if not p0_list and not p1_list else ('🟡' if not p0_list else '🔴')
print(f'DEEP_SCAN_JSON:{{"p0":{len(p0_list)},"p1":{len(p1_list)},"verdict":"{verdict}"}}')
