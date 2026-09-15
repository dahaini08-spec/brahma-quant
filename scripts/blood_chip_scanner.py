#!/usr/bin/env python3
"""
blood_chip_scanner.py — 带血筹码监控器
[2026-09-15 苏摩111] 把"找带血筹码"变成长期运行的流程

触发条件：
  1. 折价率 ≤ -20%（当前价 vs 1年高点）
  2. vs 200日均价 ≤ -5%（跌破均线，不是只是从高点回落）
  3. Crypto恐慌指数 ≤ 25（Extreme Fear）→ 触发深度研究

运行频率：每12h（OpenClaw cron 06:00 UTC + 18:00 UTC）
输出：
  - 普通模式：简报（折价+恐慌指数+观察池状态）
  - 触发模式：深度研究报告（买入理由+反对理由+UDTV四维）

数据源：
  - Crypto价格：Binance API（/api/v3/klines）
  - Crypto恐慌：Alternative.me Fear & Greed Index
  - 美股价格：Yahoo Finance API（如可用）/ web_search fallback
  - 美股恐慌：CNN Fear & Greed Index（如可用）

接入位置：
  - OpenClaw cron → 每12h运行 → 推送到Jarvis
  - 手动触发：python3 scripts/blood_chip_scanner.py
"""

import sys, os, json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
WATCHLIST = DATA / 'watchlist.json'

# ── 数据获取 ────────────────────────────────────────────────

def fetch_binance_klines(symbol: str, interval: str = '1d', limit: int = 365) -> list:
    """从Binance API获取K线数据"""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[blood_chip] Binance API error for {symbol}: {e}", file=sys.stderr)
        return []

def fetch_crypto_fear_greed() -> dict:
    """从Alternative.me获取Crypto恐慌贪婪指数 — v2增加历史数据"""
    url = "https://api.alternative.me/fng/?limit=7"  # 取7天历史
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            v = data['data'][0]
            history = data['data'][1:]
            return {
                'value': int(v['value']),
                'classification': v['value_classification'],
                'timestamp': v['timestamp'],
                'history': [{'value': int(d['value']), 'classification': d['value_classification']} for d in history]
            }
    except Exception as e:
        print(f"[blood_chip] Fear&Greed API error: {e}", file=sys.stderr)
        return {'value': -1, 'classification': 'UNKNOWN', 'history': [], 'error': str(e)}

def fetch_yahoo_stock(symbol: str) -> dict:
    """从Yahoo Finance获取美股数据"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            result = data['chart']['result'][0]
            quotes = result['indicators']['quote'][0]
            closes = [c for c in quotes['close'] if c is not None]
            if not closes:
                return {}
            current = closes[-1]
            high_1y = max(closes)
            avg_200d = sum(closes[-200:]) / len(closes[-200:]) if len(closes) >= 200 else sum(closes) / len(closes)
            return {
                'symbol': symbol,
                'current': current,
                'high_1y': high_1y,
                'avg_200d': avg_200d,
                'discount_pct': ((current - high_1y) / high_1y * 100),
                'vs_200d_pct': ((current - avg_200d) / avg_200d * 100),
                'source': 'yahoo'
            }
    except Exception as e:
        print(f"[blood_chip] Yahoo Finance error for {symbol}: {e}", file=sys.stderr)
        return {}

# ── 核心逻辑 ────────────────────────────────────────────────

def analyze_crypto_asset(symbol: str) -> dict:
    """分析单个Crypto资产 — v2增加2年最大回撤"""
    klines = fetch_binance_klines(symbol, '1d', 730)  # 2年
    if not klines or not isinstance(klines, list) or len(klines) < 30:
        return {'symbol': symbol, 'error': 'insufficient data'}
    
    closes = [float(k[4]) for k in klines]
    current = closes[-1]
    high_1y = max(closes[-365:]) if len(closes) >= 365 else max(closes)
    high_2y = max(closes)
    avg_200d = sum(closes[-200:]) / len(closes[-200:]) if len(closes) >= 200 else sum(closes) / len(closes)
    low_2y = min(closes[-730:]) if len(closes) >= 730 else min(closes)
    discount_pct = ((current - high_1y) / high_1y * 100)
    vs_200d_pct = ((current - avg_200d) / avg_200d * 100)
    max_drawdown_2y = ((low_2y - high_2y) / high_2y * 100)  # 2年最大回撤
    
    return {
        'symbol': symbol,
        'current': current,
        'high_1y': high_1y,
        'high_2y': high_2y,
        'low_2y': low_2y,
        'avg_200d': avg_200d,
        'discount_pct': round(discount_pct, 2),
        'vs_200d_pct': round(vs_200d_pct, 2),
        'max_drawdown_2y': round(max_drawdown_2y, 2),
        'source': 'binance',
        'data_points': len(closes)
    }

def check_triggers(asset: dict, fear_greed: dict, rules: dict) -> dict:
    """检查触发条件 — v2增加先行指标"""
    triggers = []
    
    if 'discount_pct' in asset:
        if asset['discount_pct'] <= rules.get('discount_threshold', -20):
            triggers.append(f"折价{asset['discount_pct']:.1f}% ≤ {rules['discount_threshold']}%阈值")
    
    if 'vs_200d_pct' in asset:
        if asset['vs_200d_pct'] <= rules.get('vs_200d_threshold', -5):
            triggers.append(f"vs200d={asset['vs_200d_pct']:+.1f}% ≤ {rules['vs_200d_threshold']}%阈值")
    
    fg_value = fear_greed.get('value', -1)
    if fg_value > 0 and fg_value <= rules.get('fear_greed_extreme', 25):
        triggers.append(f"恐慌指数{fg_value} ≤ {rules['fear_greed_extreme']}(Extreme Fear)")
    elif fg_value > 0 and fg_value <= rules.get('fear_greed_fear', 45):
        triggers.append(f"恐慌指数{fg_value} ≤ {rules['fear_greed_fear']}(Fear)")
    
    # v2先行指标：折价百分位（当前折价 vs 2年最大回撤）
    if 'discount_pct' in asset and 'max_drawdown_2y' in asset:
        max_dd = asset['max_drawdown_2y']
        if max_dd < 0:
            percentile = asset['discount_pct'] / max_dd * 100  # 0=高点, 100=最低点
            asset['drawdown_percentile'] = round(percentile, 1)
            if percentile > 80:
                triggers.append(f"回撤百分位{percentile:.0f}% > 80%（接近2年最低）")
    
    # v2先行指标：恐慌持续时间
    fg_history = fear_greed.get('history', [])
    if fg_history:
        consecutive_fear = sum(1 for d in fg_history if d.get('value', 100) <= 25)
        if consecutive_fear >= 3:
            triggers.append(f"恐慌持续{consecutive_fear}天（连续Extreme Fear）")
    
    return {
        'triggered': len(triggers) > 0,
        'triggers': triggers,
        'needs_deep_research': fg_value > 0 and fg_value <= rules.get('fear_greed_extreme', 25)
    }

def generate_report(watchlist: dict, crypto_results: list, stock_results: list, 
                    fear_greed: dict, trigger_results: dict) -> str:
    """生成报告"""
    beijing = timezone(timedelta(hours=8))
    now = datetime.now(beijing)
    
    lines = []
    lines.append(f"🩸 带血筹码扫描 | {now.strftime('%m-%d %H:%M')} 北京时间")
    lines.append("")
    
    # 恐慌指数
    fg = fear_greed
    fg_emoji = "🟢" if fg['value'] > 55 else "🟡" if fg['value'] > 45 else "🟠" if fg['value'] > 25 else "🔴"
    lines.append(f"市场情绪: {fg_emoji} Crypto恐慌指数 = {fg['value']} ({fg['classification']})")
    lines.append("")
    
    # Crypto观察池
    lines.append("━━━ Crypto 观察池 ━━━")
    for r in crypto_results:
        if 'error' in r:
            lines.append(f"  ❌ {r['symbol']}: {r['error']}")
            continue
        disc = r['discount_pct']
        v200d = r['vs_200d_pct']
        disc_emoji = "🩸" if disc <= -20 else "⚠️" if disc <= -10 else "🟢"
        lines.append(f"  {disc_emoji} {r['symbol']}: ${r['current']:,.2f}")
        lines.append(f"     1y高=${r['high_1y']:,.2f} 折价={disc:+.1f}% | 200d均=${r['avg_200d']:,.2f} vs200d={v200d:+.1f}%")
    
    # 美股观察池
    if stock_results:
        lines.append("")
        lines.append("━━━ 美股观察池 ━━━")
        for r in stock_results:
            if 'error' in r or not r:
                lines.append(f"  ❌ {r.get('symbol','?')}: 数据获取失败")
                continue
            disc = r['discount_pct']
            v200d = r['vs_200d_pct']
            disc_emoji = "🩸" if disc <= -20 else "⚠️" if disc <= -10 else "🟢"
            lines.append(f"  {disc_emoji} {r['symbol']}: ${r['current']:,.2f}")
            lines.append(f"     1y高=${r['high_1y']:,.2f} 折价={disc:+.1f}% | 200d均=${r['avg_200d']:,.2f} vs200d={v200d:+.1f}%")
    
    # 触发条件
    all_triggers = []
    for sym, tr in trigger_results.items():
        if tr['triggered']:
            all_triggers.extend([(sym, t) for t in tr['triggers']])
    
    lines.append("")
    if all_triggers:
        lines.append("━━━ ⚠️ 触发关注 ━━━")
        for sym, trigger in all_triggers:
            lines.append(f"  🔔 {sym}: {trigger}")
        
        deep = any(tr.get('needs_deep_research', False) for tr in trigger_results.values())
        if deep:
            lines.append("")
            lines.append("  📌 恐慌区间已触发，建议进入深度研究（第三步）")
    else:
        lines.append("━━━ ✅ 无触发 | 继续等待 ━━━")
    
    lines.append("")
    lines.append("📊 梵天带血筹码监控器 | 不是建议")
    
    return "\n".join(lines)

# ── 主入口 ────────────────────────────────────────────────

def main():
    # 加载观察池
    if not WATCHLIST.exists():
        print(f"[blood_chip] watchlist.json not found at {WATCHLIST}", file=sys.stderr)
        return
    
    watchlist = json.loads(WATCHLIST.read_text())
    rules = watchlist.get('trigger_rules', {})
    
    crypto_assets = watchlist.get('crypto', [])
    stock_assets = watchlist.get('us_stocks', [])
    
    # 并行获取数据
    crypto_results = []
    for asset in crypto_assets:
        sym = asset.get('binance_symbol') or asset.get('symbol')
        r = analyze_crypto_asset(sym)
        r['name'] = asset.get('name', sym)
        r['notes'] = asset.get('notes', '')
        crypto_results.append(r)
    
    stock_results = []
    for asset in stock_assets:
        sym = asset.get('symbol')
        r = fetch_yahoo_stock(sym)
        if r:
            r['name'] = asset.get('name', sym)
            r['notes'] = asset.get('notes', '')
        else:
            r = {'symbol': sym, 'error': 'yahoo api blocked', 'name': asset.get('name', sym)}
        stock_results.append(r)
    
    # 恐慌指数
    fear_greed = fetch_crypto_fear_greed()
    
    # 检查触发条件
    trigger_results = {}
    all_results = crypto_results + stock_results
    for r in all_results:
        if 'error' not in r:
            trigger_results[r['symbol']] = check_triggers(r, fear_greed, rules)
    
    # 生成报告
    report = generate_report(watchlist, crypto_results, stock_results, fear_greed, trigger_results)
    print(report)
    
    # 保存结果到文件
    output = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'fear_greed': fear_greed,
        'crypto': crypto_results,
        'stocks': stock_results,
        'triggers': trigger_results,
        'report': report
    }
    
    out_file = DATA / 'blood_chip_scan.json'
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    
    # 追加历史记录
    history_file = DATA / 'blood_chip_history.jsonl'
    with open(history_file, 'a') as f:
        f.write(json.dumps(output, ensure_ascii=False) + '\n')
    
    return output

if __name__ == '__main__':
    main()
