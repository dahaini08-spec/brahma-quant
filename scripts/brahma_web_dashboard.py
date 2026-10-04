#!/usr/bin/env python3
"""
梵天Web仪表盘 — 手机浏览器访问版
[2026-09-22 苏摩111]

启动: python3 scripts/brahma_web_dashboard.py --port 8899
手机访问: http://<服务器IP>:8899/?token=梵天

安全: 简单token认证，不暴露无密码
"""

import json, time, os, sys, re, subprocess, html
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

_BASE = Path(__file__).parent.parent
_DATA = _BASE / 'data'
_LATEST = _DATA / 'auto_analysis_latest.json'
_SIGNAL_LOG = _DATA / 'live_signal_log.jsonl'
_TOKEN = '梵天'
_PORT = 8899

def _sf(v, d=0):
    try: return float(v) if v is not None else d
    except Exception: return d

def load_latest():
    if not _LATEST.exists(): return {}
    try: return json.loads(_LATEST.read_text())
    except Exception: return {}

def load_signals(n=10):
    if not _SIGNAL_LOG.exists(): return []
    signals = []
    for line in reversed(_SIGNAL_LOG.read_text().strip().split('\n')):
        if len(signals) >= n: break
        try: signals.append(json.loads(line))
        except Exception: pass
    return signals

def count_signals():
    if not _SIGNAL_LOG.exists(): return 0
    return sum(1 for l in _SIGNAL_LOG.read_text().strip().split('\n') if l.strip())

def check_procs():
    procs = {'supercronic': False, 'cvd': False, 'liqmap': False, 'watchdog': False}
    try:
        out = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5).stdout
        if 'supercronic' in out: procs['supercronic'] = True
        if 'cvd_ws_collector' in out: procs['cvd'] = True
        if 'liq_multi_exchange' in out or 'liqmap' in out: procs['liqmap'] = True
        if 'independent_watchdog' in out: procs['watchdog'] = True
    except Exception: pass
    return procs

def parse_output(d):
    out = d.get('output', '')
    results = {}
    for sym in ['BTC', 'ETH']:
        marker = f'{sym}/USDT'
        idx = out.find(marker)
        if idx < 0:
            results[sym] = {'price': 0, 'score': 0, 'regime': '?', 'hurst': 0,
                           'direction': '?', 'action': '?', 'cross_val': '?',
                           'liq_short': 0, 'liq_long': 0, 'fvg': 0}
            continue
        if sym == 'BTC':
            next_idx = out.find('ETH/USDT', idx + 1)
            section = out[idx:next_idx] if next_idx > 0 else out[idx:idx+5000]
        else:
            section = out[idx:idx+5000]
        price_m = re.search(r'\$([\d,]+\.?\d*)', section[:200])
        score_m = re.search(r'score[=: ]+(\d+\.?\d*)', section)
        regime_m = re.search(r'(CHOP_MID|BULL_TREND|BEAR_TREND|BEAR_EARLY|BULL_EARLY|BEAR_RECOVERY)', section)
        hurst_m = re.search(r'Hurst[=: ]*0\.(\d+)', section) or re.search(r'H=(\d\.\d+)', section)
        dir_m = re.search(r'方向[=: ]*(LONG|SHORT)', section)
        act_m = re.search(r'(ENTER|WATCH|EXECUTE|REJECT)', section)
        cv_m = re.search(r'(\d)/4', section)
        ls_m = re.search(r'止损墙[：: ]*\$([\d,]+)', section)
        ll_m = re.search(r'支撑池[：: ]*\$([\d,]+)', section)
        fvg_m = re.search(r'FVG磁铁[：: ]*\$([\d,]+)', section) or re.search(r'主磁铁.*?\$([\d,]+)', section)
        results[sym] = {
            'price': _sf(price_m.group(1).replace(',','')) if price_m else 0,
            'score': _sf(score_m.group(1)) if score_m else 0,
            'regime': regime_m.group(1) if regime_m else '?',
            'hurst': float('0.' + hurst_m.group(1)) if hurst_m else 0,
            'direction': dir_m.group(1) if dir_m else '?',
            'action': act_m.group(1) if act_m else '?',
            'cross_val': cv_m.group(1) if cv_m else '?',
            'liq_short': _sf(ls_m.group(1).replace(',','')) if ls_m else 0,
            'liq_long': _sf(ll_m.group(1).replace(',','')) if ll_m else 0,
            'fvg': _sf(fvg_m.group(1).replace(',','')) if fvg_m else 0,
        }
    return results

def render_html():
    d = load_latest()
    signals = load_signals(10)
    procs = check_procs()
    parsed = parse_output(d)
    btc = parsed.get('BTC', {})
    eth = parsed.get('ETH', {})
    proc_count = sum(1 for v in procs.values() if v)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

    def card_html(sym, data):
        action = data.get('action', '?')
        color = '#00ff88' if action == 'ENTER' else '#ffcc00' if action == 'WATCH' else '#ff4444'
        return f'''
        <div class="card">
            <div class="card-title">{sym}</div>
            <div class="card-price">${data.get('price', 0):,.0f}</div>
            <div class="card-row"><span class="label">体制</span><span>{data.get('regime', '?')}</span></div>
            <div class="card-row"><span class="label">score</span><span class="bold">{data.get('score', 0):.0f}</span></div>
            <div class="card-row"><span class="label">Hurst</span><span>{data.get('hurst', 0):.3f}</span></div>
            <div class="card-row"><span class="label">方向</span><span>{data.get('direction', '?')}</span></div>
            <div class="card-row"><span class="label">交叉</span><span>{data.get('cross_val', '?')}/4</span></div>
            <div class="card-row"><span class="label">决策</span><span class="bold" style="color:{color}">{action}</span></div>
        </div>'''

    def level_html(sym, data):
        price = data.get('price', 0)
        ls = data.get('liq_short', 0)
        ll = data.get('liq_long', 0)
        fvg = data.get('fvg', 0)
        rows = f'<div class="level-sym">{sym} ${price:,.0f}</div>'
        if ls:
            pct = (ls - price) / price * 100 if price else 0
            rows += f'<div class="level-row"><span class="label">止损墙</span><span style="color:#ff4444">${ls:,.0f} ({pct:+.1f}%)</span></div>'
        if ll:
            pct = (ll - price) / price * 100 if price else 0
            rows += f'<div class="level-row"><span class="label">支撑池</span><span style="color:#00ff88">${ll:,.0f} ({pct:+.1f}%)</span></div>'
        if fvg:
            rows += f'<div class="level-row"><span class="label">FVG磁铁</span><span style="color:#ff00ff">${fvg:,.0f}</span></div>'
        return f'<div class="card">{rows}</div>'

    # 信号表格
    sig_rows = ''
    for s in signals:
        ts = s.get('ts_iso', '?')[:16]
        sym = s.get('symbol', '?')[:6]
        d_dir = s.get('direction', '?')[:5]
        sc = f'{_sf(s.get("score", 0)):.0f}'
        rg = s.get('regime', '?')[:11]
        act = s.get('action', '?')[:7]
        feat = str(len(s.get('features', {})))
        sig_rows += f'<tr><td>{ts}</td><td>{sym}</td><td>{d_dir}</td><td class="bold">{sc}</td><td>{rg}</td><td>{act}</td><td class="dim">{feat}</td></tr>'

    proc_html = ''
    for name, ok in procs.items():
        proc_html += f'<div class="proc-row"><span class="{"ok" if ok else "bad"}">{"✅" if ok else "❌"}</span> {name}</div>'

    nano_count = sum(1 for s in signals if s.get('features'))

    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>梵天仪表盘</title>
<meta http-equiv="refresh" content="10">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0a0a; color:#e0e0e0; font-family:monospace; padding:12px; max-width:900px; margin:0 auto; }}
.header {{ text-align:center; padding:12px; border:1px solid #00ffff; border-radius:8px; margin-bottom:12px; }}
.header h1 {{ color:#00ffff; font-size:18px; }}
.header .time {{ color:#888; font-size:12px; }}
.grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:12px; }}
.card {{ background:#111; border:1px solid #333; border-radius:8px; padding:12px; }}
.card-title {{ color:#00ffff; font-size:14px; margin-bottom:8px; }}
.card-price {{ font-size:20px; font-weight:bold; margin-bottom:8px; }}
.card-row {{ display:flex; justify-content:space-between; padding:3px 0; font-size:13px; }}
.label {{ color:#888; }}
.bold {{ font-weight:bold; }}
.dim {{ color:#666; }}
.section {{ background:#111; border:1px solid #333; border-radius:8px; padding:12px; margin-bottom:12px; }}
.section-title {{ color:#00ffff; font-size:14px; margin-bottom:8px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ color:#888; text-align:left; padding:4px; border-bottom:1px solid #333; }}
td {{ padding:4px; border-bottom:1px solid #222; }}
.proc-row {{ padding:2px 0; font-size:13px; }}
.ok {{ color:#00ff88; }} .bad {{ color:#ff4444; }}
@media(max-width:600px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="header">
    <h1>🏛️ 梵天系统仪表盘</h1>
    <div class="time">{now} | 10s自动刷新</div>
</div>
<div class="grid">
    {card_html('BTC', btc)}
    {card_html('ETH', eth)}
    <div class="card">
        <div class="card-title">系统状态</div>
        <div class="card-row"><span class="label">进程</span><span class="bold" style="color:{"#00ff88" if proc_count==4 else "#ff4444"}">{proc_count}/4</span></div>
        {proc_html}
        <div class="card-row" style="margin-top:8px"><span class="label">信号总数</span><span>{count_signals()}</span></div>
        <div class="card-row"><span class="label">耗时</span><span>{d.get('elapsed_s', '?')}s</span></div>
    </div>
</div>
<div class="section">
    <div class="section-title">📊 最近10条信号</div>
    <table>
        <tr><th>时间</th><th>标的</th><th>方向</th><th>score</th><th>体制</th><th>action</th><th>特征</th></tr>
        {sig_rows}
    </table>
</div>
<div class="grid">
    <div class="section" style="margin-bottom:0">
        <div class="section-title">🎯 关键价位</div>
        {level_html('BTC', btc)}
        {level_html('ETH', eth)}
    </div>
    <div class="section" style="margin-bottom:0">
        <div class="section-title">🧠 交易员大脑</div>
        <div class="card-row"><span class="label">BTC</span><span>{btc.get('action','?')} {btc.get('direction','?')} {btc.get('cross_val','?')}/4</span></div>
        <div class="card-row"><span class="label">ETH</span><span>{eth.get('action','?')} {eth.get('direction','?')} {eth.get('cross_val','?')}/4</span></div>
        <div class="card-row" style="margin-top:8px"><span class="label">NanoJev</span><span style="color:#00ffff">{nano_count}条带特征</span></div>
    </div>
</div>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        urlpath = parsed.path

        # [2026-10-04] /reports/ 静态文件路由（无需token，直接访问HTML报告）
        if urlpath.startswith('/reports/'):
            fname = urlpath[len('/reports/'):]
            fpath = _BASE / 'reports' / fname
            if fpath.exists() and fpath.suffix in ('.html', '.json', '.txt'):
                data = fpath.read_bytes()
                ct = 'text/html; charset=utf-8' if fpath.suffix == '.html' else 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'404 Not Found')
            return

        token = params.get('token', [''])[0]
        if token != _TOKEN:
            self.send_response(403)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write('<h1>403 Forbidden</h1><p>请加 ?token=梵天</p>'.encode())
            return
        html_content = render_html()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode())

    def log_message(self, *args):
        pass  # 静默日志


def main():
    port = _PORT
    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        port = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else _PORT
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'🏛️ 梵天Web仪表盘启动')
    print(f'📱 手机访问: http://<服务器IP>:{port}/?token=梵天')
    print(f'🔑 token: {_TOKEN}')
    print(f'🔄 10秒自动刷新')
    print(f'Ctrl+C退出')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n退出')

if __name__ == '__main__':
    main()
