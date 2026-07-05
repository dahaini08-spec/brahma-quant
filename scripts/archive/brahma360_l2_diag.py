#!/usr/bin/env python3
"""
brahma360_l2_diag.py — 梵天360诊断 L2（每6H）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
整合原：健康看守-12h + PF衰退预警-daily + 系统状态汇总
纯Python采集 → 简短文字报告（无需AI）
"""
import json, os, time, sys, glob
from datetime import datetime, timezone, timedelta
# ── signal_utils 标准读取（2026-06-02 设计院Bug修复）────────────────────
def _load_clean_signals(hours=None, min_score=0, valid_only=False, unsettled_only=False):
    """标准化信号读取：避免历史残留信号污染统计/广播。"""
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.join(_o.path.dirname(__file__)))
    try:
        from signal_utils import load_signals as _su
        return _su(hours=hours or 8760, min_score=min_score, valid_only=valid_only,
                   unsettled_only=unsettled_only)
    except Exception:
        from pathlib import Path as _P
        import json as _j
        _f = _P(__file__).parent.parent / 'data' / 'live_signal_log.jsonl'
        if not _f.exists(): return []
        _all = [_j.loads(l) for l in open(_f) if l.strip()]
        if valid_only: _all = [l for l in _all if l.get('valid')]
        if unsettled_only: _all = [l for l in _all if not l.get('settled')]
        return _all
# ────────────────────────────────────────────────────────────────────────


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CST  = timezone(timedelta(hours=8))

def now_cst(): return datetime.now(CST).strftime('%Y-%m-%d %H:%M CST')

def read_json(path, default=None):
    try: return json.load(open(path))
    except: return default or {}

def check_pf_trend():
    """检查PF衰退：对比最近7天回测均值"""
    results_dir = os.path.join(BASE, 'dharma', 'results')
    pattern = os.path.join(results_dir, 'system_backtest_*.json')
    files = sorted(glob.glob(pattern))[-7:]
    if len(files) < 2:
        return dict(ok=True, note='PF趋势数据不足')
    pfs = []
    for f in files:
        d = read_json(f)
        # 尝试读取avg_pf
        pf = (d.get('avg_pf') or d.get('global', {}).get('avg_pf') or
              d.get('summary', {}).get('avg_pf'))
        if pf: pfs.append(float(pf))
    if len(pfs) < 2:
        return dict(ok=True, note='PF数据无法解析')
    trend = pfs[-1] - pfs[0]
    ok = pfs[-1] >= 1.1
    return dict(
        current=round(pfs[-1], 3), baseline=round(pfs[0], 3),
        trend=round(trend, 3), ok=ok,
        warn=f'PF衰退警告: {pfs[-1]:.3f}（基准{pfs[0]:.3f}，下降{abs(trend):.3f}）' if not ok else '',
        note=f'PF={pfs[-1]:.3f} 趋势{"↑" if trend>0 else "↓"}{abs(trend):.3f}'
    )

def check_cron_health():
    """统计cron任务失败率"""
    try:
        import subprocess
        result = subprocess.run(
            ['openclaw', 'cron', 'list'],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.split('\n')
        total = sum(1 for l in lines if len(l.strip()) > 40 and '│' not in l and 'ID' not in l and 'Schedule' not in l)
        errors = sum(1 for l in lines if 'error' in l.lower())
        return dict(total=total, errors=errors, ok=errors==0,
                    note=f'cron: {total}任务 {errors}错误',
                    warn=f'cron任务错误: {errors}个失败' if errors else '')
    except Exception as e:
        return dict(ok=True, note=f'cron状态获取失败: {e}')

def check_signal_history():
    """检查信号历史统计"""
    path = os.path.join(BASE, 'data', 'live_signal_log.jsonl')  # [FIX-E v6.0] 修正空壳数据源
    try:
        d = json.load(open(path))
        if isinstance(d, list):
            recent = [s for s in d if time.time() - s.get('entry_ts', 0) < 7*86400]
            wins = sum(1 for s in recent if s.get('pnl_pct', 0) > 0)
            wr = wins / len(recent) if recent else 0
            return dict(recent_7d=len(recent), win_rate=round(wr,3),
                        note=f'近7日信号: {len(recent)}条 WR={wr:.0%}')
    except: pass
    return dict(note='信号历史无数据')

def check_wuqu_paper():
    path = os.path.join(BASE, 'data', 'wuqu_paper_state.json')
    d = read_json(path)
    # [v24.2] 字段对齐: n_total/n_tp/n_sl/n_timeout (旧字段total_signals/stats已废弃)
    n       = d.get('n_total', 0) or d.get('total_trades', 0) or d.get('total_signals', 0)
    n_tp    = d.get('n_tp', 0) or d.get('wins', 0)
    n_sl    = d.get('n_sl', 0)
    n_to    = d.get('n_timeout', 0)
    n_open  = len(d.get('open', {})) or 0
    denom   = n_tp + n_sl
    wr      = n_tp / denom if denom > 0 else 0
    avg_pnl = d.get('avg_pnl_pct', 0) or 0
    start_ts = d.get('reset_at') or d.get('start_ts')
    if start_ts and isinstance(start_ts, str):
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(start_ts.replace('Z','+00:00'))
            days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        except: days = 0
    elif isinstance(start_ts, (int, float)):
        days = (time.time() - start_ts) / 86400
    else:
        days = 0
    rate = n / days if days > 0 else 0
    eta_days = (200 - n) / rate if rate > 0 else 999
    return dict(
        total=n, win_rate=round(wr,3), days_running=round(days,1),
        signal_rate=round(rate,2),
        note=f'武曲Paper: {n}/200条 | WR={wr:.0%} TP={n_tp} TO={n_to} avg_pnl={avg_pnl:.2f}% | 运行{days:.1f}天 | 预计{eta_days:.0f}天达标'
    )

def run():
    # ── 指令总线：全局静默时不运行诊断 ──────────────────────
    try:
        sys.path.insert(0, os.path.join(BASE, 'scripts'))
        from command_register import check_override, DOMAIN_ALL, DOMAIN_ALERTS
        silenced, reason = check_override(DOMAIN_ALL)
        if not silenced:
            silenced, reason = check_override(DOMAIN_ALERTS)
        if silenced:
            print(f'HEARTBEAT_OK (指令覆盖: {reason})')
            return
    except: pass
    # ─────────────────────────────────────────────────────────
    print(f"🔱 梵天360 L2系统诊断 | {now_cst()}")
    print("=" * 55)

    # L0 生命体征（快速）
    sys.path.insert(0, os.path.join(BASE, 'scripts'))
    try:
        import brahma360_guardian as g360
        l0 = g360.run.__wrapped__() if hasattr(g360.run, '__wrapped__') else None
    except: l0 = None

    mem = open('/proc/meminfo').read().split('\n')
    total = int([l for l in mem if 'MemTotal' in l][0].split()[1])//1024
    avail = int([l for l in mem if 'MemAvailable' in l][0].split()[1])//1024
    used = total - avail
    print(f"【L0 生命体征】")
    print(f"  RAM     {used}MB/{total}MB | 可用{avail}MB {'✅' if avail>400 else '⚠️'}")

    ws = read_json(os.path.join(BASE, 'data', 'ws_guardian_state.json'))
    ws_age = int(time.time() - ws.get('ts', 0))
    ws_ok = ws.get('status')=='active' and ws_age < 300
    print(f"  守护官  pid={ws.get('pid','?')} 心跳{ws_age}s前 {'✅' if ws_ok else '🚨'}")

    pos_d = read_json(os.path.join(BASE, 'data', 'brahma_state.json'))
    pos = pos_d.get('positions', pos_d.get('open_positions', []))
    print(f"  持仓    {len(pos)}个 {'✅' if len(pos)==0 else '⚠️ 持仓中'}")

    # L2 深度诊断
    print(f"\n【L2 深度诊断】")

    pf = check_pf_trend()
    print(f"  PF趋势  {pf.get('note','?')} {'✅' if pf['ok'] else '⚠️'}")
    if pf.get('warn'): print(f"  ⚠️ {pf['warn']}")

    cr = check_cron_health()
    print(f"  任务    {cr.get('note','?')} {'✅' if cr['ok'] else '⚠️'}")

    sh = check_signal_history()
    print(f"  信号史  {sh.get('note','?')}")

    wq = check_wuqu_paper()
    print(f"  武曲    {wq.get('note','?')}")

    # Kronos状态
    kf = '/tmp/kronos_signal.json'
    if os.path.exists(kf):
        kd = read_json(kf)
        age_h = (time.time() - os.path.getmtime(kf)) / 3600
        print(f"  Kronos  更新{age_h:.1f}H前 {kd.get('_meta',{}).get('updated_at','?')}")
        for sym in ['BTCUSDT','ETHUSDT']:
            kp = kd.get(sym,{}); d_str=kp.get('direction','?'); c_str=kp.get('confidence',0)
            print(f"          {sym}: {d_str} {c_str:.0%}")

    # 磁盘
    st = os.statvfs(BASE)
    free_gb = st.f_bavail * st.f_frsize / 1e9
    print(f"  磁盘    {free_gb:.1f}GB剩余 {'✅' if free_gb>2 else '⚠️'}")

    # 汇总
    issues = []
    if avail < 400: issues.append(f'RAM可用仅{avail}MB')
    if not ws_ok:   issues.append(f'ws_guardian心跳{ws_age}s')
    if pf.get('warn'): issues.append(pf['warn'])
    if cr.get('warn'): issues.append(cr['warn'])
    if free_gb < 2: issues.append(f'磁盘{free_gb:.1f}GB')

    # 资产模块健康检测（L6合法性 + RSI + RR）
    try:
        import subprocess
        ar = subprocess.run(
            ['python3', 'scripts/brahma360_asset_check.py'],
            capture_output=True, text=True, timeout=180,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        asset_out = ar.stdout.strip().split('\n')
        asset_errors = [l for l in asset_out if '🚨' in l and 'L6' in l]
        asset_warns  = [l for l in asset_out if '⚠️' in l]
        if asset_errors:
            issues.extend([f'资产模块异常: {e}' for e in asset_errors[:3]])
        print('\n  [资产模块检测]')
        for l in asset_out[1:-2]:  # 跳过标题和总计行
            print(f'  {l}')
    except Exception as e:
        print(f'  [资产检测跳过: {e}]')

    # 新闻局 IP 质量评分（检查最近10帖是否含禁词）
    try:
        import sys as _sys
        _sq_path = os.path.join(os.path.dirname(BASE), 'scripts', 'square')
        if _sq_path not in _sys.path: _sys.path.insert(0, _sq_path)
        from ip_wrapper import BANNED_PHRASES
        bureau = os.path.join(os.path.dirname(BASE), 'memory', 'square-bureau.jsonl')
        ip_score = 100; ip_warn = ''
        if os.path.exists(bureau):
            import json as _json
            recent = []
            for _l in open(bureau).readlines()[-10:]:
                try: recent.append(_json.loads(_l))
                except: pass
            ban_hits = []
            for _p in recent:
                _content = _p.get('content', '')
                for _w in BANNED_PHRASES:
                    if _w in _content: ban_hits.append(_w)
            if ban_hits:
                ip_score = max(0, 100 - len(set(ban_hits)) * 10)
                ip_warn = f'禁词命中: {", ".join(list(set(ban_hits))[:3])}'
        ip_icon = '✅' if ip_score >= 80 else '⚠️'
        print(f"  新闻局  IP质量={ip_score}/100 {ip_icon}{' ' + ip_warn if ip_warn else ''}")
        if ip_score < 80: issues.append(f'新闻局IP质量低({ip_score}/100): {ip_warn}')
    except Exception as _e:
        print(f"  新闻局  IP检查跳过({_e})")

    print(f"\n{'='*55}")
    if issues:
        print(f"⚠️ 发现{len(issues)}个问题:")
        for i in issues: print(f"  · {i}")
    else:
        print(f"✅ 系统全部正常 | 梵天360 L2诊断通过")

if __name__ == '__main__':
    run()
