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
MIN_SCORE          = 140  # [铁证封印 2026-08-05 设计院自主] 155→140
# 历史WR反推：grade≥极强+score≥140 WR=58%(n=19) EV正向；score115-145死亡区已绕过
# 执行条件：MIN_SCORE=140 AND grade_num≥80（见下方门控）

# [铁证封印 2026-08-06 设计院自主] 标的差异化阈值
# BTC WR=33%(n=52) → 需要score≥145才有正EV；ETH WR=58%(n=19) → 140足够
MIN_SCORE_BTC      = 145
MIN_SCORE_ETH      = 140
MIN_SCORE_OTHER    = 148

# [铁证封印 2026-08-06 设计院自主] TRADFI代币硬封禁
# 实证：22条信号WR=0%，梵天SMC/OB逻辑在美股代币上结构性失效
TRADFI_HARD_BLOCK = {
    'SNDKUSDT','TSLAUSDT','AAPLUSDT','NVDAUSDT','MSFTUSDT','GOOGLUSDT',
    'AMZNUSDT','METAUSDT','AMDUSDT','SAMSUNGUSDT','SKHYNIXUSDT',
    'SOXLUSDT','SOXSUSDT','MSTRUSDT','COINUSDT',
}

# [铁证封印 2026-08-06 设计院自主] rr1过高封禁
# 实证：score 120-140 + rr1>1.5 → EV=-1.0（全亏），TP太远无法触达
MAX_RR1_AUTO       = 1.8  # rr1>1.8 且 score<155 → 拒绝执行（神级除外）
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
    """返回武曲自己开的持仓
    [B2修复 2026-07-24]: 统一读 wuqu_positions.json（SSOT）
    不再读brahma_state.wuqu_positions（存在旧字符串鬼数据，已证实会导致拥堵开单）
    """
    wuqu_path = DATA_DIR / 'wuqu_positions.json'
    try:
        raw = json.loads(wuqu_path.read_text())
        if isinstance(raw, list):
            return raw
        elif isinstance(raw, dict):
            return list(raw.values())
    except Exception:
        pass
    return []


def _add_wuqu_position(symbol: str, direction: str, entry: float, qty: float):
    """武曲开仓后写入 wuqu_positions.json
    [B2修复 2026-07-24]: 不再写 brahma_state，统一写 wuqu_positions.json
    """
    wuqu_path = DATA_DIR / 'wuqu_positions.json'
    try:
        raw = json.loads(wuqu_path.read_text()) if wuqu_path.exists() else []
        if not isinstance(raw, list):
            raw = list(raw.values()) if isinstance(raw, dict) else []
        raw = [p for p in raw if p.get('symbol') != symbol]  # 去重
        raw.append({
            'symbol': symbol,
            'side': direction,
            'entry_price': entry,
            'size': qty,
            'open_ts': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'source': 'auto_execute_gate',
        })
        wuqu_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    except Exception:
        pass  # [静默]


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


# ─── [P1 设计院 2026-07-18 FIX-M4] 信号去重门控 · 文件持久化版 ─────────────────
# 修复: _DEDUP_CACHE内存字典 → cron每次新进程清空 = 完全无效
# 改用 auto_executed_signals.json 文件持久化，跨进程有效
_DEDUP_FILE  = Path(__file__).parent.parent / 'data' / 'auto_executed_signals.json'
_DEDUP_TTL   = 300   # 保留参数，不再使用（文件去重不设过期）

def _dedup_check(signal: dict) -> tuple:
    """[FIX-M4] 文件持久化去重，返回 (is_dup, reason)"""
    sig_id = signal.get('signal_id', '')
    sym    = signal.get('symbol', '?')
    dir_   = (signal.get('direction') or signal.get('signal_dir', '?'))[:5]
    score  = int(float(signal.get('score', 0) or 0) // 10 * 10)
    key    = sig_id if sig_id else f'{sym}:{dir_}:{score}'

    try:
        _ids = json.loads(_DEDUP_FILE.read_text()) if _DEDUP_FILE.exists() else []
        if not isinstance(_ids, list):
            _ids = list(_ids) if hasattr(_ids, '__iter__') else []
    except Exception:
        _ids = []

    if key in _ids:
        return True, f'去重门控(文件): {key} 已在auto_executed_signals中'

    # 写入（保留最近500条）
    _ids.append(key)
    if len(_ids) > 500:
        _ids = _ids[-500:]
    try:
        _DEDUP_FILE.write_text(json.dumps(_ids, ensure_ascii=False))
    except Exception:
        pass
    return False, ''
# ─── [END P1] ─────────────────────────────────────────────────────────────────


def auto_execute(signal: dict, dry_run: bool = False) -> dict:
    """
    五重门控 + 执行入口
    Returns: {'executed': bool, 'reason': str, 'order': dict|None}
    [设计院2026-07-18 苏摩111封印] 全局矛盾修复版
    """
    # [P1 去重门控] 文件持久化去重（防cron进程重启后内存缓存失效）
    _is_dup, _dup_reason = _dedup_check(signal)
    if _is_dup:
        return {'executed': False, 'reason': _dup_reason, 'order': None}  # FIX: blocked→executed统一

    # [P2 设计院 2026-07-13 修复 2026-07-18]
    _score_p2   = float(signal.get('score_final', signal.get('score', 0)) or 0)
    # FIX-M1: timing空字符串时get默认值不生效 → 改为 or 'UNKNOWN'
    _timing_p2  = signal.get('timing_status') or signal.get('timing_badge') or 'UNKNOWN'
    _regime_p2  = signal.get('regime', '?')
    _dir_p2     = signal.get('direction', signal.get('signal_dir', '?'))

    # 规则1: score≥155 但 timing明确为WAIT/STANDBY → 禁止执行
    # FIX-M1: 空字符串/UNKNOWN视为timing未注入，不触发拦截（auto_executor TIER_1逻辑一致）
    if _score_p2 >= 155 and _timing_p2 in ('WAIT', 'STANDBY'):
        return {'executed': False,
                'reason': f'P2高分timing拦截: score={_score_p2:.0f} timing={_timing_p2}（等待入场时机）',
                'order': None}

    # 规则2: BULL_TREND LONG WR门控 [铁证封印 2026-08-02 设计院自主]
    # 实盘数据 n=192: 全区间WR均<45% (120-139: WR=26.1%, 140-154: WR=28.6%, 160+: WR=15.4%)
    # BULL_TREND LONG = 系统性亏损方向，全线进入OBSERVE模式，暂停自动执行
    if _regime_p2 == 'BULL_TREND' and _dir_p2 == 'LONG':
        _sw_p2 = {}
        try:
            import json as _json_sw, os as _os_sw
            _sw_file = _os_sw.path.join(_os_sw.path.dirname(__file__), '..', 'data', 'signal_weights.json')
            if _os_sw.path.exists(_sw_file):
                _sw_all = _json_sw.loads(open(_sw_file).read())
                if _score_p2 >= 160:   _sw_p2 = _sw_all.get('BULL_TREND:LONG:160+', {})
                elif _score_p2 >= 155: _sw_p2 = _sw_all.get('BULL_TREND:LONG:155-159', {})
                elif _score_p2 >= 140: _sw_p2 = _sw_all.get('BULL_TREND:LONG:140-154', {})
                elif _score_p2 >= 120: _sw_p2 = _sw_all.get('BULL_TREND:LONG:120-139', {})
                else:                  _sw_p2 = _sw_all.get('BULL_TREND:LONG:<120', {})
        except Exception:
            pass
        _action_p2 = _sw_p2.get('action', 'BLOCK')
        _min_n_p2  = _sw_p2.get('min_n_required', 0)
        if _action_p2 in ('BLOCK', 'OBSERVE'):
            return {'executed': False,
                    'reason': f'P2 WR门控: BULL_TREND LONG score={_score_p2:.0f} action={_action_p2} WR全线<45% 暂停执行（等WR≥55%自动解锁）',
                    'order': None}

    # 规则3: OBV反向时 score<165 禁止执行
    # 规则2b: Kronos方向置信度门控 [顶层决策 2026-08-02 设计院自主]
    # 根因: 历史BULL_TREND LONG 192条 WR=32.8%，主因是p_up方向不匹配
    # 门控: 做多需p_up>0.55，做空需p_up<0.45
    _p_up_gate = float(signal.get('s23_p_up', -1) or -1)
    if _p_up_gate >= 0:  # 有有效p_up数据才门控
        if _dir_p2 == 'LONG'  and _p_up_gate < 0.55:
            return {'executed': False,
                    'reason': f'Kronos方向门控: LONG需p_up>0.55，当前p_up={_p_up_gate:.2f}',
                    'order': None}
        if _dir_p2 == 'SHORT' and _p_up_gate > 0.45:
            return {'executed': False,
                    'reason': f'Kronos方向门控: SHORT需p_up<0.45，当前p_up={_p_up_gate:.2f}',
                    'order': None}

    # 规则3 OBV门控:
    _risk_p2 = signal.get('_risk_flags', [])
    if 'OBV_DIVERGENCE' in _risk_p2 and _score_p2 < 165:
        return {'executed': False,
                'reason': f'P2 OBV反向拦截: score={_score_p2:.0f}<165 量能未配合',
                'order': None}
    sym       = signal.get('symbol', '')
    direction = signal.get('direction', '')
    score     = float(signal.get('score', 0))
    regime    = signal.get('regime', '')
    entry_lo  = float(signal.get('entry_lo', 0))
    entry_hi  = float(signal.get('entry_hi', 0))

    # ── 门控-1：时段硬封禁 22:00-01:00 UTC ─────────────────────────────
    # [铁证封印 2026-08-06 设计院] 亚洲深夜低流动性时段，价格随机性高，技术信号可信度低
    import datetime as _dt
    _utc_hour = _dt.datetime.utcnow().hour
    if _utc_hour >= 22 or _utc_hour < 1:
        # 神级信号（score≥17 5）调过时段封禁
        if score < 175:
            r = f'时段封禁: UTC {_utc_hour:02d}:00 属亚洲深夜低流动性时段(22-01 UTC)，score={score:.0f}<175'
            _log('BLOCKED', signal, r)
            return {'executed': False, 'reason': r, 'order': None}

    # ── 门控0：valid + grade 门控 ──────────────────────────────────
    # [FIX-M3 2026-07-18 苏摩111] valid=False逻辑修正：valid字段存在且为False时直接BLOCKED
    # 原Bug: valid=False只拦截score≥999的mock，普通valid=False信号流入后续门控
    # 修复: valid字段明确为False → 直接BLOCKED（无valid字段=旧格式，保持放行兼容）
    _valid = signal.get('valid', None)
    if _valid is False:  # 明确False，不含None/缺失
        r = f'valid=False，信号未通过DharmaBridge质量门控（score={score:.0f}）'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}
    # [P1-1 设计院 2026-07-18 苏摩111封印] grade门控统一为数值门控，彻底废除文字白名单
    # 根因：文字白名单('神级'/'极强'/'VIP')导致数值grade永远不匹配 → 8天0执行
    # v2修复：文字格式grade兜底路径也改为数值提取，提取失败直接放行（score≥155已是主要门控）
    # [FIX-ROOT 2026-07-22 苏摩111] 优先读grade_num整数字段，彻底解决中文grade误杀
    try:
        from brahma_brain.grade_utils import parse_grade as _pg_gate
    except ImportError:
        from grade_utils import parse_grade as _pg_gate
    _grade_num = signal.get('grade_num') or _pg_gate(
        signal.get('grade', 0),
        structure_grade=int(signal.get('structure_grade', 0) or 0),
        effective_grade=float(signal.get('effective_grade', 0) or 0),
    )
    if _grade_num > 0 and _grade_num < 70:
        r = f'grade={_grade_num} < 70，结构质量不足，拒绝执行'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # [铁证门控 2026-08-05 设计院自主] grade≥极强必要条件
    # 根据：score独立无效(115-145死亡区WR=0~11%)，grade是更可靠分层器
    # 证据：grade≥极强(80+)+score>=140 WR=58%(n=19) vs grade=强(60-79) WR=23%
    _grade_emoji = signal.get('grade', '')
    _grade_str_ok = str(_grade_emoji) in ('🔴神级', '🟠极强', '🔴神级+', '🟠极强+')
    _grade_num_ok = _grade_num >= 80
    if not (_grade_str_ok or _grade_num_ok):
        r = f'grade不足(需≥极强/80+，实际={_grade_emoji or _grade_num})，WR风险区拒绝执行'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控1：score 门槛（标的差异化）─────────────────────────────
    # [铁证封印 2026-08-06 设计院] BTC WR=33% → 需score≥145；ETH WR=58% → 140足够
    if sym == 'BTCUSDT':
        _min_score_eff = MIN_SCORE_BTC
    elif sym == 'ETHUSDT':
        _min_score_eff = MIN_SCORE_ETH
    elif sym in TRADFI_HARD_BLOCK:
        r = f'TRADFI_BLOCK: {sym} 代币化美股，梵天SMC逻辑结构性失效(22条信号WR=0%)'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}
    else:
        _min_score_eff = MIN_SCORE_OTHER
    if score < _min_score_eff:
        r = f'score={score} < {_min_score_eff}({sym}专属门槛)'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控2：死穴硬拒绝 ──────────────────────────────────────────
    combo = f'{regime}_{direction}'
    if combo in HARD_BLOCK:
        r = f'HARD_BLOCK: {combo} 宪法级死穴'
        _log('BLOCKED', signal, r)
        return {'executed': False, 'reason': r, 'order': None}

    # ── 门控2b：rr1过高封禁 ─────────────────────────────────────────
    # [铁证封印 2026-08-06 设计院] score 120-140 + rr1>1.5 EV=-1.0，TP太远无法触达
    _rr1 = float(signal.get('rr1', 0) or 0)
    if _rr1 > MAX_RR1_AUTO and score < 155:
        r = f'rr1={_rr1:.2f}>{MAX_RR1_AUTO} 且score={score:.0f}<155，TP太远历史EV=-1.0，拒绝执行'
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
            pass  # [静默]
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
    # [BTC专属 设计院2026-07-18 苏摩111封印]
    # BTC最小单位0.001×价格≈65U，NAV=90U时标准10%×3x=27U不够
    # BTC专属：pos_pct=15%，leverage=5x → notional=90×15%×5=67.5U > 65U ✅
    _BTC_SYMBOLS = {'BTCUSDT', 'BTCPERP', 'BTC-PERP'}
    if sym in _BTC_SYMBOLS:
        _default_pct = 15.0   # BTC专属15%
        _default_lev = 5      # BTC专属5x
    else:
        _default_pct = MAX_POS_PCT_NAV * 100  # 其他标的沿用10%
        _default_lev = 5

    sig_pos_pct = float(signal.get('pos_pct', _default_pct)) / 100
    # BTC信号：允许最高15%；其他标的：硬上限10%
    _pct_cap = 0.15 if sym in _BTC_SYMBOLS else MAX_POS_PCT_NAV
    final_pct = min(sig_pos_pct, _pct_cap)
    pos_usdt = nav * final_pct

    entry_price = (entry_lo + entry_hi) / 2 if entry_hi > entry_lo else entry_lo
    leverage    = int(signal.get('leverage', _default_lev))  # 设计院封印：BTC=5x, 其他=5x
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
        # [P0-2 设计院修复 2026-07-18 苏摩111封印]
        # 根因：NAV=90U × 10% × 3x = 27U，BTC最小单位0.001 × 64500 = 64.5U，远超pos_usdt
        # [FIX-C7 2026-07-18] BTC专属通道：最小手=65U，上限用BTC专属pct_cap×NAV而非NAV×30%
        # 非BTC：最小手通常<10U，NAV×30%=27U足够；BTC：最小手=65U，需要BTC专属上限
        _min_notional = min_step * entry_price
        _btc_syms = {'BTCUSDT', 'BTCPERP', 'BTC-PERP'}
        if sym in _btc_syms:
            _max_notional_cap = nav * 0.15 * 5  # BTC专属：15%NAV×5x = 可承受最大notional
        else:
            _max_notional_cap = nav * 0.30  # 其他标的：NAV×30%
        if _min_notional <= _max_notional_cap:
            qty = min_step
            qty = round(qty, qty_precision)
            _log('WARN_QTY_FLOOR', signal,
                 f'qty=0→floor to min_step={min_step}, notional={_min_notional:.2f}U (cap={_max_notional_cap:.1f}U)')
        else:
            r = f'qty=0且min_notional={_min_notional:.1f}U > cap={_max_notional_cap:.1f}U，拒绝执行（标的价格过高）'
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
    pass  # [静默]

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
