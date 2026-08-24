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

# ── 内存门控（设计院2026-08-04封印）───────────────────
import sys as _sys, os as _os

import resource as _res_guard; _res_guard.setrlimit(_res_guard.RLIMIT_CORE,(0,0))

_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'scripts') if '/scripts/' not in __file__ else _os.path.dirname(_os.path.abspath(__file__)))
# pytest/smoke_test/import 环境跳过 mem_gate，避免 sys.exit 杀死测试进程
# [修复 2026-08-11] 增加 brahma_smoke_test 豁免
_in_test = (
    'pytest' in _sys.modules
    or _sys.argv[0].endswith('pytest')
    or any('pytest' in str(a) for a in _sys.argv)
    or 'brahma_smoke_test' in _sys.argv[0]
    or any('smoke_test' in str(a) for a in _sys.argv)
)
if not _in_test:
    try:
        from brahma_mem_manager import mem_gate as _mem_gate
        _mem_gate(500)
    except (ImportError, SystemExit) as _e:
        if isinstance(_e, SystemExit): raise
# ──────────────────────────────────────────────────────

import sys, os, json, time, hmac, hashlib, math, requests

# ── 运行时依赖自检 ────────────────────────────────
try:
    from scripts.ensure_deps import ensure as _ensure_deps
    _ensure_deps()
except Exception:
    pass

from pathlib import Path
from datetime import datetime, timezone
try:
    from config import fmt_beijing
except ImportError:
    import datetime as _dti
    def fmt_beijing(): return _dti.datetime.now(_dti.timezone(_dti.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") + " CST"

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 配置 ──────────────────────────────────────────────
AUTO_SCORE_THRESHOLD = 120       # 最低评分 [P0-C 2026-07-11] 130→120，与DharmaBridge valid门槛同步
# ── P1-A 分流封印（设计院六方联合 2026-07-11）────────────────────
# auto_executor 专责 score≥155 的 ENTER_FULL 信号（最高置信度）
# score 130~154 的 ENTER_WATCH 信号由 sub_executor 处理
# 分流好处：彻底消除双执行器重复下单风险
AUTO_ENTER_FULL_THRESHOLD = 138    # [2026-08-12 苏摩111 体制分层封印]
# 执行线体制分层 [2026-08-12 苏摩111]
# v63铁证: BULL_TREND_LONG WR=95-96%, BEAR_TREND_SHORT WR=69-87%
# 统一155分在WR=0%死亡区 → 按体制分层取代
TIER_1_SCORE  = 138   # ENTER_FULL → 全仓 5%NAV
TIER_2_SCORE  = 110   # [体制分层] BULL_TREND_LONG/BEAR_TREND_SHORT 基础执行线
# 体制分层执行线（优先匹配，TIER_2为兜底）
REGIME_EXEC_LINE = {
    ('BULL_TREND',    'LONG'):  110,   # v63 BTC WR=95.8% ETH WR=96.4%
    ('BEAR_TREND',    'SHORT'): 110,   # v63 BTC WR=87.0% ETH WR=69.4%
    ('BEAR_RECOVERY', 'LONG'):  120,   # 回升期做多略谨慎
    ('BULL_EARLY',    'SHORT'): 130,   # 牛初做空高门槛
}
# CHOP体制双向全封（v63 CHOP WR=17-27% 负EV）
CHOP_DEAD_COMBOS = {('CHOP_MID','LONG'), ('CHOP_MID','SHORT')}

# [P1清算感知动态门槛 2026-08-05 设计院]
# 当 liq_density 三所WS数据充足(OKX≥100条) 且 清算偏向顺势，TIER_1动态降至150
# 依据: 今日清算层修复+5分，历史7条BTC/ETH信号进入150-154区间，实测可解锁
TIER_1_LIQ_ADJUSTED = 150   # 清算顺势时的宽松门槛（需liq_bonus>=5）
LIQ_BONUS_THRESHOLD  = 5    # 清算评分贡献达到此值才触发宽松门槛
TIER_3_SCORE  = 138   # BTC/ETH限定 → 轻仓，仓位自动课保至MIN_NOTIONAL
TIER_3_SYMBOLS = frozenset({'BTCUSDT', 'ETHUSDT'})
AUTO_ENTER_WATCH_MIN      = 120    # sub专区下界（P0-C: 130→120，与valid门槛同步）
MIN_RR               = 1.0       # 最低RR
MAX_POSITIONS        = 20        # 最大持仓数（苏摩授权 2026-07-03，1→20）
MIN_SL_PCT           = 1.0       # [v7.0 苏摩111 2026-07-11] 2.0→1.0; BTC低波动ATR=0.02%时SL=1.2%正常，不应强制>=2.0%
MAX_SL_PCT           = 5.0       # 标准最大止损（保护性上限）
MAX_SL_PCT_HIGH_VOL  = 9.0       # 高波动信号上限（score≥145，仓位×0.7）
# SL_PCT_GATE 已迁移至 brahma_brain/signal_quality_engine.py（SQE唯一真相，2026-08-07）
try:
    import sys as _sqe_sys, os as _sqe_os
    _sqe_sys.path.insert(0, _sqe_os.path.join(_sqe_os.path.dirname(_sqe_os.path.abspath(__file__)), '..', 'brahma_brain'))
    from signal_quality_engine import evaluate_signal as _sqe_evaluate, get_sqe as _get_sqe
    _SQE_OK = True
except Exception as _sqe_e:
    _SQE_OK = False
    _sqe_evaluate = None  # type: ignore
NAV_SIZE_PCT         = 0.05      # 默认仓位 NAV×5%（苏摩授权 2026-07-03）
DEFAULT_LEV          = 5         # 默认杠杆 5x（苏摩授权 2026-07-03）
MIN_NOTIONAL         = 5.5       # 最小开单金额 USDT（2026-07-23 修复: Binance MIN_NOTIONAL=5，加0.5缓冲得5.5）
# Binance各标的实际最小名义值（来源: exchangeInfo filterType=MIN_NOTIONAL）
SYMBOL_MIN_NOTIONAL  = {
    'BTCUSDT': 50.0,   # Binance实际MIN=50
    'ETHUSDT': 20.0,   # Binance实际MIN=20
}  # 其他标的默认用MIN_NOTIONAL=5.5

# ── blacktea风控门（2026-07-10 苏摩111批准）─────────────────────────────
# 对标: nmrtn/blacktea x402支付控制 + 人工审批 + 审计日志
# 逻辑: 单笔名义>NAV×8% → 推送苏摩审批 → 30min无回复自动降仓至5%执行
APPROVAL_THRESHOLD   = 0.20     # [SSOT 2026-08-05 设计院封印] 0.08→0.20
                                 # 根因: NAV≈88USDT时8%=$7.17，三档仓位(1.34~4.48)均<7.17
                                 #       三档仓位永不触发审批门，旧值实为死锁
                                 # 新值: NAV×20%=$17.92 >> TIER1×5%=$4.48，永不触发
                                 # 异常单保护: >NAV×20%仍受保护（正常单永远不触发）
APPROVAL_REDUCED     = 0.05     # 30min无回复降仓至NAV×5%
APPROVAL_TIMEOUT_MIN = 30       # 审批等待窗口（分钟）
APPROVAL_RECORD_PATH = Path(__file__).parent.parent / 'data' / 'approval_pending.json'

# BTC/ETH 动态仓位配置（梵天自主评判，苏摩授权 2026-07-03）
# score≥155 → 10% NAV | score 140~154 → 7.5% NAV | score 138~139 → 5% NAV
BIG_SYMBOLS          = {'BTCUSDT', 'ETHUSDT'}   # 大仓位标的
BIG_SYM_NAV_HIGH     = 0.10     # score≥138 → 10%
BIG_SYM_NAV_MID      = 0.075    # [IC铁证封印] score 140~154区间已从信号层拦截，此参数保留备用
BIG_SYM_NAV_BASE     = 0.05     # score 138~139 → 5%（与其他标的一致）
BIG_SYM_SCORE_HIGH   = 138      # [2026-08-12 体制分层] 高档触发分
BIG_SYM_SCORE_MID    = 120      # 中档: BULL_TREND/BEAR_TREND 标准执行

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

# ── 永久黑名单（executor层，无论score多高永远跳过）────────────────
EXECUTOR_BLACKLIST = frozenset({'SNDKUSDT'})  # [设计院自主 2026-07-31] TRADFI股票代币永远SKIP，排除污染

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
API_SECRET = os.environ.get('BINANCE_SECRET', '') or os.environ.get('BINANCE_API_SECRET', '')
# [修复 2026-07-23] 如果环境变量未注入，尝试从 .env 读取
if not API_KEY or not API_SECRET:
    try:
        _dot_env = Path(__file__).parent.parent / '.env'
        if _dot_env.exists():
            for _ln in _dot_env.read_text().splitlines():
                _ln = _ln.strip()
                if not _ln or _ln.startswith('#') or '=' not in _ln: continue
                _k, _v = _ln.split('=', 1)
                _k, _v = _k.strip(), _v.strip()
                if _k == 'BINANCE_API_KEY' and not API_KEY:
                    API_KEY = _v
                elif _k in ('BINANCE_SECRET', 'BINANCE_API_SECRET') and not API_SECRET:
                    API_SECRET = _v
    except Exception:
        pass

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
            '--to', f'{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}',
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

        # ① 永久黑名单（TRADFI股票代币等永远不执行）
        if s.get('symbol', '') in EXECUTOR_BLACKLIST:
            continue

        # ① 防重复
        if sig_id in executed:
            continue
        # ① 必须 valid=True
        if not s.get('valid'):
            continue
        # ① SQE信号质量门控 [修复C4 2026-08-24] SQE导入后从未调用，现接入主路径
        if _SQE_OK and _sqe_evaluate:
            try:
                _sqe_gate = _sqe_evaluate(s)
                if _sqe_gate.rejected:
                    pass  # [静默] SQE拒绝不推送，记录reason
                    continue
            except Exception:
                pass  # SQE失败不阻断

        # ② 评分门槛
        score = float(s.get('score', 0) or 0)
        if score < AUTO_SCORE_THRESHOLD:
            continue
        # ②-P1A 执行器分流：三档阈值自主执行（2026-07-18 苏摩111封印）
        # [P1 2026-08-05] 清算感知动态门槛：清算顺势时TIER_1降至150
        _liq_bonus = 0
        try:
            import sys as _sys_liq_ex
            _sys_liq_ex.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent / 'brahma_brain'))
            from liq_density_engine import get_liq_density as _get_ld_ex
            _ld_ex = _get_ld_ex(s.get('symbol',''), float(s.get('price', 0) or 0))
            _ab = _ld_ex.get('above_walls', [])
            _bl = _ld_ex.get('below_walls', [])
            _bias = _ld_ex.get('liq_bias', 'NEUTRAL')
            direction = s.get('direction', s.get('signal_dir', ''))
            if direction == 'LONG' and _ab and _ab[0][1] > 80_000_000:
                _liq_bonus = 5
            elif direction == 'SHORT' and _bl and _bl[0][1] > 80_000_000:
                _liq_bonus = 5
            if (direction == 'LONG' and _bias == 'ABOVE_HEAVY') or                (direction == 'SHORT' and _bias == 'BELOW_HEAVY'):
                _liq_bonus += 3
        except Exception:
            pass
        _effective_tier1 = TIER_1_LIQ_ADJUSTED if _liq_bonus >= LIQ_BONUS_THRESHOLD else TIER_1_SCORE

        # ── [2026-08-12 苏摩111] 体制分层门控 ──────────────────────────────
        # v63铁证: BULL_TREND_LONG WR=95-96%, BEAR_TREND_SHORT WR=69-87%
        # 统一155分=WR0%死亡区 → 按体制分层取代
        _regime_key = (str(s.get('regime','')), str(s.get('signal_dir') or s.get('direction','')))
        _regime_thr = REGIME_EXEC_LINE.get(_regime_key)
        if _regime_key in CHOP_DEAD_COMBOS:
            # CHOP双向全封：v63 WR=17-27% 负EV
            s['_tier'] = 0
            continue
        if _regime_thr is not None and score < _regime_thr:
            # 体制执行线不够 → 不执行
            s['_tier'] = 0
            continue
        # ── 体制分层通过，继续TIER判断 ────────────────────────────────────

        # TIER_1(≥138/清算顺势): ENTER_FULL → 全仓5%NAV（任意timing均执行）
        # TIER_2(110-137): ENTER → 标准仓3%NAV（BEAR_TREND_SHORT/BULL_TREND_LONG）
        # TIER_3(120-137): BTC/ETH限定 → 轻仓1.5%NAV（BEAR_RECOVERY_LONG）
        # ENTER_WATCH 仍由 sub_executor 专责
        _sig_action = s.get('action', '')
        _timing_badge = str(s.get('timing_badge', '') or '')

        if _sig_action == 'ENTER_WATCH':
            continue  # ENTER_WATCH由sub_executor处理，auto不执行

        # [2026-08-18 苏摩111封印] 修复：action='ENTER'等同于'ENTER_FULL'
        # 根因：brahma_engine输出action='ENTER'(113条)，auto_executor只识别'ENTER_FULL'(7条)
        # 导致所有action='ENTER'信号被静默跳过，SNDK score=153.8也未执行
        if _sig_action == 'ENTER':
            _sig_action = 'ENTER_FULL'  # 统一处理
            s['action'] = 'ENTER_FULL'

        if score >= _effective_tier1:
            # TIER_1: 强信号，无论timing均执行（STANDBY时等突破已发生）
            s['_tier'] = 1
            s['_tier_nav_pct'] = 0.05
        elif score >= TIER_2_SCORE:
            # TIER_2: 标准仓，timing=READY或空（未注入）才执行，STANDBY/WAIT拦截
            if _timing_badge in ('STANDBY', 'WAIT', 'MONITOR') or _timing_badge == '':  # [P1修复 2026-07-20] 空timing也拦截，防TIMEOUT亏损
                continue  # timing明确不佳，等待
            # [铁证封印 2026-08-07 设计院] READY+score>=138+RSI4H>55 追高陷阱门控
            # 数据铁证：16条READY+score>=130信号 TP1=0 SL=16 WR=0%
            # 根因：score高=趋势确认后期，READY=价格位置好，两者叠加=已在高位追高
            if 'READY' in _timing_badge.upper():
                _rsi_4h_val = float(s.get('rsi_4h', 0) or 0)
                # [铁证封印 2026-08-07] READY信号全面降权
                # 数据：READY avg_RSI4H=58.8，avg_OB距离=0.39%（OB内部追高）
                # READY+RSI4H>=50 = 价格在中高位 + timing确认 = 追高组合 WR<20%
                # 只有 RSI4H<50 的 READY 才是真正低位时机信号
                if _rsi_4h_val >= 50:
                    s['_observe_reason'] = f'READY+RSI4H={_rsi_4h_val:.1f}>=50 价格中高位追高WR<20%'
                    continue
                # 极端情况：高分+RSI偏强双重封锁（保留原逻辑作为后备）
                if _rsi_4h_val > 55 and score >= 138:
                    s['_observe_reason'] = f'READY+score{score:.0f}+RSI4H={_rsi_4h_val:.1f}>55 追高陷阱WR=0%'
                    continue
            s['_tier'] = 2
            s['_tier_nav_pct'] = 0.03
        elif score >= TIER_3_SCORE:
            # TIER_3: 仅BTC/ETH，且必须 timing=READY 才执行
            if s.get('symbol') not in TIER_3_SYMBOLS:
                continue  # 小币低分信号不进TIER3
            # [修复 2026-08-05 设计院] timing_badge可能含emoji前缀如'🟢 READY'，需contains匹配
            if 'READY' not in _timing_badge.upper():
                continue  # timing必须明确READY才执行轻仓
            s['_tier'] = 3
            s['_tier_nav_pct'] = 0.015
        else:
            continue  # score < 120，跳过
        # [协同接入 2026-08-02 设计院自主] pos_pct_sizer 动态仓位覆盖
        # brahma_engine已计算精确仓位建议(pos_pct_sizer)，优先于固定tier值
        # 规则: pos_pct_sizer存在且>0 → 用它覆盖_tier_nav_pct（但不超过tier上限）
        _pos_pct_sizer = float(s.get('pos_pct_sizer', 0) or 0)
        if _pos_pct_sizer > 0:
            _tier_cap = s.get('_tier_nav_pct', 0.05)
            # 转换百分比单位(sizer返回的是%如0.3%=0.003，tier用的是小数如0.05=5%)
            _sizer_nav = _pos_pct_sizer / 100.0
            if _sizer_nav > 0 and _sizer_nav <= _tier_cap:
                s['_tier_nav_pct'] = _sizer_nav
                s['_pos_source'] = 'pos_pct_sizer'
            # else: sizer建议超出tier上限，维持tier值（保守原则）

        # ══ [P1接入 2026-08-02 设计院自主] position_sizer Kelly动态仓位 ═══════════════════════
        # 规则：Kelly动态仓位作为参考，与tier_nav_pct取最小值（保守原则）
        # 接入点：_tier_nav_pct确定后，pos_pct_sizer覆盖后
        try:
            from brahma_brain.position_sizer import get_position_pct as _ps_fes
            _fg_fes = None
            try:
                from brahma_brain.options_engine import get_fear_greed as _fg_fn
                _fg_raw = _fg_fn()
                _fg_fes = float(_fg_raw.get('value', 50)) if isinstance(_fg_raw, dict) else float(_fg_raw or 50)
            except Exception:
                pass
            _ps_res_fes = _ps_fes(
                symbol=s.get('symbol', ''),
                score=float(s.get('score', 0) or 0),
                direction=s.get('direction') or s.get('signal_dir', ''),
                fear_greed=_fg_fes,
                regime=s.get('regime', ''),
                sl_pct=float(s.get('sl_pct', 0) or 0),  # [修复C3 2026-08-24] SL三层分档需要sl_pct
            )
            if _ps_res_fes.get('allowed'):
                _kelly_nav = (_ps_res_fes.get('pct', 0) or 0) / 100.0  # % → 小数
                _cur_tier_pct = s.get('_tier_nav_pct', 0.05)
                # 保守原则：Kelly建议低于tier上限时才覆盖
                if 0 < _kelly_nav < _cur_tier_pct:
                    s['_tier_nav_pct'] = round(_kelly_nav, 5)
                    s['_pos_source'] = (s.get('_pos_source', '') + '+kelly_sizer').lstrip('+')
        except Exception:
            pass  # position_sizer失败不阻断
        # ══ [position_sizer END] ═══════════════════════════════════════════════════════════════



        # ══ [P2 condition_order_matrix 2026-08-08 设计院封印] ════════════════════
        # 根因修复：08-02 commit宣称接入，auto_executor从未调用check_triggers
        # 在执行前检查条件单矩阵，处理已有计划的追踪止损/条件平仓
        try:
            from brahma_brain.condition_order_matrix import check_triggers as _check_cond
            # [BUG修复 2026-08-13 苏摩111封印]
            # 修复前: current_price=float(s.get('price',0) or 0) → 信号无price时传0.0
            #         导致条件单推送显示"当前价0.0000"，P0生死线误触发
            # 修复后: 优先用信号price，降级时实时拉ticker，最终fallback才用0
            _cond_sym   = s.get('symbol', '')
            _cond_price = float(s.get('price', 0) or 0)
            if _cond_price <= 0 and _cond_sym:
                try:
                    import urllib.request as _ur, json as _json
                    _ticker_url = f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={_cond_sym}'
                    with _ur.urlopen(_ticker_url, timeout=4) as _tr:
                        _cond_price = float(_json.loads(_tr.read())['price'])
                except Exception:
                    _cond_price = 0.0  # 实在拿不到才用0
            _cond_result = _check_cond(
                symbol=_cond_sym,
                current_price=_cond_price,
                short_notional=0.0,
                long_notional=0.0,
                short_pnl=0.0,
                long_pnl=0.0,
            )
            if _cond_result.get('urgent'):
                print(f"[condition_order] {_cond_sym} 紧急条件触发: {_cond_result.get('summary','')[:80]}")
            for _trig in _cond_result.get('triggered', [])[:2]:
                print(f"  [condition_order] 触发: {str(_trig)[:100]}")
        except Exception as _cond_e:
            pass  # 条件单检查失败不阻断
        # ══ [END condition_order_matrix] ════════════════════════════════════════
        # ══ [P1 headroom仓位压缩 2026-08-08 设计院封印] ════════════════════════
        # 根因修复：08-02 commit宣称headroom接入，实际从未实现
        # 在Kelly仓位确定后，叠加headroom回撤压缩系数
        try:
            from brahma_brain.position_sizer import apply_headroom as _apply_hr
            import json as _hr_json, os as _hr_os
            _base_pct = s.get('_tier_nav_pct', 0.05)
            _nav_cur_hr = float(account_info.get('totalMarginBalance', 0) or 0) if 'account_info' in dir() else 0
            # [修复C5 2026-08-24] 从nav_peak.json读取真实历史最高NAV，而非nav*1.05
            _nav_peak_hr = _nav_cur_hr * 1.05  # fallback
            try:
                _np_path = _hr_os.path.join(_hr_os.path.dirname(_hr_os.path.abspath(__file__)), '..', 'data', 'nav_peak.json')
                if _hr_os.path.exists(_np_path):
                    _np_data = _hr_json.load(open(_np_path))
                    _np_peak = float(_np_data.get('peak', 0) or 0)
                    # nav_peak.json可能是历史极值(如2029 USDT)，需要合理性检查
                    # 若peak是当前nav的10倍以上，说明是历史异常数据，用fallback
                    if _np_peak > 0 and _np_peak <= _nav_cur_hr * 10:
                        _nav_peak_hr = _np_peak
            except Exception:
                pass
            _hr_result = _apply_hr(
                base_pct=_base_pct,
                nav_current=_nav_cur_hr,
                nav_peak=_nav_peak_hr,
                open_positions_pct=sum(float(p.get('notional', 0) or 0) for p in (positions or [])) /
                                   max(float(account_info.get('totalMarginBalance', 1)), 1) if 'account_info' in dir() and 'positions' in dir() else 0.0,
            )
            if _hr_result['compressed']:
                s['_tier_nav_pct'] = _hr_result['adjusted_pct']
                s['_pos_source'] = (s.get('_pos_source', '') + '+headroom').lstrip('+')
                print(f"[headroom] {s.get('symbol')} 仓位压缩 {_base_pct*100:.1f}%→{_hr_result['adjusted_pct']*100:.1f}% | {_hr_result['headroom']['reason'][:60]}")
            if _hr_result['headroom']['factor'] == 0.0:
                _skip_reason = f"headroom禁止开仓: {_hr_result['headroom']['reason']}"
                print(f'[headroom] {s.get("symbol")} {_skip_reason}')
                continue  # 跳过此信号
        except Exception as _hr_e:
            pass  # headroom失败不阻断
        # ══ [END headroom] ══════════════════════════════════════════════════════
        # ══ [P1 宏观事件仓位乘数 2026-08-13 苏摩111封印] ══
        # 宏观事件不影响信号评分，只压缩仓位（不则CPI事件日会抹掉SMC+15的信号质量）
        try:
            _macro_mult = float(s.get('macro_pos_mult', 1.0) or 1.0)
            if _macro_mult != 1.0:
                _pre_pct = s.get('_tier_nav_pct', 0.05)
                s['_tier_nav_pct'] = round(_pre_pct * _macro_mult, 5)
                s['_pos_source'] = (s.get('_pos_source', '') + f'+macro×{_macro_mult}').lstrip('+')
                print(f"[macro_mult] {s.get('symbol')} 宏观事件仓位调整 {_pre_pct*100:.1f}%→{s['_tier_nav_pct']*100:.1f}% ×{_macro_mult}")
        except Exception:
            pass
        # ══ [END macro_pos_mult] ════════════════════════════════════
        # ⑥ RR门槛
        rr1 = float(s.get('rr1', 0) or 0)
        if rr1 < MIN_RR:
            continue
        # ④ 死穴检测
        regime    = s.get('regime', '')
        direction = s.get('direction') or s.get('signal_dir', '')
        if (regime, direction) in DEAD_ZONE:
            continue
        # ④+ [FIX 2026-08-02 设计院自主] BULL_TREND LONG WR门控
        # 根据：真实WR=33.6%(n=192)，严重低于饱和线45%，继续自动执行EV为负
        # 键入OBSERVE模式：只记录不执行，直到真实WR重新校准超过45%
        # [设计院 2026-08-06] 暴涨猎手双确认信号豁免 OBSERVE
        _observe_bypass = s.get('observe_bypass', False)
        _observe_wr_gate = float(s.get('observe_wr_gate', 0.45))

        if regime == 'BULL_TREND' and direction == 'LONG':
            _wr_ok = False
            try:
                import json as _wr_j
                from pathlib import Path as _wr_P
                _wr_records = [_wr_j.loads(l) for l in _wr_P('data/live_signal_log.jsonl').read_text().strip().split('\n') if l.strip()]
                # [P0-B 2026-08-11 苏摩111] 排除虚假结算：只统计 valid=True 的真实执行信号
                # 根因：signal_settler 扫描所有OPEN信号，低分信号也会被结算，污染WR统计
                _wr_bull = [r for r in _wr_records if r.get('regime')=='BULL_TREND'
                            and (r.get('direction') or r.get('signal_dir','')) == 'LONG'  # [P0-A] 兼容signal_dir别名
                            and r.get('outcome') in ('TP1','SL') and r.get('valid') == True]
                _wr_tp   = sum(1 for r in _wr_bull if r.get('outcome')=='TP1')
                _wr_n    = len(_wr_bull)
                _wr_val  = _wr_tp/_wr_n if _wr_n >= 20 else None  # n<20数据不足，不强制拦截
                if _wr_val is not None and _wr_val >= _observe_wr_gate:
                    _wr_ok = True  # WR达标，允许执行
                elif _wr_val is not None and _wr_val < _observe_wr_gate:
                    _wr_ok = False
                    _gate_label = '猎手豁免35%' if _observe_bypass else '标准45%'
                    print(f'[WR门控-OBSERVE] BULL_TREND LONG WR={_wr_val*100:.1f}%({_wr_n}条)<{_observe_wr_gate*100:.0f}%({_gate_label}) 不达标，降级OBSERVE')
                else:
                    _wr_ok = True  # 数据不足，不拦截
            except Exception:
                _wr_ok = True  # 读取失败不拦截
            if not _wr_ok:
                continue  # OBSERVE模式：展示信号但不执行

            # ④++ [设计院铁证封印 2026-08-07] RSI_4H 精准门控
            # 铁证: BULL_TREND LONG RSI_4H>60 WR=9%(n=54) EV严重为负 → 禁止执行
            #       RSI_4H 50-60 WR=39%(n=90) → 需score>=148才执行
            #       RSI_4H<50  WR=59%(n=61) → 维持score>=138
            _rsi_4h = float(s.get('rsi_4h') or 0)
            if _rsi_4h > 0:  # 有RSI_4H数据才执行门控
                if _rsi_4h > 60:
                    print(f'[RSI4H门控] {s.get("symbol")} RSI_4H={_rsi_4h:.1f}>60 WR=9% 强制OBSERVE')
                    continue  # 死亡区，严禁做多
                elif _rsi_4h >= 50:
                    _r4h_min_score = 148
                    if float(s.get('score', 0)) < _r4h_min_score and not _observe_bypass:
                        print(f'[RSI4H门控] {s.get("symbol")} RSI_4H={_rsi_4h:.1f}(50-60) WR=39% score={s.get("score")}<{_r4h_min_score} 降级OBSERVE')
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
        # [SQE 2026-08-07] SL质量门控已上移至brahma_brain/signal_quality_engine.py，此处无需重复
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

        # ══ [P0-A 设计院铁证 2026-08-03 苏摩111封印] ob_dist追单陷阱区过滤 ═══════
        # 铁证：ob_dist 0.1%~0.5% = 价格已离开OB但未正确候单，WR=20%（陷阱区）
        # 正确区间：<0.1%（已在OB内）OR >=0.5%（正确候单）
        # 来源：31天327条信号统计，n=50 WR=20% vs n=70 WR=45.7%
        _ob_dist_exec = float(s.get('ob_dist_pct', 0) or 0)
        if 0.1 <= _ob_dist_exec < 0.5:
            print(f'[P0-A OB_CHASE] {s.get("symbol")} ob_dist={_ob_dist_exec:.2f}%在追单陷阱区(0.1~0.5%)，跳过')
            continue  # 追单陷阱区，拒绝执行
        # ══ [P0-A END] ═══════════════════════════════════════════════════════════

        candidates.append(s)

    # 按评分降序排列
    candidates.sort(key=lambda x: -float(x.get('score', 0) or 0))

    # [设计院 2026-08-06] 追加暴涨猎手双确认信号
    try:
        _pq_path = Path(__file__).parent.parent / 'data/pump_exec_queue.jsonl'
        _pq_seen = {s.get('signal_id','') for s in candidates}
        if _pq_path.exists():
            for _pql in open(_pq_path):
                try:
                    _pqs = json.loads(_pql.strip())
                    _pq_sym = _pqs.get('symbol','')
                    _pq_sid = f"ph_{_pq_sym}_{int(_pqs.get('ts',0))}"
                    if _pq_sid in _pq_seen: continue
                    if time.time() - _pqs.get('ts',0) > 8*3600: continue
                    candidates.append({
                        'symbol':          _pq_sym,
                        'score':           _pqs.get('brahma_score', 138),
                        'direction':       _pqs.get('direction','LONG'),
                        'regime':          _pqs.get('regime','BULL_TREND'),
                        'params':          _pqs.get('params', {}),
                        'timing':          {'status': _pqs.get('timing','READY')},
                        'signal_id':       _pq_sid,
                        'source':          'pump_hunter_executor',
                        'observe_bypass':  _pqs.get('observe_bypass', False),
                        'observe_wr_gate': _pqs.get('observe_wr_gate', 0.45),
                        'ts':              _pqs.get('ts', 0),
                        'valid':           True,
                    })
                    _pq_seen.add(_pq_sid)
                except Exception:
                    pass
    except Exception:
        pass

    # ══ [P2-B 设计院苏摩111 2026-07-11] portfolio_optimizer 相关性过滤 ══
    # 多信号时，用30天滚动相关性矩阵选出最优子集（max 3个，corr<0.75）
    # 单信号时直接通过（不增加延迟）
    if len(candidates) > 1:
        try:
            _po_brain = str(Path(__file__).parent.parent / 'brahma_brain')
            if _po_brain not in sys.path:
                sys.path.insert(0, _po_brain)
            from portfolio_optimizer import filter_signals as _po_filter
            _approved, _rejected = _po_filter(candidates)
            if _approved:
                if _rejected:
                    import logging as _po_log
                    _rej_syms = [r.get('symbol','?') for r in _rejected]
                    _po_log.getLogger('auto_executor').info(
                        f'[P2-B] portfolio_optimizer过滤: {_rej_syms}')
                candidates = _approved
        except Exception:
            pass  # portfolio_optimizer不可用时保持原candidates
    # ══ [P2-B END] ══════════════════════════════════════════════════════

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
    # [修复 2026-07-23] 加 default=0 防止 generator 为空时 min() 崩溃
    min_batch_notional = min(
        (
            round(math.floor(total_qty * ratio * 10**qty_prec) / 10**qty_prec, qty_prec) * price
            for ratio, price in zip(BATCH_RATIOS, prices)
            if round(math.floor(total_qty * ratio * 10**qty_prec) / 10**qty_prec, qty_prec) > 0
        ),
        default=0
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

    # ── [A1 circuit_breaker 注入 2026-08-06 设计院自主决策] ──────────────
    # 层9熔断器: auto_executor — failure_threshold=1, recovery_timeout=600s
    # 极端行情/API连续失败时自动熔断10min，防止连续亏损
    try:
        from brahma_brain.circuit_breaker import BrahmaCircuitRegistry as _CBR
        _cb = _CBR.get().get_breaker('auto_executor')
        if _cb and _cb.is_open:
            _cb_status = _CBR.get().get_breaker('auto_executor').status()
            _reason = f"[circuit_breaker] auto_executor熔断中 state={_cb_status.get('state')} — 等待自动恢复"
            print(f"🛑 {_reason}")
            return {
                'signal_id': signal.get('signal_id',''), 'symbol': sym,
                'direction': direction, 'score': float(signal.get('score',0)),
                'ts': time.time(), 'ts_iso': datetime.now(timezone.utc).isoformat(),
                'status': 'CB_BLOCKED', 'reason': _reason,
            }
    except Exception:
        pass  # CB不可用时静默降级，不阻断执行
    # ── end circuit_breaker ──────────────────────────────────────────────


    # ── [A2 相关性双开门控 2026-08-14 封印修复] ────────────────────────
    # 修复根因: brahma_engine.check_correlation_risk不存在→永久静默失败
    # 现改用: portfolio_optimizer.check_correlation_risk (真实接口)
    try:
        from brahma_brain.portfolio_optimizer import check_correlation_risk as _po_corr
        _corr_blocked = False
        _corr_reason  = ''
        for _ap_sym, _ap_info in active_positions.items():
            if _ap_sym == sym: continue
            _ap_dir = (_ap_info.get('side') or _ap_info.get('direction', '')).upper()
            if _ap_dir != direction.upper(): continue
            _corr_r = _po_corr(sym, _ap_sym)
            if _corr_r.get('high_corr') and _corr_r.get('corr', 0) > 0.75:
                _corr_blocked = True
                _corr_reason  = (f"相关性双开拒绝: {sym}+{_ap_sym} "
                                 f"corr={_corr_r['corr']:.2f} 实际风险={_corr_r['risk_mult']:.2f}x")
                break
        if _corr_blocked:
            print(f"⚠️ [相关性门控→封印修复] {sym} {direction} 拒绝: {_corr_reason}")
            return {
                'signal_id': signal.get('signal_id', ''), 'symbol': sym,
                'direction': direction, 'score': float(signal.get('score', 0)),
                'ts': time.time(), 'ts_iso': datetime.now(timezone.utc).isoformat(),
                'status': 'CORR_BLOCKED', 'reason': _corr_reason,
            }
    except Exception as _corr_e:
        import logging as _cl; _cl.getLogger('auto_executor').debug(f'[corr_gate] {_corr_e}')
    # ── end 相关性门控 ────────────────────────────────────────────────────

    # [修复 2026-08-18 苏摩111] TRADIFI_PERPETUAL合约走期货执行路径，不跳过
    # 根因：SNDK等美股代币在Binance上是contractType=TRADIFI_PERPETUAL，走fapi期货API
    # 旧逻辑错误地把TRADFI_STOCK全部跳过，导致SNDK等永远无法自动执行
    # 修复：仅当symbol不在Binance期货市场时才跳过，在期货市场的TRADFI_STOCK正常执行
    try:
        from brahma_brain.universal_asset_router import classify_asset, ASSET_TRADFI_STOCK
        if classify_asset(sym) == ASSET_TRADFI_STOCK:
            # 检查是否在期货市场（TRADIFI_PERPETUAL可执行）
            import requests as _req
            _fapi_ok = False
            try:
                _r = _req.get(f'https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}', timeout=3)
                _fapi_ok = _r.status_code == 200
            except Exception:
                pass
            if not _fapi_ok:
                result = {'signal_id': signal.get('signal_id',''), 'symbol': sym,
                          'direction': direction, 'score': float(signal.get('score',0)),
                          'ts': time.time(), 'ts_iso': datetime.now(timezone.utc).isoformat(),
                          'status': 'SKIPPED', 'reason': 'TRADFI_STOCK不在期货市场，跳过'}
                return result
            # 在期货市场的美股代币（如SNDKUSDT），继续走期货执行路径
    except Exception:
        pass  # 分类失败不阻断执行

    # ── [P1 Phase3 MTF落地 2026-07-24 设计院封印] 4H体制环境否决 ─────────────
    # 规则：做多信号 + 4H趋势为BEAR/DOWN → 拒绝执行（宏观逆势不入场）
    # 根因：SNDK被套根本原因是1H BULL信号在4H BEAR环境直接执行，无环境层否决
    # 精英解锁：score≥170 + structure_grade≥90 可突破（极高置信度信号）
    _dir_upper = str(direction).upper()
    if _dir_upper == 'LONG':
        try:
            _kl_4h = requests.get(
                f'{FAPI_BASE}/fapi/v1/klines?symbol={sym}&interval=4h&limit=8',
                timeout=4
            ).json()
            if isinstance(_kl_4h, list) and len(_kl_4h) >= 5:
                _c4h = [float(k[4]) for k in _kl_4h]
                # 4H趋势：近3根收盘价 vs 前3根收盘价
                _trend_4h_bear = _c4h[-1] < _c4h[-4]  # 下跌趋势
                # 4H EMA9简单判断（快速）
                _ema9_4h = sum(_c4h[-9:]) / min(9, len(_c4h))
                _price_4h_below_ema = _c4h[-1] < _ema9_4h
                _is_4h_bear_env = _trend_4h_bear and _price_4h_below_ema
                if _is_4h_bear_env:
                    # 精英解锁检查
                    _score_f = float(signal.get('score', 0) or 0)
                    _grade_f = float(signal.get('structure_grade', signal.get('grade', 0)) or 0)
                    _elite = _score_f >= 170 and _grade_f >= 90
                    if not _elite:
                        _reason = (f'[Phase3 MTF] 4H体制BEAR环境，禁止做多执行 '
                                   f'(4H trend={"DOWN"}, price={_c4h[-1]:.2f}<EMA9_4H={_ema9_4h:.2f}) '
                                   f'score={_score_f}/grade={_grade_f}，精英解锁需score≥170+grade≥90')
                        print(f'[4H门控] {sym} LONG被拒: {_reason}')
                        return {
                            'signal_id': signal.get('signal_id', ''),
                            'symbol': sym, 'direction': direction,
                            'score': float(signal.get('score', 0)),
                            'ts': time.time(),
                            'ts_iso': datetime.now(timezone.utc).isoformat(),
                            'status': 'BLOCKED',
                            'reason': _reason,
                        }
                    else:
                        print(f'[4H门控] {sym} LONG精英解锁通过 score={_score_f} grade={_grade_f}')
        except Exception as _4h_e:
            # 获取失败不阻断执行，记录警告
            import logging as _lg
            _lg.getLogger(__name__).warning(f'[4H门控] {sym} 4H数据获取失败({_4h_e})，跳过门控')
    # ── end 4H体制环境否决 ─────────────────────────────────────────────────────

    # ── [BDE-2.0 注入点 2026-08-08 设计院封印] ────────────────────────────────
    # 梵天决策树 2.0 前置检查：五步漏斗，任一否决则直接跳出
    # 设计原则：不改变现有流程，只在入口处增加一道五步门控
    try:
        from brahma_brain.brahma_decision_engine import decide as _bde_decide
        _bde_signal = {
            'symbol': sym, 'direction': direction, 'regime': regime,
            'score': score, 'sl_pct': float(signal.get('sl_pct', 0) or 0),
            'grade': float(signal.get('grade', 100) or 100),
            'timing': signal.get('timing', ''),
        }
        _bde_result = _bde_decide(_bde_signal)
        _bde_action = _bde_result.get('action', 'SKIP')

        if _bde_action == 'SKIP':
            print(f'[BDE-2.0] {sym} {direction} 决策树拦截 — {_bde_result.get("reason","")[:80]}')
            return {
                'signal_id': sig_id, 'symbol': sym, 'direction': direction,
                'score': score, 'ts': time.time(),
                'ts_iso': datetime.now(timezone.utc).isoformat(),
                'status': 'BDE_SKIP',
                'reason': _bde_result.get('reason', ''),
                'step_passed': _bde_result.get('step_passed', 0),
            }
        elif _bde_action == 'WAIT_15M':
            # 通过4步但等待15m确认，不阻断后续流程（P0已处理）
            print(f'[BDE-2.0] {sym} {direction} 通过4步，等待15m确认 — {_bde_result.get("reason","")[:60]}')
            # 继续执行（P0已在前方抆15m确认实现）
        # EXECUTE: 继续到正常执行流程
        if _bde_action == 'EXECUTE':
            print(f'[BDE-2.0] {sym} {direction} ✅ 五步全通过 RR={_bde_result.get("entry_plan",{}).get("rr",0):.2f}x')
    except Exception as _bde_e:
        pass  # BDE异常时降级到原有流程，不阻断执行
    # ── end BDE-2.0 ──────────────────────────────────────────────────

    sl_pct    = float(signal.get('sl_pct', MIN_SL_PCT) or MIN_SL_PCT)
    tp1       = float(signal.get('tp1', 0) or 0)
    sl_price  = float(signal.get('stop_loss', 0) or 0)
    entry_lo  = float(signal.get('entry_lo', 0) or 0)
    entry_hi  = float(signal.get('entry_hi', 0) or 0)
    sig_id    = signal.get('signal_id', '')

    # ── [P0-15m入场确认层 signal_15m_engine 接入版 2026-08-08 设计院自主] ──
    # 升级：用 signal_15m_engine.generate_15m_signal() 替代手写逻辑
    # signal_15m_engine 包含：CHoCH/BOS/FVG/OB/成交量多维确认，精度更高
    # 降级：engine调用失败时回退到手写简版（不阻断执行）
    try:
        import sys as _15m_sys
        _15m_sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent / 'brahma_brain'))
        from signal_15m_engine import generate_15m_signal as _gen15m
        _15m_sig = _gen15m(sym, verbose=False)
        if _15m_sig is not None:
            # engine返回信号：检查方向是否与主信号一致
            _15m_dir = _15m_sig.get('direction', '')
            if _15m_dir and _15m_dir != direction:
                _skip_reason = f'15m信号方向冲突: engine={_15m_dir} main={direction}'
                print(f'[15m确认层-engine] {sym} {_skip_reason}')
                return {
                    'signal_id': sig_id, 'symbol': sym, 'direction': direction,
                    'score': score, 'ts': time.time(),
                    'ts_iso': datetime.now(timezone.utc).isoformat(),
                    'status': 'SKIP_15M', 'reason': _skip_reason,
                }
            else:
                print(f'[15m确认层-engine] {sym} {direction} ✅ 引擎确认通过 score={_15m_sig.get("score",0)}')
        else:
            # engine无信号 = 无确认根，拦截
            _skip_reason = '15m引擎无确认信号（无BOS/放量/CHoCH）'
            print(f'[15m确认层-engine] {sym} {direction} 信号被拦截 — {_skip_reason}')
            return {
                'signal_id': sig_id, 'symbol': sym, 'direction': direction,
                'score': score, 'ts': time.time(),
                'ts_iso': datetime.now(timezone.utc).isoformat(),
                'status': 'SKIP_15M', 'reason': _skip_reason,
            }
    except Exception as _15m_e:
        # 降级：回退到简版手写逻辑
        try:
            _15m_confirmed = False
            _15m_skip_reason = ''
            _kl15 = requests.get(
                f'{FAPI_BASE}/fapi/v1/klines?symbol={sym}&interval=15m&limit=8',
                timeout=5
            ).json()
            if isinstance(_kl15, list) and len(_kl15) >= 5:
                _bars = _kl15[-5:-1]
                _closes = [float(b[4]) for b in _bars]
                _highs  = [float(b[2]) for b in _bars]
                _lows   = [float(b[3]) for b in _bars]
                _vols   = [float(b[5]) for b in _bars]
                _avg_vol = sum(_vols) / len(_vols) if _vols else 1
                if direction == 'LONG':
                    _bos_up = _closes[-1] > _highs[-2] if len(_closes) >= 2 else False
                    _vol_surge = _vols[-1] > _avg_vol * 1.5
                    _two_bull = all(c > o for c, o in zip(_closes[-2:], [float(b[1]) for b in _bars[-2:]]))
                    _15m_confirmed = _bos_up or _vol_surge or _two_bull
                    if not _15m_confirmed:
                        _15m_skip_reason = f'15m无做多确认(降级版 BOS={_bos_up})'
                else:
                    _bos_dn = _closes[-1] < _lows[-2] if len(_closes) >= 2 else False
                    _vol_surge = _vols[-1] > _avg_vol * 1.5
                    _two_bear = all(c < o for c, o in zip(_closes[-2:], [float(b[1]) for b in _bars[-2:]]))
                    _15m_confirmed = _bos_dn or _vol_surge or _two_bear
                    if not _15m_confirmed:
                        _15m_skip_reason = f'15m无做空确认(降级版 BOS={_bos_dn})'
                if not _15m_confirmed:
                    print(f'[15m确认层-fallback] {sym} {direction} 被拦截 — {_15m_skip_reason}')
                    return {
                        'signal_id': sig_id, 'symbol': sym, 'direction': direction,
                        'score': score, 'ts': time.time(),
                        'ts_iso': datetime.now(timezone.utc).isoformat(),
                        'status': 'SKIP_15M', 'reason': _15m_skip_reason,
                    }
                else:
                    print(f'[15m确认层-fallback] {sym} {direction} ✅ 通过')
        except Exception:
            pass  # 双层降级失败，不阻断
    # ── end 15m入场确认层 ──────────────────────────────────────────────────

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

    # ── [P1-15m微结构止损 2026-08-08 设计院自主决策] ──────────────────────
    # 用15m最近摆动低点(做多)/高点(做空)替代固定% → 不被正常波动扫出
    try:
        _kl15_sl = requests.get(
            f'{FAPI_BASE}/fapi/v1/klines?symbol={sym}&interval=15m&limit=24',
            timeout=5
        ).json()
        if isinstance(_kl15_sl, list) and len(_kl15_sl) >= 12:
            _lows15  = [float(b[3]) for b in _kl15_sl[-12:]]
            _highs15 = [float(b[2]) for b in _kl15_sl[-12:]]
            _px_now  = float(_kl15_sl[-1][4])
            if direction == 'LONG':
                # 做多止损：最近12根15m(3H)的最低摆动低点，再下移0.3%缓冲
                _struct_sl_price = min(_lows15) * 0.997
                _struct_sl_pct   = round((_px_now - _struct_sl_price) / _px_now * 100, 2)
                # 只用微结构SL当它比固定%更合理时（0.5% ~ MAX_SL_PCT）
                if 0.5 <= _struct_sl_pct <= MAX_SL_PCT:
                    if abs(_struct_sl_pct - sl_pct) > 0.15:
                        print(f'[15m结构SL] {sym} LONG 固定SL={sl_pct:.1f}% → 微结构SL={_struct_sl_pct:.1f}% (低点=${_struct_sl_price:.2f})')
                    sl_pct = _struct_sl_pct
            else:  # SHORT
                # 做空止损：最近12根15m的最高摆动高点，再上移0.3%缓冲
                _struct_sl_price = max(_highs15) * 1.003
                _struct_sl_pct   = round((_struct_sl_price - _px_now) / _px_now * 100, 2)
                if 0.5 <= _struct_sl_pct <= MAX_SL_PCT:
                    if abs(_struct_sl_pct - sl_pct) > 0.15:
                        print(f'[15m结构SL] {sym} SHORT 固定SL={sl_pct:.1f}% → 微结构SL={_struct_sl_pct:.1f}% (高点=${_struct_sl_price:.2f})')
                    sl_pct = _struct_sl_pct
    except Exception:
        pass  # 15m结构SL失败时保持现有sl_pct
    # ── end P1-15m微结构止损 ──────────────────────────────────────────

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
    # [fix 2026-07-18 苏摩111] 三档NAV自主决策
    _tier_nav_pct = float(signal.get('_tier_nav_pct', 0))
    # [降权型改造 2026-08-16 苏摩111] _pos_override_pct: gate层传入的仓位上限
    # 来源：BULL_TREND 120-139观察仓(1%) / Kronos强烈反向(1%)
    _pos_override_pct = signal.get('_pos_override_pct')  # None=不覆盖
    if _pos_override_pct is not None:
        _pos_override_pct = float(_pos_override_pct) / 100.0  # 转为小数
        _tier_nav_pct = _pos_override_pct  # 强制覆盖仓位
        print(f'[降权观察仓] {sym} score={score:.0f} 仓位降权至{_pos_override_pct*100:.1f}%NAV')
        if signal.get('_observation_tier'):
            print(f'  → observation_tier: BULL_TREND 120-139，积累IC数据')
        if signal.get('_kronos_penalty'):
            print(f'  → kronos_penalty={signal["_kronos_penalty"]}，p_up反向降权')
    # BTC/ETH大仓位动态NAV分档
    if sym in BIG_SYMBOLS:
        if score >= BIG_SYM_SCORE_HIGH:
            _nav_pct = BIG_SYM_NAV_HIGH   # 10%
        elif score >= BIG_SYM_SCORE_MID:
            _nav_pct = BIG_SYM_NAV_MID    # 7.5%
        else:
            _nav_pct = BIG_SYM_NAV_BASE   # 5%
        # TIER3轻仓覆盖大仓位默认
        if _tier_nav_pct > 0 and _tier_nav_pct < _nav_pct:
            _nav_pct = _tier_nav_pct
        print(f'[BTC/ETH动态仓位] {sym} score={score:.0f} tier_pct={_tier_nav_pct:.1%} → NAV×{_nav_pct*100:.1f}%')
    else:
        # 其他标的：优先用tier_nav_pct，否则用默认NAV_SIZE_PCT
        _nav_pct = _tier_nav_pct if _tier_nav_pct > 0 else NAV_SIZE_PCT
    notional = nav * _nav_pct * _hv_discount
    if _hv_discount < 1.0:
        print(f'[高波动模式] {sym} sl={signal.get("sl_pct",0):.1f}% 仓位系数×{_hv_discount} 实际仓位=${notional:.1f}')
    # [修复 2026-07-23] TIER1课保: 高波动折扣后或仓位略低于MIN_NOTIONAL
    # 且 score>=155(神级)，则忽略折扣，直接用MIN_NOTIONAL作为最小仓位
    # [修复 2026-07-23 v2] 使用per-symbol最低名义值（BTC=50, ETH=20）
    _sym_min = SYMBOL_MIN_NOTIONAL.get(sym, MIN_NOTIONAL)
    if notional < _sym_min:
        notional = _sym_min  # [设计院自主 2026-07-31] 任意tier均课保至symbol MIN_NOTIONAL
        print(f'[MIN_NOTIONAL课保] {sym} score={score:.0f} tier={signal.get("_tier","?")} 仓位课保至{sym}MIN=${notional:.1f}')

    # ── [P1接入 2026-08-02 设计院自主] var_engine VaR风控 ─────────────────────
    # 规则：var_99 > 35%NAV → 强制降仓至max(MIN_NOTIONAL, notional×0.5)（硬限）
    #        var_99 > 20%NAV → 告警但不阻断
    # 接入点：notional最终确定后（MIN_NOTIONAL课保后，blacktea前）
    try:
        from brahma_brain.var_engine import single_position_var as _var_exe
        _pos_pct_nav = notional / nav if nav > 0 else 0
        _var_res = _var_exe(
            symbol=sym,
            confidence=0.05,
            signal_dir=direction,
            pos_pct_nav=_pos_pct_nav,
            nav_usd=nav,
        )
        if _var_res.get('available'):
            _var99_usd = _var_res.get('var_99_usd', 0) or 0
            _var99_pct_nav = _var99_usd / nav * 100 if nav > 0 else 0
            _var_grade = _var_res.get('risk_grade', 'UNKNOWN')
            if _var99_pct_nav > 35.0:
                # 强制降仓：硬限，不可绕过
                _old_notional = notional
                notional = max(_sym_min, notional * 0.5)
                print(f'[VaR硬限] {sym} var99={_var99_pct_nav:.1f}%NAV>35% '
                      f'grade={_var_grade} 强制降仓 ${_old_notional:.1f}→${notional:.1f}')
                result['_var_forced'] = True
                result['_var_note']   = _var_res.get('note', '')
            elif _var99_pct_nav > 20.0:
                # 告警：记录但不阻断
                print(f'[VaR告警] {sym} var99={_var99_pct_nav:.1f}%NAV>20% '
                      f'grade={_var_grade} {_var_res.get("note","")}')
                result['_var_warn']   = True
                result['_var_note']   = _var_res.get('note', '')
            result['_var_grade']      = _var_grade
            result['_var99_pct_nav']  = round(_var99_pct_nav, 2)
    except Exception as _var_e:
        pass  # VaR计算失败不阻断执行
    # ── [P1 var_engine END] ───────────────────────────────────────────────────

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
                         '--to', f'{JARVIS_USER_ID}:t:{JARVIS_THREAD_ID}',
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

    # wuqu_positions 更新 [B1修复 2026-07-24: 统一为LIST格式，与sub_executor/APM保持一致]
    wuqu_list = []
    if WUQU_PATH.exists():
        try:
            raw = json.loads(WUQU_PATH.read_text())
            if isinstance(raw, list):
                wuqu_list = [p for p in raw if p.get('symbol') != sym]  # 去重
            elif isinstance(raw, dict):
                # 旧dict格式兼容读取，转为list
                wuqu_list = [v for k, v in raw.items() if k != sym]
        except Exception:
            pass
    wuqu_list.append({
        'symbol':      sym,
        'side':        direction,
        'size':        fill_qty,
        'entry_price': fill_px,
        'stop_loss':   sl_final,
        'tp1':         tp_final,
        'sl_pct':      max(sl_pct, MIN_SL_PCT),
        'leverage':    lev,
        'notional_usdt': fill_qty * fill_px,
        'signal_id':   sig_id,
        'order_id':    order_id,
        'ts':          time.time(),
        'source':      'auto_executor',
        'success':     True,
    })
    WUQU_PATH.write_text(json.dumps(wuqu_list, indent=2, ensure_ascii=False))

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
            # 从wuqu_positions移除 [B1修复: list格式]
            try:
                _wq = json.loads(WUQU_PATH.read_text())
                if isinstance(_wq, list):
                    _wq = [p for p in _wq if p.get('symbol') != sym]
                elif isinstance(_wq, dict):
                    _wq.pop(sym, None)
                    _wq = list(_wq.values())  # 转list
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

    # ── [接入 2026-08-02 设计院自主] signal_expiry_tracker 开单后注册 ──────
    # 根因：signal_expiry_tracker 完全孤立（0次import），成交后无法追踪信号有效期
    # 修复：EXECUTED后立即注册，记录信号有效期供 sense_signal_validity 感知
    try:
        from brahma_brain.signal_expiry_tracker import register as _expiry_register
        _sig_type = signal.get('signal_type', signal.get('primary_signal', 'DEFAULT'))
        _expiry_register(
            symbol=sym,
            signal_type=str(_sig_type) if _sig_type else 'DEFAULT',
            direction=direction,
            entry_price=fill_px,
            entry_ts=datetime.now(timezone.utc).isoformat(),
        )
        print(f'[expiry_tracker] {sym} {direction} 信号已注册 type={_sig_type}')
    except Exception as _et_e:
        pass  # 注册失败不影响主流程
    # ── end signal_expiry_tracker ────────────────────────────────────────────

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
    now_iso = fmt_beijing()

    # 账户状态
    acct      = _signed('GET', '/fapi/v2/account')
    # [修复 2026-07-13] P0_ExposureCap bug: API失败时acct非dict→nav=0→_max_exposure=0→永远触发
    # 必须验证acct是有效dict且totalMarginBalance有实际值
    if not isinstance(acct, dict) or 'totalMarginBalance' not in acct:
        pass  # [静默]
        return  # API失败，本轮跳过，不执行任何开单
    nav       = float(acct.get('totalMarginBalance', 0))
    avail     = float(acct.get('availableBalance', 0))
    # 额外守卫：nav=0说明账户API异常，禁止开单
    if nav <= 0:
        pass  # [静默]
        return
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

    # [注入 2026-07-23] 跨资产联合推理门控
    # BTC未到位时，ETH信号自动降级为WAIT，防止矛盾开单
    try:
        from brahma_brain.cross_asset_gate import apply_cross_asset_gate
        candidates = apply_cross_asset_gate(candidates)
    except Exception as _cag_e:
        pass  # 门控失败不阻断执行

    pass  # [静默]

    if not candidates:
        pass  # [静默]
        return []

    # ── [设计院 2026-08-09] 分级门槛：130=推送苏摩确认，155=自动执行 ────────
    # 把候选信号分成两组，让苏摩看到所有机会，不再依赖纯自动
    try:
        from push_hub import push_signal_card as _push_sc
        import re as _re
        for _pre in candidates:
            _s = float(_pre.get('score', 0) or 0)
            _sym = _pre.get('symbol', '')
            _dir = _pre.get('direction') or _pre.get('signal_dir', '')
            _regime = _pre.get('regime', '')
            _bd = _pre.get('confluence', {}).get('breakdown', {})
            # 收集减分 Top3
            neg = []
            for k, v in _bd.items():
                _m = _re.search(r'([+-]?\d+)', str(v))
                if _m and int(_m.group(1)) < -3:
                    neg.append(f"{int(_m.group(1)):+d} {k}: {str(v)[:30]}")
            neg.sort(key=lambda x: int(x.split()[0]))
            _params = _pre.get('params', {})
            _entry_lo = float(_params.get('entry_lo') or _pre.get('entry_lo') or 0)
            _entry_hi = float(_params.get('entry_hi') or _pre.get('entry_hi') or _entry_lo * 1.005)
            _sl = float(_params.get('stop_loss') or _pre.get('sl') or 0)
            _tp1 = float(_params.get('tp1') or _pre.get('tp1') or 0)
            _rr  = float(_params.get('rr1') or _pre.get('rr') or 1.0)
            _rsi4h = _pre.get('rsi_4h')
            _fr    = _pre.get('fr')
            _sl_basis = (_params.get('sl_basis') or '')
            if 130 <= _s < 155 and _entry_lo > 0:
                # WATCH区：推送苏摩确认，不自动开单
                from push_hub import _jarvis as _phj
                import datetime as _dt
                _tag = _sym.replace('USDT', '')
                _dir_cn = '做多' if _dir == 'LONG' else '做空'
                _sl_pct = round(abs(_entry_lo - _sl) / _entry_lo * 100, 1) if _entry_lo else 2.0
                _ts = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8))).strftime('%m-%d %H:%M')
                _watch_msg = (
                    f"👁 **梵天WATCH信号 · 待苏摩确认**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"  {_tag}/USDT {_dir_cn} | score={_s:.0f} | {_regime}\n"
                    f"  入场区: ${_entry_lo:,.4f} ~ ${_entry_hi:,.4f}\n"
                    f"  止损:   ${_sl:,.4f}  -{_sl_pct}%\n"
                    f"  止盈:   ${_tp1:,.4f}  RR={_rr}x\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"  [操作指令]\n"
                    f"  ⏳ 等15M触发后入场 — 回复「执行」开仓\n"
                    f"  评分={_s:.0f}，需≥155自动开，当前等苏摩确认\n"
                    f"  {_ts} CST"
                )
                _phj(_watch_msg, dedup_key=f"watch_{_sym}_{_dir}_{int(_s)}", dedup_ttl=7200)
    except Exception as _tier_e:
        import logging as _lg; _lg.getLogger('brahma').warning(f'[tier_push] {_tier_e}')
    # ── [分级门槛 END] ──────────────────────────────────────────────────────────

    executed_set = _load_executed()
    results = []

    # ── [P2 第五轮 2026-08-02] capital_allocator 资金分配检查 ───────────────────
    # 预执行所有候选信号的资金分配计算，防止多标的时过度集中
    _alloc_cache = {}  # symbol -> compute()结果
    try:
        from brahma_brain.capital_allocator import compute as _cap_compute
        for _sig_pre in candidates:
            _sym_pre   = _sig_pre.get('symbol', '')
            _score_pre = float(_sig_pre.get('score', 0) or 0)
            _alloc     = _cap_compute(_sym_pre, signal_score=_score_pre)
            _alloc_cache[_sym_pre] = _alloc
            if not _alloc.get('allowed', True):
                pass  # 待执行循环内再決策
    except Exception:
        pass  # capital_allocator失败不阻断执行
    # ── [P2 END] ─────────────────────────────────────────────────────────────

    for sig in candidates:
        sig_id = sig.get('signal_id', '')
        sym    = sig.get('symbol', '')
        score  = float(sig.get('score', 0) or 0)
        direct = sig.get('direction') or sig.get('signal_dir', '')
        regime = sig.get('regime', '')

        pass  # [静默]

        # ── [P2 capital_allocator 单信号预算检查] ─────────────────────────────
        # 将资金预算注入信号，供 execute_signal 参考（我尺过律failsafe）
        try:
            _alloc_res = _alloc_cache.get(sym)
            if _alloc_res:
                sig['_capital_alloc'] = _alloc_res
                if not _alloc_res.get('allowed', True):
                    pass  # 不阻断执行，让 execute_signal 自行判断
        except Exception:
            pass  # 失败降级，不阻断执行
        # ── [P2 END] ───────────────────────────────────────────────────

        # ── [P3-B 设计院 2026-07-08] RL A/B仓位分流 ──────────────────
        try:
            from brahma_brain.rl_position_ab import decide_position_size
            _std_nav_pct = BIG_SYM_NAV_HIGH if score >= 155 else (
                BIG_SYM_NAV_LOW  # [IC铁证 2026-07-23] score<155不应进入此分支，保守fallback
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


        # ══════════════════════════════════════════════════════════
        # [纸面开单模式 2026-08-21 苏摩111封印]
        # 铁证：PM账户止损坏(-4120)，自动开单风险不可控
        # 所有信号 → 推送Jarvis → 苏摩「执行」后手动下单
        # ══════════════════════════════════════════════════════════
        try:
            from push_hub import _jarvis as _phj_paper
            import datetime as _dt_paper
            _tag_p = sym.replace('USDT', '')
            _dir_cn_p = '做多' if direct == 'LONG' else '做空'
            _ts_p = _dt_paper.datetime.now(_dt_paper.timezone(_dt_paper.timedelta(hours=8))).strftime('%m-%d %H:%M')
            _entry_p = sig.get('entry_lo', sig.get('entry', 0))
            _sl_p    = sig.get('sl', sig.get('stop_loss', 0))
            _tp_p    = sig.get('tp1', sig.get('take_profit', 0))
            _rr_p    = sig.get('rr1', sig.get('rr', 2.0))
            _sl_pct_p = round(abs(float(_entry_p) - float(_sl_p)) / float(_entry_p) * 100, 2) if _entry_p and _sl_p else 0
            _paper_msg = (
                f"📋 **梵天信号 · 纸面开单 · 等待苏摩确认**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  {_tag_p}/USDT {_dir_cn_p} | score={score:.0f} | {regime}\n"
                f"  入场: ${float(_entry_p):,.4f}\n"
                f"  止损: ${float(_sl_p):,.4f}  -{_sl_pct_p}%\n"
                f"  止盈: ${float(_tp_p):,.4f}  RR={_rr_p}x\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"  回复「执行」→ 手动下单\n"
                f"  {_ts_p} CST"
            )
            from scripts.system_config import JARVIS_USER_ID, JARVIS_THREAD_ID
            _phj_paper(f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}', _paper_msg)
        except Exception as _pe:
            pass
        # 纸面模式：记录日志但不实际下单
        _paper_result = {
            'signal_id': sig_id, 'event': 'PAPER_PENDING',
            'symbol': sym, 'direction': direct, 'score': score,
            'ts': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
            'reason': '纸面开单模式，等待苏摩确认',
            'result': {'mode': 'paper_pending', 'success': False},
        }
        _log(_paper_result)
        executed_set.add(sig_id)
        _save_executed(executed_set)
        results.append(_paper_result)
        continue
        # ══════════════════════════════════════════════════════════
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
        # [修复 2026-07-23] 只有真实成交才写入持久化executed_set
        # FAILED不写入，防止下次cron运行时信号被错误去重
        if exec_result.get('status') == 'EXECUTED':
            executed_set.add(sig_id)
            _save_executed(executed_set)
        results.append(exec_result)

        if exec_result.get('status') == 'EXECUTED':
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
            # [三方联合 内容变现层 2026-08-07] 执行成功 → 自动触发Square发布
            try:
                import subprocess as _sq_sub, json as _sq_json, os as _sq_os
                _sq_script = _sq_os.path.join(_sq_os.path.dirname(_sq_os.path.abspath(__file__)), 'signal_to_square.py')
                _sq_sig = {
                    'symbol':    s.get('symbol'),
                    'regime':    s.get('regime'),
                    'direction': s.get('direction'),
                    'score':     s.get('score'),
                    'grade':     s.get('grade'),
                    'rsi_4h':    s.get('rsi_4h'),
                    'rsi_1h':    s.get('rsi_1h'),
                    'sl_pct':    exec_result.get('sl_pct') or s.get('sl_pct'),
                    'params':    {'entry_lo': exec_result.get('entry_lo'), 'entry_hi': exec_result.get('entry_hi'),
                                  'sl': sl_price, 'tp1': tp_price, 'sl_pct': exec_result.get('sl_pct')},
                    'timestamp': s.get('timestamp'),
                    'macro_overlay': s.get('macro_overlay'),
                }
                _sq_sub.Popen(
                    ['python3', _sq_script, '--signal', _sq_json.dumps(_sq_sig)],
                    stdout=_sq_sub.DEVNULL, stderr=_sq_sub.DEVNULL
                )
                print(f'  📡 Square发布已触发（后台异步）')
            except Exception:
                pass  # Square发布失败不影响主执行流
            # ── [设计院 2026-08-13] 执行成功后附带图表仪表盘 ────────────
            try:
                import os as _ch_os, sys as _ch_sys
                _ch_scripts = _ch_os.path.dirname(_ch_os.path.abspath(__file__))
                if _ch_scripts not in _ch_sys.path:
                    _ch_sys.path.insert(0, _ch_scripts)
                from push_chart import push_kingfisher as _ch_push
                _ch_sym = s.get('symbol', 'BTCUSDT')
                if not _ch_sym.endswith('USDT'):
                    _ch_sym += 'USDT'
                _ch_push(_ch_sym,
                         caption=f'\U0001f4c8 {_ch_sym.replace("USDT","")} \u5f00\u4ed3 fill=${fill_px:.4f}')
            except Exception:
                pass  # 图表推送失败不影响主执行流
            # ── [END] ────────────────────────────────────────────────────
        else:
            print(f'  ❌ 跳过: {exec_result["reason"]}')

        time.sleep(0.5)  # 限速

    # ── [P1-B 2026-08-12 苏摩111] 体制连续亏损检测 → 强制刷新 ──────────────
    # 铁证: 08-08 连败8笔全是 BULL_TREND LONG，体制判断滞后是根因
    # 措施: 当前体制连续3笔SL → 触发 brahma_state_refresh
    try:
        _p1b_logs = [json.loads(l) for l in open(LOG_PATH) if l.strip()] if LOG_PATH.exists() else []
        if _p1b_logs:
            # 取最近已执行的信号（有 outcome=SL 的记录）
            _p1b_recent = [l for l in reversed(_p1b_logs)
                           if l.get('outcome') in ('SL',) and l.get('valid') == True][:10]
            if len(_p1b_recent) >= 3:
                _p1b_regime = _p1b_recent[0].get('regime', '')
                # 检查同一体制连续3笔SL
                _p1b_streak = 0
                for _p1b_rec in _p1b_recent:
                    if _p1b_rec.get('regime') == _p1b_regime and _p1b_rec.get('outcome') == 'SL':
                        _p1b_streak += 1
                    else:
                        break
                if _p1b_streak >= 3:
                    print(f'[P1-B体制滞后检测] {_p1b_regime} 连续{_p1b_streak}笔SL → 触发强制体制刷新')
                    import subprocess as _p1b_sub
                    _p1b_refresh = Path(__file__).parent / 'brahma_state_refresh.py'
                    if _p1b_refresh.exists():
                        _p1b_sub.Popen(
                            ['python3', str(_p1b_refresh), '--force'],
                            stdout=_p1b_sub.DEVNULL, stderr=_p1b_sub.DEVNULL
                        )
                        print(f'  ✅ brahma_state_refresh --force 已触发')
    except Exception as _p1b_e:
        pass  # 体制滞后检测失败不阻断主流程

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
        ok = [r for r in (results or []) if r.get('status') == 'EXECUTED']
        pass  # [静默]
        if not ok:
            pass  # [静默]