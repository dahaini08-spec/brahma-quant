#!/usr/bin/env python3
"""
🔱 梵天 v11 · 统一执行引擎  execute_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计院裁决（2026-05-15）：
  职责：纯执行层 — 拿信号包 → 下市价单 → 挂止损单 → 回写记录
  不含：信号判断 / 体制检测 / RSI计算（全部交给 signal_router_v3）

来源：从 dharma_live.py 执行层原样提取
      保留全部 FIX-03 补丁（avgPrice 回查 / D8 字段完整性）

用法：
  from lana.execute_engine import place_order, adapt_signal, get_balance, get_open_pos

  sig_v11 = route_all_signals(...)   # signal_router_v3 输出
  sig_ex  = adapt_signal(sig_v11)    # 字段适配
  result  = place_order(sig_ex, balance, dry_run=False)  # [R4-fix audit-2026-06-17] 注意: dry_run=False为实单，由ws_guardian调用时已有paper/live模式判断
"""

# ── [设计院 2026-05-19] 架构审计注意 ───────────────────────────────
# 此文件被 brahma_coordinator.py L1校验 和 ast_scanner 引用
# 正常实盘下单主路: brahma_core → hunter_main → hunter_executor
# execute_engine 为旧版兼容层，新功能请勿在此扩展
# ──────────────────────────────────────────────────────────────────
import json, os, subprocess
from datetime import datetime, timezone
from typing import Dict, Optional

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNAL_LOG = os.path.join(_ROOT, 'signals', 'signal_history.json')
EXEC_LOG   = os.path.join(_ROOT, 'logs', 'execute_engine.log')
os.makedirs(os.path.dirname(SIGNAL_LOG), exist_ok=True)
os.makedirs(os.path.dirname(EXEC_LOG),   exist_ok=True)


# ─── binance-cli 封装（原样搬运自 dharma_live）────────────────────

def _cli(*args, timeout=15):
    try:
        r = subprocess.run(
            ['binance-cli'] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        out = r.stdout.strip()
        return (True, json.loads(out)) if out else (False, r.stderr.strip())
    except Exception as e:
        return False, str(e)


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(EXEC_LOG, 'a') as f:
            f.write(f"{datetime.now(timezone.utc).date()} {line}\n")
    except Exception:
        _ = None  # 非致命


# ─── 账户查询（原样搬运）────────────────────────────────────────────

def get_balance() -> float:
    """查询可用 USDT 余额"""
    ok, d = _cli('futures-usds', 'futures-account-balance-v3')
    if not ok:
        return 0.0
    try:
        for b in (d if isinstance(d, list) else []):
            if b.get('asset') == 'USDT':
                return float(b.get('availableBalance', 0))
    except Exception:
        _ = None  # 非致命
    return 0.0


def get_open_pos() -> int:
    """查询当前持仓数"""
    ok, d = _cli('futures-usds', 'get-position-risk-v3')
    if not ok:
        return 0
    try:
        return sum(1 for p in (d if isinstance(d, list) else [])
                   if abs(float(p.get('positionAmt', 0))) > 0)
    except Exception:
        return 0


# ─── 字段适配器：v11信号包 → execute 标准格式 ─────────────────────

def adapt_signal(sig_v11: dict) -> dict:
    """
    将 signal_router_v3 输出的信号包适配为 execute 执行格式
    字段映射（7项全部对齐）：
      币种.lower()  → symbol
      方向（做多→多）→ direction
      当前价        → entry
      止损价        → stop_loss
      止盈T1        → tp1
      止盈T2        → tp2
      仓位%NAV/100  → position_pct
    """
    direction_map = {'做多': '多', '做空': '空'}
    direction_raw = sig_v11.get('方向', '')
    direction = direction_map.get(direction_raw, direction_raw)

    return {
        # 执行层核心字段
        'symbol':       sig_v11.get('币种', '').lower(),
        'direction':    direction,
        'entry':        sig_v11.get('当前价', 0),
        'stop_loss':    sig_v11.get('止损价', 0),
        'tp1':          sig_v11.get('止盈T1', 0),
        'tp2':          sig_v11.get('止盈T2', 0),
        'position_pct': _apply_regime_kelly(
            base_pct=round(sig_v11.get('仓位%NAV', 1.5) / 100, 6),
            regime=sig_v11.get('体制', ''),
            direction=direction_raw
        ),

        # 传递 v11 元数据（用于记录）
        'signal_id':    sig_v11.get('信号ID', ''),
        'strength':     sig_v11.get('强度', ''),
        'channel':      sig_v11.get('信号来源', 'T'),
        'regime':       sig_v11.get('体制', ''),
        'ci_low':       sig_v11.get('CI下界', 0),
        'exit_track':   sig_v11.get('出场轨道', ''),
        'rsi_1h':       sig_v11.get('RSI_1H', 0),
        'rr1':          sig_v11.get('盈亏比T1', 0),
        'score':        sig_v11.get('CI下界', 0),  # 用 CI下界 作为 score 字段

        # 原始 v11 包完整保留
        '_v11': sig_v11,
    }


# ─── 核心执行函数（原样搬运自 dharma_live.execute + FIX-03）────────

def place_order(sig: Dict, balance: float, dry_run: bool = False) -> Dict:
    """
    下市价单 + 挂止损单
    FIX-03: avgPrice 回查写入 entry_price
    """
    sym  = sig['symbol'].upper()
    side = 'BUY' if sig['direction'] == '多' else 'SELL'
    sl_s = 'SELL' if side == 'BUY' else 'BUY'

    entry        = sig['entry']
    position_pct = sig['position_pct']
    notional     = balance * position_pct * 10   # 10x 杠杆
    qty          = round(notional / entry, 3) if entry > 0 else 0

    if qty <= 0:
        return {'ok': False, 'reason': f'qty计算异常: notional={notional:.2f} entry={entry}'}

    _log(f"{'[DRY]' if dry_run else '[LIVE]'} {sym} {side} qty={qty} notional={notional:.2f}U "
         f"sl={sig['stop_loss']} tp1={sig['tp1']}")

    # ── dry_run 分支 ──────────────────────────────────────────────
    if dry_run or sig.get('demo'):
        sig['entry_price'] = sig.get('entry_price') or sig.get('entry')
        return {'ok': True, 'dry_run': True, 'qty': qty, 'notional': round(notional, 2)}

    # ── 真实下单 ──────────────────────────────────────────────────
    ok, order = _cli(
        'futures-usds', 'new-order',
        '--symbol', sym,
        '--side', side,
        '--type', 'MARKET',
        '--quantity', str(qty)
    )
    if not ok:
        _log(f'❌ 下单失败: {order}')
        return {'ok': False, 'reason': str(order)}

    order_id = str(order.get('orderId', ''))
    _log(f'✅ 市价单成交 orderId={order_id}')

    # FIX-03: 查询实际成交价写入 entry_price
    try:
        _fill_ok, _fill = _cli(
            'futures-usds', 'get-order',
            '--symbol', sym,
            '--order-id', order_id
        )
        if _fill_ok:
            avg_price = float(_fill.get('avgPrice') or _fill.get('price') or 0)
            if avg_price > 0:
                sig['entry_price'] = avg_price
                sig['qty']         = float(_fill.get('executedQty', qty))
                sig['order_id']    = order_id
                _log(f'   avgPrice={avg_price}  executedQty={sig["qty"]}')
    except Exception:
        sig['entry_price'] = sig.get('entry_price') or sig.get('entry')

    # 挂止损单
    sl_price = sig['stop_loss']
    ok_sl, _ = _cli(
        'futures-usds', 'new-order',
        '--symbol', sym,
        '--side', sl_s,
        '--type', 'STOP_MARKET',
        '--quantity', str(qty),
        '--stopPrice', str(sl_price),
        '--reduceOnly', 'true'
    )
    if ok_sl:
        _log(f'   止损单已挂 stopPrice={sl_price}')
    else:
        _log(f'   ⚠️ 止损单挂单失败，启动order_watcher软件止损 stopPrice={sl_price}')
        # P1: order_watcher接管软件止损 (2026-05-17)
        try:
            import sys as _sys_ow, os as _os_ow
            _sys_ow.path.insert(0, _os_ow.path.dirname(_os_ow.path.dirname(__file__)))
            from order_watcher import register_limit_order, start_watcher
            register_limit_order(
                symbol    = sym,
                order_id  = 'sl_soft_' + order_id,
                side      = sl_s,
                qty       = float(qty),
                limit_px  = float(sl_price),
                signal_id = sig.get('signal_id',''),
            )
            start_watcher()
            _log(f'   ✅ order_watcher软件止损已注册 {sym} SL={sl_price}')
        except Exception as _e_ow:
            _log(f'   order_watcher注册失败: {_e_ow}')

    return {'ok': True, 'order_id': order_id, 'qty': qty, 'notional': round(notional, 2)}


# ─── 信号记录（FIX-03 字段完整性保证）────────────────────────────

def save_signal(sig: Dict, exec_result: Dict):
    """
    将执行后的信号写入 signal_history.json
    FIX-03: 确保 D8 必填字段完整
    """
    now_str = datetime.now(timezone.utc).isoformat()

    record = {
        # 标识
        'id':           sig.get('signal_id') or f"EX{int(datetime.now(timezone.utc).timestamp())}",
        'symbol':       sig.get('symbol', '').upper(),
        'direction':    'LONG' if sig.get('direction') == '多' else 'SHORT',
        'score':        sig.get('score', 0),

        # 入场（FIX-03）
        'entry_price':  sig.get('entry_price') or sig.get('entry'),
        'entry':        sig.get('entry_price') or sig.get('entry'),
        'stop_loss':    sig.get('stop_loss'),
        'tp1':          sig.get('tp1'),
        'target1':      sig.get('tp1'),
        'tp2':          sig.get('tp2'),

        # 仓位
        'qty':          exec_result.get('qty', 0),
        'notional':     exec_result.get('notional', 0),
        'position_pct': sig.get('position_pct', 0),
        'order_id':     sig.get('order_id') or exec_result.get('order_id', ''),

        # 执行元数据
        'dry_run':      exec_result.get('dry_run', False),
        'channel':      sig.get('channel', 'T'),
        'strength':     sig.get('strength', ''),
        'regime':       sig.get('regime', ''),
        'ci_low':       sig.get('ci_low', 0),
        'exit_track':   sig.get('exit_track', ''),
        'rsi_1h':       sig.get('rsi_1h', 0),

        # 生命周期（FIX-03）
        'status':       'PENDING',
        'result':       None,
        'pnl':          None,
        'pnl_pct':      None,
        'exit_price':   None,
        'close_time':   None,
        'close_reason': None,
        'created_at':   now_str,
        'timestamp':    now_str,

        # 引擎版本标记
        '_engine':      'execute_engine_v11',
    }

    # FIX-03: entry_price 最终兜底
    if not record['entry_price'] and sig.get('order_id'):
        try:
            ok, fill = _cli(
                'futures-usds', 'get-order',
                '--symbol', sig['symbol'].upper(),
                '--order-id', str(sig['order_id'])
            )
            if ok:
                avg = float(fill.get('avgPrice') or fill.get('price') or 0)
                if avg > 0:
                    record['entry_price'] = avg
                    record['entry']       = avg
        except Exception:
            _ = None  # 非致命

    # 追加写入
    hist = []
    if os.path.exists(SIGNAL_LOG):
        try:
            with open(SIGNAL_LOG) as f:
                hist = json.load(f)
        except Exception:
            _ = None  # 非致命
    hist.append(record)
    with open(SIGNAL_LOG, 'w', encoding='utf-8') as f:
        json.dump(hist[-200:], f, ensure_ascii=False, indent=2)

    _log(f'📝 信号已记录 → {record["id"]}  {record["symbol"]} {record["direction"]}')
    return record


def _apply_regime_kelly(base_pct: float, regime: str, direction: str) -> float:
    """
    E1体制感知Kelly乘数 · 2026-05-17
    N10达摩院落地(2026-05-17):
      BEAR_CRASH做空: SHORT全负收益 → 改用flat0.8x（不放大）
      BEAR_CRASH做多: RSI<20最强信号，维持原乘数
    """
    try:
        import sys as _s, os as _o
        _s.path.insert(0, _o.path.join(_o.path.dirname(__file__), '..'))
        from lana.hunter_v2.hunter_config import get_regime_kelly_mult, KELLY_MAX, KELLY_BASE
        mult = get_regime_kelly_mult(regime, direction)

        # N10落地: BEAR_CRASH做空 → flat固定仓位(KELLY_BASE×0.8)
        # 不依赖信号分数放大，防止短空亏损扩大
        is_bear_short = (regime == 'BEAR_CRASH' and direction in ('做空','SHORT'))
        if is_bear_short:
            # flat模式: 固定用KELLY_BASE×0.8，不随信号分数浮动
            flat_pct = KELLY_BASE * 0.8
            return round(min(flat_pct, KELLY_MAX), 6)

        adjusted = base_pct * mult
        return round(min(adjusted, KELLY_MAX), 6)
    except Exception:
        return base_pct
