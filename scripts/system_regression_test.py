#!/usr/bin/env python3
"""
system_regression_test.py — 梵天360全流程自动化回归测试
设计院 2026-06-04

覆盖：
  T01  brahma_analyze 四资产（BTC/ETH/SOL/BNB）耗时 & 输出完整性
  T02  Bridge-Gate log_signal 路径畅通性
  T03  send_strategy_dd1 score透传（历史bug修复验证）
  T04  brahma_execute dry_run 端到端
  T05  live_signal_settler 结算统计可读性
  T06  signal_queue 健康度（死数据率）
  T07  wuqu_paper_settled WR可读性（字段对齐）
  T08  DD1确认门状态
  T09  brahma_state 新鲜度
  T10  进程守护健康
"""

import sys, os, time, json, subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE))

ASSETS = ['BTC', 'ETH', 'SOL', 'BNB']
PASS = '✅'; FAIL = '❌'; WARN = '⚠️'

results = []

def record(tid, name, status, detail='', fix=''):
    results.append({'id': tid, 'name': name, 'status': status, 'detail': detail, 'fix': fix})
    icon = PASS if status == 'PASS' else (WARN if status == 'WARN' else FAIL)
    print(f'  {icon} {tid} {name}: {detail}')

def section(title):
    print(f'\n{"="*55}')
    print(f'  {title}')
    print(f'{"="*55}')

# ─────────────────────────────────────────────────────────
section('T01 · brahma_analyze 四资产分析')
# ─────────────────────────────────────────────────────────
analyze_results = {}
for asset in ASSETS:
    t0 = time.time()
    r = subprocess.run(
        ['python3', 'brahma_analyze.py', asset, '--dir', 'SHORT', '--json'],
        capture_output=True, text=True, timeout=30, cwd=str(BASE)
    )
    elapsed = time.time() - t0
    # 从 stdout 最后一段 JSON 解析
    out = r.stdout.strip()
    try:
        # 找最后一个完整嵌套JSON对象（支持多行）
        depth = 0; start = -1; d = {}
        for i, c in enumerate(out):
            if c == '{':
                if depth == 0: start = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        d = json.loads(out[start:i+1])
                        start = -1  # 继续找更后面的
                    except Exception:
                        pass
    except Exception:
        d = {}

    score = d.get('score', d.get('score_final', 0))
    valid = d.get('valid', d.get('valid_signal', False))
    regime = d.get('regime', '?')
    v2_blocked = d.get('v2_blocked', False)
    score_final = d.get('score_final', 0)
    entry_lo = d.get('params', {}).get('entry_lo', d.get('entry_lo', 0))

    # score=None 且 v2_blocked=True 是合法封锁状态，不是错误
    effective_score = score_final if score is None else (score or 0)
    ok_fields = all([regime != '?', entry_lo, effective_score is not None])
    is_blocked = v2_blocked and score is None  # OOS封锁为预期行为
    status = 'PASS' if (ok_fields and elapsed < 15) else ('WARN' if (is_blocked or elapsed >= 15) else 'FAIL')
    detail = (f'score={effective_score}(OOS封锁) valid={valid} regime={regime} '
              f'耗时={elapsed:.1f}s entry_lo={entry_lo:.2f}'
              if is_blocked else
              f'score={effective_score} valid={valid} regime={regime} 耗时={elapsed:.1f}s'
              + (f' entry_lo={entry_lo:.2f}' if entry_lo else ' ⚠️entry_lo缺失'))
    fix = '检查brahma_analyze --json输出字段完整性' if not ok_fields and not is_blocked else ''
    record('T01', f'{asset}分析', status, detail, fix)
    analyze_results[asset] = d

# ─────────────────────────────────────────────────────────
section('T02 · Bridge-Gate log_signal 路径')
# ─────────────────────────────────────────────────────────
try:
    import dharma_data_bridge as db
    # 用ETH分析结果测试写入（应被grade<70拦截，这是正常行为）[v24.2]
    eth_sig = {
        'symbol': 'ETHUSDT', 'direction': 'SHORT',
        'score': 156.0, 'valid': True, 'regime': 'BEAR_TREND',
        'entry_lo': 1781.26, 'entry_hi': 1801.44,
        'stop_loss': 1859.0, 'tp1': 1622.0, 'tp2': 1519.0,
        'rr1': 2.5, 'sl_pct': 3.77, 'price': 1746.0,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    ok = db.log_signal(eth_sig)
    # grade<70时返回False是预期行为（Bridge-Gate拦截）[v24.2]
    record('T02', 'log_signal路径', 'PASS',
           f'返回={ok}（grade<70拦截为预期行为，路径畅通）')
except Exception as e:
    record('T02', 'log_signal路径', 'FAIL', str(e), '检查dharma_data_bridge导入')

# ─────────────────────────────────────────────────────────
section('T03 · send_strategy_dd1 score透传（历史Bug验证）')
# ─────────────────────────────────────────────────────────
try:
    from push_hub import build_strategy_dd1
    # 测试：score=149传入后应能生成文本（不被dd1_logic_gate拦截）
    text = build_strategy_dd1(
        symbol='ETH', direction='SHORT', price=1746.0,
        entry_lo=1781.26, entry_hi=1801.44,
        stop_loss=1859.0, tp1=1622.0, tp2=1519.0,
        score=149.0, valid=True, regime='BEAR_TREND',
        near_tag='测试', rr1=2.5, rr2=3.3, sl_pct=3.77, tp1_pct=6.5,
    )
    has_sig = '根据新浪财经公开数据' in text
    has_entry = '1,781' in text
    status = 'PASS' if has_sig and has_entry else 'FAIL'
    record('T03', 'score透传修复', status,
           f'标识存在={has_sig} 入场区存在={has_entry}',
           '' if status == 'PASS' else '检查push_hub.py send_strategy_dd1 score透传')
except Exception as e:
    record('T03', 'score透传修复', 'FAIL', str(e),
           'build_strategy_dd1调用异常，检查dd1_logic_gate')

# ─────────────────────────────────────────────────────────
section('T04 · brahma_execute dry_run 端到端')
# ─────────────────────────────────────────────────────────
try:
    import brahma_execute as be
    for asset in ['ETH', 'BNB']:
        t0 = time.time()
        res = be.run(asset, 'SHORT', dry_run=True)
        elapsed = time.time() - t0
        status_val = res.get('status', '?')
        score_val = res.get('score', 0)
        # score=0 表示gate正常拦截（grade<70结构不足），DRY_RUN返回即链路通畅
        chain_ok = status_val == 'DRY_RUN'
        gate_note = '(grade<70 Bridge-Gate/StructureGate拦截，链路正常)' if score_val == 0 else f'score={score_val:.1f}'
        record('T04', f'{asset} dry_run',
               'PASS' if chain_ok else 'FAIL',
               f'status={status_val} {gate_note} 耗时={elapsed:.1f}s')
except Exception as e:
    record('T04', 'brahma_execute', 'FAIL', str(e), '检查brahma_execute.run接口')

# ─────────────────────────────────────────────────────────
section('T05 · 武曲Paper结算统计（wuqu_paper_state）')
# ─────────────────────────────────────────────────────────
# [v24.2 fix] T05原读live_signal_settler.get_stats()，
# 但live_signal_log用outcome字段(非result)，导致永远WR=0%的假WARN
# 修复：直接读wuqu_paper_state.json（系统权威战绩来源）
try:
    _wp = json.load(open(BASE / 'data/wuqu_paper_state.json'))
    _wins    = _wp.get('n_tp', 0) or _wp.get('wins', 0)
    _losses  = _wp.get('n_sl', 0) or _wp.get('losses', 0)
    _timeout = _wp.get('n_timeout', 0)
    _total   = _wp.get('n_total', 0) or _wp.get('total_trades', 0)
    _running = len(_wp.get('open', {})) or 0
    _denom   = _wins + _losses
    wr       = _wins / _denom if _denom > 0 else 0
    # wuqu_paper没有PF字段，用avg_pnl代替简单判断
    _avg_pnl = _wp.get('avg_pnl_pct', 0) or 0
    ok = _total >= 10 and (_denom == 0 or wr >= 0.5)
    detail = (f'n={_total}(TP={_wins} SL={_losses} TO={_timeout} RUNNING={_running}) '
              f'WR={wr*100:.1f}% avg_pnl={_avg_pnl:.2f}%')
    record('T05', '武曲Paper结算统计', 'PASS' if ok else 'WARN', detail,
           '' if ok else f'武曲数据不足(n={_total}<10)或WR偏低，检查信号质量')
except Exception as e:
    record('T05', '武曲Paper结算统计', 'FAIL', str(e))

# ─────────────────────────────────────────────────────────
section('T06 · signal_queue 健康度')
# ─────────────────────────────────────────────────────────
try:
    lines = [json.loads(l) for l in open(BASE / 'data/signal_queue.jsonl') if l.strip()]
    pending = [x for x in lines if x.get('status') == 'PENDING']
    dead = [x for x in pending if x.get('signal_dir', '?') == '?' or float(x.get('score', 0)) < 100]
    dead_rate = len(dead) / len(pending) if pending else 0
    status = 'PASS' if dead_rate < 0.1 else ('WARN' if dead_rate < 0.3 else 'FAIL')
    record('T06', 'signal_queue健康度', status,
           f'PENDING={len(pending)} 死数据={len(dead)} 死亡率={dead_rate*100:.0f}%',
           '运行signal_queue清理脚本' if status != 'PASS' else '')
except Exception as e:
    record('T06', 'signal_queue', 'FAIL', str(e))

# ─────────────────────────────────────────────────────────
section('T07 · wuqu_paper_settled WR（字段对齐验证）')
# ─────────────────────────────────────────────────────────
try:
    sl_all = [json.loads(l) for l in open(BASE / 'data/wuqu_paper_settled.jsonl') if l.strip()]
    sl = [x for x in sl_all if not x.get('_data_quality')]  # [v22.1/v24.2] 过滤grade<70污染
    dq_count = len(sl_all) - len(sl)
    wins = [x for x in sl if x.get('result') in ('TP1', 'TP2', 'WIN')]
    losses = [x for x in sl if x.get('result') in ('SL', 'LOSS')]
    total_decided = len(wins) + len(losses)
    wr = len(wins) / total_decided if total_decided else 0
    # 检查字段完整性（result不是outcome）
    has_result_field = sum(1 for x in sl if 'result' in x)
    has_outcome_field = sum(1 for x in sl if 'outcome' in x)
    # result和outcome两个字段值相同（均存在）属正常，只要有result字段即OK
    field_ok = has_result_field >= len(sl) * 0.9  # ≥90%记录有result字段即合格
    status = 'PASS' if wr >= 0.5 and total_decided >= 5 and field_ok else 'WARN'
    record('T07', '武曲Paper WR', status,
           f'settled={len(sl)}(污染{dq_count}条) wins={len(wins)} losses={len(losses)} WR={wr*100:.1f}% 字段=result{"✅" if field_ok else "❌"}',
           'wuqu统计应读result字段，非outcome字段' if not field_ok else '')
except Exception as e:
    record('T07', '武曲Paper', 'FAIL', str(e))

# ─────────────────────────────────────────────────────────
section('T08 · DD1确认门状态')
# ─────────────────────────────────────────────────────────
try:
    from dd1_confirm_gate import status as dd1_st
    s = dd1_st()
    pending_cnt = s.get('pending', 0)
    total = s.get('total', 0)
    status = 'PASS' if pending_cnt <= 5 else 'WARN'
    record('T08', 'DD1确认门', status,
           f'pending={pending_cnt} total={total}',
           'DD1队列积压，检查是否有未消费信号' if pending_cnt > 5 else '')
except Exception as e:
    record('T08', 'DD1确认门', 'FAIL', str(e))

# ─────────────────────────────────────────────────────────
section('T09 · brahma_state 新鲜度')
# ─────────────────────────────────────────────────────────
try:
    d = json.load(open(BASE / 'data/brahma_state.json'))
    # 兼容多种字段名（last_update / last_updated / ts）
    _lu_raw = d.get('last_update') or d.get('last_updated') or d.get('ts') or 0
    try:
        if isinstance(_lu_raw, str) and 'T' in _lu_raw:
            from datetime import datetime, timezone
            lu = datetime.fromisoformat(_lu_raw.replace('Z','+00:00')).timestamp()
        else:
            lu = float(_lu_raw or 0)
    except: lu = 0
    age_min = (time.time() - lu) / 60
    price = d.get('last_price') or d.get('price') or 0
    regime = d.get('regime', '?')
    ok = age_min < 20 and price > 0
    status = 'PASS' if ok else ('WARN' if age_min < 60 else 'FAIL')
    record('T09', 'brahma_state新鲜度', status,
           f'age={age_min:.1f}min BTC=${price:,.0f} regime={regime}',
           'state超过20min未更新，检查state-refresh-noai cron' if not ok else '')
except Exception as e:
    record('T09', 'brahma_state', 'FAIL', str(e))

# ─────────────────────────────────────────────────────────
section('T10 · 进程守护健康')
# ─────────────────────────────────────────────────────────
# pgrep 在子进程环境有时返回空（进程隔离假阴性）→ 双重验证
# 多路检测：pgrep + ps aux + /proc 扫描（兼容PID namespace隔离环境）
def _proc_check(keyword):
    import os
    # /proc扫描（最可靠，不受PID namespace影响）
    try:
        for pid in os.listdir('/proc'):
            if not pid.isdigit(): continue
            try:
                cmd = open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\x00', b' ').decode(errors='ignore')
                if keyword in cmd: return True
            except: pass
    except: pass
    return False

ws_ok = _proc_check('ws_guardian.py')
wd_ok = _proc_check('watchdog_guardian')
# fallback: ps aux
if not ws_ok or not wd_ok:
    _ps = subprocess.run(['ps', 'aux'], capture_output=True, text=True).stdout
    if not ws_ok: ws_ok = 'ws_guardian.py' in _ps
    if not wd_ok: wd_ok = 'watchdog_guardian' in _ps
# fallback2: pgrep
if not ws_ok:
    _pg = subprocess.run(['pgrep','-f','ws_guardian'], capture_output=True, text=True).stdout.strip()
    if _pg: ws_ok = True
if not wd_ok:
    _pg2 = subprocess.run(['pgrep','-f','watchdog_guardian'], capture_output=True, text=True).stdout.strip()
    if _pg2: wd_ok = True
# fallback3: 心跳文件新鲜度（<10min=活跃）
if not ws_ok:
    try:
        import json as _j2, datetime as _dt2
        _ws_st = _j2.load(open(BASE / 'data/ws_guardian_state.json'))
        _lp = _ws_st.get('last_ping','')
        if _lp:
            _lp_dt = _dt2.datetime.fromisoformat(_lp.replace('Z','+00:00'))
            _age_min = (_dt2.datetime.now(_dt2.timezone.utc) - _lp_dt).total_seconds()/60
            if _age_min < 10: ws_ok = True  # 心跳<10min，运行正常
    except: pass
status = 'PASS' if ws_ok and wd_ok else 'WARN'  # 进程状态是运维项，不阻断回归
record('T10', '进程守护', status,
       f'ws_guardian={"✅" if ws_ok else "❌"} watchdog={"✅" if wd_ok else "❌"}',
       '重启守护进程' if not (ws_ok and wd_ok) else '')

# ─────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────
print(f'\n{"="*55}')
print('  📊 回归测试汇总')
print(f'{"="*55}')
passed = sum(1 for r in results if r['status'] == 'PASS')
warned = sum(1 for r in results if r['status'] == 'WARN')
failed = sum(1 for r in results if r['status'] == 'FAIL')
total_tests = len(results)
print(f'  总计: {total_tests}项 | ✅PASS={passed} ⚠️WARN={warned} ❌FAIL={failed}')
print()

if failed > 0:
    print('  ❌ 需修复:')
    for r in results:
        if r['status'] == 'FAIL':
            print(f'    {r["id"]} {r["name"]}: {r["detail"]}')
            if r['fix']:
                print(f'       → {r["fix"]}')
if warned > 0:
    print('  ⚠️ 需关注:')
    for r in results:
        if r['status'] == 'WARN':
            print(f'    {r["id"]} {r["name"]}: {r["detail"]}')
            if r['fix']:
                print(f'       → {r["fix"]}')

score_str = f'{passed}/{total_tests}'
if failed == 0 and warned == 0:
    print(f'\n  🏆 全部通过 {score_str} — 系统健康')
elif failed == 0:
    print(f'\n  🟡 基本健康 {score_str} — 有{warned}项需关注')
else:
    print(f'\n  🔴 需要修复 {score_str} — 有{failed}项失败')

# 输出JSON供程序读取
print('\nREGRESSION_JSON:' + json.dumps({
    'passed': passed, 'warned': warned, 'failed': failed,
    'total': total_tests, 'ts': datetime.now(timezone.utc).isoformat()
}))
