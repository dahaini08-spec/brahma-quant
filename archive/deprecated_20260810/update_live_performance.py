#!/usr/bin/env python3
"""
update_live_performance.py — 每日自动更新 brahma-quant/LIVE_PERFORMANCE.md
设计院彻底修复 2026-07-24 · 苏摩111封印

根因修复：
  旧版 POSITIONS 列表为硬编码静态数据 → 过期数据长期残留
  修复后：
    - 活跃持仓：实时从 Binance FAPI /positionRisk 拉取（零硬编码）
    - 已平仓记录：从 pipeline_exec_log.jsonl 的 live_open + live_close 对 构建
    - 兜底：若 pipeline_exec_log 无平仓记录，保留历史 CLOSED_HISTORY
"""
import sys, os, json, subprocess, hmac, hashlib, time, requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

LIVE_MD_PATH = Path.home() / '.openclaw/workspace/brahma-quant/LIVE_PERFORMANCE.md'
TRADING_DIR  = Path(__file__).parent.parent
EXEC_LOG     = TRADING_DIR / 'data/pipeline_exec_log.jsonl'

# ── 历史已平仓（pipeline_exec_log 建立之前的记录，永久保留）──────────────
CLOSED_HISTORY = [
    {'n':1,'sym':'BTCUSDT',  'dir':'SHORT','entry':60094,   'exit':58603,  'pnl':2.48,  'dur':'14h','score':152,'regime':'BEAR_TREND'},
    {'n':2,'sym':'GALAUSDT', 'dir':'SHORT','entry':0.00268, 'exit':0.00228,'pnl':14.87, 'dur':'3d', 'score':163,'regime':'BEAR_TREND'},
    {'n':3,'sym':'PIXELUSDT','dir':'SHORT','entry':0.00543, 'exit':0.00455,'pnl':16.24, 'dur':'4d', 'score':158,'regime':'BEAR_TREND'},
    {'n':4,'sym':'BNBUSDT',  'dir':'SHORT','entry':570.6,   'exit':543.34, 'pnl':4.78,  'dur':'2d', 'score':147,'regime':'BEAR_TREND'},
]

def get_api_creds():
    from system_config import API_KEY, API_SECRET, FAPI_BASE
    return API_KEY, API_SECRET, FAPI_BASE

def fetch_live_positions():
    """从 Binance FAPI 实时拉取所有活跃合约持仓"""
    try:
        api_key, api_secret, fapi_base = get_api_creds()
        ts = int(time.time() * 1000)
        params = f'timestamp={ts}'
        sig = hmac.new(api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
        r = requests.get(
            f'{fapi_base}/fapi/v2/positionRisk',
            headers={'X-MBX-APIKEY': api_key},
            params={'timestamp': ts, 'signature': sig},
            timeout=8
        )
        r.raise_for_status()
        positions = []
        for p in r.json():
            amt = float(p.get('positionAmt', 0))
            if abs(amt) < 1e-9:
                continue
            entry  = float(p['entryPrice'])
            mark   = float(p['markPrice'])
            upnl   = float(p['unRealizedProfit'])
            liq    = float(p.get('liquidationPrice', 0))
            direction = 'LONG' if amt > 0 else 'SHORT'
            pnl_pct = (mark - entry) / entry * 100 if direction == 'LONG' else (entry - mark) / entry * 100
            positions.append({
                'symbol':    p['symbol'],
                'direction': direction,
                'entry':     entry,
                'mark':      mark,
                'upnl':      upnl,
                'pnl_pct':   pnl_pct,
                'liq':       liq,
                'leverage':  float(p.get('leverage', 1)),
            })
        return positions, None
    except Exception as e:
        return [], str(e)

def load_closed_from_log():
    """从 pipeline_exec_log.jsonl 读取 live_open / live_close 配对，构建已平仓记录"""
    if not EXEC_LOG.exists():
        return []
    opens  = {}
    closes = []
    for line in EXEC_LOG.read_text().strip().splitlines():
        try:
            d = json.loads(line)
            t = d.get('type', '')
            sym = d.get('symbol', '')
            if t == 'live_open':
                opens[sym] = d
            elif t == 'live_close':
                closes.append((sym, d))
        except Exception:
            continue
    closed = []
    for sym, c in closes:
        o = opens.get(sym, {})
        entry     = float(o.get('entry', c.get('entry', 0)))
        exit_p    = float(c.get('exit', c.get('exit_price', 0)))
        direction = o.get('direction', c.get('direction', 'LONG'))
        if direction == 'LONG':
            pnl_pct = (exit_p - entry) / entry * 100 if entry else 0
        else:
            pnl_pct = (entry - exit_p) / entry * 100 if entry else 0
        ts_str = c.get('ts', '')[:10]
        dur    = c.get('duration', '?')
        score  = o.get('score', c.get('score', '?'))
        regime = o.get('regime', c.get('regime', '?'))
        closed.append({
            'sym': sym, 'dir': direction,
            'entry': entry, 'exit': exit_p,
            'pnl': pnl_pct, 'dur': dur,
            'score': score, 'regime': regime,
            'date': ts_str,
        })
    return closed

def fmt_price(p):
    if p == 0:
        return '$0'
    if p < 0.01:
        return f'${p:.6f}'
    if p < 1:
        return f'${p:.4f}'
    if p < 100:
        return f'${p:.2f}'
    return f'${p:,.2f}'

def main():
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # ── 1. 实时持仓 ────────────────────────────────────────────────────────
    live_positions, err = fetch_live_positions()

    if err:
        active_section = f'> ⚠️ API拉取失败: {err}\n'
    elif not live_positions:
        active_section = '> 当前无活跃持仓（空仓）\n'
    else:
        rows = []
        for p in live_positions:
            emoji = '✅' if p['pnl_pct'] > 0 else '⚠️' if p['pnl_pct'] < -5 else '⏳'
            pnl_str = f'**{p["pnl_pct"]:+.2f}%** {emoji}'
            rows.append(
                f'| {p["symbol"]} | {p["direction"]} | {fmt_price(p["entry"])} | {fmt_price(p["mark"])} | {pnl_str} | - | - |'
            )
        avg_pnl = sum(p['pnl_pct'] for p in live_positions) / len(live_positions)
        active_section = (
            '| Symbol | Direction | Entry | Current | PnL | Signal Score | Regime |\n'
            '|--------|-----------|-------|---------|-----|-------------|--------|\n'
            + '\n'.join(rows)
            + f'\n\n*Avg active PnL: {avg_pnl:+.2f}%*\n'
        )

    # ── 2. 已平仓：log 记录 + 历史记录合并 ──────────────────────────────────
    log_closed  = load_closed_from_log()
    all_closed  = list(CLOSED_HISTORY)  # 历史记录始终保留

    # log 里的平仓追加（不与历史重复）
    history_syms = {c['sym'] for c in CLOSED_HISTORY}
    for c in log_closed:
        if c['sym'] not in history_syms:
            n = len(all_closed) + 1
            all_closed.append({
                'n': n, 'sym': c['sym'], 'dir': c['dir'],
                'entry': c['entry'], 'exit': c['exit'],
                'pnl': c['pnl'], 'dur': c['dur'],
                'score': c['score'], 'regime': c['regime'],
            })

    closed_rows = []
    for c in all_closed:
        pnl_val  = c['pnl']
        pnl_str  = f'**{pnl_val:+.2f}%** {"✅" if pnl_val > 0 else "❌"}'
        score_s  = str(c.get('score', '?'))
        regime_s = str(c.get('regime', '?'))
        closed_rows.append(
            f'| {c["n"]} | {c["sym"]} | {c["dir"]} | {fmt_price(c["entry"])} | {fmt_price(c["exit"])} | {pnl_str} | {c["dur"]} | {score_s} | {regime_s} |'
        )
    n_win   = sum(1 for c in all_closed if c['pnl'] > 0)
    avg_cls = sum(c['pnl'] for c in all_closed) / len(all_closed) if all_closed else 0
    closed_section = (
        '| # | Symbol | Dir | Entry | Exit | PnL | Duration | Score | Regime |\n'
        '|---|--------|-----|-------|------|-----|----------|-------|--------|\n'
        + '\n'.join(closed_rows)
        + f'\n\n**Closed: {n_win}/{len(all_closed)} profitable | Avg: {avg_cls:+.2f}%**\n'
    )

    # ── 3. 写 LIVE_PERFORMANCE.md ─────────────────────────────────────────
    content = f"""# 🏆 Brahma Live Performance Tracker

> **Real trades. Real money. Autonomous signals. Zero manual intervention.**
>
> All positions opened by Brahma Pro autonomously. Updated daily by cron.
> ⚠️ Active positions pulled from Binance API in real-time (no hardcoded data).

---

## 📊 Active Positions ({now_str})

{active_section}
---

## 📈 Closed Trades — 2026 Track Record

{closed_section}
---

## 🧠 How Brahma Generates These Signals

```
Market Data (Binance FAPI)
    ↓
35-Dimensional Confluence Scoring
    ↓
Regime Filter (5-state machine)
  → BEAR_TREND: SHORT multiplier = 1.60x
  → CHOP_MID:  EV = -0.11%/trade → SKIP
    ↓
6-Agent Joint Review (debate gate)
    ↓
timing_filter (3-layer entry timing)
    ↓
Kronos p_up forecast
    ↓
Signal Card → Auto Executor → Binance
```

---

## 📉 Risk Management Rules (Hard-coded)

| Rule | Value | Reason |
|------|-------|--------|
| BEAR_TREND_LONG | ❌ BLOCKED | WR=45% death zone |
| StructureGate | grade≥80 required | WR=47% zone protection |
| GapGate | gap>0.5% = stale | Entry timing precision |
| Max position | 5% NAV | Kelly-adjusted |

---

*Last updated: {now_str}*

---

*[← Back to README](README.md) · [Dharma Proof →](DHARMA_PROOF.md)*
"""

    LIVE_MD_PATH.write_text(content)
    print(f'[live-perf] ✅ LIVE_PERFORMANCE.md updated ({now_str}) | live_pos={len(live_positions)} closed={len(all_closed)}')

    # git commit
    try:
        repo = str(LIVE_MD_PATH.parent)
        subprocess.run(['git', '-C', repo, 'add', 'LIVE_PERFORMANCE.md'], check=True)
        subprocess.run(['git', '-C', repo, 'commit', '-m',
                        f'perf: auto-update live performance {datetime.now(timezone.utc).strftime("%Y-%m-%d")}'],
                       check=True, capture_output=True)
        print('[live-perf] ✅ git committed')
    except subprocess.CalledProcessError as e:
        print(f'[live-perf] git: {e}')

if __name__ == '__main__':
    main()
