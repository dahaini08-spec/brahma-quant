"""
core_data.py — Step1-3 市场数据接口适配层（第二刀）
接入位置：brahma_brain/brahma_core.py → from brahma_brain.core_data import fetch_market_data
2026-09-07 设计院拆块封印（苏摩111）

职责：
1. 封装 _analyze_step1/2/3 的调用，对外暴露单一函数 fetch_market_data()
2. 标准化返回的 MarketSnapshot dataclass，不再用裸dict传递
3. 捕获数据获取异常，返回 MarketSnapshot(ok=False) 而非崩溃
4. 外部（paper_engine/auto_executor）通过这层获取市场数据，不直接调用step函数
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MarketSnapshot:
    """标准化市场数据快照——替代裸dict传递"""
    symbol: str = ""
    signal_dir: str = "LONG"
    price: float = 0.0
    regime: str = "UNKNOWN"

    # Step1: 市场分析
    ms: dict = field(default_factory=dict)
    cv_adj: float = 0.0
    cv_verdict: str = ""
    causal_v_result: dict = field(default_factory=dict)

    # Step2: 方向确认
    signal_dir_final: str = "LONG"

    # Step3: SMC
    smc: dict = field(default_factory=dict)
    smc_4h: dict = field(default_factory=dict)
    mtf_result: dict = field(default_factory=dict)

    # 元信息
    ok: bool = True
    error: str = ""
    elapsed_ms: float = 0.0

    @property
    def rsi_1h(self) -> float:
        return float(self.ms.get("rsi_1h") or self.ms.get("momentum", {}).get("rsi_1h") or 50.0)

    @property
    def atr_1h(self) -> float:
        return float(self.ms.get("atr_1h") or self.ms.get("momentum", {}).get("atr_1h") or 0.0)

    @property
    def is_chop(self) -> bool:
        return "CHOP" in self.regime.upper()

    @property
    def is_bear(self) -> bool:
        return "BEAR" in self.regime.upper()


def fetch_market_data(
    symbol: str,
    signal_dir: str = None,
    deep: bool = False,
) -> MarketSnapshot:
    """
    统一市场数据获取入口。
    封装 Step1→Step2→Step3，返回标准化 MarketSnapshot。
    外部代码只调这一个函数，不直接调用 _analyze_step1/2/3。
    """
    import time
    t0 = time.time()

    try:
        from brahma_brain.brahma_core_analyze_steps import (
            _analyze_step1, _analyze_step2, _analyze_step3,
        )
    except ImportError:
        try:
            from brahma_core_analyze_steps import (
                _analyze_step1, _analyze_step2, _analyze_step3,
            )
        except ImportError as e:
            return MarketSnapshot(symbol=symbol, ok=False, error=f"import_error:{e}")

    try:
        # Step1: 市场分析
        r1 = _analyze_step1(symbol, signal_dir)
        ms = r1.get("ms", {})
        cv_adj = float(r1.get("_cv_adj", 0))
        cv_verdict = str(r1.get("_cv_verdict", ""))
        causal_v_result = r1.get("_causal_v_result", {})

        # Step2: 方向确认
        r2 = _analyze_step2(symbol, ms, signal_dir, deep)
        signal_dir_final = r2.get("signal_dir", signal_dir or "LONG")

        # Step3: SMC
        price = float(ms.get("price", 0))
        r3 = _analyze_step3(symbol, ms, signal_dir_final, price)
        smc = r3.get("smc", {})
        smc_4h = r3.get("_smc_4h", {})
        mtf_result = r3.get("_mtf_result", {})
        price = float(r3.get("price", price))

        regime = str(ms.get("regime", "UNKNOWN"))

        return MarketSnapshot(
            symbol=symbol,
            signal_dir=signal_dir or "LONG",
            signal_dir_final=signal_dir_final,
            price=price,
            regime=regime,
            ms=ms,
            cv_adj=cv_adj,
            cv_verdict=cv_verdict,
            causal_v_result=causal_v_result,
            smc=smc,
            smc_4h=smc_4h,
            mtf_result=mtf_result,
            ok=True,
            elapsed_ms=round((time.time() - t0) * 1000, 1),
        )

    except Exception as e:
        return MarketSnapshot(
            symbol=symbol,
            signal_dir=signal_dir or "LONG",
            ok=False,
            error=str(e),
            elapsed_ms=round((time.time() - t0) * 1000, 1),
        )
