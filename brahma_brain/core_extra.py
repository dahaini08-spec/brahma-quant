"""
core_extra.py — Step4 extra_data构建适配层（第三刀）
接入位置：brahma_brain/brahma_core.py → from brahma_brain.core_extra import build_extra_data
2026-09-07 设计院拆块封印（苏摩111）

职责：
1. 封装 _analyze_step4() 的调用，标准化出口
2. 返回 ExtraData dataclass，替代裸dict传递
3. 捕获所有外部引擎异常（CoinGlass/OI/Deribit等），ok=False不崩溃
4. 提供 .get() 兼容方法，平滑迁移旧代码
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtraData:
    """Step4 extra_data标准化容器"""
    symbol: str = ""
    price: float = 0.0
    signal_dir: str = "LONG"

    # 核心数据
    raw: dict = field(default_factory=dict)        # 原始extra_data dict（兼容旧代码）
    bd: dict = field(default_factory=dict)         # _bd（block-d数据）
    spec: dict = field(default_factory=dict)       # _spec
    sm: dict = field(default_factory=dict)         # _sm（smart money）

    # 关键字段快速访问
    fear_greed: int = 50
    onchain_score: float = 0.0
    volume_score: float = 0.0
    divergence_score: float = 0.0

    ok: bool = True
    error: str = ""
    elapsed_ms: float = 0.0

    def get(self, key: str, default: Any = None) -> Any:
        """兼容 extra_data.get() 的旧调用方式"""
        return self.raw.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def __contains__(self, key: str) -> bool:
        return key in self.raw

    @property
    def klines_1h(self) -> dict:
        return self.raw.get("_klines_1h", {})

    @property
    def k4h_closes(self) -> list:
        return self.raw.get("_k4h_closes", [])

    @property
    def coinglass(self) -> dict:
        return self.raw.get("coinglass", {})

    @property
    def macro(self) -> dict:
        return self.raw.get("macro", {})

    @property
    def liq_snap(self) -> dict:
        return self.raw.get("liq_snap", {})


def build_extra_data(
    symbol: str,
    ms: dict,
    smc: dict,
    signal_dir: str,
    price: float,
    causal_v_result: dict | None = None,
) -> ExtraData:
    """
    统一 Step4 extra_data 构建入口。
    封装 _analyze_step4()，返回 ExtraData 而非裸dict。
    所有外部引擎失败（CoinGlass 403、Deribit 超时等）均被捕获。
    """
    import time
    t0 = time.time()
    causal_v_result = causal_v_result or {}

    try:
        from brahma_brain.brahma_core_step4 import _analyze_step4
    except ImportError:
        try:
            from brahma_core_step4 import _analyze_step4
        except ImportError as e:
            return ExtraData(symbol=symbol, price=price, signal_dir=signal_dir,
                             ok=False, error=f"import_error:{e}")

    try:
        result = _analyze_step4(
            symbol=symbol,
            ms=ms,
            smc=smc,
            signal_dir=signal_dir,
            price=price,
            _causal_v_result=causal_v_result,
        )
        raw = result.get("extra_data", {})
        bd  = result.get("_bd", {})
        spec = result.get("_spec", {})
        sm   = result.get("_sm", {})

        return ExtraData(
            symbol=symbol,
            price=float(raw.get("price", price)),
            signal_dir=signal_dir,
            raw=raw,
            bd=bd,
            spec=spec,
            sm=sm,
            fear_greed=int(raw.get("fear_greed", 50) or 50),
            onchain_score=float(raw.get("onchain_score", 0) or 0),
            volume_score=float((raw.get("volume") or {}).get("score", 0)),
            divergence_score=float((raw.get("divergence") or {}).get("score", 0)),
            ok=True,
            elapsed_ms=round((time.time() - t0) * 1000, 1),
        )

    except Exception as e:
        return ExtraData(
            symbol=symbol,
            price=price,
            signal_dir=signal_dir,
            ok=False,
            error=str(e)[:200],
            elapsed_ms=round((time.time() - t0) * 1000, 1),
        )
