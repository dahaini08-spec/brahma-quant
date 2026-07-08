"""
no_trade_guard.py — 网络层交易端点拦截器
设计院 2026-07-08 | 第三方审计v4.0 Step4 补丁

动态审计专用：monkey-patch requests，
任何代码试图POST Binance下单接口 → 直接RuntimeError
"""
import os
import requests
import requests.sessions

_ORIG_REQUEST = requests.sessions.Session.request
_INSTALLED = False

BLOCKED_METHODS_PATHS = [
    ('POST',   '/fapi/v1/order'),
    ('DELETE', '/fapi/v1/order'),
    ('POST',   '/fapi/v1/leverage'),
    ('POST',   '/fapi/v1/marginType'),
    ('POST',   '/fapi/v2/order'),
    ('POST',   '/papi/v1/order'),
]

READ_ONLY_ALLOWED = [
    '/fapi/v1/ticker',
    '/fapi/v1/klines',
    '/fapi/v1/depth',
    '/fapi/v1/trades',
    '/fapi/v1/fundingRate',
    '/fapi/v1/openInterest',
    '/fapi/v1/exchangeInfo',
    '/futures/data/',
    '/fapi/v2/positionRisk',
    '/fapi/v2/account',
]


def guarded_request(self, method, url, *args, **kwargs):
    method_u = (method or '').upper()
    url_s = str(url)

    if 'binance.com' in url_s or 'fapi' in url_s:
        for blk_method, blk_path in BLOCKED_METHODS_PATHS:
            if blk_path in url_s and method_u == blk_method:
                raise RuntimeError(
                    f"[NO_TRADE_GUARD] 🚫 交易端点被拦截: {method_u} {blk_path}\n"
                    f"  动态审计模式：禁止真实下单"
                )
        # 记录公开行情访问（不拦截）
        for ro_path in READ_ONLY_ALLOWED:
            if ro_path in url_s:
                break

    return _ORIG_REQUEST(self, method, url, *args, **kwargs)


def install():
    """安装网络守卫 + 设置安全环境变量"""
    global _INSTALLED
    if _INSTALLED:
        return

    # 强制安全环境变量
    os.environ['BRAHMA_SIGNAL_ONLY']            = 'true'
    os.environ['BRAHMA_LIVE_TRADING_ENABLED']   = 'false'
    os.environ['AGENT_LIVE_TRADING_ENABLED']    = 'false'
    os.environ['PAPER_TRADING_DEFAULT']         = 'true'
    os.environ['BRIDGE_DRY_RUN']                = 'true'
    os.environ['BINANCE_API_KEY']               = ''
    os.environ['BINANCE_SECRET']                = ''
    os.environ['BINANCE_API_SECRET']            = ''

    requests.sessions.Session.request = guarded_request
    _INSTALLED = True
    print("[NO_TRADE_GUARD] ✅ 已安装：交易端点拦截器激活，只读模式")


def uninstall():
    global _INSTALLED
    requests.sessions.Session.request = _ORIG_REQUEST
    _INSTALLED = False
    print("[NO_TRADE_GUARD] 已卸载")


if __name__ == '__main__':
    install()
    # 验证：只读端点允许
    import requests as req
    try:
        r = req.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5)
        price = r.json().get('price', '?')
        print(f"[TEST] 只读行情: BTC=${price} ✅")
    except Exception as e:
        print(f"[TEST] 行情失败: {e}")
    # 验证：下单端点被拦截
    try:
        req.post('https://fapi.binance.com/fapi/v1/order', json={}, timeout=5)
        print("[TEST] ❌ 下单未被拦截！")
    except RuntimeError as e:
        print(f"[TEST] 下单拦截成功 ✅: {str(e)[:60]}")
