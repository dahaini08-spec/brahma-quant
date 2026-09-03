"""
kronos_bridge.py — Kronos 兼容桥接层
2026-09-03 苏摩111修复封印

历史：kronos_engine(torch依赖) → 2026-08-12被har_rv_engine替代
      kronos_bridge 原来指向 kronos_engine（已不存在）→ ImportError
修复：改为指向 har_rv_engine，保持接口兼容

对外暴露：
  get_har_rv(symbol) → dict  (原 kronos 的 vol prediction)
  p_up_proxy         → float (替代 kronos p_up)
"""
from brahma_brain.har_rv_engine import get_har_rv  # noqa: F401


def get_vol_prediction(symbol: str, interval: str = '1h') -> dict:
    """兼容原 kronos_engine.get_vol_prediction() 接口"""
    return get_har_rv(symbol)
