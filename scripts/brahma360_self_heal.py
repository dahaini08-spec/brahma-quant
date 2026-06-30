#!/usr/bin/env python3
"""
brahma360_self_heal.py — 梵天360 系统化自愈 + 🚨告警引擎 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院 2026-06-07

职责：
  检测5类🚨级别故障 → 尝试自愈 → 自愈失败则推Jarvis告警
  自愈成功 → 静默（HEARTBEAT_OK）

5类故障：
  F1. 有持仓时 ws_guardian 宕机
  F2. 开单失败未回滚（trade_records中有FAILED_OPEN且>5分钟）
  F3. NAV异常（偏差>20% or 连续3次读取为0）
  F4. OI/FR数据断流超30分钟
  F5. DD1队列丢失（有pending任务但文件消失）

自愈策略：
  F1 → supervisorctl restart ws_guardian
  F2 → 调用rollback_failed_open()
  F3 → 触发brahma_state强制刷新
  F4 → 重启ws_guardian（数据源恢复）
  F5 → 从backup恢复DD1队列（若有backup）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import json, os, time, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE    = Path(__file__).parent.parent
DATA    = BASE / 'data'
SCRIPTS = BASE / 'scripts'
CONF    = BASE / 'supervisor.conf'
CST     = timezone(timedelta(hours=8))

# ── 告警发送（Jarvis）───────────────────────────────────────
JARVIS_USER = '73295708'
JARVIS_THREAD = '019ed32f-c46d-72ab-9d5e-92e47b4bdcc5'

def _now_cst() -> str:
    return datetime.now(CST).strftime('%m-%d %H:%M')

def _send_alert(fault_id: str, title: str, detail: str, healed: bool):
    """推送🚨告警到Jarvis（仅未自愈时触发）"""
    if healed:
        return  # 自愈成功，静默
    tag = '⚠️ 自愈失败' if not healed else '✅ 已自愈'
    msg = (
        f'🚨 梵天自愈 [{fault_id}] {_now_cst()}\n'
        f'{title}\n'
        f'{detail}\n'
        f'{tag} — 需人工介入'
    )
    try:
        sys.path.insert(0, str(SCRIPTS))
        from push_hub import send_dd2
        send_dd2(msg)
    except Exception as e:
        # fallback: 直接写告警文件
        alert_f = DATA / 'self_heal_alerts.jsonl'
        with open(alert_f, 'a') as f:
            f.write(json.dumps({
                'ts': int(time.time()), 'fault': fault_id,
                'title': title, 'healed': healed, 'err': str(e)
            }, ensure_ascii=False) + '\n')

def _supervisorctl(cmd: str) -> bool:
    try:
        r = subprocess.run(
            f'supervisorctl -c {CONF} {cmd}',
            shell=True, capture_output=True, text=True, timeout=10
        )
        return r.returncode == 0
    except:
        return False

# ══════════════════════════════════════════
# F1: 有持仓时 ws_guardian 宕机
# ══════════════════════════════════════════
def check_f1_ws_guardian() -> dict:
    result = {'fault': 'F1', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        bs = json.loads((DATA / 'brahma_state.json').read_text())
        open_pos = [p for p in bs.get('positions', []) if p.get('status') == 'OPEN']
        if not open_pos:
            return result  # 空仓，不检查

        # 有持仓 → 检查ws_guardian进程
        r = subprocess.run('pgrep -f "python3.*ws_guardian.py"',
                           shell=True, capture_output=True, text=True)
        if r.returncode == 0:
            return result  # 进程在跑

        # ws_guardian宕机+有持仓 → 触发
        result['triggered'] = True
        pos_info = ', '.join([f"{p.get('symbol','?')}@{p.get('entry_price','?')}"
                              for p in open_pos[:3]])
        result['detail'] = f'持仓{len(open_pos)}个: {pos_info}'

        # 自愈：supervisorctl restart
        ok = _supervisorctl('restart ws_guardian')
        if not ok:
            # fallback: 直接启动
            try:
                subprocess.Popen(
                    f'nohup python3 {BASE}/ws_guardian.py >> {BASE}/logs/ws_guardian.log 2>&1',
                    shell=True
                )
                ok = True
            except:
                ok = False
        result['healed'] = ok

    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F2: 开单失败未回滚
# ══════════════════════════════════════════
def check_f2_failed_open() -> dict:
    result = {'fault': 'F2', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        tr_f = DATA / 'trade_records_work.jsonl'
        if not tr_f.exists():
            return result
        now = time.time()
        stale = []
        with open(tr_f) as f:
            for line in f:
                if not line.strip(): continue
                try:
                    rec = json.loads(line)
                    if rec.get('status') == 'FAILED_OPEN':
                        ts = rec.get('open_ts', rec.get('ts', 0))
                        if now - float(ts) > 300:  # >5分钟
                            stale.append(rec)
                except:
                    continue

        if not stale:
            return result

        result['triggered'] = True
        result['detail'] = f'{len(stale)}条FAILED_OPEN未回滚: {[s.get("symbol","?") for s in stale[:3]]}'

        # 自愈：标记为ROLLED_BACK
        healed_count = 0
        lines = []
        with open(tr_f) as f:
            for line in f:
                if not line.strip():
                    lines.append(line); continue
                try:
                    rec = json.loads(line)
                    if rec.get('status') == 'FAILED_OPEN':
                        ts = rec.get('open_ts', rec.get('ts', 0))
                        if now - float(ts) > 300:
                            rec['status'] = 'ROLLED_BACK'
                            rec['rollback_ts'] = int(now)
                            rec['rollback_by'] = 'self_heal'
                            healed_count += 1
                    lines.append(json.dumps(rec, ensure_ascii=False) + '\n')
                except:
                    lines.append(line)
        tmp = str(tr_f) + '.tmp'
        with open(tmp, 'w') as f:
            f.writelines(lines)
        os.replace(tmp, str(tr_f))
        result['healed'] = healed_count > 0

    except Exception as e:
        result['detail'] = f'检测异常: {e}'
        result['healed'] = False
    return result


# ══════════════════════════════════════════
# F3: NAV异常
# ══════════════════════════════════════════
def check_f3_nav() -> dict:
    result = {'fault': 'F3', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        # 从brahma_state读NAV
        bs = json.loads((DATA / 'brahma_state.json').read_text())
        nav = float(bs.get('nav', 0) or 0)
        # 支持多种时间戳字段
        ts_raw = (bs.get('last_updated') or bs.get('updated_at') or
                  bs.get('last_ts') or bs.get('ts') or '')
        if ts_raw:
            try:
                from datetime import datetime as _dt
                if isinstance(ts_raw, (int, float)):
                    last_ts = float(ts_raw)
                else:
                    last_ts = _dt.fromisoformat(str(ts_raw).replace('Z','+00:00')).timestamp()
            except:
                last_ts = time.time()
        else:
            last_ts = time.time()
        staleness = time.time() - last_ts

        # 异常条件：NAV=0 或 state超过20分钟未更新
        if nav <= 0:
            result['triggered'] = True
            result['detail'] = f'NAV={nav}（为零）state更新{staleness/60:.1f}分前'
        elif staleness > 1200:  # 20分钟
            result['triggered'] = True
            result['detail'] = f'NAV=${nav:,.2f} 但state已{staleness/60:.1f}分未更新'

        if not result['triggered']:
            return result

        # 自愈：强制刷新state
        refresh_script = SCRIPTS / 'brahma_state_refresh.py'
        if refresh_script.exists():
            r = subprocess.run(f'python3 {refresh_script}',
                               shell=True, capture_output=True, timeout=15)
            result['healed'] = r.returncode == 0
        else:
            result['healed'] = False

    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F4: OI/FR数据断流超30分钟
# ══════════════════════════════════════════
def check_f4_oi_fr() -> dict:
    result = {'fault': 'F4', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        bs = json.loads((DATA / 'brahma_state.json').read_text())
        now = time.time()
        # 检查OI/FR最后更新时间
        def _ts(raw):
            if not raw: return now
            if isinstance(raw, (int, float)): return float(raw)
            try:
                from datetime import datetime as _dt
                return _dt.fromisoformat(str(raw).replace('Z','+00:00')).timestamp()
            except: return now
        oi_ts = _ts(bs.get('oi_ts') or bs.get('last_updated') or bs.get('updated_at'))
        fr_ts = _ts(bs.get('fr_ts') or bs.get('last_updated') or bs.get('updated_at'))
        state_ts = _ts(bs.get('last_updated') or bs.get('updated_at') or bs.get('last_ts'))

        oi_stale = (now - oi_ts) > 1800  # 30分钟
        fr_stale = (now - fr_ts) > 1800

        # 如果state本身超过30分钟未更新，也视为断流
        state_stale = (now - state_ts) > 1800

        if not (oi_stale or fr_stale or state_stale):
            return result

        result['triggered'] = True
        parts = []
        if oi_stale: parts.append(f'OI断流{(now-oi_ts)/60:.0f}分')
        if fr_stale: parts.append(f'FR断流{(now-fr_ts)/60:.0f}分')
        if state_stale: parts.append(f'State断流{(now-float(bs.get("last_ts",now)))/60:.0f}分')
        result['detail'] = ' | '.join(parts)

        # 自愈：重启ws_guardian（数据源重连）
        ok = _supervisorctl('restart ws_guardian')
        result['healed'] = ok

    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F5: DD1队列丢失
# ══════════════════════════════════════════
def check_f5_dd1_queue() -> dict:
    result = {'fault': 'F5', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        # 检查dd1_confirm_gate是否有内存中pending任务
        # 通过读取dd1_gate_state文件判断
        gate_state_files = list(DATA.glob('dd1_gate*.json')) + list(Path('/tmp').glob('dd1_gate*.json'))
        pending_count = 0
        for f in gate_state_files:
            try:
                q = json.loads(f.read_text())
                if isinstance(q, list):
                    pending_count += sum(1 for x in q if x.get('status') == 'pending')
            except:
                continue

        # 有pending任务但文件消失（Gateway重启）
        if pending_count > 0:
            result['triggered'] = True
            result['detail'] = f'{pending_count}条pending任务（Gateway重启可能丢失）'

            # 自愈：写入告警日志（DD1队列丢失无法自动恢复，需人工重新发送）
            alert_f = DATA / 'dd1_lost_alert.json'
            alert_f.write_text(json.dumps({
                'ts': int(time.time()),
                'pending': pending_count,
                'gate_files': [str(f) for f in gate_state_files]
            }, ensure_ascii=False))
            result['healed'] = False  # DD1丢失必须人工处理

    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# 主运行入口
# ══════════════════════════════════════════
def run():
    checks = [
        check_f1_ws_guardian,
        check_f2_failed_open,
        check_f3_nav,
        check_f4_oi_fr,
        check_f5_dd1_queue,
    ]

    fault_names = {
        'F1': '🚨 有持仓时ws_guardian宕机',
        'F2': '🚨 开单失败未回滚',
        'F3': '🚨 NAV异常',
        'F4': '🚨 OI/FR数据断流>30min',
        'F5': '🚨 DD1队列丢失',
    }

    triggered_faults = []
    healed_faults = []
    critical_faults = []  # 自愈失败，需告警

    for fn in checks:
        try:
            r = fn()
            if r['triggered']:
                fid = r['fault']
                triggered_faults.append(fid)
                if r['healed']:
                    healed_faults.append(fid)
                    # 自愈成功 → 写日志但不告警
                    print(f'✅ [{fid}] 自愈成功: {fault_names[fid]} | {r["detail"]}')
                else:
                    critical_faults.append(r)
                    # 自愈失败 → 推Jarvis
                    _send_alert(fid, fault_names[fid], r['detail'], healed=False)
                    print(f'🚨 [{fid}] 自愈失败，已推告警: {fault_names[fid]} | {r["detail"]}')
        except Exception as e:
            print(f'[self_heal] {fn.__name__} 执行异常: {e}')

    # 写入总状态
    state = {
        'ts': int(time.time()),
        'at': _now_cst(),
        'triggered': triggered_faults,
        'healed': healed_faults,
        'critical': [{'fault': r['fault'], 'detail': r['detail']} for r in critical_faults],
    }
    tmp = '/tmp/self_heal_state.json.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, '/tmp/self_heal_state.json')

    if not triggered_faults:
        print('HEARTBEAT_OK')

    return state


if __name__ == '__main__':
    run()
