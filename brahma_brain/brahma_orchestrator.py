"""
brahma_orchestrator.py — 梵天分析编排器 v24.0
职责：编排。调用brahma_core.analyze()并注入模块化后处理
这是未来拆分brahma_core.analyze()的过渡层

当前：
  brahma_orchestrator.analyze() 
    → brahma_core.analyze()（原始1589行）
    → 模块化后注入（s20/s22从modules/提取）
    → [设计院 2026-06-30 全量接入] signal_selector 双向裁决

未来（武曲Paper 200条后）：
  brahma_orchestrator.analyze()
    → fetch_market_state()
    → scorer.confluence()
    → params.calculator()
    → gates.check()
    → emitter.emit()
"""
import sys, os

# 确保包路径正确
_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_dir)
for p in [_root, os.path.join(_root,'scripts'), _dir]:
    if p not in sys.path: sys.path.insert(0, p)

from brahma_brain.brahma_core import analyze as _core_analyze, format_report

def analyze(symbol: str, signal_dir: str = None, dry_run: bool = False, deep: bool = False) -> dict:
    """
    编排入口。当前透传到brahma_core.analyze()
    deep=True: 深度分析模式，跳过NEUTRAL快速退出，返回完整数据
    """
    return _core_analyze(symbol, signal_dir=signal_dir, deep=deep)


def analyze_with_selector(symbol: str) -> dict:
    """
    [设计院 2026-06-30 全量接入] 双向分析 + signal_selector 裁决
    同时跑 LONG + SHORT，由 signal_selector 择优输出最佳方向
    返回: signal_selector.select() 结果（含 chosen_dir, score, card）
    fail-safe: 异常时降级到单向 analyze()
    """
    try:
        from signal_selector import select as _sel, format_signal_card as _fmt_card
        from regime_scorer import score as _regime_score
        _long_res  = _core_analyze(symbol, signal_dir='LONG',  deep=True)
        _short_res = _core_analyze(symbol, signal_dir='SHORT', deep=True)
        _regime    = _regime_score(symbol)
        _selected  = _sel(_short_res, _long_res, _regime)
        _selected['signal_card'] = _fmt_card(_selected)
        return _selected
    except Exception as _e:
        print(f'[Orchestrator] signal_selector降级: {_e}')
        return _core_analyze(symbol, signal_dir=signal_dir, deep=deep)


__all__ = ['analyze', 'analyze_with_selector', 'format_report']
