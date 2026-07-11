"""
Brahma 2.0 P0-Plus — Import Smoke Test
验证核心包在全新环境下可 import，不依赖 .env 或 API Key
封印: 2026-07-11
"""


def test_import_brahma_v6():
    import brahma_v6  # noqa: F401


def test_import_dharma():
    import dharma  # noqa: F401


def test_import_guardrails():
    import guardrails  # noqa: F401


def test_import_nerve_system():
    import nerve_system  # noqa: F401


def test_import_brahma_brain():
    import brahma_brain  # noqa: F401


def test_import_dharma_simfactory():
    import dharma_simfactory  # noqa: F401


def test_simfactory_submodules():
    from dharma_simfactory import cost_model, metrics  # noqa: F401


def test_simfactory_cost_model_basic():
    from dharma_simfactory.cost_model import get_trade_cost, apply_cost

    cost = get_trade_cost("BTCUSDT", "1h")
    assert cost > 0, "trade cost should be positive"

    net = apply_cost(0.01, "BTCUSDT", "1h")
    assert net < 0.01, "net return after cost should be less than gross"


def test_simfactory_metrics_basic():
    import pandas as pd
    from dharma_simfactory.metrics import calc_metrics

    returns = pd.Series([0.01, -0.005, 0.02, -0.01, 0.015])
    m = calc_metrics(returns)

    assert m["trades"] == 5
    assert 0.0 <= m["win_rate"] <= 1.0
    assert m["max_drawdown"] <= 0.0
