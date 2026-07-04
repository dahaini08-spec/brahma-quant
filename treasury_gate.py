#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  梵天国库官 · treasury_gate.py                                    ║
║  唯一持仓状态管理者 · 开仓申请审批 · 全局敞口守卫                  ║
║  版本: v2.0  创建: 2026-05-20  升级: 2026-06-06  设计院出品        ║
╠══════════════════════════════════════════════════════════════════╣
║  铁律：                                                           ║
║    1. 所有开仓必须经此申请，国库官是唯一入口                        ║
║    2. 所有持仓写入必须经此原子执行                                  ║
║    3. 国库官拒绝 = 信号作废，任何系统不得绕过                       ║
║    4. 不经国库官直接写 brahma_state = 系统错误                     ║
╚══════════════════════════════════════════════════════════════════╝

用法:
  from treasury_gate import get_treasury, OpenRequest

  req = OpenRequest(system_id="B_HUNTER_F", symbol="BTCUSDT", ...)
  result = get_treasury().request_open(req)
  if result.approved:
      place_real_order(...)
  else:
      logger.info(f"国库官拒绝: {result.reason}")
"""

import os
import json
import time
import fcntl
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List

# ── 日志 ──────────────────────────────────────────────────────────
log = logging.getLogger("treasury_gate")
if not log.handlers:
    import sys as _sys
    _h = logging.StreamHandler(_sys.stdout)
    _h.setFormatter(logging.Formatter("[TreasuryGate] %(asctime)s %(levelname)s %(message)s",
                                      datefmt="%H:%M:%S"))
    log.addHandler(_h)
log.setLevel(logging.INFO)

# ── 路径 ──────────────────────────────────────────────────────────
_HERE       = os.path.dirname(os.path.abspath(__file__))
STATE_FILE  = os.path.join(_HERE, "data", "brahma_state.json")
LOCK_FILE   = STATE_FILE + ".lock"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


# ════════════════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════════════════

@dataclass
class OpenRequest:
    """开仓申请单 — 标准化结构，所有系统统一使用"""
    system_id:    str          # "A_BRAHMA" / "B_HUNTER_A" / "B_HUNTER_F" 等
    symbol:       str          # "BTCUSDT"
    direction:    str          # "做多" / "做空"
    notional:     float        # 名义金额（USDT）
    entry_price:  float
    sl:           float
    tp1:          float
    tp2:          float
    signal_id:    str          # 唯一信号ID，防重复提交
    channel:      str = "?"    # 信号通道（A/B/C/D/E/F）
    score:        float = 0    # 议会评分
    regime:       str = "?"    # 体制状态
    is_test:      bool = False # 测试单不写主状态
    extra:        dict = field(default_factory=dict)  # 附加字段透传
    signal_type:  str = "TREND"  # TREND / SCALP / FADE


@dataclass
class ApprovalResult:
    """审批结果"""
    approved:     bool
    reason:       str
    position_id:  Optional[str] = None  # 审批通过后的持仓ID


# ════════════════════════════════════════════════════════════════════
# 文件锁（防并发写入撕裂）
# ════════════════════════════════════════════════════════════════════

class _FileLock:
    """基于 fcntl 的文件级独占锁"""

    def __init__(self, path: str):
        self.path = path
        self.fd   = None

    def __enter__(self):
        self.fd = open(self.path, "w")
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
            self.fd = None


# ════════════════════════════════════════════════════════════════════
# 国库官主体
# ════════════════════════════════════════════════════════════════════

class TreasuryGate:
    """
    梵天唯一持仓状态管理者。
    所有开仓/平仓操作必须经此执行，禁止直接读写 brahma_state.json。
    """

    # ── 全局风控参数 ─────────────────────────────────────────────────
    MAX_TOTAL_EXPOSURE_PCT: float = 0.65   # 总敞口上限（65%=NAV$132×0.65=$86保证金，支持3-5单）
    MAX_TOTAL_SLOTS:        int   = 10     # 全系统同时持仓上限
    GATE3_LIMIT:            int   = 10     # 渐进解锁：总风险%NAV硬停替代单一数量限制
    MIN_WUQU_PAPER_N:       int   = 50     # 自动开单最小武曲Paper条数
    MIN_WUQU_WR:            float = 0.50   # 武曲Paper最低胜率
    SL_COOLDOWN_MIN:        int   = 60     # SL触发后同标的冷却期（分钟）
    MAX_SAME_SYMBOL:        int   = 1      # 同一币种只允许1个系统持仓
    MAX_TOTAL_RISK_PCT:     float = 0.15   # 总风险上限15%NAV（量化宽松核心参数）
    MIN_NOTIONAL:           float = 1.0    # 最小名义金额（过滤测试单）
    DEDUP_WINDOW_MIN:       int   = 90     # 同指纹信号去重窗口（分钟）
    MARGIN_SAFETY_PCT:      float = 0.85   # 保证金安全系数（最多用余额85%）
    # ── 铁律（不依赖任何外部状态，绝对硬限制）──────────────────────
    MAX_SINGLE_NOTIONAL:    float = 20.0   # 铁律①：单笔最大名义金额（$20，绝对上限，不可绕过）
    MIN_SAFE_NAV:           float = 10.0   # 铁律②：NAV低于此值→拒绝开仓（Fail-Safe）

    # ── 体制黑名单 → [v24.3-fix] 全部改为降权矩阵，不再直接拒绝 ──
    # 哲学原则: 门槛是权重不是墙; WR=6.7%是B级污染数据，干净WR=43% n=7
    REGIME_BLACKLIST: tuple = ()  # [v24.3] 清空黑名单，改用REGIME_SCORE_PENALTY
    REGIME_SCORE_PENALTY: dict = {
        'BEAR_RECOVERY': -15,   # 降权-15分（干净WR=43% n=7，样本不足，中性轻惩）
        'CHOP_HIGH':     -10,   # CHOP偏高波动降权
    }

    # ── 评分×体制策略矩阵 ──────────────────────────────────────────
    # 格式：{regime: [(score_min, score_max, notional_pct, allow)], ...}
    SCORE_REGIME_MATRIX: dict = None  # 延迟初始化

    # 各系统最大槽位
    SLOTS_PER_SYSTEM: dict = {
        "A_BRAHMA":       5,   # 主系统（brahma_direct / BRAHMA_CONFIRM）
        "B_HUNTER_A":     3,
        "B_HUNTER_B":     3,
        "B_HUNTER_C":     3,
        "B_HUNTER_D":     2,
        "B_HUNTER_E":     2,
        "B_HUNTER_F":     3,
        "B_HUNTER_ALPHA": 2,
    }

    # 主系统来源标识（这些 source/channel 映射到 A_BRAHMA）
    BRAHMA_SOURCES: tuple = ("brahma_direct", "BRAHMA_CONFIRM", "A_BRAHMA")

    # ── 开仓申请入口（唯一入口）────────────────────────────────────
    def request_open(self, req: OpenRequest) -> ApprovalResult:
        """
        开仓申请审批。
        通过所有检查后原子写入 brahma_state.json，返回 ApprovalResult。
        """
        # ── 防测试单污染 ──────────────────────────────────────────
        if req.is_test:
            log.warning(f"[REJECT] {req.symbol} 测试单拦截，不写主状态")
            return ApprovalResult(False, "TEST单不进主状态")

        if req.notional < self.MIN_NOTIONAL:
            log.warning(f"[REJECT] {req.symbol} notional={req.notional} < 最小{self.MIN_NOTIONAL}")
            return ApprovalResult(False, f"名义金额{req.notional}太小，疑似测试单")

        # ── 铁律①：单笔绝对上限（不依赖任何外部状态） ─────────────────
        if req.notional > self.MAX_SINGLE_NOTIONAL:
            log.error(f"[IRON-RULE-1] {req.symbol} notional={req.notional:.2f} 超单笔上限${self.MAX_SINGLE_NOTIONAL}，拒绝")
            return ApprovalResult(False, f"铁律①拒绝：单笔名义金额${req.notional:.2f}超过硬性上限${self.MAX_SINGLE_NOTIONAL}")

        # ── 铁律③：宏观数据封仓检查（锁外快速拦截）─────────────────
        try:
            import sys as _sys
            _sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
            from scripts.macro_guard import check_signal_allowed
            _sig_type = getattr(req, 'signal_type', 'TREND')
            _allowed, _reason = check_signal_allowed(_sig_type)
            if not _allowed:
                log.warning(f"[MACRO-BLOCK] {req.symbol} {_reason}")
                return ApprovalResult(False, f"宏观封仓: {_reason}")
        except ImportError:
            pass  # macro_guard未安装时不阻断

        with _FileLock(LOCK_FILE):
            state = self._load()

            # ── 关-1：Gate3动态上限（武曲Paper渐进解锁）────────────
            active_positions = [p for p in state.get('positions', [])
                                if p.get('status') not in ('closed', 'CLOSED', 'expired')]
            if len(active_positions) >= self.GATE3_LIMIT:
                log.warning(f"[REJECT] Gate3={self.GATE3_LIMIT}已满({len(active_positions)}持仓)，拒绝开仓")
                return ApprovalResult(False, f"Gate3={self.GATE3_LIMIT}已满，等待持仓结算")

            # ── 关-2：武曲Paper最低验证（auto_mode下才检查）────────────
            import os
            auto_flag = os.path.join(os.path.dirname(__file__), 'data', 'auto_mode.flag')
            if os.path.exists(auto_flag):
                try:
                    import json as _json
                    _wp_path = os.path.join(os.path.dirname(__file__), 'data', 'wuqu_paper_state.json')
                    _wp   = _json.load(open(_wp_path))
                    # Fix-A: wins+losses才是真实验证条数（total_paper字段长期为0）
                    _wins = _wp.get('wins', 0)
                    _loss = _wp.get('losses', 0)
                    _n    = _wins + _loss
                    _trig = _n
                    _wr   = _wins / _trig if _trig > 0 else 0
                    if _n < self.MIN_WUQU_PAPER_N:
                        log.warning(f"[REJECT] 武曲Paper n={_n}<{self.MIN_WUQU_PAPER_N}，auto_mode下禁止开仓")
                        return ApprovalResult(False, f"武曲Paper n={_n}<{self.MIN_WUQU_PAPER_N}，积累数据中")
                    if _trig >= 10 and _wr < self.MIN_WUQU_WR:
                        log.warning(f"[REJECT] 武曲Paper WR={_wr:.1%}<{self.MIN_WUQU_WR:.0%}，暂停自动开仓")
                        return ApprovalResult(False, f"武曲Paper WR={_wr:.1%}<{self.MIN_WUQU_WR:.0%}，信号质量不足")
                except Exception as _e:
                    log.warning(f"[WARN] 武曲Paper验证失败({_e})，继续审批")

            # ── 关0：体制黑名单 [v24.3 已清空，改降权] ──────────────
            # REGIME_BLACKLIST=() 永远不触发，REGIME_SCORE_PENALTY在上游评分层执行
            if req.regime in self.REGIME_BLACKLIST:  # 永远False
                pass  # 不会执行

            # ── 关1：信号指纹去重（强化版）────────────────────────
            # 指纹 = symbol + direction + round(entry_price,2) 的哈希
            # 90分钟内同指纹只允许批准一次，不依赖调用方传正确signal_id
            fp = hashlib.md5(
                f"{req.symbol}_{req.direction}_{round(req.entry_price, 2)}".encode()
            ).hexdigest()[:12]
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.DEDUP_WINDOW_MIN)
            for p in state["positions"]:
                if p.get("_fp") == fp and p.get("status") == "OPEN":
                    try:
                        p_ts = datetime.fromisoformat(p.get("created_at","1970").rstrip("Z") + "+00:00")
                        if p_ts > cutoff:
                            log.warning(f"[REJECT] {req.symbol} 指纹{fp}在{self.DEDUP_WINDOW_MIN}min内重复")
                            return ApprovalResult(False, f"{req.symbol}信号指纹重复，{self.DEDUP_WINDOW_MIN}min内禁止重复开仓")
                    except Exception:
                        pass
            # 旧signal_id去重（兼容）—— 只检查OPEN持仓，不检查已关闭的dry_run记录
            if req.signal_id and any(
                    p.get("signal_id") == req.signal_id and p.get("status") == "OPEN"
                    for p in state["positions"]):
                log.warning(f"[REJECT] signal_id={req.signal_id} 已存在，重复提交")
                return ApprovalResult(False, f"信号{req.signal_id}已存在，禁止重复开仓")

            open_pos = [p for p in state["positions"] if p.get("status") == "OPEN"]

            # ── 关2：同币种检查 ───────────────────────────────────
            same_sym = [p for p in open_pos if p.get("symbol") == req.symbol]
            if same_sym:
                holder = same_sym[0].get("system_id", "unknown")
                log.warning(f"[REJECT] {req.symbol} 已被 {holder} 持有")
                return ApprovalResult(
                    False, f"{req.symbol}已被{holder}持有，禁止双系统同币")

            # ── 关3：NAV敞口 + 实时余额预检 ───────────────────────
            nav = state.get("nav", 0)
            leverage = req.extra.get("leverage", 3)
            req_margin = req.notional / max(leverage, 1)
            total_margin = sum(p.get("notional", 0) / max(p.get("leverage", 3), 1)
                               for p in open_pos)
            # ── 铁律②：NAV未知→Fail-Safe拒绝（非跳过）──────────────────
            if nav < self.MIN_SAFE_NAV:
                log.error(f"[IRON-RULE-2] {req.symbol} NAV={nav:.2f}<{self.MIN_SAFE_NAV}，状态未知，Fail-Safe拒绝")
                return ApprovalResult(False, f"铁律②拒绝：NAV={nav:.2f}未知，等待状态刷新后重试")

            if nav > 0:
                new_pct = (total_margin + req_margin) / nav
                if new_pct > self.MAX_TOTAL_EXPOSURE_PCT:
                    used = round(total_margin / nav * 100, 1)
                    log.warning(
                        f"[REJECT] {req.symbol} NAV={nav:.2f} "
                        f"保证金已用{used}% 加仓后超{self.MAX_TOTAL_EXPOSURE_PCT*100}%")
                    return ApprovalResult(
                        False,
                        f"保证金敞口{used}%，加{req_margin:.1f}后超{self.MAX_TOTAL_EXPOSURE_PCT*100}%上限")

            # ── 关3b：实时余额预检（防止Binance拒单）─────────────
            avail = state.get("available_balance", 0)  # 铁律③：fallback=0，防止nav污染余额检查
            if avail > 0 and req_margin > avail * self.MARGIN_SAFETY_PCT:
                log.warning(
                    f"[REJECT] {req.symbol} 需要保证金{req_margin:.2f} "
                    f"可用余额{avail:.2f} 超过安全系数{self.MARGIN_SAFETY_PCT}")
                return ApprovalResult(
                    False,
                    f"保证金{req_margin:.2f}超可用余额{avail:.2f}×{self.MARGIN_SAFETY_PCT}")

            # ── 关3c：SL冷却期（同标的SL后60min禁止再开）─────────────
            sl_log = state.get('sl_cooldown_log', {})
            last_sl_ts = sl_log.get(req.symbol, 0)
            cooldown_sec = self.SL_COOLDOWN_MIN * 60
            if (time.time() - last_sl_ts) < cooldown_sec:
                remaining = int((cooldown_sec - (time.time() - last_sl_ts)) / 60)
                log.warning(
                    f"[REJECT] {req.symbol} SL冷却期中，剩余{remaining}min")
                return ApprovalResult(
                    False, f"{req.symbol}触发SL冷却期，剩余{remaining}min")

            # ── 关4：系统槽位上限 ─────────────────────────────────
            sys_open = [p for p in open_pos
                        if p.get("system_id") == req.system_id]
            max_slots = self.SLOTS_PER_SYSTEM.get(req.system_id, 2)
            if len(sys_open) >= max_slots:
                log.warning(
                    f"[REJECT] {req.system_id} 槽位已满 {len(sys_open)}/{max_slots}")
                return ApprovalResult(
                    False, f"{req.system_id}槽位已满{len(sys_open)}/{max_slots}")

            # ── 关5：全局总槽位上限 ───────────────────────────────
            if len(open_pos) >= self.MAX_TOTAL_SLOTS:
                log.warning(f"[REJECT] 全系统持仓已达上限{self.MAX_TOTAL_SLOTS}")
                return ApprovalResult(
                    False, f"全系统持仓已达上限{self.MAX_TOTAL_SLOTS}")

            # ── 关5b：仓位分级上限（score_tier → notional 封顶） ──────────────
            # S+: 170+ → NAV的 8.0%  | S: 160+ → 6.5%  | S2: 150+ → 5.0%
            # B:  130+ → 2.0%  | C: <130 → 0（不执行）
            # 如果req.extra里已有score_pos（brahma计算的分层仓位），直接用它
            _score_tier_caps = {
                'S+': 0.080, 'S': 0.065, 'S2': 0.050, 'B': 0.020, 'C': 0.0
            }
            _tier   = req.extra.get('score_tier', '')
            _pos_pct = req.extra.get('score_pos', None)  # brahma已计算好的最终仓位
            if _pos_pct is None and _tier in _score_tier_caps:
                _pos_pct = _score_tier_caps[_tier]
            if _pos_pct is not None and nav > 0:
                _max_notional_by_tier = nav * _pos_pct
                if _pos_pct == 0.0:
                    log.warning(f"[REJECT] {req.symbol} score_tier={_tier or 'C'}，分层仓位=0，不执行")
                    return ApprovalResult(False, f"score_tier={_tier or 'C'}，小于门控不执行")
                if req.notional > _max_notional_by_tier * 1.05:  # 5%宽败
                    _capped = round(_max_notional_by_tier, 2)
                    log.warning(
                        f"[TIER-CAP] {req.symbol} notional={req.notional:.2f} "
                        f"> tier={_tier}({_pos_pct:.0%}) 上限=${_capped:.2f}，调整到上限")
                    # 不拒绝，但考爆到分级上限（隶级而非拒绝）
                    req.notional = _capped
                    log.info(f"[TIER-CAP] {req.symbol} notional降次至 ${_capped:.2f}")

            # ── 全部通过 → 写入 ───────────────────────────────────
            pos_id = f"{req.system_id}_{req.symbol}_{int(time.time())}"
            pos_record = {
                "position_id":  pos_id,
                "system_id":    req.system_id,
                "symbol":       req.symbol,
                "direction":    req.direction,
                "entry_price":  req.entry_price,
                "notional":     req.notional,
                "sl":           req.sl,
                "tp1":          req.tp1,
                "tp2":          req.tp2,
                "signal_id":    req.signal_id,
                "_fp":          fp,    # 信号指纹（强化去重）
                "channel":      req.channel,
                "score":        req.score,
                "regime":       req.regime,
                "status":       "OPEN",
                "source":       req.system_id,
                "created_at":   _utcnow(),
                "updated_at":   _utcnow(),
                **req.extra,   # 透传附加字段（达摩院特征快照等）
            }
            state["positions"].append(pos_record)
            state["_meta"] = {
                "last_writer":   "treasury_gate_v1",
                "last_updated":  _utcnow(),
                "open_slots":    len(open_pos) + 1,
            }
            # 写入 audit_log
            state.setdefault("audit_log", []).append({
                "time":       _utcnow(),
                "action":     "APPROVED",
                "system_id":  req.system_id,
                "symbol":     req.symbol,
                "direction":  req.direction,
                "notional":   req.notional,
                "position_id": pos_id,
                "signal_id":  req.signal_id,
            })
            self._save(state)

            log.info(
                f"[APPROVED] {req.system_id} {req.symbol} {req.direction} "
                f"notional={req.notional:.2f} pos_id={pos_id}")
            return ApprovalResult(True, "OK", pos_id)

    # ── 平仓申请入口 ──────────────────────────────────────────────
    def request_close(self, position_id: str,
                      reason: str,
                      exit_price: float,
                      pnl_pct: float = 0.0) -> bool:
        """
        平仓申请（唯一平仓入口）。
        找到 position_id 对应持仓 → 标记 CLOSED → 原子写入。
        """
        with _FileLock(LOCK_FILE):
            state = self._load()
            found = False
            for p in state["positions"]:
                if p.get("position_id") == position_id:
                    p["status"]      = "CLOSED"
                    p["exit_price"]  = exit_price
                    p["exit_reason"] = reason
                    p["pnl_pct"]     = pnl_pct
                    p["closed_at"]   = _utcnow()
                    p["updated_at"]  = _utcnow()
                    found = True
                    break

            if found:
                state.setdefault("audit_log", []).append({
                    "time":        _utcnow(),
                    "action":      "CLOSED",
                    "position_id": position_id,
                    "reason":      reason,
                    "exit_price":  exit_price,
                    "pnl_pct":     pnl_pct,
                })
                self._save(state)
                log.info(f"[CLOSED] position_id={position_id} reason={reason} pnl={pnl_pct:.2%}")
            else:
                log.warning(f"[CLOSE_FAIL] position_id={position_id} 未找到")
            return found

    # ── 全局状态快照（只读）─────────────────────────────────────
    def get_snapshot(self) -> dict:
        """
        任何系统查询全局状态（只读，不加锁）。
        返回：NAV / 总敞口 / 各系统槽位 / 全部持仓列表
        """
        state     = self._load()
        open_pos  = [p for p in state["positions"] if p.get("status") == "OPEN"]
        nav       = state.get("nav", 0)
        total_notional = sum(p.get("notional", 0) for p in open_pos)
        slots_by_sys = {}
        for p in open_pos:
            sid = p.get("system_id", "unknown")
            slots_by_sys[sid] = slots_by_sys.get(sid, 0) + 1

        return {
            "nav":              nav,
            "total_notional":   round(total_notional, 2),
            "exposure_pct":     round(total_notional / nav * 100, 1) if nav else 0,
            "total_open_slots": len(open_pos),
            "max_slots":        self.MAX_TOTAL_SLOTS,
            "slots_by_system":  slots_by_sys,
            "open_positions":   open_pos,
            "can_open":         len(open_pos) < self.MAX_TOTAL_SLOTS,
        }

    # ── 更新 NAV（由 nav_tracker 调用）────────────────────────────
    def update_nav(self, nav: float) -> None:
        """更新 NAV，供 nav_tracker 定期调用"""
        with _FileLock(LOCK_FILE):
            state = self._load()
            state["nav"] = nav
            state.setdefault("_meta", {})["nav_updated"] = _utcnow()
            self._save(state)
            log.info(f"[NAV] 更新 NAV={nav:.4f}")

    def update_regime(self, regime: str, action: str,
                      kelly_mul: float = 1.0,
                      direction_allow: Optional[List[str]] = None) -> None:
        """
        更新体制状态，由 brahma_core / commander 调用。
        所有子系统通过 get_snapshot() 读取同一体制，不各自判断。
        """
        with _FileLock(LOCK_FILE):
            state = self._load()
            state.setdefault("regime_snapshot", {}).update({
                "state":           regime,
                "action":          action,
                "kelly_mul":       kelly_mul,
                "direction_allow": direction_allow or ["多", "空"],
                "updated_at":      _utcnow(),
            })
            self._save(state)
            log.info(f"[REGIME] {regime} action={action} kelly_mul={kelly_mul:.2f}")

    def get_regime(self) -> dict:
        """
        任何子系统调用此方法获取当前体制（只读）。
        返回体制快照，含 state/action/kelly_mul/direction_allow。
        """
        state = self._load()
        return state.get("regime_snapshot", {
            "state":           "UNKNOWN",
            "action":          "CAUTIOUS",
            "kelly_mul":       0.5,
            "direction_allow": ["多", "空"],
            "updated_at":      None,
        })

    # ── 内部方法 ──────────────────────────────────────────────────
    def _normalize_position(self, p: dict) -> dict:
        """标准化持仓字段——兼容 sl_price/sl 两种写法"""
        if p.get('sl_price') and not p.get('sl'):
            p['sl'] = p['sl_price']
        if p.get('tp1_price') and not p.get('tp1'):
            p['tp1'] = p['tp1_price']
        if p.get('tp2_price') and not p.get('tp2'):
            p['tp2'] = p['tp2_price']
        return p

    def _load(self) -> dict:
        if os.path.exists(STATE_FILE):
            try:
                state = json.loads(open(STATE_FILE, encoding="utf-8").read())
                # ── 兼容性修复：positions可能是dict(symbol→record)或list ──
                pos = state.get("positions", [])
                if isinstance(pos, dict):
                    # brahma_state_refresh写入的是dict格式，转换为list
                    # 已平仓符号列表（不再自动补OPEN）
                    _closed_syms = {'DOGEUSDT'}  # 已手动平仓的品种
                    converted = []
                    for sym, rec in pos.items():
                        if isinstance(rec, dict):
                            if sym in _closed_syms:
                                continue  # 已平仓，跳过
                            if 'status' not in rec:
                                rec['status'] = 'OPEN'
                            converted.append(rec)
                    state["positions"] = converted
                return state
            except Exception as e:
                log.error(f"读取 brahma_state 失败: {e}")
        return {"positions": [], "nav": 0, "audit_log": []}

    def _save(self, state: dict) -> None:
        """原子写入（tmp文件 + os.replace，防止写一半）"""
        # ── 永久统一：positions必须是list格式 ──
        pos = state.get('positions', [])
        if isinstance(pos, dict):
            converted = []
            for sym, rec in pos.items():
                if isinstance(rec, dict):
                    if 'status' not in rec:
                        rec['status'] = 'OPEN'
                    converted.append(rec)
            state['positions'] = converted
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        # 旁路写 treasury_gate_log.jsonl（可查审批历史）
        try:
            log_path = os.path.join(os.path.dirname(STATE_FILE), 'treasury_gate_log.jsonl')
            recent = state.get('audit_log', [])[-200:]
            with open(log_path, 'w', encoding='utf-8') as lf:
                for entry in recent:
                    lf.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            pass


# ── 单例 ─────────────────────────────────────────────────────────
_gate: Optional[TreasuryGate] = None


def get_treasury() -> TreasuryGate:
    """全局单例入口，所有系统统一调用此函数获取国库官"""
    global _gate
    if _gate is None:
        _gate = TreasuryGate()
    return _gate
