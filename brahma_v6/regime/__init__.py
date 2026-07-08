"""brahma_v6.regime — 体制识别模块"""
from brahma_v6.regime.regime_v2 import RegimeProbEngine, REGIMES, REGIME_EXECUTION_POLICY

# 兼容别名：regime_hmm_v2 → regime_v2
try:
    import brahma_v6.regime.regime_v2 as regime_hmm_v2
    regime_hmm_v2 = regime_hmm_v2
except ImportError:
    regime_hmm_v2 = None

__all__ = ['RegimeProbEngine', 'REGIMES', 'REGIME_EXECUTION_POLICY', 'regime_hmm_v2']
