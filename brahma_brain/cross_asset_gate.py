#!/usr/bin/env python3
# ponytail: cross_asset_gate 348行，有意为之，重构前先 grep 所有调用方
"""
cross_asset_gate.py — 梵天跨资产联合推理门控 v1.0
设计院 · 苏摩111批准 · 2026-07-23

核心能力：
  单脑缺陷: 每个标的独立打分，互不知晓 → 产出矛盾信号
  联合门控: BTC/ETH信号交叉验证，矛盾时自动降级为WAIT

工作原理：
  1. 检查BTC距入场区距离 (btc_gap)
  2. 用Beta计算: 若BTC下行到入场区，ETH联动跌幅
  3. 若联动跌幅 > ETH当前止损距离 → ETH信号降级WAIT
  4. 同时计算ETH更优入场区间（BTC插针完成后）

适用场景:
  BULL_TREND LONG: BTC未到位 + ETH已在入场区 → ETH降级
  BEAR_TREND SHORT: BTC未到位 + ETH已在入场区 → 同理检查

封印规则:
  - BTC是市场锚，优先级高于ETH/山寨
  - 只有BTC gap > 1.5% 时才触发门控（小波动不干扰）
  - 联动跌幅 > ETH止损距离的80% 才触发（留20%容错）
"""

import sys, os, time, json, requests, urllib.parse
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# ─── 常量 ────────────────────────────────────────────────
BTC_ANCHOR_SYMBOLS = {'BTCUSDT', 'BTCDOMUSDT'}  # BTC是锚
ETH_LINKED_SYMBOLS = {'ETHUSDT'}                  # ETH紧跟BTC
DEFAULT_BETA_BTC_ETH = 1.23                        # 近期48H Beta均值
TRIGGER_GAP_PCT   = 1.5    # BTC距入场区>1.5%才触发
SL_COVERAGE_RATIO = 0.80   # 联动跌幅覆盖80%止损距离时触发
FAPI = 'https://fapi.binance.com'

# ─── 工具函数 ─────────────────────────────────────────────

def _pub(path, params={}):
    qs = urllib.parse.urlencode(params)
    try:
        r = _HTTP.get(f'{FAPI}{path}?{qs}', timeout=6)
        return r.json()
    except Exception:
        return {}

def _get_price(symbol: str) -> float:
    d = _pub('/fapi/v1/ticker/price', {'symbol': symbol})
    return float(d.get('price', 0))

def _calc_beta(symbol_a: str, symbol_b: str, hours: int = 48) -> float:
    """计算 symbol_b 相对 symbol_a 的Beta（收益率回归）"""
    try:
        import numpy as np
        ka = _pub('/fapi/v1/klines', {'symbol': symbol_a, 'interval': '1h', 'limit': hours + 1})
        kb = _pub('/fapi/v1/klines', {'symbol': symbol_b, 'interval': '1h', 'limit': hours + 1})
        if len(ka) < 10 or len(kb) < 10:
            return DEFAULT_BETA_BTC_ETH
        ca = np.array([float(k[4]) for k in ka])
        cb = np.array([float(k[4]) for k in kb])
        ra = np.diff(np.log(ca))
        rb = np.diff(np.log(cb))
        cov = np.cov(ra, rb)
        beta = cov[0, 1] / cov[0, 0] if cov[0, 0] > 0 else DEFAULT_BETA_BTC_ETH
        return max(0.5, min(2.5, beta))  # 合理范围约束
    except Exception:
        return DEFAULT_BETA_BTC_ETH


# ─── 核心门控逻辑 ─────────────────────────────────────────

class CrossAssetGate:
    """
    跨资产联合推理门控
    调用方式:
        gate = CrossAssetGate()
        signal = gate.check(eth_signal, all_active_signals)
    """

    def __init__(self, beta_hours: int = 48):
        self._beta_cache: dict = {}
        self._price_cache: dict = {}
        self._cache_ts: float = 0
        self._beta_hours = beta_hours

    def _refresh_prices(self):
        """刷新价格缓存（60s TTL）"""
        if time.time() - self._cache_ts < 60:
            return
        for sym in ['BTCUSDT', 'ETHUSDT']:
            p = _get_price(sym)
            if p > 0:
                self._price_cache[sym] = p
        self._cache_ts = time.time()

    def _get_beta(self, anchor: str, target: str) -> float:
        key = f'{anchor}_{target}'
        if key not in self._beta_cache:
            self._beta_cache[key] = _calc_beta(anchor, target, self._beta_hours)
        return self._beta_cache[key]

    def check(self, signal: dict, peer_signals: list = None) -> dict:
        """
        检查单个信号的跨资产一致性
        返回: 修改后的signal（可能降级timing_badge为WAIT）
        """
        signal = dict(signal)  # 不修改原始对象
        sym       = signal.get('symbol', '')
        direction = signal.get('direction', '')
        entry_lo  = signal.get('entry_lo', 0)
        sl        = signal.get('sl', signal.get('sl_price', 0))
        sl_pct    = signal.get('sl_pct', 0)

        # 只处理ETH信号（BTC不需要自己检查自己）
        if sym not in ETH_LINKED_SYMBOLS:
            return signal
        if not entry_lo or not sl_pct:
            return signal

        self._refresh_prices()
        eth_now = self._price_cache.get('ETHUSDT', _get_price('ETHUSDT'))
        btc_now = self._price_cache.get('BTCUSDT', _get_price('BTCUSDT'))

        if eth_now <= 0 or btc_now <= 0:
            return signal

        # ── 找BTC的活跃信号入场区 ──
        btc_entry_lo = self._find_btc_entry(peer_signals or [])
        if not btc_entry_lo:
            # 没有BTC信号——不触发门控
            signal['cross_asset_check'] = 'NO_BTC_SIGNAL'
            return signal

        # ── 计算BTC距入场区距离 ──
        btc_gap_pct = (btc_now - btc_entry_lo) / btc_now * 100  # 正值=高于入场区

        if btc_gap_pct < TRIGGER_GAP_PCT:
            # BTC已接近或进入入场区——不触发门控
            signal['cross_asset_check'] = f'BTC_GAP_OK({btc_gap_pct:.1f}%<{TRIGGER_GAP_PCT}%)'
            return signal

        # ── BTC下行到入场区时，ETH联动跌幅 ──
        beta = self._get_beta('BTCUSDT', 'ETHUSDT')
        eth_linked_drop_pct = btc_gap_pct * beta  # ETH联动跌幅（百分比）

        # ── ETH当前止损距离 ──
        eth_sl_dist_pct = sl_pct  # 已经是百分比

        # ── 触发判断 ──
        if eth_linked_drop_pct < eth_sl_dist_pct * SL_COVERAGE_RATIO:
            # 联动跌幅覆盖不到80%止损距离——不触发
            signal['cross_asset_check'] = (
                f'SAFE(联动跌{eth_linked_drop_pct:.1f}%<SL距{eth_sl_dist_pct:.1f}%×{SL_COVERAGE_RATIO})'
            )
            return signal

        # ══ 触发降级 ══
        eth_better_entry_lo = eth_now * (1 - eth_linked_drop_pct / 100)
        eth_better_entry_hi = eth_better_entry_lo * 1.008  # +0.8%区间

        # 更优止损（从更低位置入场，止损更紧）
        eth_better_sl_dist = eth_sl_dist_pct * 0.6  # 止损距离缩短40%
        eth_better_sl      = eth_better_entry_lo * (1 - eth_better_sl_dist / 100)

        # RR改善计算
        tp1_pct   = signal.get('rr1', 2.0) * eth_sl_dist_pct
        tp1_price = eth_better_entry_lo * (1 + tp1_pct / 100)
        new_rr    = tp1_pct / eth_better_sl_dist if eth_better_sl_dist > 0 else 0

        signal['timing_badge']         = '⚠️ WAIT_BTC_ANCHOR'
        signal['cross_asset_triggered'] = True
        signal['cross_asset_reason']    = (
            f'BTC距入场区{btc_gap_pct:.1f}% > {TRIGGER_GAP_PCT}%，'
            f'ETH联动跌幅{eth_linked_drop_pct:.1f}% > SL距{eth_sl_dist_pct:.1f}%×{SL_COVERAGE_RATIO}，'
            f'等BTC插针${btc_entry_lo:.0f}后再入场'
        )
        signal['better_entry_lo']  = round(eth_better_entry_lo, 4)
        signal['better_entry_hi']  = round(eth_better_entry_hi, 4)
        signal['better_sl']        = round(eth_better_sl, 4)
        signal['better_rr']        = round(new_rr, 2)
        signal['beta_used']        = round(beta, 4)
        signal['btc_gap_pct']      = round(btc_gap_pct, 2)
        signal['eth_linked_drop']  = round(eth_linked_drop_pct, 2)

        return signal

    @staticmethod
    def _is_signal_valid(sig: dict) -> bool:
        """
        信号有效性检查 — 修复3大缺陷之首要门:
          1. expires_at 字段存在且未过期
          2. valid 字段为 True（或无此字段时默认通过）
          3. ts 存在时，信号年龄不超过 24H（兜底）
        """
        now_ts = time.time()

        # ── 检查 expires_at ──────────────────────────────────
        expires = sig.get('expires_at', '')
        if expires:
            try:
                exp_ts = datetime.fromisoformat(
                    expires.replace('Z', '+00:00')
                ).timestamp()
                if now_ts > exp_ts:
                    return False   # 已过期
            except Exception:
                pass  # 解析失败时不拦截

        # ── 检查 valid 字段 ──────────────────────────────────
        if 'valid' in sig and not sig['valid']:
            return False

        # ── 兜底：信号年龄超过 24H 视为过期 ─────────────────
        ts = sig.get('ts', 0)
        if ts and (now_ts - ts) > 86400:
            return False

        return True

    def _find_btc_entry(self, peer_signals: list) -> Optional[float]:
        """
        从同批信号中找BTC的最佳入场区下沿。
        修复: 只使用未过期 + valid=True 的BTC信号作为锚，
              防止历史旧信号（如 $64,001）错误触发跨资产门控。
        """
        btc_sigs = [
            s for s in peer_signals
            if s.get('symbol') == 'BTCUSDT'
            and s.get('direction') == 'LONG'
            and s.get('entry_lo', 0) > 0
            and self._is_signal_valid(s)   # ← 核心修复：过期信号被排除
        ]
        if not btc_sigs:
            return None
        # 取 score 最高的有效 BTC 信号
        best = max(btc_sigs, key=lambda x: x.get('score', 0))
        return best.get('entry_lo', 0)

    def check_batch(self, signals: list) -> list:
        """批量检查信号列表，自动交叉验证"""
        result = []
        for sig in signals:
            checked = self.check(sig, peer_signals=signals)
            result.append(checked)
        return result

    def format_alert(self, signal: dict) -> str:
        """格式化降级告警消息"""
        if not signal.get('cross_asset_triggered'):
            return ''
        sym = signal.get('symbol', '')
        lo  = signal.get('better_entry_lo', 0)
        hi  = signal.get('better_entry_hi', 0)
        sl  = signal.get('better_sl', 0)
        rr  = signal.get('better_rr', 0)
        btc_gap = signal.get('btc_gap_pct', 0)
        drop    = signal.get('eth_linked_drop', 0)
        return (
            f"⚠️ **跨资产门控触发** [{sym}]\n"
            f"原信号降级为 WAIT_BTC_ANCHOR\n"
            f"原因: BTC距入场区{btc_gap:.1f}%，ETH联动跌幅{drop:.1f}%将触发止损\n"
            f"更优入场区: ${lo:.2f}~${hi:.2f}\n"
            f"更优止损:   ${sl:.2f}\n"
            f"更优RR:     {rr:.1f}x\n"
            f"策略: 等BTC插针完成后，在${lo:.0f}~${hi:.0f}接多"
        )


# ─── 全局单例 ─────────────────────────────────────────────
_gate_instance: Optional[CrossAssetGate] = None

def get_gate() -> CrossAssetGate:
    global _gate_instance
    if _gate_instance is None:
        _gate_instance = CrossAssetGate()
    return _gate_instance


# ─── 便捷函数 ─────────────────────────────────────────────

def apply_cross_asset_gate(signals: list) -> list:
    """
    主入口：对信号列表做跨资产联合推理
    注入点：brahma_analysis_runner / auto_executor
    """
    if not signals:
        return signals
    gate = get_gate()
    return gate.check_batch(signals)


# ─── 测试 ─────────────────────────────────────────────────

if __name__ == '__main__':
    import requests
try:
    from brahma_bus import _SESS as _HTTP  # [HTTP Session共享 2026-08-02 设计院自主]
except ImportError:
    _HTTP = requests  # fallback as req

    print('=== cross_asset_gate 实时测试 ===\n')

    def pub(path, params={}):
        qs = urllib.parse.urlencode(params)
        return req.get(f'https://fapi.binance.com{path}?{qs}', timeout=8).json()

    btc_now = float(pub('/fapi/v1/ticker/price', {'symbol': 'BTCUSDT'})['price'])
    eth_now = float(pub('/fapi/v1/ticker/price', {'symbol': 'ETHUSDT'})['price'])

    # 模拟当前场景：BTC信号入场区$64001，ETH信号$1915
    test_signals = [
        {
            'symbol': 'BTCUSDT', 'direction': 'LONG', 'score': 132,
            'entry_lo': 64001, 'entry_hi': 64193,
            'sl': 63262, 'sl_pct': 1.15, 'rr1': 2.5,
            'timing_badge': '🟢 READY', 'valid': True,
        },
        {
            'symbol': 'ETHUSDT', 'direction': 'LONG', 'score': 121,
            'entry_lo': 1915.0, 'entry_hi': 1919.0,
            'sl': 1893.0, 'sl_pct': 1.15, 'rr1': 2.0,
            'timing_badge': '🟢 READY', 'valid': True,
        },
    ]

    print(f'当前价格: BTC=${btc_now:,.0f}  ETH=${eth_now:.2f}')
    print(f'测试信号: BTC入场区$64001  ETH入场区$1915\n')

    gate = CrossAssetGate()
    results = gate.check_batch(test_signals)

    for r in results:
        sym = r['symbol']
        badge = r.get('timing_badge', '')
        triggered = r.get('cross_asset_triggered', False)
        check = r.get('cross_asset_check', '')
        print(f'[{sym}] timing={badge}')
        if triggered:
            print(gate.format_alert(r))
        else:
            print(f'  cross_asset_check={check}')
        print()
