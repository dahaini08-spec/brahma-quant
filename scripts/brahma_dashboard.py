#!/usr/bin/env python3
"""
brahma_dashboard.py — 梵天三大信号系统实时仪表盘 v1.0
设计院 · 2026-07-23 · 苏摩111封印

三大信号系统：
  System-1: 梵天主信号  (live_signal_log.jsonl · 35维矩阵 · score≥138触发)
  System-2: OI高级扫描  (oi_advanced_signals.jsonl · 持仓量异动 · 多空博弈)
  System-3: 暴涨猎手    (pump_signal_queue.jsonl · TIGHT压缩 · 97.5%胜率)

输出: 独立HTML仪表盘 + 终端ASCII预览
"""

import sys, os, json, time, hmac, hashlib, urllib.parse, requests
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

sys.path.insert(0, str(BASE))
try:
    from scripts.system_config import API_KEY, API_SECRET
except Exception:
    API_KEY = os.environ.get('BINANCE_API_KEY','')
    API_SECRET = os.environ.get('BINANCE_API_SECRET','')

FAPI = 'https://fapi.binance.com'

# ─── 工具函数 ──────────────────────────────────────────────────────

def _signed(path, params={}):
    if not API_KEY or not API_SECRET: return {}
    p = dict(params); p['timestamp'] = int(time.time()*1000)
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.get(f'{FAPI}{path}?{qs}&signature={sig}',
                         headers={'X-MBX-APIKEY': API_KEY}, timeout=8)
        return r.json()
    except Exception:
        return {}

def _pub(path, params={}):
    try:
        qs = urllib.parse.urlencode(params)
        r = requests.get(f'{FAPI}{path}?{qs}', timeout=8)
        return r.json()
    except Exception:
        return {}

def _now_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

def _age_str(ts):
    if not ts: return '?'
    age = time.time() - float(ts)
    if age < 60: return f'{int(age)}s前'
    if age < 3600: return f'{int(age/60)}min前'
    return f'{age/3600:.1f}H前'

def _score_color(score, is_oi=False):
    """返回CSS颜色和等级"""
    if is_oi:
        if score >= 90: return '#ff4757', '🔴极强'
        if score >= 75: return '#ff6b35', '🟠强'
        if score >= 60: return '#ffd32a', '🟡中'
        return '#aaa', '⚪弱'
    else:
        if score >= 175: return '#ff1744', '🔴神级'
        if score >= 155: return '#ff4757', '🔴极强'
        if score >= 138: return '#ff6b35', '🟠强'
        if score >= 120: return '#ffd32a', '🟡中等'
        return '#aaa', '⚪观望'

def _timing_color(badge):
    badge = badge or ''
    if 'READY' in badge: return '#00e676', badge
    if 'MONITOR' in badge: return '#ffd32a', badge
    if 'WAIT' in badge: return '#ff6b35', badge
    if 'STANDBY' in badge: return '#aaa', badge
    return '#aaa', badge or 'N/A'

# ─── 数据采集 ──────────────────────────────────────────────────────

def collect_account():
    """账户状态"""
    acc = _signed('/fapi/v2/account')
    if not isinstance(acc, dict) or 'totalMarginBalance' not in acc:
        return {'nav': 0, 'avail': 0, 'pnl': 0, 'positions': []}
    nav = float(acc.get('totalMarginBalance', 0))
    avail = float(acc.get('availableBalance', 0))
    pnl = float(acc.get('totalUnrealizedProfit', 0))
    positions = []
    for p in acc.get('positions', []):
        amt = float(p.get('positionAmt', 0))
        if abs(amt) == 0: continue
        entry = float(p.get('entryPrice', 0))
        upnl = float(p.get('unrealizedProfit', 0))
        notional = abs(float(p.get('notional', amt * entry)))
        pnl_pct = (upnl / (notional - abs(upnl))) * 100 if notional > 0 else 0
        positions.append({
            'symbol': p['symbol'],
            'amt': amt,
            'side': 'LONG' if amt > 0 else 'SHORT',
            'entry': entry,
            'upnl': upnl,
            'pnl_pct': pnl_pct,
            'notional': notional,
        })
    return {'nav': nav, 'avail': avail, 'pnl': pnl, 'positions': positions}

def collect_market():
    """BTC/ETH市场价格"""
    result = {}
    for sym in ['BTCUSDT', 'ETHUSDT']:
        try:
            r = _pub('/fapi/v1/ticker/24hr', {'symbol': sym})
            result[sym] = {
                'price': float(r.get('lastPrice', 0)),
                'pct24h': float(r.get('priceChangePercent', 0)),
                'vol_b': float(r.get('quoteVolume', 0)) / 1e9,
            }
        except Exception:
            result[sym] = {'price': 0, 'pct24h': 0, 'vol_b': 0}
    return result

def collect_system1():
    """System-1: 梵天主信号"""
    f = DATA / 'live_signal_log.jsonl'
    if not f.exists(): return []
    now = time.time()
    TTL = 6 * 3600
    seen = {}
    for line in f.read_text().strip().split('\n'):
        try:
            d = json.loads(line)
            if not d.get('valid'): continue
            age = now - d.get('ts', 0)
            if age > TTL: continue
            sid = d.get('signal_id', '')
            if sid not in seen or d.get('ts',0) > seen[sid].get('ts',0):
                seen[sid] = d
        except Exception:
            pass
    signals = sorted(seen.values(), key=lambda x: -x.get('score', 0))
    return signals[:8]

def collect_system2():
    """System-2: OI高级扫描"""
    f = DATA / 'oi_advanced_signals.jsonl'
    if not f.exists(): return []
    now = time.time()
    TTL = 2 * 3600
    seen = {}
    for line in f.read_text().strip().split('\n'):
        try:
            d = json.loads(line)
            age = now - d.get('ts', 0)
            if age > TTL: continue
            sym = d.get('symbol', '')
            if sym not in seen or d.get('ts',0) > seen[sym].get('ts',0):
                seen[sym] = d
        except Exception:
            pass
    signals = sorted(seen.values(), key=lambda x: -x.get('oi_score', 0))
    return signals[:8]

def collect_system3():
    """System-3: 暴涨猎手"""
    signals = []
    # pump_signal_queue
    f = DATA / 'pump_signal_queue.jsonl'
    if f.exists():
        now = time.time()
        TTL = 24 * 3600
        for line in f.read_text().strip().split('\n'):
            try:
                d = json.loads(line)
                age = now - d.get('ts', 0)
                if age < TTL and d.get('score', 0) >= 75:
                    signals.append(d)
            except Exception:
                pass
    # pump_detected
    f2 = DATA / 'pump_detected.json'
    if f2.exists():
        try:
            d = json.load(open(f2))
            for item in d.get('pumped', []):
                signals.append({
                    'signal_type': 'PUMP_DETECTED',
                    'symbol': item.get('symbol', ''),
                    'score': 80,
                    'direction': 'LONG',
                    'price': item.get('price', 0),
                    'pct24h': item.get('pct24h', 0),
                    'ts': item.get('ts', time.time()),
                    'valid': True,
                })
        except Exception:
            pass
    return sorted(signals, key=lambda x: (-x.get('score',0), -x.get('ts',0)))[:6]

def collect_regime():
    """当前体制"""
    f = DATA / 'brahma_state.json'
    if not f.exists(): return {'regime': 'UNKNOWN', 'last_update': 0}
    try:
        d = json.load(open(f))
        return {'regime': d.get('regime','UNKNOWN'), 'last_update': d.get('last_update',0),
                'btc': d.get('_price_btc',0), 'eth': d.get('_price_eth',0)}
    except Exception:
        return {'regime': 'UNKNOWN', 'last_update': 0}

def collect_wuqu():
    """wuqu持仓SL/TP"""
    f = DATA / 'wuqu_positions.json'
    if not f.exists(): return []
    try:
        d = json.load(open(f))
        return d if isinstance(d, list) else d.get('positions', [])
    except Exception:
        return []

# ─── HTML仪表盘生成 ────────────────────────────────────────────────

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>梵天信号仪表盘</title>
<style>
:root {{
  --bg: #0a0e1a; --card: #111827; --border: #1e293b;
  --text: #e2e8f0; --muted: #64748b; --accent: #6366f1;
  --green: #00e676; --red: #ff4757; --orange: #ff6b35;
  --yellow: #ffd32a; --blue: #38bdf8;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:'SF Pro Display',system-ui,sans-serif;
        font-size:13px; min-height:100vh; }}
.header {{ background:linear-gradient(135deg,#0f172a,#1e1b4b);
           padding:16px 24px; border-bottom:1px solid var(--border);
           display:flex; justify-content:space-between; align-items:center; }}
.header h1 {{ font-size:18px; font-weight:700; letter-spacing:.5px;
               background:linear-gradient(90deg,#818cf8,#38bdf8); -webkit-background-clip:text;
               -webkit-text-fill-color:transparent; }}
.ts {{ color:var(--muted); font-size:11px; }}
.grid-top {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
              gap:8px; padding:12px 16px; }}
.stat-card {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
               padding:12px 16px; }}
.stat-label {{ color:var(--muted); font-size:11px; margin-bottom:4px; }}
.stat-value {{ font-size:22px; font-weight:700; font-variant-numeric:tabular-nums; }}
.stat-sub {{ font-size:11px; color:var(--muted); margin-top:2px; }}
.systems {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr));
             gap:12px; padding:0 16px 16px; }}
.system-card {{ background:var(--card); border:1px solid var(--border); border-radius:12px;
                 overflow:hidden; }}
.system-header {{ padding:12px 16px; border-bottom:1px solid var(--border);
                   display:flex; align-items:center; gap:8px; }}
.system-badge {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; animation:pulse 2s infinite; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
.system-title {{ font-weight:700; font-size:14px; }}
.system-meta {{ color:var(--muted); font-size:11px; margin-left:auto; }}
.signal-list {{ padding:8px 0; }}
.signal-row {{ padding:8px 16px; border-bottom:1px solid #1a2332;
                display:grid; grid-template-columns:120px 60px 80px 80px 1fr;
                gap:8px; align-items:center; transition:background .15s; cursor:default; }}
.signal-row:hover {{ background:#1a2332; }}
.signal-row:last-child {{ border-bottom:none; }}
.sym {{ font-weight:700; font-size:13px; font-family:monospace; }}
.score-badge {{ border-radius:6px; padding:2px 7px; font-size:11px; font-weight:700;
                 text-align:center; white-space:nowrap; }}
.dir {{ font-size:11px; font-weight:600; }}
.timing {{ font-size:11px; }}
.details {{ font-size:10px; color:var(--muted); line-height:1.5; }}
.empty {{ padding:20px 16px; text-align:center; color:var(--muted); font-size:12px; }}
.positions-section {{ padding:0 16px 16px; }}
.pos-card {{ background:var(--card); border:1px solid var(--border); border-radius:12px;
              overflow:hidden; }}
.pos-header {{ padding:12px 16px; border-bottom:1px solid var(--border); font-weight:700;
                font-size:14px; display:flex; align-items:center; gap:8px; }}
.pos-row {{ padding:10px 16px; display:grid;
             grid-template-columns:130px 80px 90px 90px 80px 1fr; gap:8px; align-items:center;
             border-bottom:1px solid #1a2332; }}
.pos-row:last-child {{ border-bottom:none; }}
.regime-pill {{ display:inline-flex; align-items:center; gap:5px;
                 background:#1e293b; border-radius:20px; padding:3px 10px; font-size:12px;
                 border:1px solid var(--border); }}
.up {{ color:var(--green); }} .dn {{ color:var(--red); }}
.neutral {{ color:var(--muted); }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🦞 梵天信号仪表盘</h1>
    <div class="ts">三大信号系统实时感知 · 自动60s刷新 · {ts}</div>
  </div>
  <div style="display:flex;align-items:center;gap:10px;">
    <div class="regime-pill">
      <span style="width:7px;height:7px;border-radius:50%;background:{regime_color};flex-shrink:0;"></span>
      <span style="font-weight:600;">{regime}</span>
    </div>
    <div class="ts" style="text-align:right;">体制更新 {regime_age}</div>
  </div>
</div>

<!-- 顶部统计 -->
<div class="grid-top">
  <div class="stat-card">
    <div class="stat-label">账户NAV</div>
    <div class="stat-value">${nav:.2f}</div>
    <div class="stat-sub">可用 ${avail:.2f}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">未实现盈亏</div>
    <div class="stat-value {pnl_cls}">{pnl_sign}${abs_pnl:.4f}</div>
    <div class="stat-sub">{pnl_pct:+.2f}%</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">BTC</div>
    <div class="stat-value">${btc_price:,.0f}</div>
    <div class="stat-sub {btc_cls}">{btc_pct:+.2f}% 24H</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">ETH</div>
    <div class="stat-value">${eth_price:,.0f}</div>
    <div class="stat-sub {eth_cls}">{eth_pct:+.2f}% 24H</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">主信号(6H有效)</div>
    <div class="stat-value">{s1_count}</div>
    <div class="stat-sub">最高 score={s1_max}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">OI信号(2H)</div>
    <div class="stat-value">{s2_count}</div>
    <div class="stat-sub">最高 {s2_max}</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">猎手预警</div>
    <div class="stat-value">{s3_count}</div>
    <div class="stat-sub">TIGHT压缩监控</div>
  </div>
</div>

<!-- 三大信号系统 -->
<div class="systems">
{system1_html}
{system2_html}
{system3_html}
</div>

<!-- 当前持仓 -->
{positions_html}

</body>
</html>'''

def _regime_color(regime):
    r = (regime or '').upper()
    if 'BULL' in r: return '#00e676'
    if 'BEAR' in r and 'RECOVERY' not in r: return '#ff4757'
    if 'RECOVERY' in r: return '#ffd32a'
    if 'CHOP' in r: return '#aaa'
    return '#aaa'

def build_system1_html(signals):
    if not signals:
        return '<div class="system-card"><div class="system-header"><span class="system-badge" style="background:#aaa"></span><span class="system-title">System-1 · 梵天主信号</span><span class="system-meta">暂无有效信号</span></div><div class="empty">6小时内暂无score≥120信号</div></div>'

    rows = ''
    for s in signals:
        score = s.get('score', 0)
        color, grade = _score_color(score)
        sym = s.get('symbol','?')
        direction = s.get('direction','?')
        dir_color = '#00e676' if direction == 'LONG' else '#ff4757'
        timing = s.get('timing_badge','')
        tc, timing_label = _timing_color(timing)
        age = _age_str(s.get('ts'))
        entry_lo = s.get('entry_lo', s.get('price',0))
        entry_hi = s.get('entry_hi', entry_lo)
        sl_pct = s.get('sl_pct', s.get('sl', 0))
        if isinstance(sl_pct, float) and sl_pct > 1:
            sl_str = f'SL={sl_pct:.1f}%'
        else:
            sl_str = f'SL={sl_pct}'
        rr = s.get('rr1', s.get('rr', 0))
        regime = s.get('regime','')

        rows += f'''<div class="signal-row">
  <div class="sym">{sym}</div>
  <div><span class="score-badge" style="background:{color}22;color:{color};border:1px solid {color}44">{score:.0f}</span></div>
  <div class="dir" style="color:{dir_color}">{'▲' if direction=='LONG' else '▼'} {direction}</div>
  <div class="timing" style="color:{tc}">{timing_label}</div>
  <div class="details">${entry_lo:.4g}~${entry_hi:.4g} {sl_str} RR={rr:.1f} {age}</div>
</div>'''

    top_score = signals[0].get('score',0) if signals else 0
    badge_color = '#ff4757' if top_score >= 155 else '#ff6b35' if top_score >= 138 else '#ffd32a'
    return f'''<div class="system-card">
  <div class="system-header">
    <span class="system-badge" style="background:{badge_color}"></span>
    <span class="system-title">System-1 · 梵天主信号</span>
    <span class="system-meta">35维矩阵 | {len(signals)}条有效</span>
  </div>
  <div class="signal-list">{rows}</div>
</div>'''

def build_system2_html(signals):
    if not signals:
        return '<div class="system-card"><div class="system-header"><span class="system-badge" style="background:#aaa"></span><span class="system-title">System-2 · OI持仓量扫描</span><span class="system-meta">暂无信号</span></div><div class="empty">2小时内暂无OI异动信号</div></div>'

    rows = ''
    for s in signals:
        score = s.get('oi_score', 0)
        color, grade = _score_color(score, is_oi=True)
        sym = s.get('symbol','?')
        direction = s.get('direction','NEUTRAL')
        dir_map = {'LONG_BUILD':'▲多建仓','SHORT_BUILD':'▼空建仓','LONG_UNWIND':'↓多平仓',
                   'SHORT_COVER':'↑空回补','NEUTRAL':'— 中性'}
        dir_label = dir_map.get(direction, direction)
        dir_color = '#00e676' if 'LONG_BUILD' in direction or 'SHORT_COVER' in direction else \
                    '#ff4757' if 'SHORT_BUILD' in direction or 'LONG_UNWIND' in direction else '#aaa'
        age = _age_str(s.get('ts'))
        chg_4h = s.get('chg_4h',0)
        chg_24h = s.get('chg_24h',0)
        whale = s.get('whale_l',0)
        rsi = s.get('rsi_1h',0)
        details_str = f'OI4H={chg_4h:+.1f}% OI24H={chg_24h:+.1f}% 鲸鱼{whale:.0f}% RSI={rsi:.0f}'
        score_details = s.get('score_details', [])
        detail_short = ' | '.join(score_details[:2]) if score_details else ''

        rows += f'''<div class="signal-row">
  <div class="sym">{sym}</div>
  <div><span class="score-badge" style="background:{color}22;color:{color};border:1px solid {color}44">{score:.0f}</span></div>
  <div class="dir" style="color:{dir_color};font-size:10px;">{dir_label}</div>
  <div class="timing" style="color:#aaa;font-size:10px;">{age}</div>
  <div class="details">{detail_short}</div>
</div>'''

    top_score = signals[0].get('oi_score',0) if signals else 0
    badge_color = '#ff4757' if top_score >= 90 else '#ff6b35' if top_score >= 75 else '#ffd32a'
    return f'''<div class="system-card">
  <div class="system-header">
    <span class="system-badge" style="background:{badge_color}"></span>
    <span class="system-title">System-2 · OI持仓量扫描</span>
    <span class="system-meta">多空博弈 | {len(signals)}条活跃</span>
  </div>
  <div class="signal-list">{rows}</div>
</div>'''

def build_system3_html(signals):
    if not signals:
        return '<div class="system-card"><div class="system-header"><span class="system-badge" style="background:#aaa"></span><span class="system-title">System-3 · 暴涨猎手</span><span class="system-meta">暂无预警</span></div><div class="empty">TIGHT压缩监控中 · 97.5%历史胜率</div></div>'

    rows = ''
    for s in signals:
        score = s.get('score',0)
        color = '#ff1744' if score >= 90 else '#ff6b35' if score >= 75 else '#ffd32a'
        sym = s.get('symbol','?')
        sig_type = s.get('signal_type', 'PUMP')
        direction = s.get('direction','LONG')
        dir_color = '#00e676' if direction == 'LONG' else '#ff4757'
        age = _age_str(s.get('ts'))
        price = s.get('price',0)
        entry_lo = s.get('entry_lo', price*0.995)
        entry_hi = s.get('entry_hi', price*1.005)
        sl_pct = s.get('sl_pct',0)
        rr = s.get('rr',0)
        pct24h = s.get('pct24h',0)
        valid = s.get('valid', True)
        valid_mark = '✅' if valid else '⏳'

        rows += f'''<div class="signal-row">
  <div class="sym">{sym} {valid_mark}</div>
  <div><span class="score-badge" style="background:{color}22;color:{color};border:1px solid {color}44">{score}</span></div>
  <div class="dir" style="color:{dir_color}">{'▲' if direction=='LONG' else '▼'} {sig_type[:10]}</div>
  <div class="timing" style="color:#aaa;font-size:10px;">{age}</div>
  <div class="details">${entry_lo:.4g}~${entry_hi:.4g} SL={sl_pct:.1f}% RR={rr:.1f} 24H={pct24h:+.1f}%</div>
</div>'''

    return f'''<div class="system-card">
  <div class="system-header">
    <span class="system-badge" style="background:#ff1744"></span>
    <span class="system-title">System-3 · 暴涨猎手</span>
    <span class="system-meta">TIGHT压缩 97.5% | {len(signals)}条预警</span>
  </div>
  <div class="signal-list">{rows}</div>
</div>'''

def build_positions_html(positions, wuqu):
    if not positions:
        return '<div class="positions-section"><div class="pos-card"><div class="pos-header">📦 当前持仓</div><div class="empty" style="padding:16px;">暂无持仓</div></div></div>'

    # 合并wuqu的SL/TP
    wuqu_map = {p.get('symbol',''): p for p in wuqu}
    rows = ''
    for p in positions:
        sym = p['symbol']
        side = p['side']
        amt = abs(p['amt'])
        entry = p['entry']
        upnl = p['upnl']
        pnl_pct = p['pnl_pct']
        notional = p['notional']
        upnl_color = 'up' if upnl >= 0 else 'dn'
        wq = wuqu_map.get(sym, {})
        sl = wq.get('stop_loss', '—')
        tp = wq.get('take_profit', '—')
        sl_str = f'${sl}' if sl != '—' else '—'
        tp_str = f'${tp}' if tp != '—' else '—'
        side_color = '#00e676' if side == 'LONG' else '#ff4757'

        rows += f'''<div class="pos-row">
  <div class="sym">{sym}</div>
  <div style="color:{side_color};font-weight:700;">{'▲' if side=='LONG' else '▼'} {side}</div>
  <div style="font-family:monospace;font-size:12px;">${entry:.4f}</div>
  <div class="{upnl_color}" style="font-family:monospace;font-size:12px;">{'+' if upnl>=0 else ''}${upnl:.4f}</div>
  <div class="{upnl_color}" style="font-size:12px;">{pnl_pct:+.2f}%</div>
  <div class="details">qty={amt} SL={sl_str} TP={tp_str} ${notional:.2f}名义</div>
</div>'''

    return f'''<div class="positions-section">
<div class="pos-card">
  <div class="pos-header"><span style="color:#38bdf8;">📦</span> 当前持仓 ({len(positions)})</div>
  {rows}
</div>
</div>'''

# ─── 终端输出 ──────────────────────────────────────────────────────

def print_ascii(account, regime_info, s1, s2, s3):
    RED='\033[91m'; GREEN='\033[92m'; YELLOW='\033[93m'
    BLUE='\033[94m'; CYAN='\033[96m'; BOLD='\033[1m'; RESET='\033[0m'

    def score_clr(score):
        if score >= 175: return RED+BOLD
        if score >= 155: return RED
        if score >= 138: return YELLOW
        return RESET

    print(f"\n{BOLD}{BLUE}{'═'*60}{RESET}")
    print(f"{BOLD}{BLUE}  🦞 梵天信号仪表盘  {_now_utc()}{RESET}")
    regime = regime_info.get('regime','?')
    rclr = GREEN if 'BULL' in regime else RED if 'BEAR' in regime and 'RECOVERY' not in regime else YELLOW
    print(f"  体制: {rclr}{BOLD}{regime}{RESET}  NAV={GREEN}${account['nav']:.2f}{RESET}  uPnL={GREEN if account['pnl']>=0 else RED}{account['pnl']:+.4f}{RESET}")
    print(f"{BLUE}{'═'*60}{RESET}")

    print(f"\n{BOLD}{CYAN}【System-1】梵天主信号 (6H有效，score≥120){RESET}")
    if not s1:
        print("  暂无有效信号")
    for sig in s1[:5]:
        score = sig.get('score',0)
        clr = score_clr(score)
        dir_ = sig.get('direction','?')
        dc = GREEN if dir_=='LONG' else RED
        timing = sig.get('timing_badge','')
        age = _age_str(sig.get('ts'))
        entry_lo = sig.get('entry_lo',0)
        sl_pct = sig.get('sl_pct',0)
        rr = sig.get('rr1',0)
        print(f"  {clr}[{score:.0f}]{RESET} {sig['symbol']:18s} {dc}{dir_:5s}{RESET} {timing:15s} ${entry_lo:.4g} SL={sl_pct:.1f}% RR={rr:.1f} {age}")

    print(f"\n{BOLD}{CYAN}【System-2】OI持仓量扫描 (2H活跃){RESET}")
    if not s2:
        print("  暂无OI异动")
    dir_map = {'LONG_BUILD':'▲多建仓','SHORT_BUILD':'▼空建仓','LONG_UNWIND':'↓多平仓','SHORT_COVER':'↑空回补','NEUTRAL':'— 中性'}
    for sig in s2[:5]:
        score = sig.get('oi_score',0)
        clr = RED if score >= 90 else YELLOW if score >= 75 else RESET
        direction = sig.get('direction','?')
        dc = GREEN if 'BUILD' in direction and 'LONG' in direction else RED if 'SHORT_BUILD' in direction else RESET
        dir_label = dir_map.get(direction, direction)
        age = _age_str(sig.get('ts'))
        details = sig.get('score_details',[''])[0] if sig.get('score_details') else ''
        print(f"  {clr}[{score:.0f}]{RESET} {sig['symbol']:18s} {dc}{dir_label:12s}{RESET} {details} {age}")

    print(f"\n{BOLD}{CYAN}【System-3】暴涨猎手 (TIGHT压缩·97.5%胜率){RESET}")
    if not s3:
        print("  暂无预警")
    for sig in s3[:4]:
        score = sig.get('score',0)
        clr = RED+BOLD if score >= 90 else YELLOW
        valid = '✅' if sig.get('valid') else '⏳'
        age = _age_str(sig.get('ts'))
        sl_pct = sig.get('sl_pct',0)
        rr = sig.get('rr',0)
        print(f"  {clr}[{score}]{RESET} {sig['symbol']:18s} {valid} SL={sl_pct:.1f}% RR={rr:.1f} {age}")

    if account['positions']:
        print(f"\n{BOLD}{CYAN}【持仓】{RESET}")
        for p in account['positions']:
            pclr = GREEN if p['upnl'] >= 0 else RED
            print(f"  {p['symbol']:18s} {p['side']:5s} entry=${p['entry']:.4f} uPnL={pclr}{p['upnl']:+.4f}{RESET}")
    print(f"\n{BLUE}{'─'*60}{RESET}\n")

# ─── 主函数 ────────────────────────────────────────────────────────

def build_dashboard(output_html=True):
    print("🔄 采集数据中...")
    account = collect_account()
    market = collect_market()
    regime_info = collect_regime()
    s1 = collect_system1()
    s2 = collect_system2()
    s3 = collect_system3()
    wuqu = collect_wuqu()

    print_ascii(account, regime_info, s1, s2, s3)

    if not output_html:
        return

    btc = market.get('BTCUSDT', {})
    eth = market.get('ETHUSDT', {})
    regime = regime_info.get('regime', 'UNKNOWN')
    regime_age = _age_str(regime_info.get('last_update'))

    pnl = account.get('pnl', 0)
    abs_pnl = abs(pnl)
    pnl_sign = '+' if pnl >= 0 else '-'
    pnl_cls = 'up' if pnl >= 0 else 'dn'
    nav = account.get('nav', 0)
    pnl_pct = (pnl / nav * 100) if nav > 0 else 0

    s1_count = len(s1)
    s1_max = max((x.get('score',0) for x in s1), default=0)
    s2_count = len(s2)
    s2_max = max((x.get('oi_score',0) for x in s2), default=0)
    s3_count = len(s3)

    btc_pct = btc.get('pct24h',0)
    eth_pct = eth.get('pct24h',0)

    html = HTML_TEMPLATE.format(
        ts=_now_utc(),
        regime=regime,
        regime_color=_regime_color(regime),
        regime_age=regime_age,
        nav=account.get('nav',0),
        avail=account.get('avail',0),
        pnl_cls=pnl_cls,
        pnl_sign=pnl_sign,
        abs_pnl=abs_pnl,
        pnl_pct=pnl_pct,
        btc_price=btc.get('price',0),
        btc_pct=btc_pct,
        btc_cls='up' if btc_pct >= 0 else 'dn',
        eth_price=eth.get('price',0),
        eth_pct=eth_pct,
        eth_cls='up' if eth_pct >= 0 else 'dn',
        s1_count=s1_count,
        s1_max=f'{s1_max:.0f}',
        s2_count=s2_count,
        s2_max=f'{s2_max:.0f}',
        s3_count=s3_count,
        system1_html=build_system1_html(s1),
        system2_html=build_system2_html(s2),
        system3_html=build_system3_html(s3),
        positions_html=build_positions_html(account.get('positions',[]), wuqu),
    )

    out_path = BASE / 'brahma_dashboard.html'
    out_path.write_text(html, encoding='utf-8')
    print(f"✅ 仪表盘已生成: {out_path}")
    return str(out_path)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天信号仪表盘')
    parser.add_argument('--no-html', action='store_true', help='仅终端输出')
    args = parser.parse_args()
    build_dashboard(output_html=not args.no_html)


# ─── 线程推送模式 ──────────────────────────────────────────────────

def build_push_message() -> str:
    """生成适合Jarvis线程推送的Markdown仪表盘"""
    account = collect_account()
    market  = collect_market()
    regime  = collect_regime()
    s1      = collect_system1()
    s2      = collect_system2()
    s3      = collect_system3()
    wuqu    = collect_wuqu()

    nav   = account.get('nav', 0)
    pnl   = account.get('pnl', 0)
    avail = account.get('avail', 0)
    btc   = market.get('BTCUSDT', {})
    eth   = market.get('ETHUSDT', {})
    reg   = regime.get('regime', '?')

    def reg_emoji(r):
        if 'BULL' in r: return '🟢'
        if 'BEAR' in r and 'RECOVERY' not in r: return '🔴'
        if 'RECOVERY' in r: return '🟡'
        return '⚪'

    def score_grade(s):
        if s >= 175: return '🔴神级'
        if s >= 155: return '🔴极强'
        if s >= 138: return '🟠强'
        if s >= 120: return '🟡中等'
        return '⚪'

    def oi_grade(s):
        if s >= 90: return '🔴极强'
        if s >= 75: return '🟠强'
        if s >= 60: return '🟡中'
        return '⚪'

    def timing_emoji(t):
        if 'READY' in (t or ''): return '🟢'
        if 'MONITOR' in (t or ''): return '🟡'
        if 'WAIT' in (t or ''): return '🟠'
        return '⚫'

    dir_map = {
        'LONG_BUILD': '▲多建仓', 'SHORT_BUILD': '▼空建仓',
        'LONG_UNWIND': '↓多平仓', 'SHORT_COVER': '↑空回补',
        'NEUTRAL': '— 中性'
    }

    lines = []
    lines.append(f"🦞 **梵天信号仪表盘** · {time.strftime('%m-%d %H:%M UTC', time.gmtime())}")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"{reg_emoji(reg)} 体制 `{reg}` · 更新 {_age_str(regime.get('last_update'))}")
    btc_c = btc.get('pct24h', 0); eth_c = eth.get('pct24h', 0)
    lines.append(f"BTC `${btc.get('price',0):,.0f}` ({btc_c:+.2f}%)  ETH `${eth.get('price',0):,.0f}` ({eth_c:+.2f}%)")
    pnl_emoji = '📈' if pnl >= 0 else '📉'
    lines.append(f"NAV `${nav:.2f}` | 可用 `${avail:.2f}` | uPnL {pnl_emoji} `{pnl:+.4f}`")

    # System-1
    lines.append(f"\n**📊 System-1 · 梵天主信号** ({len(s1)}条 · 6H有效)")
    if not s1:
        lines.append("  暂无有效信号")
    else:
        for sig in s1[:5]:
            score = sig.get('score', 0); grade = score_grade(score)
            sym = sig.get('symbol', '?'); dr = sig.get('direction', '?')
            tm = timing_emoji(sig.get('timing_badge', ''))
            age = _age_str(sig.get('ts'))
            elo = sig.get('entry_lo', sig.get('price', 0))
            ehi = sig.get('entry_hi', elo)
            sl = sig.get('sl_pct', 0); rr = sig.get('rr1', 0)
            lines.append(f"  {grade} `{sym}` {'▲' if dr=='LONG' else '▼'}{dr} {tm} score={score:.0f}")
            lines.append(f"    入场 `${elo:.4g}~${ehi:.4g}` SL={sl:.1f}% RR={rr:.1f} · {age}")

    # System-2
    lines.append(f"\n**📈 System-2 · OI持仓量扫描** ({len(s2)}条 · 2H活跃)")
    if not s2:
        lines.append("  暂无OI异动 (2H内)")
    else:
        for sig in s2[:5]:
            score = sig.get('oi_score', 0); grade = oi_grade(score)
            sym = sig.get('symbol', '?')
            dr = dir_map.get(sig.get('direction', ''), sig.get('direction', ''))
            age = _age_str(sig.get('ts'))
            chg4h = sig.get('chg_4h', 0); whale = sig.get('whale_l', 0)
            lines.append(f"  {grade} `{sym}` {dr} score={score:.0f}")
            lines.append(f"    OI4H={chg4h:+.1f}% 鲸鱼={whale:.0f}% · {age}")

    # System-3
    lines.append(f"\n**🚀 System-3 · 暴涨猎手** ({len(s3)}条预警)")
    if not s3:
        lines.append("  TIGHT压缩监控中 · 暂无预警")
    else:
        for sig in s3[:4]:
            score = sig.get('score', 0)
            grade = '🔴' if score >= 90 else '🟡'
            sym = sig.get('symbol', '?')
            valid = '✅' if sig.get('valid') else '⏳'
            age = _age_str(sig.get('ts'))
            sl = sig.get('sl_pct', 0); rr = sig.get('rr', 0); p24 = sig.get('pct24h', 0)
            lines.append(f"  {grade} `{sym}` {valid} score={score}")
            lines.append(f"    SL={sl:.1f}% RR={rr:.1f} 24H={p24:+.1f}% · {age}")

    # 持仓
    pos = account.get('positions', [])
    wuqu_map = {p.get('symbol', ''): p for p in wuqu}
    if pos:
        lines.append(f"\n**📦 当前持仓** ({len(pos)}个)")
        for p in pos:
            sym = p['symbol']; side = p['side']
            upnl = p['upnl']; pct = p['pnl_pct']
            wq = wuqu_map.get(sym, {})
            sl = wq.get('stop_loss', '—'); tp = wq.get('take_profit', '—')
            pe = '📈' if upnl >= 0 else '📉'
            lines.append(f"  {pe} `{sym}` {side} entry=`${p['entry']:.4f}`")
            lines.append(f"    uPnL=`{upnl:+.4f}` ({pct:+.2f}%) | SL=${sl} TP=${tp}")
    else:
        lines.append(f"\n**📦 当前持仓** 无")

    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🔄 每30min推送 · 梵天信号仪表盘")
    return '\n'.join(lines)


if __name__ == '__main__' and '--push' in sys.argv:
    # --push 模式：直接推送到Jarvis线程
    try:
        from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_CHANNEL
        target = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
        channel = JARVIS_CHANNEL
    except Exception:
        target = '73295708:thread:019f8768-6731-777d-8924-2426a5abd10f'
        channel = 'jarvis'

    msg = build_push_message()
    import subprocess
    subprocess.run([
        'openclaw', 'message', 'send',
        '--channel', channel,
        '--to', target,
        '--message', msg,
    ], timeout=15)
    print('[dashboard] 推送完成')
