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
    """
    美化输出 v2.0 — 设计院 2026-07-11
    原则:
      · 每块用标题行+内容行，无多余分隔线
      · 关键数字右对齐或固定宽度
      · 每条信号压缩到3行以内
      · 移动端友好：行宽≤28个全角字符
    """
    now = data['now']
    ts  = now.strftime('%m-%d %H:%M UTC')
    L   = []

    def sep():
        L.append('───────────────────────')

    # ═══ HEADER ═══════════════════════════════════════
    L.append(f'📊 梵天仪表盘  {ts}')
    sep()
    nav   = data.get('nav', 0)
    avail = data.get('avail', 0)
    pnl   = data.get('total_pnl', 0)
    pcnt  = data.get('pos_cnt', 0)
    pnl_icon = '📈' if pnl >= 0 else '📉'
    L.append(f'💰 NAV ${nav:.2f}  可用 ${avail:.2f}')
    L.append(f'{pnl_icon} 持仓 {pcnt}个  总PnL {pnl:+.3f} USDT')

    # ═══ BLOCK B — 暴涨猎手 ═══════════════════════════
    L.append('')
    exec_alerts = data.get('pump_exec_alerts', [])
    all_alerts  = data.get('pump_alerts', [])
    scan_t = data.get('pump_scan_time', '')
    scan_n = data.get('pump_scanned', 0)
    L.append(f'🐯 暴涨猎手  {scan_t}  扫{scan_n}个')
    sep()

    if exec_alerts:
        for a in exec_alerts:
            exp_ts  = a.get('expire_ts', 0)
            remain  = max(0, int((exp_ts - time.time()) / 60)) if exp_ts else 0
            exp_str = f'{remain}分钟' if remain > 0 else '⚠️过期'
            sym     = a.get('symbol', '?')
            sc      = a.get('score', '?')
            el = a.get('entry_lo', 0); eh = a.get('entry_hi', 0)
            sl = a.get('sl_price', 0); tp = a.get('tp1_price', 0)
            rr = a.get('rr', '?')
            fr = a.get('funding', 0)
            sp = a.get('short_pct', 0)
            L.append(f'✅ {sym}  score={sc}  ⚡ENTER  {exp_str}')
            L.append(f'   进场 {el:.5g}~{eh:.5g}')
            L.append(f'   SL {sl:.5g}  TP {tp:.5g}  RR={rr}')
            L.append(f'   FR {fr:+.4f}%  空{sp:.0f}%')
    else:
        L.append('  暂无 exec=✅ 信号')

    watch_alerts = [a for a in all_alerts if not a.get('exec_eligible')]
    if watch_alerts:
        watch_str = '  '.join(f'{a["symbol"]}({a["score"]})' for a in watch_alerts[:4])
        L.append(f'👀 待触: {watch_str}')
    td_t = data.get('pump_today_total', 0)
    td_p = data.get('pump_today_push', 0)
    L.append(f'  今日扫{td_t}次  推{td_p}次')

    # ═══ BLOCK C — OI猎手 ════════════════════════════
    L.append('')
    oi_gen = data.get('oi_generated', '?')
    oi_cnt = data.get('oi_count', 0)
    L.append(f'🔬 OI猎手v3  {oi_gen}  {oi_cnt}候选')
    sep()

    oi_a = data.get('oi_a', [])
    oi_b = data.get('oi_b', [])
    if oi_a:
        for sym, v in oi_a[:3]:
            sc  = v.get('oi_score', 0)
            fr  = v.get('fr', 0)
            rsi = v.get('rsi_1h', 0)
            act = v.get('action', '?')
            wl  = v.get('whale_l', 0)
            L.append(f'🏛 {sym}  sc={sc:.0f}  FR{fr:+.3f}%  RSI{rsi:.0f}  {act}')
    neg_fr = data.get('oi_neg_fr', [])
    if neg_fr:
        L.append('🔥 极端负FR:')
        for sym, v in neg_fr[:3]:
            fr  = v.get('fr', 0)
            rsi = v.get('rsi_1h', 0)
            L.append(f'   {sym}  FR{fr:+.4f}%  RSI{rsi:.0f}')
    L.append(f'  A类{len(oi_a)}  B类{len(oi_b)}')

    # ═══ BLOCK D — 梵天主脑 ══════════════════════════
    L.append('')
    bt_total = data.get('brahma_today_total', 0)
    bt_valid = data.get('brahma_today_valid', 0)
    L.append(f'🧠 梵天主脑  今日{bt_total}条  有效{bt_valid}条')
    sep()

    brahma_sigs = data.get('brahma_valid_sigs', [])
    if brahma_sigs:
        for s in brahma_sigs[-3:]:
            action = s.get('action', '?')
            icon   = '🟢' if 'ENTER' in action else ('🟡' if 'WATCH' in action else '⚪')
            sym    = s.get('symbol', '?')
            sc     = s.get('score', '?')
            regime = s.get('regime', '?')
            el = s.get('entry_lo', '?'); eh = s.get('entry_hi', '?')
            sl = s.get('stop_loss') or s.get('sl_price', '?')
            tp = s.get('tp1') or s.get('tp1_price', '?')
            rr = s.get('rr1', '?')
            # 有效时间
            exp_iso = s.get('expires_at', '')
            try:
                from datetime import datetime as _dt
                exp_dt  = _dt.fromisoformat(exp_iso.replace('Z', '+00:00'))
                remain  = max(0, int((exp_dt - now).total_seconds() / 60))
                exp_str = f'剩{remain}分钟'
            except Exception:
                exp_str = ''
            L.append(f'{icon} {sym}  sc={sc}  {action}  {exp_str}')
            L.append(f'   进场 {el}~{eh}')
            try:
                sl_v = float(sl) if sl and sl != 'None' else 0
                tp_v = float(tp) if tp and tp != 'None' else 0
                sl_s = f'${sl_v:.5g}' if sl_v else '待设置'
                tp_s = f'${tp_v:.5g}' if tp_v else '待设置'
                L.append(f'   SL {sl_s}  TP {tp_s}  RR={rr}')
            except Exception:
                L.append(f'   SL {sl}  TP {tp}  RR={rr}')
            L.append(f'   {regime}')
    else:
        L.append('  暂无有效信号')

    # ═══ BLOCK E — 持仓风险 ══════════════════════════
    risk_list = data.get('risk_list', [])
    if risk_list:
        L.append('')
        L.append(f'🎯 持仓风险  {pcnt}个')
        sep()
        for r in risk_list[:8]:
            sym_s = r['sym'][:12]
            pnl_r = r['pnl']
            sld   = r['sl_dist']
            L.append(f'{r["icon"]} {sym_s:<12}  PnL{pnl_r:+.3f}  SL距{sld:.1f}%')
        if len(risk_list) > 8:
            rest_pnl = sum(r['pnl'] for r in risk_list[8:])
            L.append(f'   另{len(risk_list)-8}个  合计{rest_pnl:+.3f}')
        risk_tag = '⚠️ 风险预警' if data.get('risk_alert') else '✅ 无清算风险'
        L.append(f'{risk_tag}  总PnL {pnl:+.3f}')

    return '\n'.join(L)


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
