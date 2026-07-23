#!/usr/bin/env python3
"""
brahma_dashboard.py — 梵天三大信号系统仪表盘 v2.0
设计院 · 苏摩111封印 · 2026-07-23

三大信号系统:
  S1  梵天主信号   live_signal_log.jsonl  35维矩阵  score≥138
  S2  OI猎手      oi_advanced_signals.jsonl  持仓量异动
  S3  暴涨猎手    pump_signal_queue.jsonl + new_alerts.json

原则:
  - 零缓存: 每次调用实时读文件 + 实时拉价格
  - 严格有效期: expires_at 或 24H 兜底, 过期信号不显示
  - signal_id 去重: 同一 id 只取最新一条
  - 当日信号: 只展示今日 UTC 00:00 之后产生的信号
  - 现代简约: 分区清晰, 无冗余, emoji 引导

用法:
  python3 scripts/brahma_dashboard.py          # 终端输出
  python3 scripts/brahma_dashboard.py --push   # 推送到 Jarvis 线程
"""

import sys, os, json, time, hmac, hashlib, urllib.parse, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
sys.path.insert(0, str(BASE))

try:
    from scripts.system_config import API_KEY, API_SECRET, JARVIS_USER_ID, JARVIS_THREAD_ID
    JARVIS_TARGET = f"{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}"
except Exception:
    API_KEY       = os.environ.get('BINANCE_API_KEY', '')
    API_SECRET    = os.environ.get('BINANCE_API_SECRET', '')
    JARVIS_TARGET = '73295708:thread:019f8768-6731-777d-8924-2426a5abd10f'

FAPI = 'https://fapi.binance.com'

# ══════════════════════════════════════════════════════════════
#  工具层
# ══════════════════════════════════════════════════════════════

def _pub(path: str, params: dict = None) -> dict:
    if params is None:
        params = {}
    qs = urllib.parse.urlencode(params)
    try:
        r = requests.get(f'{FAPI}{path}?{qs}', timeout=6)
        return r.json()
    except Exception:
        return {}


def _signed(path: str, params: dict = None) -> dict:
    if not API_KEY or not API_SECRET:
        return {}
    p = dict(params or {})
    p['timestamp'] = int(time.time() * 1000)
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.get(
            f'{FAPI}{path}?{qs}&signature={sig}',
            headers={'X-MBX-APIKEY': API_KEY}, timeout=8
        )
        return r.json()
    except Exception:
        return {}


def _now() -> float:
    return time.time()


def _today_start_ts() -> float:
    """今日 UTC 00:00 的 timestamp"""
    now_dt = datetime.now(timezone.utc)
    return datetime(now_dt.year, now_dt.month, now_dt.day, tzinfo=timezone.utc).timestamp()


def _is_valid(sig: dict, now_ts: float) -> bool:
    """
    信号有效性三重门控:
      1. expires_at 未过期
      2. valid 字段为 True (若存在)
      3. ts 存在时, 信号年龄 ≤ 24H (兜底)
    """
    # expires_at 检查
    exp = sig.get('expires_at', '')
    if exp:
        try:
            exp_ts = datetime.fromisoformat(exp.replace('Z', '+00:00')).timestamp()
            if now_ts > exp_ts:
                return False
        except Exception:
            pass

    # valid 字段
    if 'valid' in sig and not sig['valid']:
        return False

    # 24H 兜底
    ts = sig.get('ts', 0)
    if ts and (now_ts - float(ts)) > 86400:
        return False

    return True


def _age_label(ts: float, now_ts: float) -> str:
    diff = int(now_ts - ts)
    if diff < 60:
        return f'{diff}s前'
    elif diff < 3600:
        return f'{diff // 60}min前'
    else:
        return f'{diff // 3600}h{(diff % 3600) // 60}m前'


def _ttl_label(expires: str, now_ts: float) -> str:
    if not expires:
        return ''
    try:
        exp_ts = datetime.fromisoformat(expires.replace('Z', '+00:00')).timestamp()
        left = int(exp_ts - now_ts)
        if left <= 0:
            return '已过期'
        if left < 3600:
            return f'剩{left // 60}m'
        return f'剩{left // 3600}h{(left % 3600) // 60}m'
    except Exception:
        return ''


def _score_tier(score: float) -> str:
    if score >= 170: return '🔴神级'
    if score >= 155: return '🟠极强'
    if score >= 138: return '🟡强'
    if score >= 120: return '🔵中'
    return '⚪低'


def _direction_icon(direction: str) -> str:
    d = str(direction).upper()
    if 'LONG' in d:  return '⬆ LONG'
    if 'SHORT' in d: return '⬇ SHORT'
    return direction


# ══════════════════════════════════════════════════════════════
#  数据收集层 (零缓存, 实时读取)
# ══════════════════════════════════════════════════════════════

def collect_market() -> dict:
    """市场快照: 价格 / 体制 / 资金费率"""
    now_ts = _now()
    result = {
        'ts': now_ts,
        'btc': 0.0, 'eth': 0.0,
        'btc_chg': 0.0, 'eth_chg': 0.0,
        'btc_fr': 0.0, 'eth_fr': 0.0,
        'btc_rsi': 0.0, 'eth_rsi': 0.0,
        'regime': 'UNKNOWN',
        'regime_age': 0,
    }

    # 价格 + 24H
    try:
        btc_t = _pub('/fapi/v1/ticker/24hr', {'symbol': 'BTCUSDT'})
        result['btc']     = float(btc_t.get('lastPrice', 0))
        result['btc_chg'] = float(btc_t.get('priceChangePercent', 0))
    except Exception:
        pass

    try:
        eth_t = _pub('/fapi/v1/ticker/24hr', {'symbol': 'ETHUSDT'})
        result['eth']     = float(eth_t.get('lastPrice', 0))
        result['eth_chg'] = float(eth_t.get('priceChangePercent', 0))
    except Exception:
        pass

    # 资金费率
    try:
        btc_fr = _pub('/fapi/v1/premiumIndex', {'symbol': 'BTCUSDT'})
        result['btc_fr'] = float(btc_fr.get('lastFundingRate', 0)) * 100
    except Exception:
        pass
    try:
        eth_fr = _pub('/fapi/v1/premiumIndex', {'symbol': 'ETHUSDT'})
        result['eth_fr'] = float(eth_fr.get('lastFundingRate', 0)) * 100
    except Exception:
        pass

    # RSI_1H (近14根)
    def _rsi(sym):
        kl = _pub('/fapi/v1/klines', {'symbol': sym, 'interval': '1h', 'limit': 15})
        if not isinstance(kl, list) or len(kl) < 5:
            return 0.0
        closes = [float(k[4]) for k in kl]
        gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        rs = ag / al if al > 0 else 99
        return round(100 - 100 / (1 + rs), 1)

    try: result['btc_rsi'] = _rsi('BTCUSDT')
    except Exception: pass
    try: result['eth_rsi'] = _rsi('ETHUSDT')
    except Exception: pass

    # 体制
    for fname in ['data/brahma_state.json', 'data/_regime_timing_state.json']:
        f = BASE / fname
        if f.exists():
            try:
                d = json.loads(f.read_text())
                regime = d.get('regime', d.get('current_regime', ''))
                if regime:
                    result['regime'] = regime
                    result['regime_age'] = int(now_ts - float(d.get('last_update', d.get('ts', now_ts)) or now_ts))
                    break
            except Exception:
                pass

    return result


def collect_s1() -> list:
    """
    System-1: 梵天主信号
    来源: live_signal_log.jsonl
    过滤: valid=True + 未过期 + 当日 + signal_id去重 + score排序
    """
    f = DATA / 'live_signal_log.jsonl'
    if not f.exists():
        return []

    now_ts    = _now()
    today_ts  = _today_start_ts()
    seen: dict = {}  # signal_id -> record

    for line in f.read_text(encoding='utf-8').strip().split('\n'):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            # 有效性三重门
            if not _is_valid(d, now_ts):
                continue
            # 当日过滤
            ts = float(d.get('ts', 0) or 0)
            if ts < today_ts:
                continue
            # signal_id 去重: 保留最新
            sid = d.get('signal_id', f"_{d.get('symbol','')}_{ts}")
            if sid not in seen or ts > float(seen[sid].get('ts', 0)):
                seen[sid] = d
        except Exception:
            pass

    signals = sorted(seen.values(), key=lambda x: -float(x.get('score', 0)))
    return signals[:8]


def collect_s2() -> list:
    """
    System-2: OI猎手
    来源: oi_advanced_signals.jsonl (主) / oi_candidates.json (辅)
    过滤: 近 2H + 当日 + symbol去重
    """
    now_ts   = _now()
    today_ts = _today_start_ts()
    seen: dict = {}

    # 主文件
    for fname in ['oi_advanced_signals.jsonl', 'oi_candidates.jsonl']:
        f = DATA / fname
        if not f.exists():
            continue
        for line in f.read_text(encoding='utf-8').strip().split('\n'):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ts = float(d.get('ts', 0) or 0)
                if ts < today_ts:
                    continue
                if now_ts - ts > 7200:      # 超过 2H 不显示
                    continue
                sym = d.get('symbol', '')
                if sym not in seen or ts > float(seen[sym].get('ts', 0)):
                    seen[sym] = d
            except Exception:
                pass

    # 辅文件 (JSON格式)
    f2 = DATA / 'oi_candidates.json'
    if f2.exists():
        try:
            raw = json.loads(f2.read_text())
            items = raw if isinstance(raw, list) else raw.get('candidates', [])
            for d in items:
                ts = float(d.get('ts', now_ts) or now_ts)
                if ts < today_ts or now_ts - ts > 7200:
                    continue
                sym = d.get('symbol', '')
                if sym not in seen or ts > float(seen[sym].get('ts', 0)):
                    seen[sym] = d
        except Exception:
            pass

    signals = sorted(seen.values(), key=lambda x: -float(x.get('oi_score', x.get('score', 0))))
    return signals[:8]


def collect_s3() -> list:
    """
    System-3: 暴涨猎手
    来源: new_alerts.json (最新扫描结论) + pump_signal_queue.jsonl (队列)
    过滤: 当日 + score≥75 + 未过期
    """
    now_ts   = _now()
    today_ts = _today_start_ts()
    signals  = []

    # new_alerts.json — 最新扫描预警
    f = BASE / 'dharma' / 'pump_hunter' / 'new_alerts.json'
    if f.exists():
        try:
            d    = json.loads(f.read_text())
            scan_ts_str = d.get('scan_time', '')
            scan_ts = 0
            if scan_ts_str:
                try:
                    scan_ts = datetime.fromisoformat(scan_ts_str).replace(tzinfo=timezone.utc).timestamp()
                except Exception:
                    pass
            for alert in d.get('alerts', []):
                alert.setdefault('ts', scan_ts or now_ts)
                alert.setdefault('_from', 'new_alerts')
                ts = float(alert.get('ts', now_ts))
                if ts >= today_ts and float(alert.get('score', 0)) >= 75:
                    signals.append(alert)
        except Exception:
            pass

    # pump_signal_queue.jsonl — 持久化队列
    fq = DATA / 'pump_signal_queue.jsonl'
    if fq.exists():
        seen_syms = {s.get('symbol','') for s in signals}
        for line in fq.read_text(encoding='utf-8').strip().split('\n'):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ts = float(d.get('ts', 0) or 0)
                if ts < today_ts:
                    continue
                if not _is_valid(d, now_ts):
                    continue
                if float(d.get('score', d.get('pump_score', 0))) < 75:
                    continue
                sym = d.get('symbol','')
                if sym not in seen_syms:
                    signals.append(d)
                    seen_syms.add(sym)
            except Exception:
                pass

    signals.sort(key=lambda x: -float(x.get('score', x.get('pump_score', 0))))
    return signals[:6]


def collect_positions() -> list:
    """当前持仓 (wuqu_positions)"""
    f = DATA / 'wuqu_positions.json'
    if not f.exists():
        return []
    try:
        raw  = json.loads(f.read_text())
        now_ts = _now()
        result = []
        items = raw.items() if isinstance(raw, dict) else [(p.get('symbol',''), p) for p in raw]
        for sym, info in items:
            if not isinstance(info, dict):
                continue
            size = float(info.get('size', info.get('qty', 0)) or 0)
            if size == 0:
                continue
            # 实时价格
            try:
                cp = float(_pub('/fapi/v1/ticker/price', {'symbol': sym}).get('price', 0))
            except Exception:
                cp = 0.0
            entry = float(info.get('entry_price', info.get('entry', 0)) or 0)
            side  = str(info.get('side', info.get('direction', 'LONG'))).upper()
            pnl   = 0.0
            if entry and cp:
                pnl = (cp - entry) / entry * 100 if 'LONG' in side else (entry - cp) / entry * 100
            result.append({
                'symbol':      sym,
                'side':        side,
                'size':        size,
                'entry_price': entry,
                'current':     cp,
                'pnl_pct':     round(pnl, 2),
                'sl':          float(info.get('sl', 0) or 0),
                'tp1':         float(info.get('tp1', 0) or 0),
                'leverage':    info.get('leverage', 1),
            })
        return result
    except Exception:
        return []


def collect_scan_meta() -> dict:
    """暴涨猎手最近一次扫描元数据"""
    f = BASE / 'dharma' / 'pump_hunter' / 'new_alerts.json'
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text())
        return {
            'scan_time':    d.get('scan_time', ''),
            'total_scanned':d.get('total_scanned', 0),
            'btc_regime':   d.get('btc_regime', ''),
            'elapsed_sec':  d.get('elapsed_sec', 0),
        }
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════
#  格式化层 — 移动端优先，短行，信息分层
#  原则: 每行≤28字符, 标题独行, 禁止同行塞>3字段
# ══════════════════════════════════════════════════════════════

def fmt_market(m: dict) -> str:
    now_str = datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')
    regime  = m.get('regime', 'UNKNOWN')
    regime_icon = {
        'BULL_TREND': '🟢', 'BULL_EARLY': '🟩',
        'BEAR_TREND': '🔴', 'BEAR_EARLY': '🟥', 'BEAR_RECOVERY': '🟡',
        'CHOP_MID': '🟡', 'CHOP_HIGH': '🟠', 'CHOP_LOW': '⚪',
    }.get(regime, '⚫')
    btc_chg = f'+{m["btc_chg"]:.2f}%' if m["btc_chg"] >= 0 else f'{m["btc_chg"]:.2f}%'
    eth_chg = f'+{m["eth_chg"]:.2f}%' if m["eth_chg"] >= 0 else f'{m["eth_chg"]:.2f}%'
    return '\n'.join([
        f'📊 **梵天仪表盘** · {now_str}',
        f'体制 {regime_icon} **{regime}**',
        f'',
        f'BTC **${m["btc"]:,.0f}** {btc_chg}',
        f'RSI {m["btc_rsi"]}  FR {m["btc_fr"]:+.4f}%',
        f'',
        f'ETH **${m["eth"]:,.2f}** {eth_chg}',
        f'RSI {m["eth_rsi"]}  FR {m["eth_fr"]:+.4f}%',
    ])


def fmt_s1(signals: list, now_ts: float) -> str:
    lines = ['', '━━━━━━━━━━━━━━━━━━━━━━━━',
             f'🧠 **S1 梵天主信号** · {len(signals)}个有效']
    if not signals:
        lines.append('暂无当日有效信号')
        return '\n'.join(lines)
    for s in signals:
        sym     = s.get('symbol', '')
        score   = float(s.get('score', 0))
        tier    = _score_tier(score)
        dir_raw = str(s.get('direction', s.get('signal_dir', ''))).upper()
        dir_str = '⬆ 做多' if 'LONG' in dir_raw else '⬇ 做空'
        regime  = s.get('regime', '')
        elo     = float(s.get('entry_lo', 0) or 0)
        ehi     = float(s.get('entry_hi', 0) or 0)
        sl_pct  = float(s.get('sl_pct', 0) or 0)
        rr1     = float(s.get('rr1', 0) or 0)
        timing  = (s.get('timing_badge','') or s.get('action','')).replace('🟢 ','').replace('⚠️ ','')
        ttl     = _ttl_label(s.get('expires_at',''), now_ts)
        age     = _age_label(float(s.get('ts', now_ts)), now_ts)
        try:
            cp  = float(_pub('/fapi/v1/ticker/price', {'symbol': sym}).get('price', 0))
            gap = (cp - elo) / cp * 100 if elo and cp else 0
            gap_icon = '✅在区间' if abs(gap) <= 0.5 else ('⬆超出' if gap > 0 else '⬇未到')
            price_str = f'现价 ${cp:,.2f} {gap_icon}'
        except Exception:
            price_str = ''
        lines += [
            '',
            f'**{sym}** {dir_str}',
            f'{tier} {score:.0f}分  {regime}',
        ]
        if elo and ehi:
            lines.append(f'入场 {elo:,.1f}~{ehi:,.1f}')
        lines.append(f'SL {sl_pct:.1f}%  RR {rr1:.1f}x')
        if price_str:
            lines.append(price_str)
        lines.append(f'{timing}  {ttl}  {age}')
    return '\n'.join(lines)


def fmt_s2(signals: list, now_ts: float) -> str:
    lines = ['', '━━━━━━━━━━━━━━━━━━━━━━━━',
             f'📈 **S2 OI猎手** · {len(signals)}个近2H异动']
    if not signals:
        lines.append('近2H无异动信号')
        return '\n'.join(lines)
    for s in signals:
        sym      = s.get('symbol', '')
        oi_score = float(s.get('oi_score', s.get('score', 0)))
        oi_chg   = float(s.get('oi_change_pct', s.get('oi_pct', 0)) or 0)
        direction = s.get('signal', s.get('direction', s.get('bias', '')))
        regime   = s.get('regime', '')
        age      = _age_label(float(s.get('ts', now_ts)), now_ts)
        oi_icon  = '⬆' if oi_chg >= 0 else '⬇'
        dir_str  = '多头建仓' if 'LONG' in str(direction).upper() else (
                   '空头建仓' if 'SHORT' in str(direction).upper() else str(direction))
        lines += [
            '',
            f'**{sym}**  {dir_str}',
            f'OI {oi_icon}{abs(oi_chg):.2f}%  得分{oi_score:.0f}',
            f'{regime}  {age}',
        ]
    return '\n'.join(lines)


def fmt_s3(signals: list, meta: dict, now_ts: float) -> str:
    total    = meta.get('total_scanned', 0)
    btc_reg  = meta.get('btc_regime', '')
    elapsed  = meta.get('elapsed_sec', 0)
    scan_age = ''
    if meta.get('scan_time'):
        try:
            st = datetime.fromisoformat(meta['scan_time']).replace(tzinfo=timezone.utc).timestamp()
            scan_age = _age_label(st, now_ts)
        except Exception:
            pass
    lines = [
        '', '━━━━━━━━━━━━━━━━━━━━━━━━',
        f'🔥 **S3 暴涨猎手** · {len(signals)}个预警',
        f'扫描 {total}标的  {scan_age}',
        f'体制 {btc_reg}  耗时{elapsed:.0f}s',
    ]
    if not signals:
        lines.append('当日无触发预警')
        return '\n'.join(lines)
    for s in signals:
        sym   = s.get('symbol', '')
        score = float(s.get('score', s.get('pump_score', 0)))
        level = s.get('alert_level', s.get('level', ''))
        tight = s.get('tight_pct', '?')
        rsi   = s.get('rsi_1h', s.get('rsi', '?'))
        vol_h = s.get('vol_shrink_h', '?')
        age   = _age_label(float(s.get('ts', now_ts)), now_ts)
        icon  = '💣' if score >= 85 else '🚨'
        lines += [
            '',
            f'{icon} **{sym}**  {score:.0f}分 {level}',
            f'TIGHT {tight}%  RSI {rsi}',
            f'缩量{vol_h}H  {age}',
        ]
    return '\n'.join(lines)


def fmt_positions(positions: list) -> str:
    lines = ['', '━━━━━━━━━━━━━━━━━━━━━━━━',
             f'💼 **持仓** · {len(positions)}个']
    if not positions:
        lines.append('空仓')
        return '\n'.join(lines)
    for p in positions:
        sym  = p['symbol']
        side = '⬆ 做多' if 'LONG' in p['side'] else '⬇ 做空'
        pnl  = p['pnl_pct']
        pnl_icon = '✅' if pnl >= 0 else '🔻'
        lev  = p['leverage']
        lines += [
            '',
            f'**{sym}** {side} {lev}x',
            f'{pnl_icon} {pnl:+.2f}%',
            f'进场 ${p["entry_price"]:,.2f}',
            f'现价 ${p["current"]:,.2f}',
        ]
        if p['sl']:
            lines.append(f'SL ${p["sl"]:,.2f}')
        if p['tp1']:
            lines.append(f'TP1 ${p["tp1"]:,.2f}')
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════

def build_message() -> str:
    """实时构建完整仪表盘消息"""
    now_ts = _now()

    market    = collect_market()
    s1        = collect_s1()
    s2        = collect_s2()
    s3        = collect_s3()
    positions = collect_positions()
    s3_meta   = collect_scan_meta()

    parts = [
        fmt_market(market),
        fmt_s1(s1, now_ts),
        fmt_s2(s2, now_ts),
        fmt_s3(s3, s3_meta, now_ts),
        fmt_positions(positions),
        '\n' + '═' * 24,
    ]
    return '\n'.join(parts)


def push(msg: str) -> bool:
    """推送到 Jarvis 线程"""
    try:
        import subprocess
        result = subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--target', JARVIS_TARGET,
             '--message', msg],
            capture_output=True, text=True, timeout=20
        )
        return result.returncode == 0
    except Exception as e:
        print(f'[push] 失败: {e}')
        return False


if __name__ == '__main__':
    msg = build_message()

    if '--push' in sys.argv:
        ok = push(msg)
        print('[dashboard] ✅ 推送成功' if ok else '[dashboard] ❌ 推送失败')
    else:
        print(msg)
