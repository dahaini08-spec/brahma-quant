"""
square_key_router.py — Square API Key 集中路由 [2026-10-03 苏摩111 P3]
接入位置：所有square发帖模块 import get_square_key()

分流策略：
  KEY_0 (主)  → 品牌内容：快照帖/旗舰深度帖/教育帖（高权重内容）
  KEY_1 (流量) → 热度帖/行情帖（高频，独立额度）
  KEY_2 (备用) → 极端告警/宏观帖（低频，KEY_0/KEY_1故障时兜底）

好处：
  1. 单key限流不影响其他内容类型
  2. 每个key独立额度，总发帖上限翻倍
  3. 故障隔离：KEY_0失效→品牌帖暂停，热度帖不受影响
"""
import os

_KEYS = {
    0: os.environ.get('SQUARE_KEY_0', 'd9f19e3f6ba3480584db27b09bec0f27'),
    1: os.environ.get('SQUARE_KEY_1', 'c43ebad6a8434d1b91a039dbf43fda29'),
    2: os.environ.get('SQUARE_KEY_2', '278f3e81efda4274a1d8e15dbc32ec88'),
}

# 模块→key索引映射
_MODULE_KEY = {
    # KEY_0: 品牌核心内容（姓赵不宣主账号）
    'auto_post':     0,   # 快照战场报告
    'deep_post':     0,   # 旗舰深度帖
    'edu_poster':    0,   # 教育帖
    'edu':           0,   # 教育帖别名
    'chart':         0,   # 图文K线帖
    'chart_post':    0,   # 图文K线帖别名
    'video':         0,   # 视频帖
    'video_post':    0,   # 视频帖别名
    'vip_result':    0,   # VIP战绩帖
    'signal':        0,   # 信号帖
    'live_preview':  0,   # 直播预告
    'battlefield':   0,   # 战场报告
    'trade_loop':    0,   # 交易帖
    # KEY_1: 高频流量内容（蓝桉账号）
    'hot_poster':    1,   # 热度帖（高频）
    'macro_poster':  1,   # 宏观帖
    'spot_strategy': 1,   # 现货策略
    # KEY_2: 告警备用（牛来PRO账号）
    'extreme_alert': 2,   # 极端告警（低频）
}


def get_square_key(module: str = 'auto_post') -> str:
    """
    按模块名返回对应key。
    module: 见_MODULE_KEY，未知模块fallback KEY_0。
    """
    idx = _MODULE_KEY.get(module, 0)
    return _KEYS[idx]


def get_all_keys() -> dict:
    return {f'KEY_{k}': v[:8] + '...' for k, v in _KEYS.items()}
