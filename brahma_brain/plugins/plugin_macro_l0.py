"""
plugin_macro_l0.py — L0宏观守门插件
[P2-A/D 2026-08-31 苏摩111封印]
自动被 brahma_brain/plugins/__init__.py 加载
"""
import urllib.request, json, time

_CACHE = {}
_CACHE_TTL = 14400  # 4H缓存


def _fetch(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except:
        return None


def _get_dxy():
    """用EUR/USD反向代理DXY"""
    cached = _CACHE.get('dxy')
    if cached and time.time() - cached['ts'] < _CACHE_TTL:
        return cached['val']
    # EUR/USD from Binance (crypto proxy)
    r = _fetch('https://api.binance.com/api/v3/ticker/price?symbol=EURUSDT')
    if r:
        eurusd = float(r['price'])
        # DXY近似：EUR/USD越高，DXY越低
        dxy_approx = 100.0 / eurusd * 1.0574  # 校准系数
        _CACHE['dxy'] = {'ts': time.time(), 'val': dxy_approx, 'eurusd': eurusd}
        return dxy_approx
    return None


def _get_fear_greed():
    """恐贪指数"""
    cached = _CACHE.get('fg')
    if cached and time.time() - cached['ts'] < _CACHE_TTL:
        return cached['val']
    r = _fetch('https://api.alternative.me/fng/?limit=1')
    if r and r.get('data'):
        val = int(r['data'][0]['value'])
        label = r['data'][0]['value_classification']
        _CACHE['fg'] = {'ts': time.time(), 'val': val, 'label': label}
        return val
    return None


def run(r: dict) -> str:
    """L0宏观守门输出"""
    lines = ['', '【L0 宏观守门】']

    # DXY
    dxy = _get_dxy()
    if dxy:
        dxy_note = '偏强⚠️加密承压' if dxy > 104 else '偏弱✅加密友好' if dxy < 100 else '中性'
        lines.append(f'  DXY≈{dxy:.1f} {dxy_note}')
    else:
        lines.append('  DXY: 数据获取中')

    # 恐贪指数
    fg = _get_fear_greed()
    if fg is not None:
        cached_fg = _CACHE.get('fg', {})
        fg_label = cached_fg.get('label', '')
        if fg < 25:
            fg_note = f'极度恐慌🔴({fg_label})'
        elif fg > 75:
            fg_note = f'极度贪婪🔴({fg_label})'
        elif fg < 40:
            fg_note = f'恐慌🟡({fg_label})'
        else:
            fg_note = f'正常✅({fg_label})'
        lines.append(f'  恐贪指数={fg} {fg_note}')
    else:
        lines.append('  恐贪指数: 获取中')

    # 危险级事件检查（硬编码已知FOMC日历）
    import datetime
    now = datetime.datetime.utcnow()
    # 2026年已知重大事件（每季度更新）
    danger_dates = [
        (9, 17, 'FOMC利率决议'),
        (9, 18, 'FOMC记者会'),
        (10, 29, 'FOMC利率决议'),
        (11, 5, '美国大选日'),
        (12, 17, 'FOMC利率决议'),
    ]
    upcoming = []
    for month, day, name in danger_dates:
        event_dt = datetime.datetime(2026, month, day)
        delta = (event_dt - now).days
        if 0 <= delta <= 3:
            upcoming.append(f'⚠️ {name} (还有{delta}天)')

    if upcoming:
        lines.append(f'  🚨 危险级事件: {" | ".join(upcoming)}')
        lines.append(f'  → 建议降仓至1%NAV，等事件后确认方向')
    else:
        lines.append(f'  未来72H: 无危险级事件 ✅')

    # 宏观结论
    risk_score = 0
    if dxy and dxy > 104: risk_score += 1
    if fg is not None and fg < 25: risk_score += 1
    if upcoming: risk_score += 2

    if risk_score >= 2:
        conclusion = '🔴 宏观风险高 → 降仓防御'
    elif risk_score == 1:
        conclusion = '🟡 宏观轻压 → 正常操作'
    else:
        conclusion = '✅ 宏观正常 → 全力运行'

    lines.append(f'  宏观结论: {conclusion}')
    return '\n'.join(lines)
