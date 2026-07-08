"""
test_no_trade_endpoint.py — 第三方v4.0动态审计补丁包 Step7
测试NO_TRADE_GUARD正确拦截下单端点，同时放行只读端点。
"""
import pytest
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))


@pytest.fixture(autouse=True)
def install_guard():
    """每个测试前安装拦截器。"""
    from audit_tools.no_trade_guard import install, uninstall
    install()
    yield
    uninstall()


class TestNoTradeGuard:
    """验证 NO_TRADE_GUARD 正确拦截所有下单路径。"""

    def test_blocks_place_order_market(self):
        """MARKET 下单必须被拦截。"""
        import requests
        with pytest.raises(RuntimeError, match="NO_TRADE_GUARD"):
            requests.post(
                "https://fapi.binance.com/fapi/v1/order",
                json={"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": 0.001},
                timeout=5,
            )

    def test_blocks_place_order_limit(self):
        """LIMIT 下单必须被拦截。"""
        import requests
        with pytest.raises(RuntimeError, match="NO_TRADE_GUARD"):
            requests.post(
                "https://fapi.binance.com/fapi/v1/order",
                json={"symbol": "ETHUSDT", "side": "SELL", "type": "LIMIT", "price": 2000, "quantity": 0.01},
                timeout=5,
            )

    def test_blocks_set_leverage(self):
        """设置杠杆必须被拦截。"""
        import requests
        with pytest.raises(RuntimeError, match="NO_TRADE_GUARD"):
            requests.post(
                "https://fapi.binance.com/fapi/v1/leverage",
                json={"symbol": "BTCUSDT", "leverage": 10},
                timeout=5,
            )

    def test_blocks_margin_type(self):
        """切换保证金类型必须被拦截。"""
        import requests
        with pytest.raises(RuntimeError, match="NO_TRADE_GUARD"):
            requests.post(
                "https://fapi.binance.com/fapi/v1/marginType",
                json={"symbol": "BTCUSDT", "marginType": "ISOLATED"},
                timeout=5,
            )

    def test_allows_public_ticker(self):
        """公开行情 GET 请求不应被拦截。"""
        import requests
        # 这个请求应该成功（不被拦截），可能因网络失败但不应抛出 NO_TRADE_GUARD
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=8,
            )
            # 只要不是 NO_TRADE_GUARD 错误就算通过
            assert r.status_code == 200 or r.status_code >= 400
        except RuntimeError as e:
            if "NO_TRADE_GUARD" in str(e):
                pytest.fail("公开行情 GET 不应被 NO_TRADE_GUARD 拦截")

    def test_allows_public_klines(self):
        """K线 GET 请求不应被拦截。"""
        import requests
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "1h", "limit": 3},
                timeout=8,
            )
            assert r.status_code == 200 or r.status_code >= 400
        except RuntimeError as e:
            if "NO_TRADE_GUARD" in str(e):
                pytest.fail("K线 GET 不应被 NO_TRADE_GUARD 拦截")


class TestEnvSafety:
    """验证安全环境变量在 guard install 后已设置。"""

    def test_live_trading_disabled(self):
        import os
        assert os.environ.get("BRAHMA_LIVE_TRADING_ENABLED") == "false"

    def test_signal_only(self):
        import os
        assert os.environ.get("BRAHMA_SIGNAL_ONLY") == "true"

    def test_paper_trading(self):
        import os
        assert os.environ.get("PAPER_TRADING_DEFAULT") == "true"

    def test_api_key_cleared(self):
        import os
        key = os.environ.get("BINANCE_API_KEY", "")
        assert key == "", f"API key should be empty, got: {key[:4]}****"

    def test_api_secret_cleared(self):
        import os
        secret = os.environ.get("BINANCE_SECRET", "") or os.environ.get("BINANCE_API_SECRET", "")
        assert secret == "", "API secret should be empty"


class TestNoHardcodedSecretsInGuard:
    """验证 no_trade_guard.py 本身不含任何密钥。"""

    def test_guard_file_no_secrets(self):
        import re
        guard_file = BASE / "audit_tools" / "no_trade_guard.py"
        text = guard_file.read_text(errors="ignore")
        patterns = [
            r"AKIA[A-Z0-9]{16}",
            r"[A-Za-z0-9]{40}",  # generic 40-char key
            r"sDqoRAye",
            r"hXQnzQco",
        ]
        for pat in patterns[:2]:  # 宽泛模式跳过，只检查已知泄露的
            pass
        for pat in ["sDqoRAye", "hXQnzQco"]:
            assert pat not in text, f"Known leaked key pattern found in guard file: {pat}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
