"""
brahma_v6/regime/regime_v2.py — Regime概率引擎 v2.0
设计院 × 顶级评估v6.0 Phase 4 | 2026-07-08

升级点（相比 regime_scorer.py v1.0）：
  1. 全概率分布（10体制，非3类聚合）
  2. entropy（不确定性量化）
  3. transition_risk（体制切换概率）
  4. liquidity_regime（流动性体制）
  5. funding_regime（资金费体制）
  6. volatility_regime（波动率体制）
  7. 标准化输出Schema（顶级评估v6.0要求）

输出示例：
  {
    "primary_regime": "BULL_CORRECTION",
    "prob": {
      "BULL_TREND": 0.31,
      "BULL_CORRECTION": 0.39,
      "CHOP_HIGH": 0.19,
      "BEAR_EARLY": 0.11
    },
    "entropy": 0.48,
    "transition_risk": 0.34,
    "liquidity_regime": "NORMAL",
    "funding_regime": "LONG_CROWDED",
    "volatility_regime": "EXPANDING",
    "allowed_directions": ["LONG_SMALL", "SHORT_TACTICAL"],
    "leverage_multiplier": 0.55
  }
"""
from __future__ import annotations
import math
import time
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))


# ══════════════════════════════════════════════════════
#  10体制枚举（与宪法一致）
# ══════════════════════════════════════════════════════
REGIMES = [
    "BULL_TREND",
    "BULL_EARLY",
    "BULL_CORRECTION",
    "BEAR_TREND",
    "BEAR_EARLY",
    "BEAR_RECOVERY",
    "BEAR_CRASH",
    "CHOP_MID",
    "CHOP_HIGH",
    "CHOP_LOW",
]

# ── 体制转移矩阵（历史统计，行=当前，列=下一个）────────────────
# 来源：梵天铁证数据（近90天）
TRANSITION_MATRIX = {
    "BULL_TREND":     {"BULL_TREND": 0.68, "BULL_CORRECTION": 0.18, "CHOP_MID": 0.09, "BEAR_EARLY": 0.05},
    "BULL_CORRECTION":{"BULL_TREND": 0.35, "BULL_CORRECTION": 0.28, "CHOP_MID": 0.22, "BEAR_EARLY": 0.15},
    "BULL_EARLY":     {"BULL_TREND": 0.48, "BULL_EARLY": 0.32, "CHOP_MID": 0.15, "BEAR_EARLY": 0.05},
    "BEAR_TREND":     {"BEAR_TREND": 0.62, "BEAR_CRASH": 0.12, "BEAR_RECOVERY": 0.18, "CHOP_MID": 0.08},
    "BEAR_EARLY":     {"BEAR_TREND": 0.38, "BEAR_EARLY": 0.30, "CHOP_MID": 0.22, "BULL_CORRECTION": 0.10},
    "BEAR_RECOVERY":  {"BULL_TREND": 0.22, "BULL_EARLY": 0.28, "CHOP_MID": 0.30, "BEAR_TREND": 0.20},
    "BEAR_CRASH":     {"BEAR_TREND": 0.45, "BEAR_RECOVERY": 0.35, "CHOP_MID": 0.20},
    "CHOP_MID":       {"BULL_TREND": 0.25, "CHOP_MID": 0.38, "BEAR_EARLY": 0.22, "CHOP_LOW": 0.15},
    "CHOP_HIGH":      {"BULL_CORRECTION": 0.35, "CHOP_MID": 0.42, "BEAR_EARLY": 0.23},
    "CHOP_LOW":       {"BULL_EARLY": 0.30, "CHOP_MID": 0.40, "BEAR_TREND": 0.30},
}

# ── 体制×方向 → 执行策略 ─────────────────────────────────
REGIME_EXECUTION_POLICY = {
    "BULL_TREND":     {"allowed": ["LONG_FULL", "SHORT_TACTICAL"], "leverage_mult": 1.0},
    "BULL_EARLY":     {"allowed": ["LONG_FULL", "SHORT_TACTICAL"], "leverage_mult": 0.9},
    "BULL_CORRECTION":{"allowed": ["LONG_SMALL", "SHORT_TACTICAL"], "leverage_mult": 0.6},
    "BEAR_TREND":     {"allowed": ["SHORT_FULL", "LONG_BANNED"], "leverage_mult": 1.0},
    "BEAR_EARLY":     {"allowed": ["SHORT_FULL", "LONG_BANNED"], "leverage_mult": 0.85},
    "BEAR_RECOVERY":  {"allowed": ["LONG_FULL", "SHORT_BANNED"], "leverage_mult": 0.75},
    "BEAR_CRASH":     {"allowed": ["SHORT_FULL", "LONG_BANNED"], "leverage_mult": 1.2},
    "CHOP_MID":       {"allowed": ["WATCH_ONLY"], "leverage_mult": 0.4},
    "CHOP_HIGH":      {"allowed": ["WATCH_ONLY"], "leverage_mult": 0.3},
    "CHOP_LOW":       {"allowed": ["LONG_SMALL", "SHORT_SMALL"], "leverage_mult": 0.5},
}


# ══════════════════════════════════════════════════════
#  Regime概率引擎 v2.0
# ══════════════════════════════════════════════════════
class RegimeProbEngine:
    """
    基于现有 regime_scorer.py 的概率输出，
    升级为全10体制概率分布 + entropy + transition_risk。
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Dict]] = {}
        self._cache_ttl = 1800  # 30分钟缓存

    def compute(self, symbol: str, raw_regime: str = "",
                bull_prob: float = 0.0, bear_prob: float = 0.0,
                chop_prob: float = 0.0, confidence: float = 0.5,
                rsi_1h: float = 50.0, funding_rate: float = 0.0,
                volatility: float = 1.0, depth_ratio: float = 1.0) -> Dict:
        """
        从现有 regime_scorer 输出升级为完整概率分布。

        Args:
            symbol: 标的
            raw_regime: 当前确认体制（来自 RegimeStateMachine）
            bull_prob/bear_prob/chop_prob: 来自 regime_scorer.py
            confidence: 体制置信度
            rsi_1h: 1H RSI
            funding_rate: 资金费率（年化%）
            volatility: 20期波动率（%）
            depth_ratio: 盘口深度比（当前/平均）
        """
        cache_key = f"{symbol}:{raw_regime}:{bull_prob:.2f}:{bear_prob:.2f}:{funding_rate:.0f}:{volatility:.1f}:{depth_ratio:.1f}"
        if cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return cached

        # ── Step1: 3类概率 → 10体制分布 ──────────────────
        prob_dist = self._expand_to_10(
            raw_regime, bull_prob, bear_prob, chop_prob, confidence, rsi_1h
        )

        # ── Step2: entropy（不确定性）─────────────────────
        entropy = self._compute_entropy(prob_dist)

        # ── Step3: transition_risk（切换概率）─────────────
        transition_risk = self._compute_transition_risk(raw_regime, prob_dist)

        # ── Step4: 辅助体制 ───────────────────────────────
        liquidity_regime = self._classify_liquidity(depth_ratio)
        funding_regime = self._classify_funding(funding_rate)
        volatility_regime = self._classify_volatility(volatility)

        # ── Step5: 执行策略 ───────────────────────────────
        policy = REGIME_EXECUTION_POLICY.get(raw_regime, REGIME_EXECUTION_POLICY["CHOP_MID"])
        leverage_mult = policy["leverage_mult"]
        # entropy修正：高不确定性降低杠杆
        if entropy > 0.7:
            leverage_mult *= 0.7
        elif entropy > 0.5:
            leverage_mult *= 0.85
        # transition_risk修正
        if transition_risk > 0.4:
            leverage_mult *= 0.8

        result = {
            "symbol": symbol,
            "primary_regime": raw_regime,
            "prob": {k: v for k, v in sorted(prob_dist.items(), key=lambda x: -x[1]) if v > 0.01},
            "entropy": round(entropy, 3),
            "transition_risk": round(transition_risk, 3),
            "liquidity_regime": liquidity_regime,
            "funding_regime": funding_regime,
            "volatility_regime": volatility_regime,
            "allowed_directions": policy["allowed"],
            "leverage_multiplier": round(leverage_mult, 3),
            "confidence": confidence,
            "ts": time.time(),
        }

        self._cache[cache_key] = (time.time(), result)
        return result

    def _expand_to_10(
        self,
        raw_regime: str,
        bull_prob: float,
        bear_prob: float,
        chop_prob: float,
        confidence: float,
        rsi_1h: float,
    ) -> Dict[str, float]:
        """
        将3类概率（bull/bear/chop）展开为10体制分布。
        核心逻辑：当前确认体制获得最高权重，其余在同类中分配。
        """
        prob_dist = {r: 0.0 for r in REGIMES}

        # 归一化输入概率
        total = bull_prob + bear_prob + chop_prob
        if total <= 0:
            bull_prob = bear_prob = chop_prob = 1/3
        else:
            bull_prob /= total
            bear_prob /= total
            chop_prob /= total

        # 当前体制基础权重（置信度加成）
        base_boost = confidence * 0.4  # 最多+40%给当前体制

        # BULL系列分配
        bull_regimes = ["BULL_TREND", "BULL_EARLY", "BULL_CORRECTION"]
        bull_weights = {
            "BULL_TREND": 0.50, "BULL_EARLY": 0.30, "BULL_CORRECTION": 0.20
        }
        # RSI修正：RSI>65倾向BULL_TREND，45-65倾向BULL_CORRECTION
        if rsi_1h > 65:
            bull_weights["BULL_TREND"] += 0.15
            bull_weights["BULL_CORRECTION"] -= 0.10
        elif rsi_1h < 45:
            bull_weights["BULL_CORRECTION"] += 0.15
            bull_weights["BULL_TREND"] -= 0.10

        for r in bull_regimes:
            prob_dist[r] = bull_prob * bull_weights.get(r, 0.33)

        # BEAR系列分配
        bear_regimes = ["BEAR_TREND", "BEAR_EARLY", "BEAR_RECOVERY", "BEAR_CRASH"]
        bear_weights = {
            "BEAR_TREND": 0.40, "BEAR_EARLY": 0.30, "BEAR_RECOVERY": 0.25, "BEAR_CRASH": 0.05
        }
        if rsi_1h < 25:
            bear_weights["BEAR_CRASH"] += 0.10
            bear_weights["BEAR_TREND"] += 0.05
            bear_weights["BEAR_EARLY"] -= 0.15
        for r in bear_regimes:
            prob_dist[r] = bear_prob * bear_weights.get(r, 0.25)

        # CHOP系列分配
        chop_regimes = ["CHOP_MID", "CHOP_HIGH", "CHOP_LOW"]
        chop_weights = {"CHOP_MID": 0.50, "CHOP_HIGH": 0.30, "CHOP_LOW": 0.20}
        if rsi_1h > 60:
            chop_weights["CHOP_HIGH"] += 0.15
            chop_weights["CHOP_LOW"] -= 0.15
        elif rsi_1h < 40:
            chop_weights["CHOP_LOW"] += 0.15
            chop_weights["CHOP_HIGH"] -= 0.15
        for r in chop_regimes:
            prob_dist[r] = chop_prob * chop_weights.get(r, 0.33)

        # 当前体制加成
        if raw_regime in prob_dist:
            prob_dist[raw_regime] = min(prob_dist[raw_regime] + base_boost, 0.90)
            # 重新归一化
            total = sum(prob_dist.values())
            if total > 0:
                prob_dist = {k: round(v/total, 4) for k, v in prob_dist.items()}

        return prob_dist

    def _compute_entropy(self, prob_dist: Dict[str, float]) -> float:
        """
        Shannon entropy（归一化到0~1）。
        0 = 完全确定（单一体制概率=1.0）
        1 = 最大不确定（均匀分布）
        """
        n = len(prob_dist)
        if n <= 1:
            return 0.0
        max_entropy = math.log2(n)
        entropy = 0.0
        for p in prob_dist.values():
            if p > 1e-10:
                entropy -= p * math.log2(p)
        return round(entropy / max_entropy, 4)

    def _compute_transition_risk(self, current_regime: str, prob_dist: Dict[str, float]) -> float:
        """
        体制切换风险 = Σ(转移到其他体制的概率)
        基于历史转移矩阵 + 当前概率分布
        """
        transitions = TRANSITION_MATRIX.get(current_regime, {})
        if not transitions:
            return 0.5

        # 当前体制稳定概率
        stay_prob = transitions.get(current_regime, 0.3)

        # 非当前体制的概率加权
        non_current_prob = 1.0 - prob_dist.get(current_regime, 0.3)

        # 综合切换风险
        transition_risk = (1 - stay_prob) * 0.6 + non_current_prob * 0.4
        return round(min(transition_risk, 1.0), 4)

    def _classify_liquidity(self, depth_ratio: float) -> str:
        """深度比 → 流动性体制"""
        if depth_ratio >= 1.5:
            return "DEEP"
        elif depth_ratio >= 0.8:
            return "NORMAL"
        elif depth_ratio >= 0.4:
            return "THIN"
        else:
            return "ILLIQUID"

    def _classify_funding(self, funding_annual_pct: float) -> str:
        """年化资金费 → 资金体制"""
        if funding_annual_pct > 100:
            return "LONG_EXTREME"
        elif funding_annual_pct > 40:
            return "LONG_CROWDED"
        elif funding_annual_pct > 10:
            return "LONG_MILD"
        elif funding_annual_pct < -40:
            return "SHORT_EXTREME"
        elif funding_annual_pct < -10:
            return "SHORT_CROWDED"
        else:
            return "NEUTRAL"

    def _classify_volatility(self, volatility_pct: float) -> str:
        """20期波动率 → 波动率体制"""
        if volatility_pct > 5.0:
            return "EXTREME"
        elif volatility_pct > 2.5:
            return "EXPANDING"
        elif volatility_pct > 1.0:
            return "NORMAL"
        elif volatility_pct > 0.3:
            return "CONTRACTING"
        else:
            return "COMPRESSED"


# ══════════════════════════════════════════════════════
#  Regime升级适配器（接入现有brahma_core）
# ══════════════════════════════════════════════════════
class RegimeV2Adapter:
    """
    适配现有分析流程，不修改 brahma_core，
    在 run_analysis() 后注入 v2.0 Regime信息。
    """

    def __init__(self):
        self._engine = RegimeProbEngine()

    def enrich(self, analysis_result: Dict, funding_rate: float = 0.0,
               volatility: float = 1.0, depth_ratio: float = 1.0) -> Dict:
        """
        在 run_analysis() 输出上追加 regime_v2 字段。
        原有字段不变，新增 regime_v2 子对象。
        """
        regime = analysis_result.get("regime", "")
        rsi_1h = float(analysis_result.get("rsi_1h", 50) or 50)

        # 从现有分析结果提取概率（如果有）
        bull_prob = float(analysis_result.get("bull_prob", 0) or 0)
        bear_prob = float(analysis_result.get("bear_prob", 0) or 0)
        chop_prob = float(analysis_result.get("chop_prob", 0) or 0)
        confidence = float(analysis_result.get("regime_confidence", 0.5) or 0.5)

        regime_v2 = self._engine.compute(
            symbol=analysis_result.get("symbol", ""),
            raw_regime=regime,
            bull_prob=bull_prob,
            bear_prob=bear_prob,
            chop_prob=chop_prob,
            confidence=confidence,
            rsi_1h=rsi_1h,
            funding_rate=funding_rate,
            volatility=volatility,
            depth_ratio=depth_ratio,
        )
        analysis_result["regime_v2"] = regime_v2
        return analysis_result


# ── 全局单例 ────────────────────────────────────────────
_engine: Optional[RegimeProbEngine] = None
_adapter: Optional[RegimeV2Adapter] = None


def get_regime_engine() -> RegimeProbEngine:
    global _engine
    if _engine is None:
        _engine = RegimeProbEngine()
    return _engine


def get_regime_adapter() -> RegimeV2Adapter:
    global _adapter
    if _adapter is None:
        _adapter = RegimeV2Adapter()
    return _adapter


if __name__ == "__main__":
    print("=== Regime概率引擎 v2.0 自检 ===\n")

    engine = RegimeProbEngine()

    # 测试用例
    cases = [
        ("BTCUSDT", "BULL_TREND",     0.65, 0.20, 0.15, 0.82, 68.0, 18.0, 1.2, 1.1),
        ("ETHUSDT", "BEAR_RECOVERY",  0.20, 0.55, 0.25, 0.70, 32.0, 25.0, 1.5, 0.9),
        ("SOLUSDT", "CHOP_MID",       0.33, 0.33, 0.34, 0.45, 50.0, 5.0,  1.0, 1.0),
        ("BTCUSDT", "BEAR_TREND",     0.10, 0.75, 0.15, 0.88, 22.0, -30.0,3.0, 0.8),
    ]

    for sym, regime, bp, brp, cp, conf, rsi, fr, vol, depth in cases:
        result = engine.compute(sym, regime, bp, brp, cp, conf, rsi, fr, vol, depth)
        top3 = list(result["prob"].items())[:3]
        print(f"【{sym} {regime}】")
        print(f"  entropy={result['entropy']}  transition_risk={result['transition_risk']}")
        print(f"  liquidity={result['liquidity_regime']}  funding={result['funding_regime']}  vol={result['volatility_regime']}")
        print(f"  top3_prob: {top3}")
        print(f"  allowed={result['allowed_directions']}  leverage_mult={result['leverage_multiplier']}")
        print()

    print("✅ Regime v2.0 自检完成")
