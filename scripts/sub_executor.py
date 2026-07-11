#!/usr/bin/env python3
"""
sub_executor.py — 梵天子系统执行引擎
设计院架构 2026-07-03 | 苏摩授权

╔══════════════════════════════════════════════════════════════╗
║  职责：暴涨猎手 + OI猎手 独立子系统自动开单                  ║
║                                                              ║
║  架构原则：                                                  ║
║  1. 两个子系统完全独立于梵天主系统（不共享评分、不受死穴约束）║
║  2. 共享执行层（Binance API）和仓位管理层（持仓限制）        ║
║  3. 各自独立参数体系：SL/TP/仓位/杠杆 各不同                ║
║  4. 信号源：                                                 ║
║     PUMP: data/pump_signal_queue.jsonl                       ║
║     OI:   data/oi_candidates.json（实时调用分析）            ║
║                                                              ║
║  仓位规则（苏摩授权 2026-07-03）：                           ║
║  PUMP: NAV×1~3%（体制路由） × 3~5x                         ║
║  OI:   NAV×1~2%（mode路由） × 3~5x                         ║
║  全局MAX_POSITIONS共享主系统（≤20）                          ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys, os, json, time, math, requests, hmac, hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'brahma_brain'))

# ── 路径 ──────────────────────────────────────────────────────
PUMP_QUEUE      = BASE / 'data/pump_signal_queue.jsonl'
OI_CACHE        = BASE / 'data/oi_candidates.json'
WUQU_PATH       = BASE / 'data/wuqu_positions.json'
POS_STATE_PATH  = BASE / 'data/position_sl_state.json'
BRAHMA_STATE    = BASE / 'data/brahma_state.json'
SUB_LOG_PATH    = BASE / 'data/sub_executor_log.jsonl'
SUB_EXEC_SET    = BASE / 'data/sub_executed_set.json'

# ── 配置（苏摩授权 2026-07-03）────────────────────────────────
MAX_POSITIONS        = 20     # 全局最大持仓，与主系统共享
PUMP_SCORE_THRESHOLD = 75     # 暴涨猎手最低score（全力模式 苏摩授权：85→75）
OI_LAYERS_THRESHOLD  = 2      # OI猎手最低通过层数（v3: 3→2，配合oi_advanced_scanner新评分体系）
OI_SCORE_THRESHOLD   = 30.0   # OI最低oi_score（v3: 15→30，满分100新体系）
OI_MAX_AGE_H         = 2.0    # OI信号最大有效期（v3: 4→2，与30min cron对齐）
MIN_NOTIONAL         = 4.5   # [v7.0 2026-07-11 苏摩111] 5.0→4.5（NAV=99时notional=4.96被错误拦截，差额仅$0.04）
FAPI_BASE            = 'https://fapi.binance.com'

# ── 体制路由表：PUMP（铁证封印）────────────────────────────────
# 设计院修复 2026-07-05: size_pct统一为5%（NAV=100时notional=$5.15，恰好>=MIN_NOTIONAL=$5）
PUMP_PARAMS = {
    'BEAR_TREND':    {'size_pct': 0.05, 'tp_mult': 0.8, 'sl_atr': 2.0, 'lev': 3},
    'BEAR_EARLY':    {'size_pct': 0.05, 'tp_mult': 1.2, 'sl_atr': 2.0, 'lev': 3},
    'BEAR_RECOVERY': {'size_pct': 0.05, 'tp_mult': 2.0, 'sl_atr': 2.0, 'lev': 5},
    'CHOP_MID':      {'size_pct': 0.05, 'tp_mult': 1.2, 'sl_atr': 2.5, 'lev': 3},
    'BULL_TREND':    {'size_pct': 0.05, 'tp_mult': 1.5, 'sl_atr': 2.0, 'lev': 5},
    'BULL_EARLY':    {'size_pct': 0.05, 'tp_mult': 1.5, 'sl_atr': 2.0, 'lev': 5},
}
DEFAULT_PUMP_PARAMS = {'size_pct': 0.05, 'tp_mult': 1.0, 'sl_atr': 2.0, 'lev': 3}

# ── 体制路由表：OI（mode路由）──────────────────────────────────
# 设计院修复 2026-07-05: size_pct统一为5%（NAV=100时notional=$5.15，恰好>=MIN_NOTIONAL=$5）
OI_PARAMS = {
    'A_BULL': {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.5, 'lev': 5},  # mode A + 牛市
    'A_BEAR': {'size_pct': 0.05, 'sl_pct': 3.0, 'tp_mult': 1.0, 'lev': 3},  # mode A + 熊市
    'B':      {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.2, 'lev': 3},  # mode B
    'C':      {'size_pct': 0.05, 'sl_pct': 3.0, 'tp_mult': 1.0, 'lev': 3},  # mode C
}
DEFAULT_OI_PARAMS   = {'size_pct': 0.05, 'sl_pct': 2.5, 'tp_mult': 1.0, 'lev': 3}

# ── API ───────────────────────────────────────────────────────
try:
    from scripts.system_config import API_KEY, API_SECRET
except Exception:
    import importlib.util
    spec = importlib.util.spec_from_file_location('sc', BASE / 'scripts/system_config.py')
    _cfg = importlib.util.module_from_spec(spec); spec.loader.exec_module(_cfg)
    API_KEY    = _cfg.API_KEY
    API_SECRET = _cfg.API_SECRET


def _signed(method, path, params={}):
    p = dict(params)
    p['timestamp'] = int(time.time() * 1000)
    qs = '&'.join(f'{k}={v}' for k, v in p.items())
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    qs += f'&signature={sig}'
    h  = {'X-MBX-APIKEY': API_KEY}
    if method == 'GET':
        r = requests.get(f'{FAPI_BASE}{path}?{qs}', headers=h, timeout=8)
    elif method == 'POST':
        r = requests.post(f'{FAPI_BASE}{path}?{qs}', headers=h, timeout=8)
    elif method == 'DELETE':
        r = requests.delete(f'{FAPI_BASE}{path}?{qs}', headers=h, timeout=8)
    return r.json()


# ── 工具函数 ──────────────────────────────────────────────────
def _load_executed_set() -> set:
    try:
        return set(json.loads(SUB_EXEC_SET.read_text()))
    except Exception:
        return set()


def _save_executed_set(executed: set):
    SUB_EXEC_SET.write_text(json.dumps(list(executed)[-500:]))


def _load_active_positions() -> list:
    """从交易所实时拉取活跃持仓（含主系统持仓）"""
    try:
        pos = _signed('GET', '/fapi/v2/positionRisk')
        return [p for p in pos if isinstance(pos, list) and abs(float(p.get('positionAmt', 0))) > 0]
    except Exception:
        return []


def _get_nav() -> float:
    try:
        acct = _signed('GET', '/fapi/v2/account')
        return float(acct.get('totalWalletBalance', 130))
    except Exception:
        return 130.0


def _get_regime() -> str:
    try:
        s = json.loads(BRAHMA_STATE.read_text())
        return s.get('regime', 'UNKNOWN')
    except Exception:
        return 'UNKNOWN'


def _set_leverage(sym: str, lev: int):
    try:
        _signed('POST', '/fapi/v1/leverage', {'symbol': sym, 'leverage': lev})
    except Exception:
        pass


def _qty_precision(sym: str) -> int:
    try:
        ei = requests.get(f'{FAPI_BASE}/fapi/v1/exchangeInfo', timeout=5).json()
        info = next((s for s in ei.get('symbols', []) if s['symbol'] == sym), None)
        if info:
            for f in info.get('filters', []):
                if f['filterType'] == 'LOT_SIZE':
                    step = float(f['stepSize'])
                    return len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
    except Exception:
        pass
    return 2


def _write_log(record: dict):
    SUB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUB_LOG_PATH, 'a') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


# ════════════════════════════════════════════════════════════
# 子系统一：暴涨猎手执行器
# ════════════════════════════════════════════════════════════

def _execute_pump(signal: dict, nav: float, active_pos: list, executed: set) -> dict:
    """执行单个PUMP信号"""
    sig_id = signal.get('signal_id', '')
    sym    = signal.get('symbol', '')
    result = {'signal_id': sig_id, 'symbol': sym, 'sub': 'PUMP',
              'status': 'FAILED', 'reason': '', 'ts': time.time()}

    # ① 防重复
    if sig_id in executed:
        result['reason'] = 'DUPLICATE'
        return result

    # ② valid检查
    if not signal.get('valid') and signal.get('score', 0) < PUMP_SCORE_THRESHOLD:
        result['reason'] = f'score={signal.get("score")}<{PUMP_SCORE_THRESHOLD}'
        return result

    # ③ 持仓限制
    if len(active_pos) >= MAX_POSITIONS:
        result['reason'] = f'MAX_POSITIONS={MAX_POSITIONS}'
        return result

    # ④ 该标的已有持仓
    if any(p['symbol'] == sym for p in active_pos):
        result['reason'] = f'{sym}已有持仓'
        return result

    # ⑤ 信号过期（30min）
    age = time.time() - signal.get('ts', 0)
    if age > 1800:
        result['reason'] = f'信号过期{age/60:.0f}min'
        return result

    # ⑥ 获取实时价格
    try:
        r = requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price',
                         params={'symbol': sym}, timeout=5)
        px = float(r.json()['price'])
    except Exception as e:
        result['reason'] = f'价格获取失败: {e}'
        return result

    # ⑦ GapGate: 信号生成价 vs 当前价
    signal_px = signal.get('price', px)
    if signal_px > 0:
        gap = abs(px - signal_px) / signal_px * 100
        if gap > 5.0:  # PUMP信号宽容5%（高波动）
            result['reason'] = f'GapGate: 价格偏移{gap:.1f}%>5%'
            return result

    # ⑧ 仓位计算
    regime = _get_regime()
    params = PUMP_PARAMS.get(regime, DEFAULT_PUMP_PARAMS)
    notional = nav * params['size_pct']
    if notional < MIN_NOTIONAL:
        result['reason'] = f'仓位${notional:.2f}<最小${MIN_NOTIONAL}'
        return result

    lev = params['lev']
    _set_leverage(sym, lev)
    qty_prec = _qty_precision(sym)
    qty_raw  = notional * lev / px
    qty = round(math.floor(qty_raw * 10**qty_prec) / 10**qty_prec, qty_prec)
    if qty <= 0:
        result['reason'] = '数量精度为0'
        return result

    # ⑨ SL/TP
    atr_pct = signal.get('atr_pct', 3.0) or 3.0
    sl_pct  = atr_pct * params['sl_atr']
    tp_pct  = sl_pct * params['tp_mult']
    sl_px   = round(px * (1 - sl_pct / 100), 6)
    tp1_px  = round(px * (1 + tp_pct / 100), 6)

    pass  # [静默]

    # ⑩ 下单（市价）
    try:
        order = _signed('POST', '/fapi/v1/order', {
            'symbol':    sym,
            'side':      'BUY',
            'type':      'MARKET',
            'quantity':  qty,
        })
        if order.get('orderId'):
            fill_px  = float(order.get('avgPrice', px) or px)
            fill_qty = float(order.get('executedQty', qty) or qty)
            result.update({
                'status':   'FILLED',
                'order_id': order['orderId'],
                'fill_px':  fill_px,
                'fill_qty': fill_qty,
                'sl':       sl_px,
                'tp1':      tp1_px,
                'notional': fill_qty * fill_px,
                'lev':      lev,
                'regime':   regime,
            })
            executed.add(sig_id)
            pass  # [静默]
        else:
            result['reason'] = f'下单失败: {order.get("msg", str(order))}'
    except Exception as e:
        result['reason'] = f'API异常: {e}'

    return result


def run_pump_executor(nav: float, active_pos: list) -> list:
    """处理所有待执行的PUMP信号"""
    if not PUMP_QUEUE.exists():
        return []

    executed = _load_executed_set()
    lines    = PUMP_QUEUE.read_text().strip().splitlines()
    results  = []

    # 只处理PENDING信号，按score降序
    pending = []
    for line in lines:
        try:
            s = json.loads(line)
            if s.get('status') == 'PENDING':
                pending.append(s)
        except Exception:
            pass

    pending.sort(key=lambda x: -float(x.get('score', 0)))
    pass  # [静默]

    for sig in pending:
        if len(active_pos) >= MAX_POSITIONS:
            pass  # [静默]
            break
        r = _execute_pump(sig, nav, active_pos, executed)
        results.append(r)
        _write_log(r)
        if r['status'] == 'FILLED':
            # 更新活跃持仓（避免同一轮重复开）
            active_pos.append({'symbol': sig['symbol']})

    _save_executed_set(executed)

    # 标记已处理的信号（将PENDING → DONE/FAILED）
    updated_lines = []
    exec_syms = {r['symbol'] for r in results if r['status'] == 'FILLED'}
    for line in lines:
        try:
            s = json.loads(line)
            if s.get('status') == 'PENDING':
                if s.get('symbol') in exec_syms:
                    s['status'] = 'EXECUTED'
                elif time.time() - s.get('ts', 0) > 1800:
                    s['status'] = 'EXPIRED'
                updated_lines.append(json.dumps(s, ensure_ascii=False))
            else:
                updated_lines.append(line)
        except Exception:
            updated_lines.append(line)
    PUMP_QUEUE.write_text('\n'.join(updated_lines) + '\n' if updated_lines else '')

    return results


# ════════════════════════════════════════════════════════════
# 子系统二：OI猎手执行器
# ════════════════════════════════════════════════════════════

def run_oi_executor(nav: float, active_pos: list) -> list:
    """处理OI猎手候选信号"""
    if not OI_CACHE.exists():
        return []

    executed = _load_executed_set()
    results  = []

    try:
        cache    = json.loads(OI_CACHE.read_text())
        age_h    = (time.time() - (cache.get('scanned_at') or cache.get('updated_at', 0))) / 3600
        if age_h > OI_MAX_AGE_H:
            # [v3修复] 数据过期时触发自动重新扫描，而非静默跳过
            try:
                import subprocess
                subprocess.Popen(
                    ['python3', str(BASE / 'scripts/oi_advanced_scanner.py')],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
            return []

        candidates = cache.get('candidates', {})
        regime     = _get_regime()

        # 筛选：layers_pass≥4 + oi_score≥阈值 + action为buy_light/ENTER
        eligible = []
        for sym, c in candidates.items():
            layers = c.get('layers_pass', 0)
            oi_sc  = float(c.get('oi_score', 0) or 0)
            action = c.get('action', '')
            mode   = c.get('mode', 'C')

            if layers < OI_LAYERS_THRESHOLD:
                continue
            if oi_sc < OI_SCORE_THRESHOLD:
                continue
            if action not in ('buy_light', 'buy_full', 'ENTER', 'buy'):
                continue

            # 熊市OI信号：大幅降权（OI异常多数是空头堆积，需谨慎做多）
            if regime in ('BEAR_TREND', 'BEAR_EARLY') and mode != 'A':
                continue  # 熊市只执行mode A（逼空型）

            eligible.append((sym, c, mode))

        # 按oi_score降序
        eligible.sort(key=lambda x: -float(x[1].get('oi_score', 0)))
        pass  # [静默]

        for sym, c, mode in eligible:
            if len(active_pos) >= MAX_POSITIONS:
                pass  # [静默]
                break

            # 防重：基于symbol+时间窗口（4h内同标的不重复）
            oi_sig_id = f'OI_{sym}_{int((cache.get("scanned_at") or cache.get("updated_at",0))//3600)*3600}'
            if oi_sig_id in executed:
                continue

            if any(p['symbol'] == sym for p in active_pos):
                pass  # [静默]
                continue

            # 参数路由
            is_bull = regime in ('BULL_TREND', 'BULL_EARLY', 'BULL_CORRECTION')
            if mode == 'A':
                params = OI_PARAMS['A_BULL'] if is_bull else OI_PARAMS['A_BEAR']
            elif mode == 'B':
                params = OI_PARAMS['B']
            else:
                params = OI_PARAMS['C']

            # 实时价格
            try:
                r = requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price',
                                 params={'symbol': sym}, timeout=5)
                px = float(r.json()['price'])
            except Exception as e:
                pass  # [静默]
                continue

            # 仓位
            notional = nav * params['size_pct']
            if notional < MIN_NOTIONAL:
                continue

            lev = params['lev']
            _set_leverage(sym, lev)
            qty_prec = _qty_precision(sym)
            qty_raw  = notional * lev / px
            qty = round(math.floor(qty_raw * 10**qty_prec) / 10**qty_prec, qty_prec)
            if qty <= 0:
                continue

            sl_pct = params['sl_pct']
            tp_pct = sl_pct * params['tp_mult']
            sl_px  = round(px * (1 - sl_pct / 100), 6)
            tp1_px = round(px * (1 + tp_pct / 100), 6)

            oi_sc = float(c.get('oi_score', 0))
            pass  # [静默]

            result = {'signal_id': oi_sig_id, 'symbol': sym, 'sub': 'OI',
                      'status': 'FAILED', 'reason': '', 'ts': time.time(),
                      'oi_score': oi_sc, 'mode': mode, 'regime': regime}

            try:
                order = _signed('POST', '/fapi/v1/order', {
                    'symbol':   sym,
                    'side':     'BUY',
                    'type':     'MARKET',
                    'quantity': qty,
                })
                if order.get('orderId'):
                    fill_px  = float(order.get('avgPrice', px) or px)
                    fill_qty = float(order.get('executedQty', qty) or qty)
                    result.update({
                        'status':   'FILLED',
                        'order_id': order['orderId'],
                        'fill_px':  fill_px,
                        'fill_qty': fill_qty,
                        'sl':       sl_px,
                        'tp1':      tp1_px,
                        'notional': fill_qty * fill_px,
                        'lev':      lev,
                    })
                    executed.add(oi_sig_id)
                    active_pos.append({'symbol': sym})
                    pass  # [静默]
                else:
                    result['reason'] = str(order.get('msg', order))
            except Exception as e:
                result['reason'] = str(e)

            results.append(result)
            _write_log(result)

    except Exception as e:
        pass  # [静默]

    _save_executed_set(executed)
    return results


# ════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════

def _sync_wuqu_positions():
    """开仓后实时同步 wuqu_positions.json（设计院修复 2026-07-05）"""
    try:
        import time as _time
        from scripts.binance_fapi import get_positions
        import json as _json
        positions, _ = get_positions()
        active = [p for p in (positions or []) if abs(float(p.get('positionAmt', 0))) > 0]
        sl_state_path = WUQU_PATH.parent / 'position_sl_state.json'
        sl_data = _json.loads(sl_state_path.read_text()) if sl_state_path.exists() else {}
        wuqu = []
        for p in active:
            sym  = p.get('symbol', '')
            amt  = float(p.get('positionAmt', 0))
            side = 'LONG' if amt > 0 else 'SHORT'
            entry= float(p.get('entryPrice', 0))
            mark = float(p.get('markPrice', entry))
            pnl  = float(p.get('unRealizedProfit', 0))
            sl_d = sl_data.get(sym, {})
            wuqu.append({
                'symbol': sym, 'side': side, 'size': abs(amt),
                'entry_price': entry, 'mark_price': mark,
                'stop_loss':   sl_d.get('sl_price',  round(entry*(0.97 if side=='LONG' else 1.03),4)),
                'take_profit': sl_d.get('tp1_price', round(entry*(1.03 if side=='LONG' else 0.97),4)),
                'leverage': float(p.get('leverage', 5)),
                'notional_usdt': round(abs(amt)*mark, 4),
                'unrealized_pnl': round(pnl, 4),
                'updated_at': _time.time(), 'success': True,
            })
        WUQU_PATH.write_text(_json.dumps(wuqu, ensure_ascii=False, indent=2))
        pass  # [静默]
    except Exception as _e:
        pass  # [静默]


def run():
    # ── 文件锁：防止多实例并发（P1加固 2026-07-03）──────────────
    import fcntl
    _lock_path = BASE / 'data/.sub_executor.lock'
    try:
        _lock_fd = open(_lock_path, 'w')
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        pass  # [静默]
        pass  # [静默]
        return
    try:
        _run_sub_locked()
    finally:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        _lock_fd.close()


def _run_sub_locked():
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    pass  # [静默]

    nav        = _get_nav()
    active_pos = _load_active_positions()
    regime     = _get_regime()
    pass  # [静默]

    if len(active_pos) >= MAX_POSITIONS:
        pass  # [静默]
        pass  # [静默]
        return

    # ── 子系统一：暴涨猎手 ───────────────────────────────────
    pump_results = run_pump_executor(nav, active_pos)
    pump_filled  = [r for r in pump_results if r['status'] == 'FILLED']

    # ── 子系统二：OI猎手 ────────────────────────────────────
    oi_results  = run_oi_executor(nav, active_pos)
    oi_filled   = [r for r in oi_results if r['status'] == 'FILLED']

    # ── 汇报 ─────────────────────────────────────────────────
    total_filled = len(pump_filled) + len(oi_filled)
    if total_filled > 0:
        pass  # [静默]
        for r in pump_filled + oi_filled:
            sub   = r.get('sub', '?')
            sym   = r.get('symbol', '?')
            fpx   = r.get('fill_px', 0)
            fqty  = r.get('fill_qty', 0)
            nom   = r.get('notional', 0)
            print(f'  [{sub}] {sym} qty={fqty} @${fpx:.4f} 名义=${nom:.2f}')
        # 设计院修复 2026-07-05: 开仓成功后实时同步 wuqu_positions.json
        _sync_wuqu_positions()
    else:
        skipped_pump = len([r for r in pump_results if r['status'] == 'FAILED'])
        skipped_oi   = len([r for r in oi_results if r['status'] == 'FAILED'])
        pass  # [静默]
        pass  # [静默]

    pass  # [静默]


if __name__ == '__main__':
    run()
