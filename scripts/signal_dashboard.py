#!/usr/bin/env python3
"""
signal_dashboard.py — 三大系统信号仪表盘
封印: 设计院六方联合自主决策 2026-07-11

触发条件（任一满足即推送）:
  T1: 暴涨猎手出现新 exec=True 信号
  T2: OI猎手 A类信号新增或score变化>10
  T3: 梵天主脑 valid=True 新信号写入
  T4: 持仓 SL距离 < 2%（风险预警）
  T5: 定时兜底（每4H全量推送一次）

不推送条件:
  S1: 信号未变化（相同标的相同方向8H内）
  S2: 深夜00:00~07:00 无T3/T4紧急信号
  S3: 仅有 exec=False 信号变化
"""

import json
import time
import hmac
import hashlib
import sys
import os
import requests
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ── 路径常量 ──────────────────────────────────────────────
PUMP_LAST    = BASE / 'dharma' / 'pump_hunter' / 'last_alerts.json'
PUMP_STATS   = BASE / 'dharma' / 'pump_hunter' / 'hunter_stats.json'
OI_CANDS     = BASE / 'data' / 'oi_candidates.json'
SIGNAL_LOG   = BASE / 'data' / 'live_signal_log.jsonl'
WUQU_POS     = BASE / 'data' / 'wuqu_positions.json'
DASHBOARD_STATE = BASE / 'data' / 'dashboard_last_push.json'

# ── API ───────────────────────────────────────────────────
API_KEY = os.getenv('BINANCE_API_KEY', 'sDqoRAyeYHHzevKNxSj5JfkWpNUd6v8qPAhVy0Y8wbWGwC48eC7uhFOENAlVqV7b')
SECRET  = os.getenv('BINANCE_SECRET',  'hXQnzQco9SNVgKgF2m3xvBGlJjOHBVtlzqRlxOTkp0kiJAwAOTeUiGLQSAopqIj7')
BASE_URL = 'https://fapi.binance.com'

def _sign(params: dict) -> dict:
    params['timestamp'] = int(time.time() * 1000)
    qs = urlencode(params)
    params['signature'] = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return params

def _bget(path: str, params: dict = {}) -> dict | list:
    p = _sign(dict(params))
    r = requests.get(BASE_URL + path, params=p,
                     headers={'X-MBX-APIKEY': API_KEY}, timeout=8)
    return r.json()

# ── 状态读写 ──────────────────────────────────────────────
def _load_state() -> dict:
    if DASHBOARD_STATE.exists():
        try:
            return json.loads(DASHBOARD_STATE.read_text())
        except Exception:
            pass
    return {}

def _save_state(state: dict) -> None:
    DASHBOARD_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

# ── 数据采集 ──────────────────────────────────────────────
def _collect() -> dict:
    now = datetime.now(timezone.utc)
    d = {'ts': now.isoformat(), 'now': now}

    # 账户
    try:
        bal = _bget('/fapi/v2/balance')
        usdt = next((x for x in bal if x['asset'] == 'USDT'), {})
        pos  = _bget('/fapi/v2/positionRisk')
        active = [p for p in pos if abs(float(p['positionAmt'])) > 0]
        d['nav']    = float(usdt.get('balance', 0))
        d['avail']  = float(usdt.get('availableBalance', 0))
        d['pos_cnt']= len(active)
        d['total_pnl'] = sum(float(p['unRealizedProfit']) for p in active)
        d['positions'] = active
    except Exception as e:
        d['nav'] = 0; d['avail'] = 0; d['pos_cnt'] = 0
        d['total_pnl'] = 0; d['positions'] = []; d['err_account'] = str(e)

    # 暴涨猎手
    try:
        ph = json.loads(PUMP_LAST.read_text())
        hs = json.loads(PUMP_STATS.read_text())
        d['pump_scan_time']  = ph.get('scan_time', '')[:16]
        d['pump_scanned']    = ph.get('total_scanned', 0)
        d['pump_alerts']     = ph.get('alerts', [])
        d['pump_exec_alerts']= [a for a in d['pump_alerts'] if a.get('exec_eligible')]
        d['pump_today_total']= hs.get('total_signals', 0)
        d['pump_today_push'] = hs.get('total_pushed', 0)
    except Exception as e:
        d['pump_alerts'] = []; d['pump_exec_alerts'] = []; d['err_pump'] = str(e)

    # OI猎手
    try:
        oi = json.loads(OI_CANDS.read_text())
        cands = oi.get('candidates', {})
        d['oi_generated'] = oi.get('generated', '')[:16]
        d['oi_count']     = oi.get('count', 0)
        d['oi_a'] = sorted(
            [(k, v) for k, v in cands.items() if v.get('mode') == 'A'],
            key=lambda x: x[1].get('oi_score', 0), reverse=True
        )
        d['oi_b'] = sorted(
            [(k, v) for k, v in cands.items() if v.get('mode') == 'B'],
            key=lambda x: x[1].get('oi_score', 0), reverse=True
        )
        d['oi_neg_fr'] = sorted(
            [(k, v) for k, v in cands.items() if float(v.get('fr', 0)) < -0.05],
            key=lambda x: x[1].get('fr', 0)
        )[:3]
    except Exception as e:
        d['oi_a'] = []; d['oi_b'] = []; d['oi_neg_fr'] = []; d['err_oi'] = str(e)

    # 梵天主脑
    try:
        today_str = now.strftime('%Y-%m-%d')
        lines = SIGNAL_LOG.read_text().splitlines()
        all_sigs = [json.loads(l) for l in lines if l.strip()]
        today_sigs  = [s for s in all_sigs if today_str in str(s.get('ts_iso', ''))]
        valid_today = [s for s in today_sigs if s.get('valid')]
        d['brahma_today_total'] = len(today_sigs)
        d['brahma_today_valid'] = len(valid_today)
        d['brahma_valid_sigs']  = valid_today  # 全部今日有效信号
    except Exception as e:
        d['brahma_today_total'] = 0; d['brahma_today_valid'] = 0
        d['brahma_valid_sigs'] = []; d['err_brahma'] = str(e)

    # 持仓SL风险
    try:
        wuqu = json.loads(WUQU_POS.read_text())
        wmap = {w['symbol']: w for w in wuqu}
        risk_list = []
        for p in d.get('positions', []):
            sym = p['symbol']
            mp  = float(p['markPrice'])
            pnl = float(p['unRealizedProfit'])
            w   = wmap.get(sym, {})
            sl  = float(w.get('stop_loss', 0))
            tp  = float(w.get('take_profit', 0))
            sl_dist = (mp - sl) / mp * 100 if sl and mp else 99
            risk_icon = '🔴' if sl_dist < 2 else ('🟡' if sl_dist < 4 else '🟢')
            risk_list.append({
                'sym': sym, 'pnl': pnl, 'sl_dist': sl_dist,
                'tp': tp, 'mp': mp, 'icon': risk_icon
            })
        d['risk_list'] = sorted(risk_list, key=lambda x: x['sl_dist'])
        d['risk_alert'] = any(r['sl_dist'] < 2 for r in risk_list)
    except Exception as e:
        d['risk_list'] = []; d['risk_alert'] = False; d['err_risk'] = str(e)

    return d


# ── 变化检测 ──────────────────────────────────────────────
def _has_changes(data: dict, state: dict, now: datetime) -> tuple[bool, list[str]]:
    reasons = []
    hour = now.hour

    # T4: 持仓SL预警（最高优先级，凌晨也推）
    if data.get('risk_alert'):
        reasons.append('T4:SL<2%风险预警')

    # T3: 梵天主脑新有效信号
    prev_brahma_ids = set(state.get('brahma_valid_ids', []))
    cur_brahma_ids  = {s.get('signal_id', '') for s in data.get('brahma_valid_sigs', [])}
    new_brahma = cur_brahma_ids - prev_brahma_ids
    if new_brahma:
        reasons.append(f'T3:梵天新信号x{len(new_brahma)}')

    # 深夜静默（00:00~07:00）
    if 0 <= hour < 7 and not reasons:
        return False, ['S2:深夜静默']

    # T1: 暴涨猎手新exec信号
    prev_pump = set(state.get('pump_exec_syms', []))
    cur_pump  = {a['symbol'] for a in data.get('pump_exec_alerts', [])}
    new_pump  = cur_pump - prev_pump
    if new_pump:
        reasons.append(f'T1:猎手新信号{list(new_pump)}')

    # T2: OI猎手A类变化
    prev_oi_scores = state.get('oi_a_scores', {})
    cur_oi_scores  = {k: v.get('oi_score', 0) for k, v in data.get('oi_a', [])}
    new_oi_syms    = set(cur_oi_scores) - set(prev_oi_scores)
    changed_oi     = {k for k, v in cur_oi_scores.items()
                      if abs(v - prev_oi_scores.get(k, 0)) > 10}
    if new_oi_syms or changed_oi:
        reasons.append(f'T2:OI变化{list(new_oi_syms | changed_oi)[:2]}')

    # T5: 距上次推送超过4H
    last_push = state.get('last_push_ts', 0)
    if time.time() - last_push > 4 * 3600:
        reasons.append('T5:4H定时兜底')

    return bool(reasons), reasons


# ── 格式化输出 ────────────────────────────────────────────
def _format(data: dict) -> str:
    now  = data['now']
    ts   = now.strftime('%m-%d %H:%M UTC')
    lines = []

    # BLOCK A — 账户 + 体制
    lines.append(f'📊 梵天仪表盘 · {ts}')
    lines.append('─' * 32)
    lines.append(f'💰 NAV ${data["nav"]:.2f} | 可用 ${data["avail"]:.2f} | 持仓 {data["pos_cnt"]}个')
    lines.append(f'📈 总PnL {data["total_pnl"]:+.3f} USDT')
    lines.append('')

    # BLOCK B — 暴涨猎手
    exec_alerts = data.get('pump_exec_alerts', [])
    all_alerts  = data.get('pump_alerts', [])
    lines.append(f'🐯 暴涨猎手  {data.get("pump_scan_time","?")}  扫{data.get("pump_scanned",0)}个')
    lines.append('─' * 32)
    if exec_alerts:
        for a in exec_alerts:
            exp_ts = a.get('expire_ts', 0)
            remain = max(0, int((exp_ts - time.time()) / 60)) if exp_ts else 0
            exp_str = f'有效{remain}分钟' if remain > 0 else '⚠️即将过期'
            lines.append(f'✅ {a["symbol"]}  score={a["score"]}  ⚡ENTER')
            lines.append(f'   入场 ${a["entry_lo"]:.5g}~${a["entry_hi"]:.5g}')
            lines.append(f'   止损 ${a["sl_price"]:.5g}  TP1 ${a["tp1_price"]:.5g}  RR={a["rr"]}')
            lines.append(f'   FR={a["funding"]:+.4f}% 空头{a["short_pct"]:.0f}%  {exp_str}')
    else:
        lines.append('  暂无 exec=✅ 信号')

    watch_alerts = [a for a in all_alerts if not a.get('exec_eligible')]
    if watch_alerts:
        watch_str = ' '.join([f'{a["symbol"]}({a["score"]})' for a in watch_alerts[:3]])
        lines.append(f'👀 待触发: {watch_str}')
    lines.append(f'今日扫描{data.get("pump_today_total",0)}次 推送{data.get("pump_today_push",0)}次')
    lines.append('')

    # BLOCK C — OI猎手
    lines.append(f'🔬 OI猎手v3  {data.get("oi_generated","?")}  {data.get("oi_count",0)}候选')
    lines.append('─' * 32)
    oi_a = data.get('oi_a', [])
    if oi_a:
        lines.append('🏛 A类机构信号:')
        for sym, d in oi_a[:4]:
            lines.append(f'  {sym}  score={d.get("oi_score",0):.0f}  大户{d.get("whale_l",0):.0f}%  FR={d.get("fr",0):+.4f}%  RSI{d.get("rsi_1h",0):.0f}  {d.get("action","?")}')
    neg_fr = data.get('oi_neg_fr', [])
    if neg_fr:
        lines.append('🔥 极端负费率(轧空候选):')
        for sym, d in neg_fr:
            lines.append(f'  {sym}  FR={d.get("fr",0):+.4f}%  RSI{d.get("rsi_1h",0):.0f}')
    lines.append(f'A类{len(oi_a)} B类{len(data.get("oi_b",[]))}')
    lines.append('')

    # BLOCK D — 梵天主脑
    brahma_sigs = data.get('brahma_valid_sigs', [])
    lines.append(f'🧠 梵天主脑  今日{data.get("brahma_today_total",0)}条 有效{data.get("brahma_today_valid",0)}条')
    lines.append('─' * 32)
    if brahma_sigs:
        for s in brahma_sigs[-3:]:
            action  = s.get('action', '?')
            icon    = '🟢' if action == 'ENTER' else ('🟡' if action == 'WATCH' else '⚪')
            exp_iso = s.get('expires_at', '')
            try:
                from datetime import datetime
                exp_dt  = datetime.fromisoformat(exp_iso.replace('Z', '+00:00'))
                remain  = max(0, int((exp_dt - now).total_seconds() / 60))
                exp_str = f'有效{remain}分钟' if remain > 0 else '⚠️已过期'
            except Exception:
                exp_str = ''
            lines.append(f'{icon} {s.get("symbol","")}  score={s.get("score","?")}  {action}')
            lines.append(f'   入场 ${s.get("entry_lo","?")}~${s.get("entry_hi","?")}')
            lines.append(f'   止损 ${s.get("stop_loss") or s.get("sl_price","?")}  TP1 ${s.get("tp1") or s.get("tp1_price","?")}')
            lines.append(f'   {s.get("regime","?")}  {exp_str}')
            if s.get('consensus') == 'BEAR' and s.get('direction') == 'LONG':
                lines.append(f'   ⚠️ consensus=BEAR 多空分歧')
    else:
        lines.append('  暂无有效信号')
    lines.append('')

    # BLOCK E — 持仓风险雷达
    risk_list = data.get('risk_list', [])
    lines.append(f'🎯 持仓风险雷达  {data["pos_cnt"]}个持仓')
    lines.append('─' * 32)
    if risk_list:
        for r in risk_list[:6]:
            lines.append(f'{r["icon"]} {r["sym"]:14s}  PnL={r["pnl"]:+.3f}  SL距{r["sl_dist"]:.1f}%')
        if len(risk_list) > 6:
            rest_pnl = sum(r['pnl'] for r in risk_list[6:])
            lines.append(f'   ...另{len(risk_list)-6}个持仓  PnL合计{rest_pnl:+.3f}')
    lines.append(f'总PnL {data["total_pnl"]:+.3f} USDT  {"⚠️风险预警" if data.get("risk_alert") else "✅无清算风险"}')

    return '\n'.join(lines)


# ── 主入口 ────────────────────────────────────────────────
def run() -> None:
    now   = datetime.now(timezone.utc)
    state = _load_state()
    data  = _collect()

    should_push, reasons = _has_changes(data, state, now)

    if not should_push:
        print(f'[dashboard] 静默 ({reasons[0] if reasons else "无变化"})')
        return

    print(f'[dashboard] 推送触发: {reasons}')
    msg = _format(data)
    print(msg)

    # 更新状态
    state['last_push_ts']    = time.time()
    state['pump_exec_syms']  = [a['symbol'] for a in data.get('pump_exec_alerts', [])]
    state['oi_a_scores']     = {k: v.get('oi_score', 0) for k, v in data.get('oi_a', [])}
    state['brahma_valid_ids']= [s.get('signal_id', '') for s in data.get('brahma_valid_sigs', [])]
    _save_state(state)

    return msg


if __name__ == '__main__':
    run()
