#!/usr/bin/env python3
"""
regime_bus.py — 梵天体制总线 v1.0
设计院 × 苏摩111 | 2026-08-04

宪法原则：
  全系统唯一体制数据源
  所有模块通过 regime_bus.get() 获取体制
  禁止模块自行计算/读取体制文件

体制分级：
  CONFIRMED  - 梵天35维矩阵完整确认（最高可信度，用于执行层）
  CANDIDATE  - 多项指标收敛但未完整确认（用于预警层）
  EARLY      - 单指标早期信号（用于监控层，不用于执行）

职能分层：
  Layer-0 EXECUTION  (auto_executor, position_guardian)  → 只用 CONFIRMED
  Layer-1 SIGNAL     (brahma_engine, vip_strategy)       → 用 CONFIRMED
  Layer-2 ALERT      (eth_ema_gate, oi_scanner)          → 用 CANDIDATE
  Layer-3 MONITOR    (square, digest, daily_report)      → 用 EARLY / 宏观描述
"""
import json, time, os
from pathlib import Path
from typing import Optional

BASE      = Path(__file__).resolve().parents[1]
DATA      = BASE / 'data'
BUS_FILE  = DATA / 'regime_bus.json'

# ── 体制权威来源 ──────────────────────────────────────────────
SSOT_FILE = DATA / 'btc_regime_watcher_state.json'

# ── 体制枚举 ──────────────────────────────────────────────────
VALID_REGIMES = {
    'BULL_TREND', 'BULL_EARLY',
    'BEAR_TREND', 'BEAR_EARLY',
    'BEAR_RECOVERY',
    'CHOP_MID', 'CHOP_LOW',
    'UNKNOWN'
}

# ── 职能层级 ──────────────────────────────────────────────────
LAYERS = {
    'EXECUTION': 0,   # 执行层：只用CONFIRMED
    'SIGNAL':    1,   # 信号层：用CONFIRMED
    'ALERT':     2,   # 预警层：用CANDIDATE（可激进）
    'MONITOR':   3,   # 监控层：用EARLY（宏观）
}

# ── 体制可信度 → 层级访问权限 ─────────────────────────────────
CONFIDENCE_LAYER = {
    'CONFIRMED': ['EXECUTION', 'SIGNAL', 'ALERT', 'MONITOR'],
    'CANDIDATE': ['ALERT', 'MONITOR'],
    'EARLY':     ['MONITOR'],
}


def _load_bus() -> dict:
    try:
        return json.loads(BUS_FILE.read_text())
    except Exception:
        return {}

def _save_bus(state: dict):
    BUS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def get(symbol: str = 'BTCUSDT',
        layer: str = 'SIGNAL',
        default: str = 'UNKNOWN') -> str:
    """
    获取体制值。
    
    Args:
        symbol:  交易对 (BTCUSDT / ETHUSDT)
        layer:   调用者职能层（EXECUTION / SIGNAL / ALERT / MONITOR）
        default: 无数据时返回
    
    Returns:
        体制字符串
    
    Example:
        from regime_bus import get as get_regime
        regime = get_regime('ETHUSDT', layer='SIGNAL')
    """
    bus = _load_bus()
    sym_data = bus.get(symbol, {})
    if not sym_data:
        # 降级：直接读SSOT
        return _read_ssot(symbol) or default

    # key大小写兼容（存储用大写，兼容旧小写）
    def _v(key):
        return sym_data.get(key.upper()) or sym_data.get(key.lower()) or ''

    if layer in ('EXECUTION', 'SIGNAL'):
        regime = _v('confirmed')
        return regime if regime in VALID_REGIMES else default
    elif layer == 'ALERT':
        regime = _v('confirmed') or _v('candidate')
        return regime if regime in VALID_REGIMES else default
    else:  # MONITOR
        regime = _v('confirmed') or _v('candidate') or _v('early')
        return regime if regime in VALID_REGIMES else default


def update(symbol: str,
           regime: str,
           confidence: str = 'CONFIRMED',
           source: str = 'brahma_engine',
           score: float = 0.0,
           notes: str = '') -> bool:
    """
    更新体制总线（只有授权来源可调用）。
    
    授权来源：
      btc_regime_watcher (CONFIRMED)
      brahma_engine       (CONFIRMED)
      eth_ema_gate        (CANDIDATE / EARLY)
      rsi_structure_watcher (EARLY)
    """
    AUTHORIZED = {
        'btc_regime_watcher': ['CONFIRMED'],
        'brahma_engine':       ['CONFIRMED', 'CANDIDATE'],
        'consensus_engine':    ['CONFIRMED'],
        'eth_ema_gate':        ['CANDIDATE', 'EARLY'],
        'rsi_structure_watcher': ['EARLY'],
        'manual':              ['CONFIRMED', 'CANDIDATE', 'EARLY'],
    }
    allowed = AUTHORIZED.get(source, [])
    if confidence not in allowed:
        return False

    if regime not in VALID_REGIMES:
        return False

    bus = _load_bus()
    if symbol not in bus:
        bus[symbol] = {}

    prev = bus[symbol].get(confidence, '')
    bus[symbol][confidence] = regime
    bus[symbol][f'{confidence}_ts']     = time.time()
    bus[symbol][f'{confidence}_source'] = source
    bus[symbol][f'{confidence}_score']  = score
    bus[symbol]['updated_at'] = time.time()

    # 检测体制切换
    if prev and prev != regime and confidence == 'CONFIRMED':
        bus[symbol]['last_switch'] = {
            'from': prev, 'to': regime,
            'ts': time.time(), 'source': source
        }

    _save_bus(bus)
    return True


def get_full(symbol: str = 'BTCUSDT') -> dict:
    """返回该品种的完整体制状态（调试用）"""
    bus = _load_bus()
    return bus.get(symbol, {})


def sync_from_ssot():
    """从SSOT文件同步到总线（启动时调用）"""
    try:
        ssot = json.loads(SSOT_FILE.read_text())
        btc_regime = ssot.get('regime', '')
        eth_regime = ssot.get('eth_regime', btc_regime)
        if btc_regime in VALID_REGIMES:
            update('BTCUSDT', btc_regime, 'CONFIRMED', 'btc_regime_watcher')
        if eth_regime in VALID_REGIMES:
            update('ETHUSDT', eth_regime, 'CONFIRMED', 'btc_regime_watcher')
    except Exception as e:
        pass


def _read_ssot(symbol: str) -> str:
    """直接读SSOT（降级兜底）"""
    try:
        d = json.loads(SSOT_FILE.read_text())
        if 'ETH' in symbol.upper():
            return d.get('eth_regime', d.get('regime', 'UNKNOWN'))
        return d.get('regime', 'UNKNOWN')
    except Exception:
        return 'UNKNOWN'


def status_report() -> str:
    """输出体制总线当前状态报告"""
    import datetime
    bus = _load_bus()
    lines = [
        "╔══════════════════════════════════════════════════╗",
        "  梵天体制总线 Regime Bus | 状态报告",
        f"  {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "╚══════════════════════════════════════════════════╝",
        "",
    ]

    if not bus:
        lines.append("  ⚠️ 总线未初始化，请运行 sync_from_ssot()")
        return '\n'.join(lines)

    for sym, data in bus.items():
        if not isinstance(data, dict): continue
        confirmed = data.get('CONFIRMED', data.get('confirmed', '?'))
        candidate = data.get('CANDIDATE', data.get('candidate', ''))
        early     = data.get('EARLY', data.get('early', ''))
        src       = data.get('CONFIRMED_source', data.get('confirmed_source', '?'))
        ts        = data.get('CONFIRMED_ts', data.get('confirmed_ts', 0))
        age       = round((time.time() - ts) / 60) if ts else 0

        switch = data.get('last_switch', {})
        sw_str = f"  └─ 上次切换: {switch.get('from','?')}→{switch.get('to','?')} ({round((time.time()-switch.get('ts',0))/3600,1)}H前)" if switch else ""

        lines += [
            f"  [{sym}]",
            f"    CONFIRMED : {confirmed:<15} (来源:{src}, {age}分钟前)",
        ]
        if candidate: lines.append(f"    CANDIDATE : {candidate}")
        if early:     lines.append(f"    EARLY     : {early}")
        if sw_str:    lines.append(sw_str)

        # 层级访问视图
        lines += [
            f"    层级视图:",
            f"      EXECUTION/SIGNAL → {get(sym, 'SIGNAL')}",
            f"      ALERT            → {get(sym, 'ALERT')}",
            f"      MONITOR          → {get(sym, 'MONITOR')}",
            "",
        ]

    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    if '--sync' in sys.argv:
        sync_from_ssot()
        print("✅ 已从SSOT同步到总线")
    print(status_report())
