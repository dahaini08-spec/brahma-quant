"""hunter_config.py — 猎手配置 存根 (lana/hunter_v2)
转发到根目录完整配置，保持向后兼容。
"""
import sys
from pathlib import Path
# 确保根目录在搜索路径中
_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

# 独立导入根目录的 hunter_config（避免循环）
import importlib, types
_spec = importlib.util.spec_from_file_location(
    '_hunter_config_root',
    Path(_root) / 'hunter_config.py'
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# 把所有公共符号暴露到本模块
_this = sys.modules[__name__]
for _k, _v in vars(_mod).items():
    if not _k.startswith('__'):
        setattr(_this, _k, _v)

# lana/hunter_v2 本地覆写（若有）
MIN_SCORE = 150   # [N18] 达摩院v4认证门槛 PF=1.255 WR=47% (原145)
MAX_SLOTS = 3
DRY_RUN   = False
TRACK     = 'BRAHMA'
