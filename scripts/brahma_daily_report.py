#!/usr/bin/env python3
"""
brahma_daily_report.py — 梵天日报
设计院 2026-07-04 | 三合一日报

合并：brahma-360-daily + live-performance-daily + kronos-m1-check
输出：单条日报，推送到Jarvis
"""
import sys, os, json, subprocess, requests, time
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

try:
    from system_config import JARVIS_TARGET, JARVIS_CHANNEL
except:
    JARVIS_TARGET  = os.environ.get('JARVIS_TARGET','73295708:thread:019f181f-e4d1-7576-85ca-77f4a7fa8075')
    JARVIS_CHANNEL = 'jarvis'

now_utc = datetime.now(timezone.utc)

def get_market():
    """获取BTC/ETH/SOL实时行情"""
    result = {}
    for sym in ['BTCUSDT','ETHUSDT','SOLUSDT']:
        try:
            t = requests.get(
                f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={sym}',
                timeout=5).json()
            fr = requests.get(
                f'https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}',
                timeout=5).json()
            oi = requests.get(
                f'https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}',
                timeout=5).json()
            oih = requests.get(
                f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1h&limit=12',
                timeout=5).json()
            price = float(t['lastPrice'])
            oi_chg = 0
            if isinstance(oih, list) and len(oih) >= 2:
                oi_chg = (float(oih[-1]['sumOpenInterestValue'])/float(oih[0]['sumOpenInterestValue'])-1)*100
            result[sym] = {
                'price': price,
                'chg24h': float(t['priceChangePercent']),
                'fr': float(fr.get('lastFundingRate',0))*100,
                'oi_chg12h': oi_chg,
            }
        except:
            result[sym] = {'price':0,'chg24h':0,'fr':0,'oi_chg12h':0}
    return result

def get_regime():
    try:
        state = BASE / 'data' / 'regime_state.json'
        if state.exists():
            d = json.loads(state.read_text())
            return d.get('regime', d.get('btc_regime', 'UNKNOWN'))
    except: pass
    return 'UNKNOWN'

def get_performance():
    """读取实盘绩效"""
    try:
        perf = BASE / 'data' / 'live_performance.json'
        if perf.exists():
            d = json.loads(perf.read_text())
            return {
                'total_pnl': d.get('total_pnl', d.get('totalPnl', 0)),
                'wr': d.get('win_rate', d.get('winRate', 0)),
                'total_trades': d.get('total_trades', d.get('totalTrades', 0)),
                'today_pnl': d.get('today_pnl', d.get('todayPnl', 0)),
            }
    except: pass
    return {'total_pnl': 0, 'wr': 0, 'total_trades': 0, 'today_pnl': 0}

def get_signals_today():
    """今日信号统计"""
    try:
        bus = BASE / 'data' / 'signal_bus.jsonl'
        if not bus.exists(): return {'count': 0, 'max_score': 0}
        now = time.time()
        today_start = now - 86400
        sigs = []
        for l in bus.read_text().strip().split('\n'):
            try:
                s = json.loads(l)
                if s.get('ts', 0) > today_start:
                    sigs.append(float(s.get('score', 0)))
            except: pass
        return {
            'count': len(sigs),
            'max_score': max(sigs) if sigs else 0,
        }
    except:
        return {'count': 0, 'max_score': 0}

def get_open_positions():
    """当前持仓"""
    try:
        from system_config import API_KEY, API_SECRET as SECRET
        import hmac, hashlib
        ts = int(time.time()*1000)
        qs = f'timestamp={ts}'
        sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
        r = requests.get(
            f'https://fapi.binance.com/fapi/v2/positionRisk?{qs}&signature={sig}',
            headers={'X-MBX-APIKEY': API_KEY}, timeout=8).json()
        pos = [p for p in r if float(p.get('positionAmt',0)) != 0]
        pnl = sum(float(p.get('unRealizedProfit',0)) for p in pos)
        return {'count': len(pos), 'unrealized_pnl': pnl, 'positions': pos}
    except:
        return {'count': 0, 'unrealized_pnl': 0, 'positions': []}

def get_kronos_status():
    """Kronos shadow状态"""
    try:
        klog = BASE / 'data' / 'kronos_shadow_log.jsonl'
        if klog.exists():
            lines = [l for l in klog.read_text().strip().split('\n') if l.strip()]
            if lines:
                last = json.loads(lines[-1])
                n = len(lines)
                # 计算shadow WR
                correct = sum(1 for l in lines[-50:]
                              if json.loads(l).get('correct') == True)
                total = min(50, len(lines))
                wr = correct/total*100 if total > 0 else 0
                return f"SHADOW | n={n} WR={wr:.0f}% (需n≥100且WR≥Lite+2pp升级)"
    except: pass
    return "SHADOW | 数据不足"

if __name__ == '__main__':
    market  = get_market()
    regime  = get_regime()
    perf    = get_performance()
    sigs    = get_signals_today()
    pos     = get_open_positions()
    kronos  = get_kronos_status()

    date_str = now_utc.strftime('%Y-%m-%d %H:%M UTC')

    btc = market.get('BTCUSDT', {})
    eth = market.get('ETHUSDT', {})
    sol = market.get('SOLUSDT', {})

    # 一句话判断
    if 'BULL' in regime:
        outlook = "多头趋势，等回调择机做多"
    elif 'BEAR' in regime:
        outlook = "空头趋势，轻仓空为主，严控多单"
    else:
        outlook = "震荡体制，耐心等待方向确认"

    lines = [
        f"🌐 梵天日报 {date_str}",
        f"体制: {regime}",
        "",
        "市场:",
        f"  BTC  ${btc.get('price',0):,.0f}  {btc.get('chg24h',0):+.2f}%  OI{btc.get('oi_chg12h',0):+.1f}%  FR{btc.get('fr',0):+.4f}%",
        f"  ETH  ${eth.get('price',0):,.2f}  {eth.get('chg24h',0):+.2f}%  OI{eth.get('oi_chg12h',0):+.1f}%  FR{eth.get('fr',0):+.4f}%",
        f"  SOL  ${sol.get('price',0):,.2f}  {sol.get('chg24h',0):+.2f}%",
        "",
        f"今日信号: {sigs['count']}条  最高score: {sigs['max_score']:.0f}",
        f"持仓: {pos['count']}个  浮盈: ${pos['unrealized_pnl']:+.4f}",
        f"累计: {perf['total_trades']}笔  WR: {perf['wr']:.1f}%  总PnL: ${perf['total_pnl']:+.2f}",
        "",
        f"Kronos: {kronos}",
        "",
        f"📌 {outlook}",
    ]

    # 持仓详情（若有）
    if pos['positions']:
        lines.append("")
        lines.append("持仓明细:")
        for p in pos['positions'][:5]:
            sym = p['symbol']
            amt = float(p['positionAmt'])
            pnl_p = float(p.get('unRealizedProfit', 0))
            entry = float(p.get('entryPrice', 0))
            lines.append(f"  {sym} {'多' if amt>0 else '空'} entry=${entry:.4f} PnL=${pnl_p:+.4f}")

    report = '\n'.join(lines)
    print(report)

    # 推送
    try:
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', JARVIS_CHANNEL,
            '--to', JARVIS_TARGET,
            '--message', report
        ], timeout=10, cwd=str(BASE))
        print('\n✅ 日报推送完成')
    except Exception as e:
        print(f'推送失败: {e}')
