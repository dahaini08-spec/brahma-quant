# brahma_brain package — v24.0 模块化架构
# 
# 架构层级：
#   brahma_orchestrator.py   → 编排器（入口，未来slim到<100行）
#   brahma_core.py           → 核心分析引擎（原brahma_brain.py，待逐步拆分）
#   modules/s20_tardis.py    → Tardis清算墙评分（独立模块）
#   modules/s22_gex.py       → GEX Gamma评分（独立模块）
#   modules/signal_gates.py  → 信号门控集中管理（独立模块）
#   structure_quality_engine → SMC结构质量评分
#   multi_timeframe_router   → 多周期自顶向下路由
#
# 命名说明：brahma_brain.py已重命名为brahma_core.py（消除包名/文件名冲突）

from brahma_brain.brahma_core import (
    analyze,
    confluence_score,
    calc_trade_params,
    format_report,
)
# formatter也可直接import
from brahma_brain.formatter import format_report as format_report_v2

__all__ = ['analyze', 'confluence_score', 'calc_trade_params', 'format_report']

# 向后兼容：部分脚本仍用 from brahma_brain.brahma_brain import analyze
# 通过创建兼容符号解决
import sys as _sys
import brahma_brain.brahma_core as _core
# 注册 brahma_brain.brahma_brain 作为 brahma_core 的别名
_sys.modules['brahma_brain.brahma_brain'] = _core
