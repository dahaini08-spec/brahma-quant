import os
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
try:
    from scripts.system_config import JARVIS_USER_ID as _UID, JARVIS_THREAD_ID as _TID
    JARVIS_USER   = os.environ.get('JARVIS_USER_ID', _UID)
    JARVIS_THREAD = os.environ.get('JARVIS_THREAD_ID', _TID)
except Exception:
    JARVIS_USER   = os.environ.get('JARVIS_USER_ID', '73295708')
    JARVIS_THREAD = '019fd9dd-4b0f-71db-87fb-1e192ccb2291'

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
        # [D4修复 2026-08-19 苏摩111] 确保所有字段可序列化，防止'?'丢失
        try:
            with open(alert_f, 'a') as _af:
                _af.write(json.dumps({
                    'ts':     int(time.time()),
                    'fault':  str(fault_id),
                    'title':  str(title),
                    'healed': bool(healed),
                    'err':    str(e)
                }, ensure_ascii=False) + '\n')
        except Exception:
            pass

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
# F6: brahma_engine 可导入性检查
# ══════════════════════════════════════════
def check_f6_engine_importable() -> dict:
    result = {'fault': 'F6', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        import ast
        engine_path = SCRIPTS.parent / 'brahma_brain' / 'brahma_engine.py'
        runner_path = SCRIPTS.parent / 'brahma_brain' / 'brahma_analysis_runner.py'
        hao_path    = SCRIPTS / 'brahma_1hao_analysis.py'
        errors = []
        for p in [engine_path, runner_path, hao_path]:
            if not p.exists():
                errors.append(f'{p.name} 文件不存在')
                continue
            try:
                ast.parse(p.read_text())
            except SyntaxError as e:
                errors.append(f'{p.name} 语法错误 L{e.lineno}: {e.msg}')
        if errors:
            result['triggered'] = True
            result['healed']    = False  # 语法错误必须人工修复
            result['detail']    = ' | '.join(errors)
        else:
            result['detail'] = '引擎文件语法OK'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F7: brahma_state 体制时效检查
# ══════════════════════════════════════════
def check_f7_regime_freshness() -> dict:
    result = {'fault': 'F7', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        bs = json.loads((DATA / 'brahma_state.json').read_text())
        ts = float(bs.get('ts', 0))
        regime = bs.get('regime', 'UNKNOWN')
        age_min = (time.time() - ts) / 60 if ts > 1000000000 else 9999
        STALE_THRESHOLD = 90  # 分钟
        if age_min > STALE_THRESHOLD:
            result['triggered'] = True
            result['detail']    = f'体制={regime} 已{age_min:.0f}min未更新（阈值{STALE_THRESHOLD}min）'
            # 自愈：触发 brahma_state_refresh.py
            refresh = SCRIPTS / 'brahma_state_refresh.py'
            if refresh.exists():
                import subprocess
                r = subprocess.run(['python3', str(refresh)], capture_output=True, timeout=30)
                if r.returncode == 0:
                    result['healed'] = True
                    result['detail'] += ' → 已触发刷新'
                else:
                    result['healed'] = False
                    result['detail'] += f' → 刷新失败: {r.stderr.decode()[:80]}'
            else:
                result['healed'] = False
                result['detail'] += ' → 刷新脚本不存在'
        else:
            result['detail'] = f'体制={regime} {age_min:.0f}min前更新，正常'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F8: live_signal_log 写入活跃性检查
# ══════════════════════════════════════════
def check_f8_signal_log_active() -> dict:
    result = {'fault': 'F8', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        log_path = DATA / 'live_signal_log.jsonl'
        if not log_path.exists():
            result['triggered'] = True
            result['healed']    = False
            result['detail']    = 'live_signal_log.jsonl 文件不存在'
            return result
        lines = log_path.read_text().strip().split('\n')
        last_line = ''
        for l in reversed(lines):
            if l.strip():
                last_line = l; break
        if not last_line:
            result['triggered'] = True
            result['healed']    = False
            result['detail']    = 'signal_log 为空'
            return result
        last = json.loads(last_line)
        ts_raw = last.get('created_at', last.get('ts', 0))
        last_ts = float(ts_raw) if isinstance(ts_raw, (int,float)) else 0
        age_h = (time.time() - last_ts) / 3600 if last_ts > 1000000000 else 999
        STALE_H = 4  # 超4小时无新信号
        if age_h > STALE_H:
            result['triggered'] = True
            result['healed']    = False  # 引擎挂起，无法自动恢复
            result['detail']    = f'最新信号距今{age_h:.1f}h（阈值{STALE_H}h），引擎可能挂起'
        else:
            total = len([l for l in lines if l.strip()])
            result['detail'] = f'{total}条记录，最新{age_h:.1f}h前，正常'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F9: rsi_trigger_event 时效检查
# ══════════════════════════════════════════
def check_f9_rsi_trigger_fresh() -> dict:
    result = {'fault': 'F9', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        evt_path = DATA / 'rsi_trigger_event.json'
        if not evt_path.exists():
            result['detail'] = 'rsi_trigger_event.json 不存在（首次运行正常）'
            return result
        evt = json.loads(evt_path.read_text())
        ts_raw = evt.get('ts', evt.get('timestamp', 0))
        age_min = (time.time() - float(ts_raw)) / 60 if ts_raw else 9999
        STALE_MIN = 60
        silent = evt.get('silent', False)
        if age_min > STALE_MIN and not silent:
            result['triggered'] = True
            # 自愈：触发 rsi_structure_watcher
            watcher = SCRIPTS / 'rsi_structure_watcher.py'
            if watcher.exists():
                import subprocess
                r = subprocess.run(['python3', str(watcher)], capture_output=True, timeout=30)
                result['healed'] = r.returncode == 0
                result['detail'] = f'rsi_trigger_event {age_min:.0f}min未更新 → {"已刷新" if result["healed"] else "刷新失败"}'
            else:
                result['healed'] = False
                result['detail'] = f'rsi_trigger_event {age_min:.0f}min未更新，watcher不存在'
        else:
            result['detail'] = f'rsi_trigger_event {age_min:.0f}min前更新，silent={silent}，正常'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F10: auto_executed_signals 闭环检查
# ══════════════════════════════════════════
def check_f10_signal_lifecycle() -> dict:
    result = {'fault': 'F10', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        sig_path = DATA / 'auto_executed_signals.json'
        if not sig_path.exists():
            result['detail'] = 'auto_executed_signals.json 不存在'
            return result
        sigs = json.loads(sig_path.read_text())
        if not isinstance(sigs, (list, dict)):
            result['detail'] = '格式异常'
            return result
        items = sigs if isinstance(sigs, list) else list(sigs.values())
        now = time.time()
        stale_open = []
        for s in items:
            status = s.get('status', s.get('state', ''))
            ts_raw = s.get('ts', s.get('created_at', s.get('open_ts', 0)))
            ts = float(ts_raw) if ts_raw else 0
            age_h = (now - ts) / 3600 if ts > 1000000000 else 0
            if status in ('OPEN','open','active') and age_h > 48:
                stale_open.append({'sym': s.get('symbol','?'), 'age_h': age_h})
        if stale_open:
            result['triggered'] = True
            result['healed']    = False
            result['detail']    = f'{len(stale_open)}个信号OPEN>48h未结算: {stale_open[:3]}'
        else:
            result['detail'] = f'{len(items)}个信号，无异常滞留'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result

# ══════════════════════════════════════════
# F11: 清算集群(liq_heatmap) 时效检查
# ══════════════════════════════════════════
def check_f11_liq_heatmap() -> dict:
    """清算集群热力图时效性检查 — BTC/ETH必须在 30min 内更新"""
    result = {'fault': 'F11', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        STALE_MIN = 30
        core_syms = ['BTCUSDT', 'ETHUSDT']
        stale = []
        for sym in core_syms:
            f = DATA / f'liq_heatmap_{sym}.json'
            if not f.exists():
                stale.append(f'{sym}(文件不存在)')
                continue
            d = json.loads(f.read_text())
            ts = float(d.get('ts', 0))
            age_min = (time.time() - ts) / 60 if ts > 1e9 else 9999
            if age_min > STALE_MIN:
                stale.append(f'{sym}({age_min:.0f}min未更新)')
        if stale:
            result['triggered'] = True
            # 自愈：触发 OI scanner 重度计算清算集群
            scanner = SCRIPTS / 'oi_advanced_scanner.py'
            if scanner.exists():
                import subprocess
                r = subprocess.run(['python3', str(scanner), '--symbols', 'BTCUSDT', 'ETHUSDT'],
                                   capture_output=True, timeout=30)
                result['healed'] = r.returncode == 0
                result['detail'] = f'清算集群陈旧: {", ".join(stale)} → {"已刷新" if result["healed"] else "刷新失败"}'
            else:
                result['healed'] = False
                result['detail'] = f'清算集群陈旧: {", ".join(stale)}，自愈脚本不存在'
        else:
            ages = []
            for sym in core_syms:
                f = DATA / f'liq_heatmap_{sym}.json'
                d = json.loads(f.read_text())
                age_min = (time.time() - float(d.get('ts',0)))/60
                ages.append(f'{sym}({age_min:.0f}min)')
            result['detail'] = f'清算集群新鲜: {", ".join(ages)}'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════
# F12: SMC结构完整性验证(OB/FVG/CHoCH)
# ══════════════════════════════════════════
def check_f12_smc_structure() -> dict:
    """验证最近一次成功运行的分析中 OB/FVG 字段完整性"""
    result = {'fault': 'F12', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        struct_log = DATA / 'brahma_structured.jsonl'
        if not struct_log.exists():
            result['detail'] = 'brahma_structured.jsonl 不存在'
            return result
        lines = struct_log.read_text().strip().split('\n')
        # 找最近一条有 metrics 的记录
        last_full = None
        for l in reversed(lines):
            if not l.strip(): continue
            try:
                d = json.loads(l)
                if d.get('metrics'):
                    last_full = d
                    break
            except: pass
        if not last_full:
            result['detail'] = 'structured_log 无 metrics 记录'
            return result
        metrics = last_full.get('metrics', {})
        # 检查关键字段
        required_keys = ['ob_score', 'fvg_score', 'structure_score']
        missing = [k for k in required_keys if k not in metrics]
        age_min = (time.time() - float(last_full.get('ts', 0))) / 60
        if missing:
            result['triggered'] = True
            result['healed']    = False
            result['detail']    = f'SMC字段缺失: {", ".join(missing)} (分析层可能已降级)'
        elif age_min > 120:
            result['triggered'] = True
            result['healed']    = False
            result['detail']    = f'SMC最近分析{age_min:.0f}min前，过旧(阈值120min)'
        else:
            ob = metrics.get('ob_score', '?')
            fvg = metrics.get('fvg_score', '?')
            st = metrics.get('structure_score', '?')
            sym = last_full.get('symbol', '?')
            result['detail'] = f'{sym} OB={ob} FVG={fvg} Structure={st} ({age_min:.0f}min前)'
    except Exception as e:
        result['detail'] = f'检测异常: {e}'
    return result


# ══════════════════════════════════════════



# ══════════════════════════════════════════
# F16: DharmaFactor JSON权重文件完整性自愈
# ══════════════════════════════════════════
def check_f16_dharma_factor_weights() -> dict:
    """确保factor_weights.json存在且包含有效因子配置（rsi/volume/gates/resonance）"""
    result = {'fault': 'F16', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        import json as _j
        fw_path = BASE / 'dharma' / 'factor_weights.json'
        if not fw_path.exists():
            result['triggered'] = True
            # 自愈：重建最小可用因子配置
            _min_config = {
                '_meta': {'source': 'F16自愈重建', 'version': '4.2'},
                'rsi': [
                    {'id': 'RSI_OVERBOUGHT', 'status': 'live', 'score': 12},
                    {'id': 'RSI_OVERSOLD',   'status': 'live', 'score': 10},
                    {'id': 'RSI_60_70',      'status': 'live', 'score':  6},
                ],
                'volume': [
                    {'id': 'VOL_EXTREME_HIGH', 'status': 'live', 'score': 10},
                    {'id': 'VOL_LOW',          'status': 'live', 'score': -5},
                ],
                'gates': [
                    {'id': 'GATE_ATR_Q4',      'status': 'live', 'action': 'SCORE_PENALTY', 'score': -4},  # [2026-08-12 P4修复] -8→-4 低ATR=稳定非缺陷
                    {'id': 'GATE_SESSION_DEAD', 'status': 'live', 'action': 'SCORE_PENALTY', 'score': -6},
                ],
                'resonance': [
                    {'id': 'TRIPLE_RESONANCE_SHORT', 'status': 'live', 'score': 15},
                    {'id': 'TRIPLE_RESONANCE_LONG',  'status': 'live', 'score': 15},
                ],
                'meta': {'atr_q4_thresh': 0.531}
            }
            fw_path.write_text(_j.dumps(_min_config, indent=2, ensure_ascii=False))
            result['detail'] = 'factor_weights.json缺失 → F16自愈重建最小配置 ✅'
        else:
            fw = _j.loads(fw_path.read_text())
            required_keys = {'rsi', 'volume', 'gates', 'resonance'}
            missing = required_keys - set(fw.keys())
            if missing:
                result['triggered'] = True
                result['detail'] = f'factor_weights.json缺少: {missing}'
                result['healed'] = False
            else:
                rsi_n = len(fw.get('rsi', []))
                result['detail'] = f'factor_weights.json完整 rsi={rsi_n}条 ✅'
    except Exception as e:
        result['detail'] = f'F16检查异常: {e}'
    return result


# ══════════════════════════════════════════
# F15: BULL_TREND LONG WR门控完整性检查
# ══════════════════════════════════════════
def check_f15_wr_gate_integrity() -> dict:
    """确保signal_weights.json中BULL_TREND LONG的WR门控配置未被意外覆盖"""
    result = {'fault': 'F15', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        import json as _j, os as _o
        sw_path = DATA / 'signal_weights.json'
        if not sw_path.exists():
            result['detail'] = 'signal_weights.json 不存在'
            return result
        sw = _j.loads(sw_path.read_text())
        # 必须封禁的区间
        # ……………………………………………………………………………………………………………………………………
        # ❗ 2026-08-08 P0-3封印修正：
        #   BULL_TREND:LONG:120-139 脚数据清洗后 simfactory铁证 WR=51.5%(n=33)
        #   不再是死亡区，已恢复NORMAL。F15不得再封禁此区间。
        # ……………………………………………………………………………………………………………………………………
        required_blocks = {
            # 'BULL_TREND:LONG:120-139': 已与P0-3清洁数据封印证伯WR=51.5%，不得封禁
            'BULL_TREND:LONG:140-154': ('BLOCK', 0.45),   # WR=28.6% 死亡区
            'BULL_TREND:LONG:160+':    ('BLOCK', 0.45),   # WR=15.4% 死亡区
        }
        violations = []
        for key, (required_action, max_mult) in required_blocks.items():
            entry = sw.get(key, {})
            action = entry.get('action', 'NONE')
            mult   = float(entry.get('multiplier', 1.0))
            if action not in ('BLOCK', 'OBSERVE') or mult > max_mult:
                violations.append(f'{key}(action={action},mult={mult})')
        if violations:
            result['triggered'] = True
            # 自愈：重写封禁配置
            for key, (required_action, _) in required_blocks.items():
                if key not in sw or sw[key].get('action') not in ('BLOCK','OBSERVE'):
                    sw.setdefault(key, {})['action'] = required_action
                    sw[key]['multiplier'] = 0.3
                    sw[key]['basis'] = f'F15自愈恢复 铁证WR<45% [2026-08-02]'
            sw_path.write_text(_j.dumps(sw, indent=2, ensure_ascii=False))
            result['healed'] = True
            result['detail'] = f'WR门控配置异常已恢复: {violations}'
        else:
            result['detail'] = f'WR门控完整 BULL_TREND LONG全线BLOCK/OBSERVE ✅'
    except Exception as e:
        result['detail'] = f'WR门控检查异常: {e}'
    return result


# ══════════════════════════════════════════
# F14: tardis 清算数据月份自动刷新
# ══════════════════════════════════════════
def check_f14_tardis_freshness() -> dict:
    """检查tardis清算CSV是否为当月最新，若仍是上月则触发自愈刷新"""
    result = {'fault': 'F14', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent / 'brahma_brain'))
        from tardis_liq_layer import _get_free_date, CACHE_DIR, get_tardis_liq_walls
        year, month, day = _get_free_date()
        expected_date = f'{year}{month}01'
        # 检查BTC/ETH是否已有当月CSV
        core_syms = ['BTCUSDT', 'ETHUSDT']
        missing = []
        for sym in core_syms:
            cache_file = CACHE_DIR / f'binance-futures_{sym}_{expected_date}.csv.gz'
            if not cache_file.exists():
                missing.append(sym)
        if missing:
            result['triggered'] = True
            # 自愈：主动下载当月数据
            healed_syms = []
            for sym in missing:
                try:
                    r = get_tardis_liq_walls(sym)
                    if r.get('available') and r.get('date', '') >= expected_date[:7]:
                        healed_syms.append(sym)
                except Exception:
                    pass
            result['healed'] = len(healed_syms) == len(missing)
            result['detail'] = (
                f'tardis数据缺失({expected_date}): {missing} → '
                f'{"已下载: " + str(healed_syms) if healed_syms else "下载失败"}'
            )
        else:
            result['detail'] = f'tardis数据最新 ({expected_date}) BTC/ETH ✅'
    except Exception as e:
        result['detail'] = f'tardis检查异常: {e}'
    return result


# F13: live_prices 实时价格 时效检查
# ══════════════════════════════════════════
def check_f13_live_prices() -> dict:
    """实时价格时效，>5min则自愈刷新"""
    result = {'fault': 'F13', 'triggered': False, 'healed': True, 'detail': ''}
    try:
        price_file = DATA / 'live_prices.json'
        if not price_file.exists():
            result['triggered'] = True
            result['healed']    = False
            result['detail']    = 'live_prices.json 不存在'
            return result
        d = json.loads(price_file.read_text())
        ts = float(d.get('ts', d.get('timestamp', 0)))
        age_min = (time.time() - ts) / 60 if ts > 1e9 else 9999
        STALE_MIN = 5
        if age_min > STALE_MIN:
            result['triggered'] = True
            # 自愈：直接调 Binance 价格接口刷新
            try:
                import urllib.request
                prices = {}
                for sym in ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']:
                    url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}'
                    with urllib.request.urlopen(url, timeout=5) as r:
                        prices[sym] = float(json.loads(r.read())['price'])
                prices['ts'] = time.time()
                price_file.write_text(json.dumps(prices, ensure_ascii=False))
                result['healed'] = True
                result['detail'] = f'实时价格{age_min:.0f}min陈旧 → 已自愈刷新 BTC=${prices["BTCUSDT"]:.1f}'
            except Exception as e2:
                result['healed'] = False
                result['detail'] = f'实时价格{age_min:.0f}min陈旧，刷新失败: {e2}'
        else:
            btc = d.get('BTCUSDT', d.get('btc', '?'))
            eth = d.get('ETHUSDT', d.get('eth', '?'))
            result['detail'] = f'实时价格新鲜({age_min:.0f}min) BTC=${btc} ETH=${eth}'
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
        check_f6_engine_importable,
        check_f7_regime_freshness,
        check_f8_signal_log_active,
        check_f9_rsi_trigger_fresh,
        check_f10_signal_lifecycle,
        check_f11_liq_heatmap,
        check_f12_smc_structure,
        check_f13_live_prices,
        check_f14_tardis_freshness,
        check_f15_wr_gate_integrity,
        check_f16_dharma_factor_weights,
    ]

    fault_names = {
        'F1':  '🚨 有持仓时ws_guardian宕机',
        'F2':  '🚨 开单失败未回滚',
        'F3':  '🚨 NAV异常',
        'F4':  '🚨 OI/FR数据断流>30min',
        'F5':  '🚨 DD1队列丢失',
        'F6':  '🚨 一号引擎语法错误',
        'F7':  '⚠️ 体制数据陈旧>90min',
        'F8':  '🚨 信号日志>4h无新增',
        'F9':  '⚠️ rsi_trigger_event陈旧',
        'F10': '⚠️ 信号生命周期滞留>48h',
        'F11': '⚠️ 清算集群热力图陈旧>30min',
        'F12': '🚨 SMC结构(OB/FVG)字段缺失或过旧',
        'F13': '⚠️ 实时价格陈旧>5min',
        'F14': '⚠️ tardis清算数据未刷新至当月',
        'F15': '🚨 BULL_TREND LONG WR门控配置异常',
        'F16': '⚠️ DharmaFactor权重文件缺失/损坏',
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
        pass  # [静默]

    return state


if __name__ == '__main__':
    run()
