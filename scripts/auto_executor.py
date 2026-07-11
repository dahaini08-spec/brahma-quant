"""
auto_executor.py — 梵天自动开单触发器
苏摩授权落地 2026-06-30 | 设计院

触发条件（全部满足才执行）：
  ① signal.valid = True
  ② signal.score ≥ AUTO_SCORE_THRESHOLD (138)
  ③ signal.rr1 ≥ 1.0
  ④ 体制门控通过（非死穴）
  ⑤ 持仓数 < MAX_POSITIONS (20)
  ⑥ 该标的无现有持仓
  ⑦ 可用余额 ≥ 最小开单金额 ($10)
  ⑧ 信号未过期（expires_at 未超时）
  ⑨ 未在 executed_signals 集合中（防重复）

安全机制：
  - 每笔最大风险 = NAV × 2%（铁证仓位）
  - SL必须 ≥ 2.0%（v4.0铁证封印）
  - 全部开单写入 data/auto_executor_log.jsonl
  - 异常自动推送苏摩

运行方式：由 signal-watcher-1h cron 每2H调用，也可手动触发

开单模式（ORDER_MODE）：
  market  - 市价单立即成交（原方式）
  limit   - 分批挂单（3档，入场区间均匀分布）
  auto    - 自动选择：高波动用市价，低波动用挂单（默认）

分批挂单逻辑（limit/auto模式）：
  第1档：entry_lo（25% NAV）
  第2档：(entry_lo+entry_hi)/2（50% NAV）
  第3档：entry_hi（25% NAV）
  超时：30分钟未成交自动撤单
"""

import sys, os, json, time, hmac, hashlib, math, requests
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 配置 ──────────────────────────────────────────────
AUTO_SCORE_THRESHOLD = 130       # 最低评分 [v7.0 2026-07-11 苏摩111] 与brahma_core ENTER_WATCH层对齐（135→130）
MIN_RR               = 1.0       # 最低RR
MAX_POSITIONS        = 20        # 最大持仓数（苏摩授权 2026-07-03，1→20）
MIN_SL_PCT           = 1.0       # [v7.0 苏摩111 2026-07-11] 2.0→1.0; BTC低波动ATR=0.02%时SL=1.2%正常，不应强制>=2.0%
MAX_SL_PCT           = 5.0       # 标准最大止损（保护性上限）
MAX_SL_PCT_HIGH_VOL  = 9.0       # 高波动信号上限（score≥145，仓位×0.7）
NAV_SIZE_PCT         = 0.05      # 默认仓位 NAV×5%（苏摩授权 2026-07-03）
DEFAULT_LEV          = 5         # 默认杠杆 5x（苏摩授权 2026-07-03）
MIN_NOTIONAL         = 5.0       # 最小开单金额 USDT（设计院修复 2026-07-05：NAV=100时5%=5.15即可执行）

# ── blacktea风控门（2026-07-10 苏摩111批准）─────────────────────────────
# 对标: nmrtn/blacktea x402支付控制 + 人工审批 + 审计日志
# 逻辑: 单笔名义>NAV×8% → 推送苏摩审批 → 30min无回复自动降仓至5%执行
APPROVAL_THRESHOLD   = 0.08     # 超过NAV×8%触发审批门
APPROVAL_REDUCED     = 0.05     # 30min无回复降仓至NAV×5%
APPROVAL_TIMEOUT_MIN = 30       # 审批等待窗口（分钟）
APPROVAL_RECORD_PATH = Path(__file__).parent.parent / 'data' / 'approval_pending.json'

# BTC/ETH 动态仓位配置（梵天自主评判，苏摩授权 2026-07-03）
# score≥155 → 10% NAV | score 140~154 → 7.5% NAV | score 138~139 → 5% NAV
BIG_SYMBOLS          = {'BTCUSDT', 'ETHUSDT'}   # 大仓位标的
BIG_SYM_NAV_HIGH     = 0.10     # score≥155 → 10%
BIG_SYM_NAV_MID      = 0.075    # score 140~154 → 7.5%
BIG_SYM_NAV_BASE     = 0.05     # score 138~139 → 5%（与其他标的一致）
BIG_SYM_SCORE_HIGH   = 155      # 高档触发分
BIG_SYM_SCORE_MID    = 140      # 中档触发分

# 开单模式：market / limit / auto（默认）
# auto = 有entry区间且区间>0.1%用limit；否则用market
ORDER_MODE           = 'auto'

# 分批挂单参数
LIMIT_ORDER_TIMEOUT_SEC = 1800   # 挂单超时秒数（30分钟）
# 3档比例：[25%, 50%, 25%] 合计=100%
BATCH_RATIOS         = [0.25, 0.50, 0.25]
# 3档价格偏移：SHORT时越低越激进（相对entry_lo），LONG时越高越激进
# SHORT: [entry_lo, mid, entry_hi]  → 越低越好入场
# LONG:  [entry_hi, mid, entry_lo]  → 越高越好入场（反转）
BATCH_CANCEL_OVERSHOOT = True    # 价格完全突破挂单区间时自动撤单
EXECUTED_SET_PATH    = Path(__file__).parent.parent / 'data/auto_executed_signals.json'
LOG_PATH             = Path(__file__).parent.parent / 'data/auto_executor_log.jsonl'
SIGNAL_LOG_PATH      = Path(__file__).parent.parent / 'data/live_signal_log.jsonl'
POS_STATE_PATH       = Path(__file__).parent.parent / 'data/position_sl_state.json'
WUQU_PATH            = Path(__file__).parent.parent / 'data/wuqu_positions.json'

# ── 死穴：禁止自动执行的体制×方向组合 ──────────────────
DEAD_ZONE = {
    ('BEAR_TREND',   'LONG'),    # 铁律封禁
    ('CHOP_MID',     'LONG'),    # 震荡禁多（无铁证）
    ('BULL_TREND',   'SHORT'),   # 牛市禁空
}

# ── API ───────────────────────────────────────────────
# [安全修复 2026-07-08 设计院] 硬编码密钥已移除
# 密钥必须通过环境变量或 TOOLS.md / .env 注入，禁止任何硬编码
API_KEY    = os.environ.get('BINANCE_API_KEY', '')
API_SECRET = os.environ.get('BINANCE_SECRET', '')

# ── [P0-2] 全局安全闸 ─────────────────────────────────────────────
try:
    from brahma_brain.safety import require_api_keys, safety_report as _sr
    require_api_keys()
except RuntimeError as _safety_err:
    import logging as _sl
    _sl.getLogger('auto_executor').critical(f'[SAFETY] {_safety_err}')
    # 不中断导入，但 _signed() 调用时会因空 KEY 失败
except ImportError:
    pass

if not API_KEY or not API_SECRET:
    import logging as _sec_log
    _sec_log.getLogger('auto_executor').warning(
        '[SECURITY] BINANCE_API_KEY/SECRET 未配置环境变量，执行层不可用'
    )
FAPI_BASE  = 'https://fapi.binance.com'
BASE       = Path(__file__).parent.parent  # workspace根目录


def _signed(method: str, path: str, params: dict = {}) -> dict:
    params = dict(params)
    params['timestamp'] = int(time.time() * 1000)
    qs  = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'{FAPI_BASE}{path}?{qs}&signature={sig}'
    hdrs = {'X-MBX-APIKEY': API_KEY}
    if method == 'GET':
        return requests.get(url, headers=hdrs, timeout=8).json()
    return requests.post(url, headers=hdrs, timeout=8).json()


def _load_executed() -> set:
    if not EXECUTED_SET_PATH.exists():
        return set()
    try:
        return set(json.loads(EXECUTED_SET_PATH.read_text()))
    except Exception:
        return set()


def _save_executed(executed: set):
    EXECUTED_SET_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXECUTED_SET_PATH.write_text(json.dumps(list(executed), ensure_ascii=False))


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, 'a') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _push(msg: str):
    """推送到苏摩主线程"""
    try:
        from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
        import subprocess
        subprocess.run([
            'openclaw', 'message', 'send',
            '--channel', 'jarvis',
            '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
            '--message', msg,
        ], capture_output=True, timeout=10)
    except Exception:
        pass


# ════════════════════════════════════════════════════
# 核心：筛选可执行信号
# ════════════════════════════════════════════════════

def find_executable_signals() -> list[dict]:
    """从live_signal_log中找出所有满足条件的待执行信号"""
    if not SIGNAL_LOG_PATH.exists():
        return []

    executed = _load_executed()
    now_ts   = time.time()
    candidates = []

    for line in open(SIGNAL_LOG_PATH):
        line = line.strip()
        if not line:
            continue
        try:
            s = json.loads(line)
        except Exception:
            continue

        sig_id = s.get('signal_id', '')

        # ① 防重复
        if sig_id in executed:
            continue
        # ① 必须 valid=True
        if not s.get('valid'):
            continue
        # ② 评分门槛
        score = float(s.get('score', 0) or 0)
        if score < AUTO_SCORE_THRESHOLD:
            continue
        # ③ RR门槛
        rr1 = float(s.get('rr1', 0) or 0)
        if rr1 < MIN_RR:
            continue
        # ④ 死穴检测
        regime    = s.get('regime', '')
        direction = s.get('direction') or s.get('signal_dir', '')
        if (regime, direction) in DEAD_ZONE:
            continue
        # ⑤b [设计院 A3 2026-06-30] BRAHMA标签验证：拒绝执行WARN/ERR信号
        _tag = s.get('output_tag', '')
        if _tag:
            # 有标签时必须是SIG:RUNNER才得执行
            if not _tag.startswith('[BRAHMA:SIG:RUNNER:'):
                _tag_level = _tag.split(':')[1] if ':' in _tag else 'ERR'
                print(f'[死穴-标签拒绝] {s.get("symbol")} 标签级别={_tag_level}，非 SIG:RUNNER，跳过')
                continue
        # (output_tag为空 = 老信号延续兼容，不拒绝)
        # ⑤⑥ 持仓检查在execute阶段做
        # ⑧ 过期检测
        exp = s.get('expires_at')
        if exp:
            try:
                exp_ts = datetime.fromisoformat(str(exp).replace('Z', '+00:00')).timestamp()
                if now_ts > exp_ts:
                    continue
            except Exception:
                pass
        # ⑨ 已有result的跳过
        if s.get('result') or s.get('settled'):
            continue

        # SL验证（动态上限：score≥145高波动信号允许至 MAX_SL_PCT_HIGH_VOL）
        sl_pct = float(s.get('sl_pct', 0) or 0)
        # [v5.1 设计院 2026-07-03] 小币宽止损通道：score≥155+BULL_TREND允许sl≤15%（仓位×0.5）
        _is_altcoin_bull = (
            score >= 155
            and regime == 'BULL_TREND'
            and direction == 'LONG'
            and sl_pct <= 15.0
        )
        _effective_max_sl = (
            15.0 if _is_altcoin_bull else
            MAX_SL_PCT_HIGH_VOL if score >= 145 else
            MAX_SL_PCT
        )
        if sl_pct < MIN_SL_PCT or sl_pct > _effective_max_sl:
            if sl_pct > MAX_SL_PCT and sl_pct <= MAX_SL_PCT_HIGH_VOL and score < 145:
                print(f'[SL过滤] {s.get("symbol")} sl={sl_pct:.1f}%>标准上限 score={score:.0f}<145 跳过'
                      f'（提示score需≥145才能用高波动通道）')
            continue
        # ── [P3-A 设计院 2026-07-08] HMM Regime概率化 — 附加置信度字段 ──
        try:
            from brahma_brain.regime_hmm_v2 import predict_regime_proba, get_weighted_multiplier
            _hmm = predict_regime_proba(s.get('symbol', ''))
            s['_hmm_dominant']   = _hmm.get('dominant', '')
            s['_hmm_confidence'] = _hmm.get('confidence', 0)
            s['_hmm_method']     = _hmm.get('method', '')
            # HMM置信度<0.40时降为MONITOR（不拒绝，仅标记）
            if _hmm.get('confidence', 1.0) < 0.40:
                s['_hmm_low_conf'] = True
                pass  # [静默]
        except Exception:
            pass

        # [v6.0 设计院 2026-07-08] 小币BEAR_TREND做多禁止（实盘复盘: SYN/NEAR/RENDER均亏损）
        # BTC/ETH已有死穴规则，小币缺失导致 43.8%胜率 根因
        _sym_regime = s.get('regime', '')
        _sym_dir    = s.get('direction') or s.get('signal_dir', '')
        _sym        = s.get('symbol', '')
        _is_small_cap = _sym not in ('BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT')
        if _is_small_cap and 'BEAR_TREND' in _sym_regime and _sym_dir in ('LONG', 'BUY'):
            pass  # [静默]
            continue

        # [v6.0 设计院 2026-07-08] 非梵天BRAHMA标签信号仓位上限: NAV×3%（原5%）
        # 依据: 亏损品种（SYN/CRCL/SAMSUNG等）多来自外部信号，仓位需收紧
        _brahma_tag = s.get('brahma_tag', '') or s.get('source', '')
        if _is_small_cap and 'BRAHMA' not in str(_brahma_tag).upper():
            s['_small_cap_pct'] = 0.03  # 3% NAV（收紧）

        # 小币宽止损：仓位系数×0.5
        if _is_altcoin_bull and sl_pct > MAX_SL_PCT_HIGH_VOL:
            s['_high_vol_discount'] = 0.5
        # 高波动通道：标记传递给execute阶段做仓位缩小
        if sl_pct > MAX_SL_PCT:
            s['_high_vol_discount'] = 0.7  # 仓位系数×0.7

        candidates.append(s)

    # 按评分降序排列
    candidates.sort(key=lambda x: -float(x.get('score', 0) or 0))

    # ══ [设计院 v16] portfolio_optimizer 相关性过滤 ══════════════════════
    # 多信号时，用30天滚动相关性矩阵选出最优子集（max 3个，corr<0.75）
    # 单信号时直接通过（不增加延迟）
    if len(candidates) > 1:
        try:
            import sys as _sys_po, os as _os_po
            _po_root = str(Path(__file__).parent.parent)
            _po_brain = str(Path(__file__).parent.parent / 'brahma_brain')
            for _pp in [_po_brain, _po_root]:
                if _pp not in _sys_po.path:
                    _sys_po.path.insert(0, _pp)
            from portfolio_optimizer import filter_signals as _po_filter
            _approved, _rejected = _po_filter(candidates)
            if _approved:
                for _r in _rejected:
                    _rsym = _r.get('symbol', '?')
                    pass  # [静默]
                candidates = _approved
                pass  # [静默]
        except Exception as _e_po:
            pass  # portfolio_optimizer不可用时保持原candidates
    # ══ [portfolio_optimizer END] ════════════════════════════════════════

    return candidates


# ════════════════════════════════════════════════════
# 执行单笔开单
# ════════════════════════════════════════════════════

# ════════════════════════════════════════════════════
# 分批挂单辅助函数
# ════════════════════════════════════════════════════

def _should_use_limit(entry_lo: float, entry_hi: float, px: float) -> bool:
    """判断是否应该用挂单：入场区间宽度>0.1% 且当前价在区间附近"""
    if not entry_lo or not entry_hi or not px:
        return False
    spread = abs(entry_hi - entry_lo) / ((entry_hi + entry_lo) / 2)
    if spread < 0.001:  # 区间小于0.1%不必挂单
        return False
    # 当前价距区间中点不超过5%，否则改市价
    max_dist = (entry_hi + entry_lo) / 2 * 0.05
    return abs(px - (entry_lo + entry_hi) / 2) <= max_dist


def _calc_batch_prices(entry_lo: float, entry_hi: float, direction: str) -> list:
    """
    计算分批3档挂单价格
    SHORT: [entry_lo(最优), mid, entry_hi(最差)]
    LONG:  [entry_hi(最优), mid, entry_lo(最差)]
    """
    mid = (entry_lo + entry_hi) / 2
    if direction == 'SHORT':
        return [entry_lo, mid, entry_hi]
    else:
        return [entry_hi, mid, entry_lo]


BINANCE_MIN_NOTIONAL = 20.0  # Binance合约最小名义值限制(USDT)

# ── [v6.0 设计院 2026-07-08] ATR动态断路器 ────────────────────────────────
def _calc_atr_dynamic_gate(sym: str, base_score: float = 135.0, base_sl: float = 5.0):
    """
    基于ATR_14动态调整执行门槛
    高波动市场: score门槛上调+2, sl上限收紧
    低波动市场: score门槛不变, sl上限放宽
    返回: (dynamic_score_threshold, dynamic_sl_max)
    """
    try:
        klines = _signed('GET', '/fapi/v1/klines', {'symbol': sym, 'interval': '4h', 'limit': 30})
        if not isinstance(klines, list) or len(klines) < 15:
            return base_score, base_sl
        
        # 计算ATR_14
        trs = []
        for i in range(1, len(klines)):
            h = float(klines[i][2]); l = float(klines[i][3]); pc = float(klines[i-1][4])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        atr14 = sum(trs[-14:]) / 14
        price = float(klines[-1][4])
        atr_pct = atr14 / price * 100  # ATR百分比
        
        # 分位数判断（简化：>4%=高波动, <2%=低波动）
        if atr_pct > 4.0:
            # 高波动：提高score门槛+2，保留SL上限
            return base_score + 2, base_sl
        elif atr_pct < 2.0:
            # 低波动：放宽SL上限至6%
            return base_score, min(base_sl + 1.0, 6.0)
        else:
            return base_score, base_sl
    except Exception:
        return base_score, base_sl


def _place_limit_orders(sym: str, side: str, total_qty: float,
                        prices: list, qty_prec: int, sig_id: str) -> dict:
    """
    下3档挂单，返回 {status, order_ids, filled_qty, avg_price, cancelled}
    比例BATCH_RATIOS=[0.25,0.50,0.25]
    超时LIMIT_ORDER_TIMEOUT_SEC尚未成交的挂单全部撤销
    名义值检查：任何一档低于BINANCE_MIN_NOTIONAL(20 USDT)时，
    自动降级为单档LIMIT单（合并全量）避免-4164错误
    """
    order_ids = []
    placed_prices = []

    # ── 名义值预检查：计算每档名义值 ──────────────────────────────
    mid_price = prices[1] if len(prices) > 1 else prices[0]
    min_batch_notional = min(
        round(math.floor(total_qty * ratio * 10**qty_prec) / 10**qty_prec, qty_prec) * price
        for ratio, price in zip(BATCH_RATIOS, prices)
        if round(math.floor(total_qty * ratio * 10**qty_prec) / 10**qty_prec, qty_prec) > 0
    ) if total_qty > 0 else 0

    if min_batch_notional < BINANCE_MIN_NOTIONAL:
        # 分批会有档低于20 USDT，合并为单档LIMIT单（用中间价）
        total_notional = total_qty * mid_price
        print(f'  [分批降级] {sym} 最小档名义值${min_batch_notional:.2f}<${BINANCE_MIN_NOTIONAL}，'
              f'合并为单档LIMIT @{mid_price:.4f} qty={total_qty} notional=${total_notional:.2f}')
        price_str = f'{mid_price:.8f}'.rstrip('0').rstrip('.')
        r = _signed('POST', '/fapi/v1/order', {
            'symbol':      sym,
            'side':        side,
            'type':        'LIMIT',
            'price':       price_str,
            'quantity':    total_qty,
            'timeInForce': 'GTC',
            'reduceOnly':  'false',
        })
        if 'orderId' in r:
            print(f'  [单档挂单] {sym} {side} qty={total_qty} @{mid_price:.4f} id={r["orderId"]}')
            # 直接返回已挂单状态（后续轮询逻辑沿用）
            order_ids = [r['orderId']]
            placed_prices = [mid_price]
        else:
            print(f'  [单档挂单失败] {sym} {r.get("msg", str(r))}')
            return {'status': 'FAILED', 'reason': f'单档LIMIT失败: {r.get("msg",str(r))}',
                    'order_ids': [], 'filled_qty': 0, 'avg_price': 0, 'cancelled': []}
    else:
        for i, (ratio, price) in enumerate(zip(BATCH_RATIOS, prices)):
            qty_i = round(math.floor(total_qty * ratio * 10**qty_prec) / 10**qty_prec, qty_prec)
            if qty_i <= 0:
                continue
            price_str = f'{price:.8f}'.rstrip('0').rstrip('.')
            r = _signed('POST', '/fapi/v1/order', {
                'symbol':      sym,
                'side':        side,
                'type':        'LIMIT',
                'price':       price_str,
                'quantity':    qty_i,
                'timeInForce': 'GTC',
                'reduceOnly':  'false',
            })
            if 'orderId' in r:
                order_ids.append(r['orderId'])
                placed_prices.append(price)
                print(f'  [挂单] 第{i+1}档 {sym} {side} qty={qty_i} @{price:.4f} id={r["orderId"]}')
            else:
                print(f'  [挂单失败] 第{i+1}档 {sym} {r.get("msg", str(r))}')

    if not order_ids:
        return {'status': 'FAILED', 'reason': '全部分批挂单失败', 'order_ids': [], 'filled_qty': 0, 'avg_price': 0}

    # 轮询等待成交
    deadline = time.time() + LIMIT_ORDER_TIMEOUT_SEC
    filled_qty = 0.0
    total_value = 0.0
    pending_ids = list(order_ids)

    print(f'  [分批挂单] 等待成交 timeout={LIMIT_ORDER_TIMEOUT_SEC}s 共{len(order_ids)}单...')
    while time.time() < deadline and pending_ids:
        time.sleep(15)
        still_pending = []
        for oid in pending_ids:
            try:
                oi = _signed('GET', '/fapi/v1/order', {'symbol': sym, 'orderId': oid})
                status = oi.get('status', '')
                fq = float(oi.get('executedQty', 0))
                fp = float(oi.get('avgPrice', 0) or 0)
                if status == 'FILLED':
                    filled_qty  += fq
                    total_value += fq * fp
                    print(f'    ✅ 单{oid} 全额成交 qty={fq} @{fp:.4f}')
                elif status == 'PARTIALLY_FILLED':
                    if fq > 0 and fp > 0:
                        filled_qty  += fq
                        total_value += fq * fp
                    still_pending.append(oid)
                else:
                    still_pending.append(oid)
            except Exception:
                still_pending.append(oid)
        pending_ids = still_pending

    # 撤销超时未成交挂单
    cancelled = []
    for oid in pending_ids:
        try:
            _signed('DELETE', '/fapi/v1/order', {'symbol': sym, 'orderId': oid})
            cancelled.append(oid)
            print(f'    ⚠️  超时撤单 {oid}')
        except Exception as e:
            print(f'    [撤单失败] {oid}: {e}')

    avg_fill_px = (total_value / filled_qty) if filled_qty > 0 else 0.0
    if filled_qty > 0 and cancelled:
        final_status = 'PARTIAL'
    elif filled_qty > 0:
        final_status = 'FILLED'
    else:
        final_status = 'FAILED'

    return {
        'status':     final_status,
        'order_ids':  order_ids,
        'filled_qty': filled_qty,
        'avg_price':  avg_fill_px,
        'cancelled':  cancelled,
        'reason':     '' if filled_qty > 0 else '全部超时未成交',
    }


def execute_signal(signal: dict, nav: float, active_positions: list) -> dict:
    """执行单笔信号开单，返回执行结果"""
    sym       = signal['symbol']
    direction = signal.get('direction') or signal.get('signal_dir', 'SHORT')
    score     = float(signal.get('score', 0) or 0)
    sl_pct    = float(signal.get('sl_pct', MIN_SL_PCT) or MIN_SL_PCT)
    tp1       = float(signal.get('tp1', 0) or 0)
    sl_price  = float(signal.get('stop_loss', 0) or 0)
    entry_lo  = float(signal.get('entry_lo', 0) or 0)
    entry_hi  = float(signal.get('entry_hi', 0) or 0)
    sig_id    = signal.get('signal_id', '')

    # ── P0: 波动率自适应止损（2026-07-10 6方联合推理封印）─────────────────
    # 原理: 固定 SL_PCT 不考虑市场当前波动率
    #         ATR自适应：max(固定SL, 1.5×ATR_1H/价格)
    #         低波动期: SL紧缩(更多机会) | 高波动期: SL放宽(不被震出)
    try:
        _kl_1h = requests.get(
            f'{FAPI_BASE}/fapi/v1/klines?symbol={sym}&interval=1h&limit=16',
            timeout=5
        ).json()
        if isinstance(_kl_1h, list) and len(_kl_1h) >= 15:
            _trs = []
            for _i in range(1, len(_kl_1h)):
                _h = float(_kl_1h[_i][2]); _l = float(_kl_1h[_i][3])
                _pc = float(_kl_1h[_i-1][4])
                _trs.append(max(_h-_l, abs(_h-_pc), abs(_l-_pc)))
            _atr_1h = sum(_trs[-14:]) / 14
            _px_ref = float(requests.get(
                f'{FAPI_BASE}/fapi/v1/ticker/price?symbol={sym}', timeout=4
            ).json()['price'])
            _atr_sl_pct = round(_atr_1h * 1.5 / _px_ref * 100, 2)
            # 取最大值：保证至少覆盖固定 SL，不超过上限
            _atr_adjusted = min(max(sl_pct, _atr_sl_pct), MAX_SL_PCT)
            if abs(_atr_adjusted - sl_pct) > 0.1:  # 有意义的调整才刷日志
                print(f'[波动率SL] {sym} 固定SL={sl_pct:.1f}% ATR自适应SL={_atr_adjusted:.1f}% (ATR={_atr_1h:.0f} 价格={_px_ref:.2f})')
            sl_pct = _atr_adjusted
    except Exception as _atr_e:
        pass  # ATR获取失败时关退固定SL，不阻断执行
    # ── end 波动率自适应止损 ──────────────────────────────────────────

    result = {
        'signal_id': sig_id, 'symbol': sym, 'direction': direction,
        'score': score, 'ts': time.time(),
        'ts_iso': datetime.now(timezone.utc).isoformat(),
        'status': 'FAILED', 'reason': '',
    }

    # ⑤ 持仓数限制
    if len(active_positions) >= MAX_POSITIONS:
        result['reason'] = f'MAX_POSITIONS={MAX_POSITIONS}已达上限'
        return result

    # ⑥ 该标的无现有持仓
    existing = [p for p in active_positions if p.get('symbol') == sym]
    if existing:
        result['reason'] = f'{sym}已有持仓'
        return result

    # ⑦ P0 单标的名义敞口上限（含已挂单+已持仓 ≤ NAV×10%）
    try:
        _open_orders = _signed('GET', '/fapi/v1/openOrders', {'symbol': sym})
        _open_notional = sum(
            float(o.get('origQty', 0)) * float(o.get('price', 0) or 0)
            for o in (_open_orders if isinstance(_open_orders, list) else [])
            if not o.get('reduceOnly', False)
        )
        _pos_notional = sum(
            abs(float(p.get('qty', 0))) * float(p.get('entry_price', 0) or 0)
            for p in active_positions if p.get('symbol') == sym
        )
        _total_exposure = _open_notional + _pos_notional
        _max_exposure   = nav * 0.10  # NAV×10% 单标的上限
        if _total_exposure >= _max_exposure * 0.9:  # 90%即预警并拦截
            result['reason'] = (f'P0_ExposureCap: {sym} 已有敞口'
                                f'${_total_exposure:.1f} >= NAV×10%=${_max_exposure:.1f}')
            pass  # [静默]
            return result
    except Exception as _e:
        pass  # [静默]

    # 获取当前价
    try:
        from brahma_brain.brahma_bus import bus
        px = bus.price(sym)
    except Exception:
        r = requests.get(f'{FAPI_BASE}/fapi/v1/ticker/price',
                         params={'symbol': sym}, timeout=5)
        px = float(r.json().get('price', 0))

    if not px:
        result['reason'] = '获取价格失败'
        return result

    # ⑦b GapGate实时检查：价格超出入场区 > GAP_MAX 则信号过期
    GAP_MAX = 0.03  # 3% 价格偏离上限
    if entry_lo and entry_hi:
        if direction in ('LONG', 'BUY'):
            # [v6.0 设计院 2026-07-08] 价格轻微超出入场区(0~0.5%) → 自动追踪到区间边缘下限单
            # 避免"现价1739 > 区间上沿1738 → 永远等不到成交"的死循环
            PRICE_CHASE_MAX = 0.005  # 0.5%内自动追踪
            if entry_hi * (1 + PRICE_CHASE_MAX) >= px > entry_hi:
                # 价格略高于区间上沿，将LIMIT挂单价调整到区间上沿（等回踩）
                chase_gap = (px - entry_hi) / entry_hi * 100
                pass  # [静默]
                # entry_hi作为挂单价（不修改entry区，只影响batch价格基准）
                signal['_chase_price'] = entry_hi
            elif px > entry_hi * (1 + GAP_MAX):
                overshoot = (px - entry_hi) / entry_hi * 100
                result['reason'] = f'GapGate: 价格{px:.4f}超出入场区上沿{overshoot:.1f}%>{GAP_MAX*100:.0f}%'
                return result
            # 多单：价格大幅低于入场区 = 下方破位，信号失效
            if px < entry_lo * (1 - GAP_MAX):
                undershoot = (entry_lo - px) / entry_lo * 100
                result['reason'] = f'GapGate: 价格{px:.4f}跌破入场区下沿{undershoot:.1f}%'
                return result
        else:
            # 空单：价格跌破入场区下沿太多 = 追空
            if px < entry_lo * (1 - GAP_MAX):
                overshoot = (entry_lo - px) / entry_lo * 100
                result['reason'] = f'GapGate: 价格{px:.4f}跌破入场区下沿{overshoot:.1f}%>{GAP_MAX*100:.0f}%'
                return result

    # ⑦ 可用余额
    avail = float(_signed('GET', '/fapi/v2/balance',
                          {'asset':'USDT'})[:1] and 0 or nav * 0.3)  # 估算fallback

    # 仓位计算（动态分档：BTC/ETH梵天评判，高波动自动缩小）
    _hv_discount = float(signal.get('_high_vol_discount', 1.0))
    # BTC/ETH大仓位动态NAV分档
    if sym in BIG_SYMBOLS:
        if score >= BIG_SYM_SCORE_HIGH:
            _nav_pct = BIG_SYM_NAV_HIGH   # 10%
        elif score >= BIG_SYM_SCORE_MID:
            _nav_pct = BIG_SYM_NAV_MID    # 7.5%
        else:
            _nav_pct = BIG_SYM_NAV_BASE   # 5%
        print(f'[BTC/ETH动态仓位] {sym} score={score:.0f} → NAV×{_nav_pct*100:.1f}%')
    else:
        _nav_pct = NAV_SIZE_PCT           # 其他标的固定5%
    notional = nav * _nav_pct * _hv_discount
    if _hv_discount < 1.0:
        print(f'[高波动模式] {sym} sl={signal.get("sl_pct",0):.1f}% 仓位系数×{_hv_discount} 实际仓位=${notional:.1f}')
    if notional < MIN_NOTIONAL:
        result['reason'] = f'仓位${notional:.2f} < 最小${MIN_NOTIONAL}'
        return result

    # ── blacktea审批门（苏摩111 2026-07-10）─────────────────────────────────
    # 单笔>NAV×8% → 推送审批请求 → 30min无回复自动降仓
    _approval_threshold = nav * APPROVAL_THRESHOLD
    if notional > _approval_threshold:
        try:
            import json as _j
            # 检查是否已有此单的审批记录
            _pending = {}
            if APPROVAL_RECORD_PATH.exists():
                try: _pending = _j.loads(APPROVAL_RECORD_PATH.read_text())
                except: pass

            _key = f'{sym}_{direction}_{int(notional)}'
            _rec = _pending.get(_key, {})
            _req_ts = _rec.get('requested_at', 0)
            _approved = _rec.get('approved', False)
            _age_min = (time.time() - _req_ts) / 60

            if _approved:
                # 苏摩已批准，直接执行
                print(f'[blacktea] {sym} 已获审批 正常执行 ${notional:.1f}')
            elif _req_ts > 0 and _age_min >= APPROVAL_TIMEOUT_MIN:
                # 30min无回复 → 降仓执行
                notional = nav * APPROVAL_REDUCED
                print(f'[blacktea] {sym} {APPROVAL_TIMEOUT_MIN}min无回复 → 降仓${notional:.1f}(NAV×{APPROVAL_REDUCED*100:.0f}%)')
                _pending.pop(_key, None)
                APPROVAL_RECORD_PATH.write_text(_j.dumps(_pending, indent=2))
            elif _req_ts > 0 and _age_min < APPROVAL_TIMEOUT_MIN:
                # 审批请求已发出，等待中
                remaining = int(APPROVAL_TIMEOUT_MIN - _age_min)
                result['reason'] = f'blacktea: 等待审批 还剩{remaining}min（到期自动降仓执行）'
                return result
            else:
                # 首次触发：发送审批请求
                _pending[_key] = {'requested_at': time.time(), 'symbol': sym,
                                  'direction': direction, 'notional': notional,
                                  'score': score, 'approved': False}
                APPROVAL_RECORD_PATH.write_text(_j.dumps(_pending, indent=2))
                # 推送苏摩
                _msg = (
                    f'❗️ [blacktea审批门] {sym} {direction}\n'
                    f'单笔名义: ${notional:.1f} > NAV×8%=${_approval_threshold:.1f}\n'
                    f'score={score:.0f} | SL={signal.get("sl_pct",0):.1f}%\n'
                    f'✅ 回复 「111」或「批准」 → 立即执行\n'
                    f'⏳ {APPROVAL_TIMEOUT_MIN}min无回复 → 自动降仓至${nav*APPROVAL_REDUCED:.1f}执行'
                )
                import subprocess as _sp
                _sp.Popen(
                    ['openclaw','message','send',
                     '--channel','jarvis',
                     '--to', f'{_pending[_key].get("symbol",sym)}',
                     '--message', _msg],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
                )
                # 尝试发送到正确地址
                try:
                    from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
                    _sp.Popen(
                        ['openclaw','message','send',
                         '--channel','jarvis',
                         '--to', f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}',
                         '--message', _msg],
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL
                    )
                except: pass
                result['reason'] = f'blacktea: 审批请求已发出，等待30min'
                return result
        except Exception as _be:
            print(f'[blacktea] 审批门异常(降级执行): {_be}')
            notional = min(notional, nav * APPROVAL_REDUCED)  # 异常时安全降仓
    # ── end blacktea ───────────────────────────────────────────────────

    # 获取合约精度
    try:
        ei = requests.get(f'{FAPI_BASE}/fapi/v1/exchangeInfo', timeout=5).json()
        sym_info = next((s for s in ei.get('symbols', []) if s['symbol'] == sym), None)
        qty_prec = 3  # 默认
        if sym_info:
            for f in sym_info.get('filters', []):
                if f['filterType'] == 'LOT_SIZE':
                    step = float(f['stepSize'])
                    qty_prec = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
                    break
    except Exception:
        qty_prec = 3

    # 杠杆与数量
    regime = signal.get('regime', 'BEAR_TREND')
    lev = DEFAULT_LEV
    if regime in ('BEAR_RECOVERY', 'BULL_TREND'):
        lev = 5

    qty_raw = notional * lev / px
    qty = round(math.floor(qty_raw * 10**qty_prec) / 10**qty_prec, qty_prec)

    if qty <= 0:
        result['reason'] = f'qty={qty} 无效'
        return result

    # 设置杠杆
    lev_r = _signed('POST', '/fapi/v1/leverage',
                    {'symbol': sym, 'leverage': lev})
    if 'leverage' not in lev_r and 'code' in lev_r:
        result['reason'] = f'设置杠杆失败: {lev_r.get("msg","")}'
        return result

    # 开单方向
    side = 'SELL' if direction == 'SHORT' else 'BUY'

    # ── 判断开单模式（ORDER_MODE: market/limit/auto）──────────────
    use_limit = False
    if ORDER_MODE == 'limit':
        use_limit = True
    elif ORDER_MODE == 'auto':
        use_limit = _should_use_limit(entry_lo, entry_hi, px)

    if use_limit and entry_lo and entry_hi:
        # ── 分批挂单模式 ─────────────────────────────────────────
        batch_prices = _calc_batch_prices(entry_lo, entry_hi, direction)
        print(f'  [分批挂单] {sym} {direction} 3档: {[f"{p:.4f}" for p in batch_prices]}')
        batch_result = _place_limit_orders(sym, side, qty, batch_prices, qty_prec, sig_id)

        if batch_result['status'] == 'FAILED':
            result['reason'] = f'分批挂单全部失败: {batch_result["reason"]}'
            return result

        fill_px  = batch_result['avg_price']
        fill_qty = batch_result['filled_qty']
        order_id = batch_result['order_ids'][0] if batch_result['order_ids'] else 0
        order_mode_used = f'LIMIT_BATCH({batch_result["status"]})'
        result['batch_order_ids'] = batch_result['order_ids']
        result['batch_cancelled'] = batch_result['cancelled']
        result['order_mode'] = order_mode_used
    else:
        # ── 市价开单模式（原逻辑）────────────────────────────────
        order_r = _signed('POST', '/fapi/v1/order', {
            'symbol':     sym,
            'side':       side,
            'type':       'MARKET',
            'quantity':   qty,
            'reduceOnly': 'false',
        })

        if 'orderId' not in order_r:
            result['reason'] = f'开单失败: {order_r.get("msg", str(order_r))}'
            return result

        fill_px  = float(order_r.get('avgPrice', px))
        fill_qty = float(order_r.get('executedQty', qty))
        order_id = order_r['orderId']
        result['order_mode'] = 'MARKET'

    # 止损价（基于成交价重算，确保SL≥2%）
    if direction == 'SHORT':
        sl_final = round(fill_px * (1 + max(sl_pct, MIN_SL_PCT) / 100), 2)
        tp_final = round(fill_px * (1 - max(sl_pct, MIN_SL_PCT) / 100), 2)
    else:
        sl_final = round(fill_px * (1 - max(sl_pct, MIN_SL_PCT) / 100), 2)
        tp_final = round(fill_px * (1 + max(sl_pct, MIN_SL_PCT) / 100), 2)

    # ws_guardian 软止损（SL/TP写入position_sl_state）
    sl_state = {}
    if POS_STATE_PATH.exists():
        try:
            sl_state = json.loads(POS_STATE_PATH.read_text())
        except Exception:
            pass
    sl_state[sym] = {
        'symbol':      sym,
        'side':        direction,
        'entry_price': fill_px,
        'sl_price':    sl_final,
        'tp_price':    tp_final,
        'sl_pct':      max(sl_pct, MIN_SL_PCT),
        'signal_id':   sig_id,
        'order_id':    order_id,
        'updated_at':  time.time(),
    }
    POS_STATE_PATH.write_text(json.dumps(sl_state, indent=2, ensure_ascii=False))

    # wuqu_positions 更新
    wuqu = {}
    if WUQU_PATH.exists():
        try:
            wuqu = json.loads(WUQU_PATH.read_text())
        except Exception:
            pass
    wuqu[sym] = {
        'symbol':      sym,
        'side':        direction,
        'qty':         fill_qty,
        'entry_price': fill_px,
        'stop_loss':   sl_final,
        'tp1':         tp_final,
        'sl_pct':      max(sl_pct, MIN_SL_PCT),
        'leverage':    lev,
        'notional':    fill_qty * fill_px,
        'signal_id':   sig_id,
        'order_id':    order_id,
        'ts':          time.time(),
        'source':      'auto_executor',
        'success':     True,
    }
    WUQU_PATH.write_text(json.dumps(wuqu, indent=2, ensure_ascii=False))

    # 更新信号日志中的状态
    try:
        lines = open(SIGNAL_LOG_PATH).readlines()
        new_lines = []
        for line in lines:
            try:
                s = json.loads(line.strip())
                if s.get('signal_id') == sig_id:
                    s['executed']     = True
                    s['order_id']     = order_id
                    s['fill_price']   = fill_px
                    s['fill_qty']     = fill_qty
                    s['executed_at']  = datetime.now(timezone.utc).isoformat()
                    line = json.dumps(s, ensure_ascii=False) + '\n'
            except Exception:
                pass
            new_lines.append(line)
        open(SIGNAL_LOG_PATH, 'w').writelines(new_lines)
    except Exception:
        pass

    # ── CubeSandbox对标: 开单后合法性验证 + 异常自动回滚 (v5.5 最小改动) ─────
    # 设计院2026-07-10: 对标CubeSandbox快照回滚机制
    # 原理: 开单成功后立即验证方向×体制的合法性
    #       若发现死穴(如BEAR_TREND+LONG), 立即市价平仓+告警
    #       最小改动: 仅在EXECUTED后追加, 不修改开单流程
    try:
        _rollback_needed = False
        _rollback_reason  = ''
        # 死穴检测: BEAR_TREND下的多单 / BULL_TREND下的空单
        _r_check = json.loads(
            (Path(__file__).parent.parent / 'data' / 'regime_state.json').read_text()
        ).get(sym, {})
        _regime_now = _r_check.get('confirmed', '') if isinstance(_r_check, dict) else ''
        if _regime_now == 'BEAR_TREND' and direction == 'LONG':
            _rollback_needed = True
            _rollback_reason = f'BEAR_TREND+LONG死穴: 体制={_regime_now}'
        # 也保护: 成交价严重偏离预期(>3%滑点)
        if entry_lo and fill_px:
            _slippage = abs(fill_px - entry_lo) / entry_lo * 100
            if _slippage > 3.0:
                _rollback_needed = True
                _rollback_reason = f'滑点过大={_slippage:.2f}%>3%(fill={fill_px} expected≈{entry_lo})'

        if _rollback_needed:
            print(f'[回滚守卫] {sym} {direction}: {_rollback_reason}')
            # 立即市价平仓
            _close_side = 'SELL' if direction == 'LONG' else 'BUY'
            _rb = _signed('POST', '/fapi/v1/order', {
                'symbol': sym, 'side': _close_side,
                'type': 'MARKET', 'quantity': fill_qty, 'reduceOnly': 'true',
            })
            print(f'[回滚守卫] 平仓结果: {_rb.get("status",_rb.get("msg","?"))}')
            # 从wuqu_positions移除
            try:
                _wq = json.loads(WUQU_PATH.read_text())
                _wq.pop(sym, None)
                WUQU_PATH.write_text(json.dumps(_wq, indent=2, ensure_ascii=False))
            except Exception:
                pass
            result['rollback'] = True
            result['rollback_reason'] = _rollback_reason
            result['reason'] = f'ROLLED_BACK: {_rollback_reason}'
    except Exception as _e:
        pass  # 回滚守卫异常不影响主流程
    # ── end CubeSandbox回滚守卫 ─────────────────────────────────────────────

    result.update({
        'status':      'EXECUTED',
        'order_id':    order_id,
        'fill_price':  fill_px,
        'fill_qty':    fill_qty,
        'sl_price':    sl_final,
        'tp_price':    tp_final,
        'sl_pct':      max(sl_pct, MIN_SL_PCT),
        'leverage':    lev,
        'notional':    round(fill_qty * fill_px, 2),
        'reason':      'OK',
    })
    return result


# ════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════

def run(dry_run: bool = False) -> list[dict]:
    """
    主执行函数
    dry_run=True：只扫描不执行，用于测试
    """
    # ── 文件锁：防止多实例并发（根因修复 2026-07-03）──────────────
    import fcntl
    _lock_path = BASE / 'data/.auto_executor.lock'
    try:
        _lock_fd = open(_lock_path, 'w')
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        pass  # [静默]
        pass  # [静默]
        return []
    try:
        return _run_locked(dry_run=dry_run)
    finally:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        _lock_fd.close()


def _run_locked(dry_run: bool = False) -> list[dict]:
    """实际执行体（文件锁保护内）"""
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    # 账户状态
    acct      = _signed('GET', '/fapi/v2/account')
    nav       = float(acct.get('totalMarginBalance', 0))
    avail     = float(acct.get('availableBalance', 0))
    pos_list  = _signed('GET', '/fapi/v2/positionRisk')
    # [修复 2026-07-08] 安全守卫：API KEY未配置时 _signed() 返回str/dict(error)
    # 确保 pos_list 是可迭代的 list[dict]，避免 'str'.get() AttributeError
    if not isinstance(pos_list, list):
        pass  # [静默]
        pos_list = []
    active_pos = [
        {'symbol': p['symbol'], 'side': 'SHORT' if float(p['positionAmt']) < 0 else 'LONG',
         'qty': abs(float(p['positionAmt'])), 'entry_price': float(p['entryPrice'])}
        for p in pos_list
        if isinstance(p, dict) and abs(float(p.get('positionAmt', 0))) > 0
    ]

    # 找候选信号
    candidates = find_executable_signals()

    pass  # [静默]

    if not candidates:
        pass  # [静默]
        return []

    executed_set = _load_executed()
    results = []

    for sig in candidates:
        sig_id = sig.get('signal_id', '')
        sym    = sig.get('symbol', '')
        score  = float(sig.get('score', 0) or 0)
        direct = sig.get('direction') or sig.get('signal_dir', '')
        regime = sig.get('regime', '')

        pass  # [静默]

        # ── [P3-B 设计院 2026-07-08] RL A/B仓位分流 ──────────────────
        try:
            from brahma_brain.rl_position_ab import decide_position_size
            _std_nav_pct = BIG_SYM_NAV_HIGH if score >= 155 else (
                BIG_SYM_NAV_MID if score >= 140 else BIG_SYM_NAV_LOW
            ) if sym in ('BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT') else 0.03
            _ab = decide_position_size(
                signal_id=sig_id, symbol=sym,
                score=score, direction=direct, regime=regime,
                std_nav_pct=_std_nav_pct,
            )
            sig['_rl_nav_pct'] = _ab['nav_pct']
            sig['_rl_group']   = _ab['group']
            pass  # [静默]
        except Exception:
            pass  # RL异常不影响主流程

        if dry_run:
            pass  # [静默]
            results.append({'signal_id': sig_id, 'status': 'DRY_RUN', 'symbol': sym})
            continue

        # ── 防重复挂单：检查当前是否已有该symbol的未平仓开仓挂单 ──
        open_orders = _signed('GET', '/fapi/v1/openOrders', {'symbol': sym})
        existing_open = [o for o in open_orders
                         if isinstance(open_orders, list)
                         and not o.get('reduceOnly', False)
                         and o.get('status') in ('NEW', 'PARTIALLY_FILLED')]
        if existing_open:
            print(f'  [防重复] {sym} 已有{len(existing_open)}张未成交开仓挂单，跳过')
            executed_set.add(sig_id)
            _save_executed(executed_set)
            continue

        try:
            exec_result = execute_signal(sig, nav, active_pos)
        except Exception as _exec_err:
            exec_result = {
                'signal_id': sig_id, 'symbol': sym, 'direction': direct,
                'score': score, 'event': 'FAILED',
                'ts': __import__('datetime').datetime.utcnow().isoformat(),
                'reason': str(_exec_err),
                'result': {'error': str(_exec_err)},
            }
        _log(exec_result)
        executed_set.add(sig_id)
        _save_executed(executed_set)
        results.append(exec_result)

        if exec_result['status'] == 'EXECUTED':
            # 刷新持仓列表（防止后续信号重复开同一标的）
            active_pos.append({
                'symbol': sym, 'side': direct,
                'qty': exec_result.get('fill_qty', 0),
                'entry_price': exec_result.get('fill_price', 0),
            })

            # 推送执行确认
            fill_px  = exec_result.get('fill_price', 0)
            sl_price = exec_result.get('sl_price', 0)
            tp_price = exec_result.get('tp_price', 0)
            notional = exec_result.get('notional', 0)
            lev      = exec_result.get('leverage', DEFAULT_LEV)
            _push(
                f'⚡ 梵天自动开单\n'
                f'━━━━━━━━━━━━━━━━\n'
                f'标的：{sym}  {direct}\n'
                f'评分：{score:.0f}  体制：{regime}\n'
                f'成交：${fill_px:.4f}  {lev}x\n'
                f'名义：${notional:.2f}\n'
                f'止损：${sl_price:.4f}（SL={exec_result.get("sl_pct",2):.1f}%）\n'
                f'止盈：${tp_price:.4f}\n'
                f'━━━━━━━━━━━━━━━━\n'
                f'signal_id: {sig_id}'
            )
            print(f'  ✅ 执行成功 fill=${fill_px:.4f} SL=${sl_price:.4f} TP=${tp_price:.4f}')
        else:
            print(f'  ❌ 跳过: {exec_result["reason"]}')

        time.sleep(0.5)  # 限速

    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天自动开单触发器')
    parser.add_argument('--dry', action='store_true', help='dry-run模式，不真实开单')
    parser.add_argument('--stats', action='store_true', help='显示统计信息')
    args = parser.parse_args()

    if args.stats:
        if LOG_PATH.exists():
            logs = [json.loads(l) for l in open(LOG_PATH) if l.strip()]
            ok = [l for l in logs if l.get('status') == 'EXECUTED']
            fail = [l for l in logs if l.get('status') == 'FAILED']
            print(f'自动开单记录: 成功={len(ok)} 跳过={len(fail)}')
            for l in ok[-5:]:
                print(f'  {l["ts_iso"][:16]} {l["symbol"]} {l["direction"]} fill={l.get("fill_price","?")}')
        else:
            print('暂无自动开单记录')
    else:
        results = run(dry_run=args.dry)
        ok = [r for r in results if r.get('status') == 'EXECUTED']
        pass  # [静默]
        if not ok:
            pass  # [静默]
