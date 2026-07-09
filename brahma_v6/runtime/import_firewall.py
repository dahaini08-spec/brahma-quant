"""
brahma_v6/runtime/import_firewall.py
LowMem Import Firewall — 封印重依赖，防止 live host 误 import
裁决封印: 2026-07-09
"""
import os
import sys

LOWMEM = os.getenv("BRAHMA_LOWMEM", "false").lower() == "true"

HEAVY_MODULES = {
    "torch", "tensorflow", "keras",
    "sklearn", "scikit_learn",
    "statsmodels",
    "lightgbm", "xgboost", "catboost",
    "numba",
    "pyarrow", "polars",
    "matplotlib", "seaborn", "plotly",
    "huggingface_hub", "transformers",
    "scipy",
    "hmmlearn",
    "vectorbt",
    "ta",
}


class LowMemImportBlocker:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".")[0].replace("-", "_")
        if LOWMEM and root in HEAVY_MODULES:
            raise ImportError(
                f"[ImportFirewall] '{fullname}' is blocked in BRAHMA_LOWMEM mode. "
                f"This module is Research-Host only."
            )
        return None


def install_import_firewall():
    """安装导入防火墙。在 main 入口最前面调用。"""
    if LOWMEM:
        sys.meta_path.insert(0, LowMemImportBlocker())


def is_lowmem() -> bool:
    return LOWMEM
