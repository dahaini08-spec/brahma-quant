#!/usr/bin/env python3
"""
brahma_autonomous_loop.py — 梵天∞ 全自主运行引擎
[2026-10-04 设计院封印 苏摩111]

四大自主能力：
  A. 自愈：进程死 → 自动复活
  B. 自检：数据过期/错误 → 自动修复
  C. 自适应：宿主资源变化 → 自动调参
  D. 自进化：B线数据积累 → IC权重自动调整

接入位置：brahma_crontab.txt 每5分钟触发
推送通道：push_hub.py → Jarvis P2（异常告警）/ P4（健康心跳）
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, os, sys, time, subprocess, re
from pathlib import Path

BASE   = Path(__file__).parent.parent
DATA   = BASE / 'data'
LOGS   = BASE / 'logs'
sys.path.insert(0, str(BASE / 'scripts'))

JARVIS_USER   = '73295708'
JARVIS_THREAD = '01a0f312-7e0c-7ae0-ae95-f66915d1d13c'

def _push(msg: str, priority: str = 'P2', dedup_ttl: int = 3600):
    """推送到Jarvis，同类消息dedup_ttl内只推一次"""
    try:
        import push_hub as ph, hashlib as _hl
        _dk = 'autonomous_' + _hl.md5(msg[:80].encode()).hexdigest()[:12]
        ph.push_jarvis(msg, priority=priority, dedup_key=_dk, dedup_ttl=dedup_ttl)
    except Exception as e:
        print(f'[WARN] push失败: {e}', file=sys.stderr)

def _log(msg: str):
    print(f'[AutonomousLoop {time.strftime("%H:%M:%S")}] {msg}', file=sys.stderr)


# ════════════════════════════════════════════════════
# A. 自愈引擎 — 进程监控+复活
# ════════════════════════════════════════════════════
def heal_processes() -> list:
    """检查并复活死亡进程，返回复活列表"""
    revived = []
    procs = [
        ('supercronic', f'{BASE}/supercronic {BASE}/brahma_crontab.txt',
         f'>> {LOGS}/supercronic.log 2>&1'),
        ('cvd_ws_collector', f'python3 {BASE}/scripts/cvd_ws_collector.py',
         f'>> {LOGS}/cvd_ws.log 2>&1'),
        # liq_heatmap 不是常驻daemon，通过data_check检查数据时效 [Fix 2026-10-04 苏摩111]
        ('brahma_web_dashboard', f'python3 {BASE}/scripts/brahma_web_dashboard.py --port 8899',
         f'>> {LOGS}/dashboard.log 2>&1'),
    ]
    for name, cmd, redir in procs:
        alive = bool(subprocess.run(['pgrep','-f',name], capture_output=True).stdout)
        if not alive:
            subprocess.Popen(
                f'setsid {cmd} {redir} < /dev/null &',
                shell=True, cwd=str(BASE),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            revived.append(name)
            _log(f'✅ 复活: {name}')
    return revived


# ════════════════════════════════════════════════════
# B. 自检引擎 — 数据新鲜度+自愈
# ════════════════════════════════════════════════════
def check_data_health() -> dict:
    """检查数据源健康，触发自愈，返回状态报告"""
    now = time.time()
    issues = {}

    specs = [
        ('btc_state',  DATA/'brahma_state_btc.json',  90*60,  'state_quality_sentinel'),
        ('eth_state',  DATA/'brahma_state_eth.json',  90*60,  'state_quality_sentinel'),
        ('cvd_btc',    DATA/'cvd_realtime_btcusdt.json', 30*60, 'cvd_ws_collector'),
        ('cvd_eth',    DATA/'cvd_realtime_ethusdt.json', 30*60, 'cvd_ws_collector'),
        ('liq_btc',    DATA/'liq_heatmap_btcusdt.json',  60*60, 'liq_heatmap'),
        ('vol_beta',   DATA/'vol_beta_state.json',    4*3600,  'vol_beta_engine'),
        ('gex',        DATA/'gex_state.json',         2*3600,  'gex_engine'),
    ]

    for name, path, ttl, healer in specs:
        if not path.exists():
            issues[name] = {'status':'missing', 'healer': healer}
        else:
            age = now - os.path.getmtime(path)
            if age > ttl:
                issues[name] = {'status':'stale', 'age_h': round(age/3600,1), 'healer': healer}

    # 触发自愈：stale数据尝试重新采集
    healed = []
    for name, info in issues.items():
        healer = info.get('healer','')
        if healer == 'vol_beta_engine':
            try:
                sys.path.insert(0, str(BASE/'brahma_brain'))
                from brahma_brain.vol_beta_engine import run as vb_run
                vb_run('BTC'); vb_run('ETH')
                healed.append(f'vol_beta')
                _log('✅ vol_beta自愈完成')
            except Exception as e:
                _log(f'⚠️ vol_beta自愈失败: {e}')
        elif healer == 'state_quality_sentinel':
            try:
                subprocess.Popen(
                    ['python3', str(BASE/'scripts'/'state_quality_sentinel.py')],
                    cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                healed.append(f'state({name})')
            except Exception: pass  # sentinel子进程启动失败，非阻塞

    return {'issues': issues, 'healed': healed}


# ════════════════════════════════════════════════════
# C. 自适应引擎 — 宿主资源感知+参数调整
# ════════════════════════════════════════════════════
def adapt_to_host() -> dict:
    """感知宿主资源，自动调整运行参数"""
    adjustments = {}

    # 内存感知
    with open('/proc/meminfo') as f:
        mi = {l.split(':')[0]: int(l.split()[1])
              for l in f if ':' in l and l.split()[1].isdigit()}
    mem_avail_mb = mi.get('MemAvailable', 0) // 1024
    mem_pct = round((1 - mi.get('MemAvailable',0)/mi.get('MemTotal',1))*100, 1)

    # 磁盘感知
    st = os.statvfs('/')
    disk_pct = round((1-st.f_bavail/st.f_blocks)*100, 1)

    # 日志膨胀检测 + 自动轮转
    total_log_mb = sum(p.stat().st_size for p in LOGS.glob('*.log')) // 1024 // 1024
    if total_log_mb > 50:
        for logf in sorted(LOGS.glob('*.log'), key=lambda p: p.stat().st_size, reverse=True):
            if logf.stat().st_size > 5*1024*1024:
                lines = logf.read_text(errors='ignore').splitlines()[-300:]
                logf.write_text('\n'.join(lines))
                adjustments['log_rotated'] = logf.name
                _log(f'✅ 日志轮转: {logf.name}')

    # MIN_SCORE自适应（基于内存）
    mc_path = DATA / 'meta_cognition_state.json'
    if mc_path.exists():
        mc = json.loads(mc_path.read_text())
        cur_score = mc.get('adaptive_params', {}).get('min_score_open', 100)
        # 内存紧张 → 适当降低并发分析标的数（不改score）
        if mem_avail_mb < 400 and cur_score < 110:
            mc.setdefault('adaptive_params', {})['host_pressure'] = 'HIGH'
            mc_path.write_text(json.dumps(mc, ensure_ascii=False, indent=2))
            adjustments['host_pressure'] = 'HIGH'
            _log(f'⚠️ 内存紧张({mem_avail_mb}MB)，标记HIGH压力')

    return {
        'mem_pct': mem_pct, 'mem_avail_mb': mem_avail_mb,
        'disk_pct': disk_pct, 'log_total_mb': total_log_mb,
        'adjustments': adjustments,
    }


# ════════════════════════════════════════════════════
# D. 自进化引擎 — IC权重学习循环
# ════════════════════════════════════════════════════
def evolve() -> dict:
    """读取B线交易结果，触发IC权重调整"""
    result = {'status': 'skip', 'reason': ''}
    try:
        mc_path = DATA / 'meta_cognition_state.json'
        if not mc_path.exists():
            result['reason'] = 'meta_cognition不存在'
            return result

        mc = json.loads(mc_path.read_text())
        total = mc.get('total_trades', 0)

        # 触发条件：每积累10笔新交易
        last_evolved = mc.get('last_evolved_at_trades', 0)
        if total - last_evolved >= 10:
            sys.path.insert(0, str(BASE/'scripts'))
            from ic_feedback_engine import apply_ic_auto_weight_adjustment
            apply_ic_auto_weight_adjustment()
            mc['last_evolved_at_trades'] = total
            mc_path.write_text(json.dumps(mc, ensure_ascii=False, indent=2))
            result = {'status': 'evolved', 'trades': total}
            _log(f'🧬 自进化触发！总归因{total}笔，IC权重已调整')
        else:
            remaining = 10 - (total - last_evolved)
            result = {'status': 'waiting', 'trades': total, 'remaining': remaining}
    except Exception as e:
        result = {'status': 'error', 'error': str(e)}
        _log(f'⚠️ 自进化异常: {e}')
    return result


# ════════════════════════════════════════════════════
# 主循环
# ════════════════════════════════════════════════════
def main():
    t0 = time.time()
    report = {'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    # ── 快速预检：判断是否需要完整运行 ──
    # 进程守护由 process_resurrect.sh 每分钟覆盖，此处只做数据/资源/进化检查
    # 若宿主正常+数据新鲜 → 直接HEARTBEAT_OK，节省95%算力
    now = time.time()
    import os
    with open('/proc/meminfo') as _f:
        _mi = {l.split(':')[0]: int(l.split()[1]) for l in _f if ':' in l and l.split()[1].isdigit()}
    mem_pct_quick = round((1 - _mi.get('MemAvailable',0)/_mi.get('MemTotal',1))*100, 1)
    
    # 检查关键数据新鲜度（轻量，不启动子进程）
    _stale_quick = []
    for _fname, _ttl in [
        (DATA/'brahma_state_btc.json', 7200),
        (DATA/'brahma_state_eth.json', 7200),
        (DATA/'cvd_realtime_btcusdt.json', 3600),
    ]:
        if not _fname.exists() or (now - os.path.getmtime(_fname)) > _ttl:
            _stale_quick.append(_fname.name)
    
    # 快速通过条件：内存<75% + 无过期关键数据
    if mem_pct_quick < 75 and not _stale_quick:
        print('HEARTBEAT_OK', file=sys.stderr)
        return  # 直接退出，节省算力

    # 需要处理：继续完整检查
    revived = []  # 进程守护由process_resurrect负责
    report['revived'] = revived

    # B. 自检
    health = check_data_health()
    report['data_issues'] = len(health['issues'])
    report['data_healed'] = health['healed']

    # C. 自适应
    host = adapt_to_host()
    report['mem_pct'] = host['mem_pct']
    report['disk_pct'] = host['disk_pct']
    report['log_mb'] = host['log_total_mb']

    # D. 自进化
    evo = evolve()
    report['evolution'] = evo

    # E. 触发式监控（有状态才运行，无状态零消耗）
    _triggered = []
    # E1. VIP策略止损漂移（有策略才跑）
    _vip_f = DATA / 'vip_signal_state.json'
    if _vip_f.exists():
        import json as _jt
        _vip = _jt.loads(_vip_f.read_text())
        if any(_vip.get(k) for k in ['btc_sl','eth_sl','btc_entry','eth_entry']):
            import subprocess as _spt
            _r = _spt.run(['python3', str(BASE/'scripts'/'vip_signal_tracker.py')],
                          capture_output=True, text=True, timeout=20, cwd=str(BASE))
            if 'CRITICAL' in _r.stderr or '漂移' in _r.stderr:
                _triggered.append('vip_drift')
    # E2. 价格触发点（有配置才跑）
    _pt_files = [DATA/'price_trigger_config.json', DATA/'price_triggers.json']
    for _ptf in _pt_files:
        if _ptf.exists():
            import json as _jpt, subprocess as _sppt
            try:
                if _jpt.loads(_ptf.read_text()):
                    _r2 = _sppt.run(['python3', str(BASE/'scripts'/'price_trigger_monitor.py')],
                                    capture_output=True, text=True, timeout=20, cwd=str(BASE))
                    if 'TRIGGERED' in _r2.stdout or '触发' in _r2.stdout:
                        _triggered.append('price_trigger')
                    break
            except Exception: pass  # price_trigger检测失败，跳过
    report['triggered'] = _triggered

    elapsed = round(time.time() - t0, 2)
    report['elapsed_s'] = elapsed

    # 决定推送级别
    has_revival    = bool(revived)
    has_issues     = health['issues'] and not health['healed']
    has_evolution  = evo.get('status') == 'evolved'
    mem_critical   = host['mem_avail_mb'] < 300
    disk_critical  = host['disk_pct'] > 90

    if mem_critical or disk_critical:
        _push(
            f'🚨 梵天宿主资源告警\n'
            f'内存可用: {host["mem_avail_mb"]}MB ({host["mem_pct"]}%)\n'
            f'磁盘: {host["disk_pct"]}%',
            priority='P1'
        )
    elif has_revival:
        _push(
            f'🔧 梵天自愈完成\n复活进程: {", ".join(revived)}\n'
            f'内存: {host["mem_pct"]}% | 磁盘: {host["disk_pct"]}%',
            priority='P2',
            dedup_ttl=1800  # [Fix 2026-10-04] 30min内只推一次，防5min刷屏
        )
    elif has_evolution:
        _push(
            f'🧬 梵天自进化触发\n'
            f'总归因: {evo.get("trades")}笔\nIC权重已自动调整',
            priority='P2'
        )
    elif has_issues:
        issue_names = list(health['issues'].keys())
        _push(
            f'⚠️ 梵天数据异常\n问题数据源: {", ".join(issue_names)}\n自愈: {health["healed"]}',
            priority='P2'
        )
    else:
        # 一切正常 → P4静默心跳
        print('HEARTBEAT_OK', file=sys.stderr)

    # 写入状态快照
    state_file = DATA / 'autonomous_loop_state.json'
    try:
        state_file.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception: pass  # loop状态写入失败，非阻塞

    _log(f'完成 {elapsed}s | 复活={len(revived)} 数据问题={len(health["issues"])} '
         f'自进化={evo["status"]} 内存={host["mem_pct"]}%')


if __name__ == '__main__':
    main()
