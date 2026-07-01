"""

# ── STATUS: AUXILIARY ──────────────────────────────────────────
# 期权数据引擎，GEX辅助
# LAST_REVIEW: 2026-07-01 | 属于辅助计算层，修改前确认调用链
# ─────────────────────────────────────────────────────────────
options_engine.py · 期权/市场情绪引擎
brahma_brain · Phase 2

功能：
  - Deribit PCR（Put/Call Ratio）
  - 期权最大痛点 MaxPain（简化估算）
  - 恐惧贪婪指数（Alternative.me）
  - 清算热力图数据（Coinglass公开API）
  - 综合情绪评分（0~10分）
"""
import urllib.request, urllib.parse, json, time
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# 一、恐惧贪婪指数
# ═══════════════════════════════════════════════════════════════

_FEAR_CACHE = {'data': None, 'exp': 0}
_FEAR_DISK_CACHE = Path(__file__).parent.parent / 'data' / '_cache_fg.json'

def get_fear_greed() -> dict:
    """获取加密恐惧贪婪指数（内存+磁盘双层缓存，TTL=300s）"""
    now = time.time()
    # 内存缓存
    if _FEAR_CACHE['exp'] > now:
        return _FEAR_CACHE['data']
    # 磁盘缓存
    try:
        if _FEAR_DISK_CACHE.exists():
            dc = json.loads(_FEAR_DISK_CACHE.read_text())
            if dc.get('exp', 0) > now:
                _FEAR_CACHE['data'] = dc['data']
                _FEAR_CACHE['exp']  = dc['exp']
                return dc['data']
    except Exception:
        pass
    # 网络请求
    try:
        req = urllib.request.Request(
            'https://api.alternative.me/fng/?limit=1',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read())
        val = int(d['data'][0]['value'])
        label = d['data'][0]['value_classification']
        result = {'value': val, 'label': label}
        exp = now + 300  # 修复：原3600s(1H)→300s(5min)，F&G需要更频繁更新
        _FEAR_CACHE['data'] = result
        _FEAR_CACHE['exp']  = exp
        try:
            _FEAR_DISK_CACHE.write_text(json.dumps({'data': result, 'exp': exp}))
        except Exception:
            pass
        return result
    except Exception:
        return {'value': 50, 'label': 'Neutral'}

# ═══════════════════════════════════════════════════════════════
# 二、Deribit PCR（Put/Call比率）
# ═══════════════════════════════════════════════════════════════

_PCR_CACHE = {}
_PCR_DISK = Path(__file__).parent.parent / 'data' / '_cache_pcr.json'

def get_deribit_pcr(currency: str = 'BTC') -> dict:
    """
    获取Deribit期权Put/Call Ratio（内存+磁盘双缓存 TTL=1800s）
    currency: BTC or ETH
    """
    key = currency; now = time.time()
    if key in _PCR_CACHE and _PCR_CACHE[key]['exp'] > now:
        return _PCR_CACHE[key]['data']
    # 磁盘缓存
    try:
        if _PCR_DISK.exists():
            dc = json.loads(_PCR_DISK.read_text())
            entry = dc.get(key, {})
            if entry.get('exp', 0) > now:
                _PCR_CACHE[key] = entry
                return entry['data']
    except Exception:
        pass
    try:
        url = (f'https://www.deribit.com/api/v2/public/get_book_summary_by_currency'
               f'?currency={currency}&kind=option')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        puts  = sum(float(x.get('open_interest', 0))
                    for x in data.get('result', [])
                    if x.get('instrument_name', '').endswith('P'))
        calls = sum(float(x.get('open_interest', 0))
                    for x in data.get('result', [])
                    if x.get('instrument_name', '').endswith('C'))
        pcr = puts / calls if calls > 0 else 1.0
        result = {'pcr': round(pcr,3), 'puts': round(puts,0), 'calls': round(calls,0), 'signal': _pcr_signal(pcr)}
        exp = now + 300  # 修复：1800s→300s
        _PCR_CACHE[key] = {'data': result, 'exp': exp}
        try:
            dc = {}
            if _PCR_DISK.exists(): dc = json.loads(_PCR_DISK.read_text())
            dc[key] = {'data': result, 'exp': exp}
            _PCR_DISK.write_text(json.dumps(dc))
        except Exception: pass
        return result
    except Exception:
        return {'pcr': 1.0, 'puts': 0, 'calls': 0, 'signal': 'NEUTRAL'}

def _pcr_signal(pcr: float) -> str:
    if pcr > 1.5:   return 'EXTREME_BEARISH'  # 反向：底部
    if pcr > 1.2:   return 'BEARISH'
    if pcr < 0.5:   return 'EXTREME_BULLISH'  # 反向：顶部
    if pcr < 0.7:   return 'BULLISH'
    return 'NEUTRAL'

# ═══════════════════════════════════════════════════════════════
# 三、资金费率历史趋势（来自Binance，已有缓存）
# ═══════════════════════════════════════════════════════════════

def analyze_funding_trend(symbol: str) -> dict:
    """分析资金费率趋势（最近8期）"""
    try:
        url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=8'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        rates = [float(x['fundingRate']) * 100 for x in data]
        if not rates:
            return {'trend': 'NEUTRAL', 'avg': 0, 'current': 0}

        avg  = sum(rates) / len(rates)
        curr = rates[-1]
        trend = 'RISING' if curr > avg * 1.2 else ('FALLING' if curr < avg * 0.8 else 'STABLE')

        # 连续方向
        consecutive = 1
        for i in range(len(rates)-2, -1, -1):
            if (rates[i] > 0) == (rates[-1] > 0):
                consecutive += 1
            else:
                break

        return {
            'trend':       trend,
            'avg':         round(avg, 4),
            'current':     round(curr, 4),
            'consecutive': consecutive,
            'note': f'资金费率连续{consecutive}期{"正" if curr>0 else "负"}'
        }
    except Exception:
        return {'trend': 'NEUTRAL', 'avg': 0, 'current': 0, 'consecutive': 0, 'note': ''}

# ═══════════════════════════════════════════════════════════════
# 四、综合情绪评分（0~10分）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# MaxPain 真实数据（CoinGlass v4）
# ═══════════════════════════════════════════════════════════════

try:
    import sys as _op_sys, os as _op_os
    _op_sys.path.insert(0, _op_os.path.dirname(_op_os.path.dirname(_op_os.path.abspath(__file__))))
    from config import coinglass_key as _cg_fn
    CG_KEY = _cg_fn()
except Exception:
    CG_KEY = "a56a2491bca5491ca3f7c7f53b6a6963"  # fallback
CG_BASE = "https://open-api-v4.coinglass.com"
_MAXPAIN_CACHE = {}

def get_max_pain(symbol: str = 'BTC', exchange: str = 'Deribit') -> dict:
    """
    CoinGlass v4 MaxPain：最大痛点价格 + 认购/认沽OI
    到期日价格趋向MaxPain（做市商对冲机制）
    """
    key = f"{symbol}_{exchange}"
    now = time.time()
    if key in _MAXPAIN_CACHE and now - _MAXPAIN_CACHE[key]['ts'] < 300:  # 修复：1800s→300s
        return _MAXPAIN_CACHE[key]['data']

    sym = symbol.replace('USDT','').replace('PERP','')
    url = f"{CG_BASE}/api/option/max-pain?symbol={sym}&exchange={exchange}"
    try:
        req = urllib.request.Request(url, headers={
            'CG-API-KEY': CG_KEY, 'User-Agent': 'brahma/4.0'
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read())

        if str(d.get('code','0')) not in ('0','200'):
            return {'max_pain': 0, 'pcr_oi': 1.0, 'signal': 'NEUTRAL'}

        data = d.get('data', [])
        # 取最近到期日（第一条）
        today = data[0] if data else {}
        mp  = float(today.get('max_pain_price', 0))
        co  = float(today.get('call_open_interest', 0))
        po  = float(today.get('put_open_interest', 0))
        pcr = round(po / co, 3) if co > 0 else 1.0

        result = {
            'max_pain':    mp,
            'call_oi':     co,
            'put_oi':      po,
            'pcr_oi':      pcr,   # >1=认沽多=市场偏空, <1=认购多=市场偏多
            'expiry_date': today.get('date', ''),
            'signal':      'BEARISH' if pcr > 1.3 else ('BULLISH' if pcr < 0.7 else 'NEUTRAL'),
        }
        _MAXPAIN_CACHE[key] = {'ts': now, 'data': result}
        return result
    except Exception:
        return {'max_pain': 0, 'pcr_oi': 1.0, 'signal': 'NEUTRAL'}


def sentiment_score(symbol: str, signal_dir: str,
                    funding_rate: float, long_short_ratio: float) -> dict:
    """
    综合情绪评分（0~10分）
    参数来自已有数据，减少额外API调用
    """
    score   = 0
    details = []

    # 资金费率评分
    if signal_dir == 'LONG':
        if funding_rate < -0.05:
            score += 4; details.append(f'资金费极负({funding_rate:+.3f}%) 空头过热 +4')
        elif funding_rate < -0.01:
            score += 2; details.append(f'资金费负({funding_rate:+.3f}%) 偏多 +2')
        elif funding_rate > 0.05:
            score -= 1; details.append(f'资金费高正({funding_rate:+.3f}%) 多头拥挤 -1')
    else:
        if funding_rate > 0.10:
            score += 4; details.append(f'资金费极高({funding_rate:+.3f}%) 多头过热 +4')
        elif funding_rate > 0.05:
            score += 3; details.append(f'资金费高({funding_rate:+.3f}%) 偏空 +3')
        elif funding_rate > 0.03:
            score += 2; details.append(f'资金费偏高({funding_rate:+.3f}%) +2')
        elif funding_rate > 0.01:
            score += 1; details.append(f'资金费轻正 +1')

    # 多空比评分
    if signal_dir == 'LONG':
        if long_short_ratio < 30:
            score += 4; details.append(f'多空比{long_short_ratio}%多(极度看空) 反向 +4')
        elif long_short_ratio < 40:
            score += 2; details.append(f'多空比{long_short_ratio}%多(偏空) 反向 +2')
    else:
        if long_short_ratio > 75:
            score += 4; details.append(f'多空比{long_short_ratio}%多(极度拥挤) 反向 +4')
        elif long_short_ratio > 65:
            score += 2; details.append(f'多空比{long_short_ratio}%多(偏多) 反向 +2')

    # 恐惧贪婪指数（可选，有超时风险）
    try:
        fg = get_fear_greed()
        fg_val = fg['value']
        if signal_dir == 'LONG' and fg_val <= 20:
            score += 2; details.append(f'极度恐惧({fg_val}) 历史买点 +2')
        elif signal_dir == 'SHORT' and fg_val >= 80:
            score += 2; details.append(f'极度贪婪({fg_val}) 历史卖点 +2')
    except Exception:
        pass

    # P2b: MaxPain真实数据（CoinGlass付费）
    mp_data = {'max_pain': 0, 'pcr_oi': 1.0, 'signal': 'NEUTRAL'}
    try:
        sym_base = symbol.replace('USDT','').replace('PERP','')
        if sym_base in ('BTC','ETH'):
            mp_data = get_max_pain(sym_base, 'Deribit')
            mp  = mp_data['max_pain']
            pcr = mp_data['pcr_oi']
            sig = mp_data['signal']

            # PCR > 1.3: 认沽多(悲观) → 做空有利
            # PCR < 0.7: 认购多(乐观) → 做多有利
            if signal_dir == 'LONG' and sig == 'BULLISH':
                score += 2
                details.append(f'MaxPain期权PCR={pcr:.2f}看涨 +2')
            elif signal_dir == 'SHORT' and sig == 'BEARISH':
                score += 2
                details.append(f'MaxPain期权PCR={pcr:.2f}看跌 +2')
            elif mp > 0:
                details.append(f'MaxPain=${mp:,.0f} PCR={pcr:.2f}')
    except Exception:
        pass

    score = max(0, min(score, 12))  # 升级上限至12（MaxPain最多+2）
    return {
        'score':   score,
        'max':     12,
        'details': details,
        'max_pain': mp_data,
    }

# ─── 快速测试 ────────────────────────────────────────────────
if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'ETHUSDT'
    direction = sys.argv[2] if len(sys.argv) > 2 else 'SHORT'

    print(f'[Options/Sentiment] {sym} 方向={direction}')

    fg = get_fear_greed()
    print(f'  恐惧贪婪指数: {fg["value"]} ({fg["label"]})')

    ft = analyze_funding_trend(sym)
    print(f'  资金费率趋势: {ft["trend"]}  {ft["note"]}')

    score = sentiment_score(sym, direction, ft['current'], 74.3)
    print(f'  情绪评分: {score["score"]}/10')
    for d in score['details']:
        print(f'  + {d}')

    print('[Options/Sentiment] ✅ 测试完成')
