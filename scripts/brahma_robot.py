#!/usr/bin/env python3
"""
梵天自动机器人主控 [封印 2026-10-26 苏摩111]
接入位置：crontab → */5 * * * * brahma_robot.py --mode heartbeat
功能：梵天神经中枢，自动驱动整个系统运营
  - 信号发现→自动推送VIP策略
  - prediction结算→自动通知盈亏
  - 相关性分化→自动宏观预警
  - 系统异常→自动修复+告警
  = 苏摩不需要手动查询，梵天主动说话
"""
import sys, os, json, time, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

JARVIS_USER_ID  = "73295708"
JARVIS_THREAD   = "01a12e17-726c-78da-9857-4edab995522b"
STATE_PATH      = BASE / 'data' / 'robot_state.json'

# ── 推送工具 ────────────────────────────────────────────────────────────────
def _push(msg: str, priority: str = 'P2') -> bool:
    try:
        from push_hub import push_jarvis
        return push_jarvis(msg, priority=priority)
    except Exception as e:
        print(f"[robot] 推送失败: {e}")
        return False

# ── 状态管理 ────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except Exception:
        return {}

def _save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ══════════════════════════════════════════════════════════════════════════════
# 模块一：信号监控自动推送
# ══════════════════════════════════════════════════════════════════════════════
def check_signals(state: dict) -> dict:
    """
    检查brahma_state中是否有新S级信号，自动推VIP策略。
    触发条件：confluence≥160 + bias≤-5或≥+5 + 距上次推送>2H
    """
    now = time.time()
    pushed = 0

    for sym in ['btc', 'eth']:
        sf = BASE / 'data' / f'brahma_state_{sym}.json'
        if not sf.exists():
            continue
        try:
            sd = json.loads(sf.read_text())
        except Exception:
            continue

        confluence = sd.get('confluence', 0)
        signal_dir = sd.get('signal_dir', 'WAIT')
        price_ts   = sd.get('price_ts', 0)

        # 过期state不触发
        if now - price_ts > 3600:
            continue

        # S级门控：confluence≥160
        if confluence < 160:
            continue

        # 距上次推送冷却（2H）
        last_push_key = f'last_signal_push_{sym}'
        last_push     = state.get(last_push_key, 0)
        if now - last_push < 7200:
            continue

        # 触发：拉取实时VIP
        try:
            from brahma_deep_analysis import fetch_all, enrich, reason_40yr, format_vip
            from brahma_prob_tree import calc_prob_tree, format_prob_tree
            raw = fetch_all(sym.upper())
            d   = enrich(raw)
            r   = reason_40yr(d)
            if r.get('direction', 'WAIT') == 'WAIT':
                continue
            vip   = format_vip(d, r)
            tree  = calc_prob_tree(d, r)
            ptree = '\n'.join(format_prob_tree(tree, r['direction']))
            msg = (
                f"⚡ 梵天S级信号 | {sym.upper()} Confluence={confluence:.0f}\n\n"
                f"{vip}\n{ptree}"
            )
            if _push(msg, priority='P1'):
                state[last_push_key] = now
                pushed += 1
        except Exception as e:
            print(f"[robot.signals] {sym} 失败: {e}")

    return state

# ══════════════════════════════════════════════════════════════════════════════
# 模块二：prediction结算自动通知
# ══════════════════════════════════════════════════════════════════════════════
def check_predictions(state: dict) -> dict:
    """
    检查prediction_log中OPEN记录，触发结算并推送结果。
    每30min检查一次（依赖state.last_pred_check）
    """
    now = time.time()
    if now - state.get('last_pred_check', 0) < 1800:
        return state
    state['last_pred_check'] = now

    try:
        from prediction_settler import run_settler
        result = run_settler(verbose=False)
        settled = result.get('settled', 0)
        if settled > 0:
            # 读取最新结算记录
            sp = BASE / 'data' / 'prediction_settled.jsonl'
            if sp.exists():
                lines = sp.read_text().splitlines()
                recent = [json.loads(l) for l in lines[-settled:] if l.strip()]
                parts = []
                for rec in recent:
                    icon = '✅' if (rec.get('pnl_pct', 0) or 0) > 0 else '❌'
                    parts.append(
                        f"{icon} {rec['symbol']} {rec['direction']} → {rec['status']} "
                        f"pnl={rec.get('pnl_pct',0):+.2f}%"
                    )
                msg = f"📊 预测结算 | {settled}笔\n" + '\n'.join(parts)
                _push(msg, priority='P2')
    except Exception as e:
        print(f"[robot.predictions] 失败: {e}")

    return state

# ══════════════════════════════════════════════════════════════════════════════
# 模块三：相关性分化预警
# ══════════════════════════════════════════════════════════════════════════════
def check_correlation(state: dict) -> dict:
    """
    检查BTC-ETH相关性是否突然下降（分化预警）。
    阈值：<0.70 = 警告；<0.50 = P1紧急预警
    """
    cm = BASE / 'data' / 'correlation_matrix.json'
    if not cm.exists():
        return state
    try:
        d   = json.loads(cm.read_text())
        corr= d.get('corr_matrix', {})
        btc_eth = corr.get('BTC-ETH', 0.9)
        prev    = state.get('last_btc_eth_corr', 0.9)
        state['last_btc_eth_corr'] = btc_eth

        if btc_eth < 0.50 and prev >= 0.50:
            _push(
                f"🚨 P1 相关性崩溃预警\n"
                f"BTC-ETH相关性={btc_eth:.3f}（跌破0.50）\n"
                f"= 双标方向分化，铁律⑤激活：选一标不双仓",
                priority='P1'
            )
        elif btc_eth < 0.70 and prev >= 0.70:
            _push(
                f"⚠️ 相关性分化预警\n"
                f"BTC-ETH={btc_eth:.3f} < 0.70\n"
                f"= 双标操作风险上升，降低仓位",
                priority='P2'
            )
    except Exception as e:
        print(f"[robot.correlation] 失败: {e}")

    return state

# ══════════════════════════════════════════════════════════════════════════════
# 模块四：系统自检+自愈
# ══════════════════════════════════════════════════════════════════════════════
def check_system_health(state: dict) -> dict:
    """
    检查系统关键指标，自动修复可修复项，推送不可修复的告警。
    每10min检查一次。
    """
    now = time.time()
    if now - state.get('last_health_check', 0) < 600:
        return state
    state['last_health_check'] = now

    alerts = []

    # 检查state新鲜度
    for sym in ['btc', 'eth']:
        sf = BASE / 'data' / f'brahma_state_{sym}.json'
        if sf.exists():
            try:
                sd  = json.loads(sf.read_text())
                age = (now - sd.get('price_ts', 0)) / 60
                if age > 90:
                    alerts.append(f"⚠️ {sym.upper()} state过期{age:.0f}min")
                    # 自愈：触发state_refresh
                    subprocess.Popen(
                        ['python3', str(BASE/'scripts'/'brahma_state_refresh.py'), sym.upper()],
                        cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
            except Exception:
                pass

    # 检查supercronic
    r = subprocess.run(['pgrep', '-f', 'supercronic'], capture_output=True, text=True)
    if not r.stdout.strip():
        alerts.append("🚨 supercronic未运行！cron全停")
        try:
            subprocess.Popen(
                ['bash', str(BASE/'scripts'/'start_supercronic.sh')],
                cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            alerts[-1] += " → 已尝试重启"
        except Exception:
            pass

    # 检查方仓数据文件
    for fname in ['fangcang_merged_v2.json', 'fangcang_experience_matrix_v2.json',
                  'fangcang_cases_tradfi.json']:
        fp = BASE / 'data' / fname
        if not fp.exists():
            alerts.append(f"❌ 缺失文件: {fname}")

    # 只在有新告警时推送（去重）
    last_alerts = set(state.get('last_health_alerts', []))
    new_alerts  = [a for a in alerts if a not in last_alerts]
    if new_alerts:
        _push("🏛️ 梵天系统告警\n" + '\n'.join(new_alerts), priority='P1')
        state['last_health_alerts'] = list(set(alerts))
    elif not alerts:
        state['last_health_alerts'] = []

    return state

# ══════════════════════════════════════════════════════════════════════════════
# 模块五：定时播报（每日晨报+盘中播报）
# ══════════════════════════════════════════════════════════════════════════════
def check_scheduled_broadcast(state: dict) -> dict:
    """
    定时主动播报：
      UTC 01:00 → 梵天晨报（亚盘前策略）
      UTC 07:00 → 欧盘开盘布局
      UTC 13:30 → 美盘开盘布局
    """
    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)
    h = now_dt.hour
    m = now_dt.minute

    # 检查是否在播报窗口（整点后5分钟内）
    broadcast_hours = {1: '亚盘前策略', 7: '欧盘开盘布局', 13: '美盘开盘布局'}
    if h not in broadcast_hours or m > 5:
        return state

    # 去重：同一小时只播一次
    bc_key = f'broadcast_{now_dt.strftime("%Y%m%d_%H")}'
    if state.get(bc_key):
        return state
    state[bc_key] = True

    label = broadcast_hours[h]
    try:
        from brahma_deep_analysis import run_deep_analysis
        report = run_deep_analysis(['BTC', 'ETH'])
        msg = f"🏛️ 梵天{label} | {now_dt.strftime('%m/%d %H:%M')} UTC\n\n{report}"
        _push(msg, priority='P2')
    except Exception as e:
        print(f"[robot.broadcast] 失败: {e}")

    return state

# ══════════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════════
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['heartbeat', 'signal', 'health', 'broadcast', 'all'],
                   default='all')
    args = p.parse_args()

    state = _load_state()

    if args.mode in ('heartbeat', 'all', 'health'):
        state = check_system_health(state)

    if args.mode in ('heartbeat', 'all', 'signal'):
        state = check_signals(state)

    if args.mode in ('heartbeat', 'all'):
        state = check_predictions(state)
        state = check_correlation(state)

    if args.mode in ('broadcast', 'all'):
        state = check_scheduled_broadcast(state)

    _save_state(state)

if __name__ == '__main__':
    main()
