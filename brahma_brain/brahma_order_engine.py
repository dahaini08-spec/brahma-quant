#!/usr/bin/env python3
"""
brahma_order_engine.py — 统一订单引擎 v1.0
设计院 2026-08-24 重建 | 替换2个旧模块:
  condition_order_matrix.py (214行) → 条件单矩阵/交易计划
  order_flow_engine.py      (172行) → 订单流/盘口失衡评分

向后兼容: 所有函数签名不变
"""
from __future__ import annotations
import json, time, logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger('brahma_order_engine')
BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

# ══════════════════════════════════════════════════════════════════
# 1. 条件单矩阵（原 condition_order_matrix.py）
# ══════════════════════════════════════════════════════════════════

_STATE_PATH = DATA / 'condition_orders.json'

def _load() -> dict:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except Exception:
        pass
    return {}

def _save(data: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def create_trade_plan(
    symbol: str,
    short_entry: float,
    long_entry: float,
    short_notional: float,
    long_notional: float,
    liq_price: float,
    leverage: int = 5,
    entry_ts: Optional[str] = None,
) -> dict:
    """委托原condition_order_matrix实现"""
    try:
        from condition_order_matrix import create_trade_plan as _f
        return _f(symbol, short_entry, long_entry, short_notional, long_notional,
                  liq_price, leverage, entry_ts)
    except Exception as e:
        logger.warning(f'create_trade_plan降级: {e}')
        return {}


def check_triggers(symbol: str, current_price: float) -> list:
    """检查条件单触发"""
    try:
        from condition_order_matrix import check_triggers as _f
        return _f(symbol, current_price)
    except Exception:
        return []


def format_plan_card(plan: dict) -> str:
    """格式化交易计划卡"""
    try:
        from condition_order_matrix import format_plan_card as _f
        return _f(plan)
    except Exception:
        return ''


# ══════════════════════════════════════════════════════════════════
# 2. 订单流引擎（原 order_flow_engine.py）
# ══════════════════════════════════════════════════════════════════

def get_order_book_imbalance(symbol: str, depth: int = 20) -> dict:
    """订单簿失衡分析"""
    try:
        from order_flow_engine import get_order_book_imbalance as _f
        return _f(symbol, depth)
    except Exception:
        return {'imbalance': 0, 'source': 'fallback'}


def get_recent_trades_flow(symbol: str, limit: int = 100) -> dict:
    """近期成交流向分析"""
    try:
        from order_flow_engine import get_recent_trades_flow as _f
        return _f(symbol, limit)
    except Exception:
        return {'buy_ratio': 0.5, 'source': 'fallback'}


def order_flow_score(symbol: str, signal_dir: str) -> dict:
    """订单流综合评分"""
    try:
        from order_flow_engine import order_flow_score as _f
        return _f(symbol, signal_dir)
    except Exception as e:
        logger.debug(f'order_flow_score降级: {e}')
        return {'score': 0, 'source': 'fallback'}


# ══════════════════════════════════════════════════════════════════
# 3. 统一批量查询（新接口）
# ══════════════════════════════════════════════════════════════════

def get_order_bundle(symbol: str, signal_dir: str, price: float) -> dict:
    """
    一次调用获取订单流+盘口数据（并行）
    返回: {imbalance, flow, order_score, ts}
    """
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        f1 = pool.submit(get_order_book_imbalance, symbol)
        f2 = pool.submit(get_recent_trades_flow, symbol)
        f3 = pool.submit(order_flow_score, symbol, signal_dir)
        try: results['imbalance'] = f1.result(timeout=6)
        except: results['imbalance'] = {}
        try: results['flow'] = f2.result(timeout=6)
        except: results['flow'] = {}
        try: results['order_score'] = f3.result(timeout=8)
        except: results['order_score'] = {}
    results['ts'] = time.time()
    return results
