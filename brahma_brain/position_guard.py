"""
P0 持仓风控模块 — 梵天设计院封印 2026-07-24
苏摩111批准

功能：
  1. 持仓成本追踪（读取wuqu_positions）
  2. 浮亏预警（跌破成本X%）
  3. 强制止损输出（超过SL_PCT阈值必须输出）
  4. 加仓/首仓语义区分
"""
import os, json
from pathlib import Path

# 持仓文件路径
_POSITIONS_FILE = Path(__file__).parent.parent / 'data' / 'wuqu_positions.json'
_USER_POSITIONS_FILE = Path(__file__).parent.parent / 'data' / 'user_positions.json'

# 止损阈值（与梵天铁律一致）
SL_THRESHOLDS = {
    'BEAR_TREND':    0.020,  # 2.0%
    'BULL_TREND':    0.020,  # 2.0%
    'CHOP_MID':      0.025,  # 2.5%
    'BEAR_RECOVERY': 0.020,  # 2.0%
    'default':       0.020,
}

# 警告阈值（触发浮亏提示，未到止损线）
WARN_THRESHOLDS = {
    'default': 0.010,  # -1%触发预警
}


def load_user_positions() -> list:
    """
    加载用户持仓（user_positions.json优先，fallback wuqu_positions）
    格式: [{"symbol": "SNDKUSDT", "side": "LONG", "entry_price": 1592.0, "qty": 10, "ts": "..."}]
    """
    for f in [_USER_POSITIONS_FILE, _POSITIONS_FILE]:
        if f.exists():
            try:
                data = json.loads(f.read_text())
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and 'positions' in data:
                    return data['positions']
            except Exception:
                pass
    return []


def save_user_position(symbol: str, side: str, entry_price: float,
                       qty: float, note: str = '') -> bool:
    """记录新持仓到user_positions.json"""
    from datetime import datetime, timezone
    positions = load_user_positions()
    # 检查是否已有同标的持仓
    existing = [p for p in positions if p.get('symbol') == symbol and p.get('side') == side]
    if existing:
        # 加仓：更新均价
        total_qty = sum(p.get('qty', 0) for p in existing) + qty
        total_cost = sum(p.get('qty', 0) * p.get('entry_price', 0) for p in existing) + qty * entry_price
        avg_price = total_cost / total_qty if total_qty > 0 else entry_price
        for p in existing:
            p['entry_price'] = round(avg_price, 4)
            p['qty'] = total_qty
            p['add_on'] = p.get('add_on', 0) + 1
            p['note'] = f"加仓×{p['add_on']+1} 均价={avg_price:.2f}"
    else:
        # 首次建仓
        positions.append({
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'qty': qty,
            'ts': datetime.now(timezone.utc).isoformat(),
            'add_on': 0,
            'note': note or '首次建仓',
        })
    try:
        _USER_POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USER_POSITIONS_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def check_position_guard(symbol: str, current_price: float,
                         regime: str = 'default') -> dict:
    """
    P0核心函数：检查持仓风控状态
    返回:
      {
        'has_position': bool,
        'entry_price': float,
        'side': str,
        'pnl_pct': float,
        'alert_level': 'OK'|'WARN'|'STOP',
        'message': str,
        'sl_price': float,
        'is_addon': bool,
      }
    """
    positions = load_user_positions()
    sym_positions = [p for p in positions
                     if p.get('symbol', '').upper() == symbol.upper()]

    if not sym_positions:
        return {'has_position': False, 'alert_level': 'OK', 'message': ''}

    pos = sym_positions[-1]  # 取最新持仓
    entry = float(pos.get('entry_price', 0))
    side  = pos.get('side', 'LONG')
    qty   = float(pos.get('qty', 0))
    is_addon = pos.get('add_on', 0) > 0

    if entry <= 0 or current_price <= 0:
        return {'has_position': True, 'alert_level': 'OK', 'message': '持仓数据不完整'}

    # 计算PnL
    if side == 'LONG':
        pnl_pct = (current_price - entry) / entry
    else:
        pnl_pct = (entry - current_price) / entry

    # 止损阈值
    sl_pct  = SL_THRESHOLDS.get(regime, SL_THRESHOLDS['default'])
    warn_pct = WARN_THRESHOLDS['default']

    # 止损价
    if side == 'LONG':
        sl_price = round(entry * (1 - sl_pct), 2)
    else:
        sl_price = round(entry * (1 + sl_pct), 2)

    # 判断级别
    if pnl_pct <= -sl_pct:
        alert_level = 'STOP'
        msg = (
            f"🚨 [P0持仓止损] {symbol} {side}\n"
            f"  成本: ${entry:.2f}  现价: ${current_price:.2f}\n"
            f"  浮亏: {pnl_pct*100:.2f}%（超过止损线{sl_pct*100:.1f}%）\n"
            f"  止损价: ${sl_price:.2f}（{'已触发' if current_price <= sl_price and side=='LONG' else '未触发'}）\n"
            f"  ⚠️ 梵天止损铁律：必须执行止损，不等反弹！"
        )
    elif pnl_pct <= -warn_pct:
        alert_level = 'WARN'
        msg = (
            f"⚠️ [P0持仓预警] {symbol} {side}\n"
            f"  成本: ${entry:.2f}  现价: ${current_price:.2f}\n"
            f"  浮亏: {pnl_pct*100:.2f}%  止损线: -{sl_pct*100:.1f}%（${sl_price:.2f}）\n"
            f"  收紧止损，密切关注"
        )
    else:
        alert_level = 'OK'
        msg = (
            f"✅ [P0持仓正常] {symbol} {side}\n"
            f"  成本: ${entry:.2f}  现价: ${current_price:.2f}\n"
            f"  浮盈亏: {pnl_pct*100:+.2f}%"
        )

    return {
        'has_position': True,
        'entry_price': entry,
        'side': side,
        'qty': qty,
        'pnl_pct': round(pnl_pct, 4),
        'alert_level': alert_level,
        'message': msg,
        'sl_price': sl_price,
        'is_addon': is_addon,
    }


def fmt_position_guard(symbol: str, current_price: float,
                       regime: str = 'default') -> str:
    """格式化输出，供brahma_1hao_analysis注入"""
    result = check_position_guard(symbol, current_price, regime)
    if not result['has_position']:
        return ''
    lines = ['', '▌ P0 · 持仓风控']
    lines.append(result['message'])
    if result['is_addon']:
        lines.append(f"  ℹ️ 当前为加仓持仓（非首次建仓），注意总敞口控制")
    return '\n'.join(lines)
