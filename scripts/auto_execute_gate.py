"""
auto_execute_gate.py — 梵天自动执行门控 v1.1
苏摩宪法第六条 · 2026-06-19 授权
设计院修复 2026-06-19：门控4改为只计武曲自己开的仓（wuqu_positions）

职责：接收高分信号，通过五重门控后自动下单
入口：auto_execute(signal_dict)
"""
import json, time, pathlib, datetime
from pathlib import Path

# ── 苏摩授权边界常量 ──────────────────────────────────────────────
MIN_SCORE          = 138
MAX_OPEN_POSITIONS = 999  # 设计院2026-06-23授权：不限制开仓数量
MAX_POS_PCT_NAV    = 0.10  # 单笔最大10% NAV（保留风控）

IRON_DIRECTIONS = {
    'BEAR_TREND_SHORT', 'BEAR_EARLY_SHORT',
    'BULL_TREND_LONG',  'BULL_EARLY_LONG',
    # 参考级（n≥100，铁证未达 n≥1000 但方向正确，降权允许）
    'BEAR_RECOVERY_LONG', 'BULL_CORRECTION_SHORT',
}

HARD_BLOCK = {
    'BEAR_TREND_LONG',      # WR=44.6% 宪法级死穴
    'BULL_TREND_SHORT',     # WR=48.2% 宪法级死穴
    'BEAR_RECOVERY_SHORT',  # WR=47.9% avg_pnl=-0.235 封禁
    'BULL_CORRECTION_LONG', # WR=46.1% 封禁
}

# ── [P1-哲学修复 设计院 2026-06-24] 评分自然淘汰，不依赖黑名单 ──────────────
# 哲学：梵天的护城河是体制×方向×标的专属乘数矩阵（brahma_core _REGIME_MULT_ALTCOIN）
# 中小币弱信号经乘数压缩后天然低于138门槛，无需手工封禁
# 黑名单是对系统的不信任；乘数矩阵是对数据的信任
# 此列表仅保留极端死穴（WR<5% n≥20）作为最后安全网，随乘数矩阵成熟逐步清空
LIVE_WR_PENALTY = {
    # symbol_direction: (实盘WR%, n, 惩罚乘数)
    'NEARUSDT_SHORT':        (3.6, 28, 0.0),   # WR=3.6%  n=28 临时保留
    'MANAUSDT_SHORT':        (3.8, 26, 0.0),   # WR=3.8%  n=26 临时保留
    'BULL_CORRECTION_SHORT': (3.6, 28, 0.0),   # WR=3.6%  n=28 全体制死穴
}

DATA_DIR = pathlib.Path(__file__).parent.parent / 'data'

# LOT_SIZE 缓存（模块级，避免每次执行都调 exchangeInfo API）
_LOT_SIZE_CACHE: dict = {}   # symbol -> (step_size_str, qty_precision, min_step)

_TICK_SIZE_CACHE: dict = {}  # symbol -> tick_size float

def _get_lot_size(sym: str):
    """获取交易对 LOT_SIZE + PRICE_FILTER，带模块级缓存（重启清除）"""
    if sym in _LOT_SIZE_CACHE:
        return _LOT_SIZE_CACHE[sym]
    try:
        import urllib.request as _ur
        info = json.loads(_ur.urlopen(
            'https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=8).read())
        for f in info['symbols']:
            lot_step = tick = None
            for flt in f['filters']:
                if flt['filterType'] == 'LOT_SIZE':
                    lot_step = flt['stepSize']
                if flt['filterType'] == 'PRICE_FILTER':
                    tick = float(flt['tickSize'])
            if lot_step is not None:
                step  = lot_step
                prec  = len(step.rstrip('0').split('.')[-1]) if '.' in step else 0
                mstep = float(step)
                _LOT_SIZE_CACHE[f['symbol']] = (step, prec, mstep)
            if tick is not None:
                _TICK_SIZE_CACHE[f['symbol']] = tick
        if sym in _LOT_SIZE_CACHE:
            return _LOT_SIZE_CACHE[sym]
    except Exception:
        pass
    return ('0.001', 3, 0.001)  # fallback
LOG_FILE = DATA_DIR / 'auto_execute_log.jsonl'


def _load_state() -> dict:
    """加载 brahma_state.json"""
    try:
        return json.loads((DATA_DIR / 'brahma_state.json').read_text())
    except Exception:
        return {}


def _open_positions() -> list:
    """返回武曲自己开的持仓（从 brahma_state.wuqu_positions）
    
    设计院修复 v1.1 (2026-06-19):
    原来读 brahma_state['positions'] = 全局实盘持仓（含梵天/人工）
    → 导致武曲被外来持仓封死，从未成功执行任何订单
    修复：改读 brahma_state['wuqu_positions']，只计武曲自己的仓位
    """
    bs = _load_state()
    return bs.get('wuqu_positions', [])


def _add_wuqu_position(symbol: str, direction: str, entry: float, qty: float):
    """武曲开仓后写入 brahma_state.wuqu_positions"""
    try:
        state_path = DATA_DIR / 'brahma_state.json'
        bs = json.loads(state_path.read_text())
        wuqu_pos = bs.get('wuqu_positions', [])
        wuqu_pos.append({
            'symbol': symbol,
            'side': direction,
            'entry': entry,
            'qty': qty,
            'open_ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': 'wuqu_auto_execute',
        })
        bs['wuqu_positions'] = wuqu_pos
        state_path.write_text(json.dumps(bs, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f'[AutoExec] 写入 wuqu_positions 异常: {e}')


def _log(event: str, signal: dict, reason: str, result: dict = None):
    """写入执行日志"""
    entry = {
        'ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'event': event,
        'symbol': signal.get('symbol'),
        'direction': signal.get('direction'),
        'score': signal.get('score'),
        'reason': reason,
        'result': result,
    }
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def auto_execute(signal: dict, dry_run: bool = False) -> dict:
    """
    五重门控 + 执行入口
    Returns: {'executed': bool, 'reason': str, 'order': dict|None}
    """
    sym       = signal.get('symbol', '')
    direction = signal.get('direction', '')
    score     = float(signal.get('score', 0))
    regime    = signal.get('regime', '')
    entry_lo  = float(signal.get('entry_lo', 0))
    entry_hi  = float(signal.get('entry_hi', 0))

    # ── 门控0：valid + grade 白名单 ──────────────────────────────────
    # valid=False 的信号（含VIP策略mock、健康检查mock）一律拒绝
    if not signal.get('valid', True):  # 无valid字段时默认放行（兼容旧格式）
        # 特殊：score=999是mock/test，明确拒绝
        if score >= 999:
            r = f'score={score}疑似mock信号，拒绝执行'
            _log('BLOCKED', signal, r)
            return {'executed': False, 'reason': r, 'order': None}
    # grade白名单：只允许 神级/极强/VIP策略 进入实盘（排除'强'及以下）
    GRADE_WHITELIST = ('神级', '极强', 'VIP')
    grade_str = str(signal.get('grade', ''))
    if grade_str and not any(g in grade_str for g in GRADE_WHITELIST):
        # 仅当grade字段有值且不在白名单时才拒绝（无grade字段时依赖score门控）
        r = f'grade={grade_str} 不在白名单(神级/极强/VIP)，拒绝执行'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控1：score 门槛 ──────────────────────────────────────────
    if score < MIN_SCORE:
        r = f'score={score} < {MIN_SCORE}'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控2：死穴硬拒绝 ──────────────────────────────────────────
    combo = f'{regime}_{direction}'
    if combo in HARD_BLOCK:
        r = f'HARD_BLOCK: {combo} 宪法级死穴'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── [P0-3 设计院 2026-06-24] 实盘WR黑名单门控 ────────────────────────
    _live_key = f'{sym}_{direction}'
    if _live_key in LIVE_WR_PENALTY:
        _wr, _n, _mult = LIVE_WR_PENALTY[_live_key]
        if _mult == 0.0:
            r = f'LIVE_WR_BLOCK: {_live_key} 实盘WR={_wr}%(n={_n}) 封禁'
            _log('BLOCKED', signal, r)
            return {'executed': False, 'reason': r, 'order': None}
        else:
            _penalized = score * _mult
            if _penalized < MIN_SCORE:
                r = f'LIVE_WR_PENALTY: {_live_key} WR={_wr}%(n={_n}) 降权后={_penalized:.0f}<{MIN_SCORE}'
                _log('BLOCKED', signal, r)
                return {'executed': False, 'reason': r, 'order': None}

    # ── 门控3：熔断检查 ────────────────────────────────────────────
    bs = _load_state()
    if bs.get('breaker_active'):
        r = 'breaker_active=True，熔断期禁止开仓'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控3b：RiskGate v2（vnpy借鉴，苏摩111批准 2026-06-28）─────
    try:
        import sys as _sys2
        _sys2.path.insert(0, str(Path(__file__).parent))
        from brahma_risk_gate import check_entry as _rg_check, RISK_RULES as _rg_rules
        _open_pos_count = len(_open_positions())
        _nav_for_rg = 473.0  # 默认值，下面尝试实时获取
        try:
            import binance_fapi as _bf2
            _acct_rg = _bf2.get_account()[0]
            _nav_for_rg = float(_acct_rg['totalMarginBalance'])
        except Exception:
            pass
        _margin_est = _nav_for_rg * 0.10  # 估算保证金
        _rg_result = _rg_check(
            symbol=sym, nav=_nav_for_rg,
            margin_required=_margin_est,
            open_positions=_open_pos_count,
            signal_id=signal.get('signal_id', '')
        )
        if not _rg_result:
            _log('BLOCKED', signal, f'RiskGate v2: {_rg_result.reason}')
            return {'executed': False, 'reason': f'RiskGate v2: {_rg_result.reason}', 'order': None}
    except ImportError:
        pass  # RiskGate未安装时静默跳过（降级兼容）
    except Exception as _rg_err:
        import logging as _lg
        _lg.getLogger('auto_execute_gate').warning(f'RiskGate v2 检查异常（跳过）: {_rg_err}')

    # ── 门控3c：Layer 9~12（设计院 v6.0 2026-07-08）───────────────────────────
    try:
        from guardrails.layer_9_12 import check_layer9_12
        _l912_price = float(signal.get('price', 0) or 0)
        _l912_result = check_layer9_12(sym, _l912_price, direction)
        if not _l912_result['pass']:
            r = f'Layer9-12拦截[{_l912_result["blocked_by"]}]: {_l912_result["reasons"][-1] if _l912_result["reasons"] else ""}'
            _log('BLOCKED', signal, r)
            return {'executed': False, 'reason': r, 'order': None}
        # Layer9 仓位折扣
        if _l912_result.get('discount', 1.0) < 1.0:
            signal['_layer9_discount'] = _l912_result['discount']
            print(f'[Layer9-12] {sym} 仓位折扣×{_l912_result["discount"]}')
    except ImportError:
        pass  # Layer9-12 模块未安装时跳过
    except Exception as _l912_err:
        import logging as _lg
        _lg.getLogger('auto_execute_gate').warning(f'Layer9-12检查异常（跳过）: {_l912_err}')

    # ── 门控4：持仓数量上限 + 总保证金率上限 ──────────────────────
    open_pos = _open_positions()
    # [设计院修复 2026-06-23] NAV 实时从交易所获取，避免 brahma_state 旧值导致误拦截
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        import binance_fapi as _bf
        _acct = _bf.get_account()[0]
        nav = float(_acct['totalMarginBalance'])
    except Exception:
        nav = float(bs.get('nav', bs.get('nav_usdt', 130)))
    # 检查同标的同方向是否已持仓
    for p in open_pos:
        if p.get('symbol') == sym and p.get('side') == direction:
            r = f'{sym} {direction} 已有持仓，不重复开仓'
            _log('BLOCKED', signal, r)
            return {'executed': False, 'reason': r, 'order': None}
    # [设计院2026-06-23] MAX_OPEN_POSITIONS=999，持仓数不再限制
    # 总保证金率保护：改用实际margin计算（修复名义值误算问题）
    import requests as _req
    total_margin = 0.0
    for p in open_pos:
        try:
            _sym = p.get('symbol','')
            _qty = float(p.get('qty') or p.get('size') or 0)
            _lev_raw = p.get('leverage')
            _lev = float(_lev_raw) if _lev_raw is not None else 3.0
            _mark = float(p.get('mark') or p.get('mark_price') or 0)
            if _mark <= 0:
                _mark_r = _req.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={_sym}', timeout=3)
                _mark = float(_mark_r.json()['price'])
            if _qty > 0 and _mark > 0 and _lev > 0:
                total_margin += (_mark * _qty) / _lev
        except: pass
    if nav > 0 and total_margin / nav > 0.90:
        r = f'总保证金率={total_margin/nav:.0%} > 90% NAV，拒绝新增仓位'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控5：仓位计算 + sizing构建 ─────────────────────────────
    # [设计院修复 2026-06-23] sizing也用实时NAV，避免仓位偏小3.5倍
    try:
        _acct2 = _bf.get_account()[0]
        nav = float(_acct2['totalMarginBalance'])
    except Exception:
        nav = float(bs.get('nav_usdt', bs.get('nav', 130)))
    sig_pos_pct = float(signal.get('pos_pct', MAX_POS_PCT_NAV * 100)) / 100
    final_pct = min(sig_pos_pct, MAX_POS_PCT_NAV)
    pos_usdt = nav * final_pct

    entry_price = (entry_lo + entry_hi) / 2 if entry_hi > entry_lo else entry_lo
    leverage    = int(signal.get('leverage', 5))  # 设计院封印：默认5倍
    sl_price    = float(signal.get('stop_loss', 0))
    tp1_price   = float(signal.get('tp1', 0))
    tp2_price   = float(signal.get('tp2', 0))

    if entry_price <= 0 or sl_price <= 0 or tp1_price <= 0:
        r = f'信号缺字段: entry_lo={entry_lo} stop_loss={sl_price} tp1={tp1_price}'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # 获取交易所数量精度（带缓存）
    import math
    _, qty_precision, min_step = _get_lot_size(sym)

    # qty = (pos_usdt × leverage) / entry_price，向下取整到stepSize
    notional = pos_usdt * leverage
    raw_qty  = notional / entry_price
    qty      = math.floor(raw_qty / min_step) * min_step
    qty      = round(qty, qty_precision)

    if qty <= 0:
        r = f'qty=0: pos_usdt={pos_usdt:.2f} entry={entry_price:.6f} step={min_step}'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # 获取tick_size并对齐entry/sl/tp价格（防止-4014 / -1111）
    # [P0-fix 2026-06-24] _TICK_SIZE_CACHE 可能为空（进程内存缓存，子进程每次重启清空）
    # 若缓存未命中，强制调用 _get_lot_size 补填缓存，避免回退到错误的 0.01
    if sym not in _TICK_SIZE_CACHE:
        _get_lot_size(sym)   # 副作用：填充 _TICK_SIZE_CACHE
    from decimal import Decimal, ROUND_DOWN, ROUND_UP
    _tick = _TICK_SIZE_CACHE.get(sym, 0.01)
    _tick_d = Decimal(str(_tick))
    def _align_down(p):
        return float(Decimal(str(p)).quantize(_tick_d, rounding=ROUND_DOWN))
    def _align_up(p):
        return float(Decimal(str(p)).quantize(_tick_d, rounding=ROUND_UP))

    if direction == 'SHORT':
        entry_price_aligned = _align_down(entry_price)
        sl_price_aligned    = _align_up(sl_price)
        tp1_aligned         = _align_down(tp1_price)
        tp2_aligned         = _align_down(tp2_price) if tp2_price else tp2_price
    else:
        entry_price_aligned = _align_down(entry_price)
        sl_price_aligned    = _align_down(sl_price)
        tp1_aligned         = _align_up(tp1_price)
        tp2_aligned         = _align_up(tp2_price) if tp2_price else tp2_price

    sizing = {
        'qty':           qty,
        'qty_precision': qty_precision,
        'tick_size':     _tick,
        'entry_price':   entry_price_aligned,
        'sl_price':      sl_price_aligned,
        'tp1_price':     tp1_aligned,
        'tp2_price':     tp2_aligned,
        'notional':      round(qty * entry_price_aligned, 4),
        'pos_usdt':      round(pos_usdt, 2),
        'pos_pct':       round(final_pct * 100, 1),
        'nav':           nav,
        'leverage':      leverage,
    }

    # ── 执行 ───────────────────────────────────────────────────────
    print(f'[AutoExec] {sym} {direction} score={score} pos=${pos_usdt:.2f} dry_run={dry_run}')

    try:
        import sys
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from hunter_executor import execute_open
        result = execute_open(signal, sizing, dry_run=dry_run)
    except Exception as e:
        r = f'execute_open 异常: {e}'
        _log('ERROR', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    if result.get('success'):
        _log('EXECUTED', signal, 'OK', result)
        # 写入武曲独立持仓追踪（仅实盘，dry_run不写入）
        if not dry_run:
            fill_price = result.get('fill_price', result.get('avg_price', entry_lo))
            fill_qty   = result.get('qty', result.get('executedQty', 0))
            _add_wuqu_position(sym, direction, fill_price, fill_qty)
        return {'executed': True, 'reason': 'OK', 'order': result}
    else:
        err = result.get('error', 'unknown')
        _log('FAILED', signal, err, result)
        return {'executed': False, 'reason': err, 'order': result}


if __name__ == '__main__':
    # 干跑测试
    print('=== auto_execute_gate 干跑测试 ===')
    test_cases = [
        # 应通过
        {'symbol':'ETHUSDT','direction':'SHORT','score':165,'regime':'BEAR_TREND',
         'entry_lo':1700,'entry_hi':1720,'stop_loss':1779,'tp1':1602,'tp2':1500,'pos_pct':5,'leverage':5},
        # 应被死穴拒绝
        {'symbol':'BTCUSDT','direction':'LONG','score':150,'regime':'BEAR_TREND',
         'entry_lo':62000,'entry_hi':63000,'stop_loss':60000,'tp1':67000,'tp2':70000},
        # score不足
        {'symbol':'BTCUSDT','direction':'SHORT','score':120,'regime':'BEAR_EARLY',
         'entry_lo':63000,'entry_hi':64000,'stop_loss':65000,'tp1':60000,'tp2':58000},
    ]
    for t in test_cases:
        r = auto_execute(t, dry_run=True)
        mark = '✅' if r['executed'] else '❌'
        sym = t['symbol']; d = t['direction']; sc = t['score']; rs = r['reason']
        print(f'  {mark} {sym} {d} score={sc}: {rs}')
