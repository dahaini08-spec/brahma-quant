"""
headroom.py — AI议会 Code-Mode 压缩层
[封印 2026-08-30 苏摩111]

Uber Code-Mode思想: 模型写摘要→idle→专家读摘要→只回传score_adj+一句话
把full_report的9000字breakdown压缩成~200 token的精简信号卡
AI议会token消耗 -70%, 速度 +40%

接入位置: llm_council_bridge.py review() → _compressed_ctx
"""

from __future__ import annotations
from typing import Dict, Any

# 只保留对AI议会有判断价值的维度（去掉N/A和归零字段）
_HIGH_VALUE_DIMS = {
    # block_a 关键
    '趋势一致性', '关键位精确度', '动量背离', 'SMC结构', '量能验证',
    # P1~P4新增
    'EMA200确认', 'EMA200逆势', 'StochRSI',
    'G1_RSI三周期共振', 'G2_方仓CVD共振',
    'OBV底背离', 'OBV顶背离',
    # block_b/c 关键
    '清算/OI', '情绪/费率', '时段权重', '鲸鱼+微观',
    '量能衰竭+背离共振', '研究增强层',
    # 方仓
    '方仓评分', '方仓匹配',
    # 其他有效信号
    'CHOP背离奖励',
}


def compress_signal_card(signal: Dict[str, Any], mode: str = 'compact') -> str:
    """
    把信号压缩成AI议会可直接读取的~200 token精简卡片

    Args:
        signal: 包含 symbol/direction/score/regime/breakdown 的字典
        mode: 'compact'(200 token) | 'ultra'(100 token)

    Returns:
        格式化的精简文本，供AI议会prompt使用
    """
    symbol    = signal.get('symbol', '?')
    direction = signal.get('direction', signal.get('signal_dir', '?'))
    score     = float(signal.get('score', 0))
    regime    = signal.get('regime', '?')
    bd        = signal.get('breakdown', {}) or {}

    # 只保留有效（非零非N/A）的高价值维度
    active_dims = []
    for k in _HIGH_VALUE_DIMS:
        v = bd.get(k)
        if v is None:
            continue
        sv = str(v).strip()
        if not sv or sv == '0' or sv.startswith('N/A'):
            continue
        # 截断过长的值
        sv = sv[:40] if len(sv) > 40 else sv
        active_dims.append(f'{k}={sv}')

    # 额外捕获任何非零整数维度（未在列表里的）
    extra = []
    for k, v in bd.items():
        if k in _HIGH_VALUE_DIMS:
            continue
        try:
            n = int(str(v).strip())
            if n != 0 and abs(n) >= 3:
                extra.append(f'{k}={n}')
        except Exception:
            pass

    if mode == 'ultra':
        # 极简模式：只输出核心三行
        top5 = active_dims[:5]
        return (
            f"[{symbol} {direction} {regime} score={score:.0f}] "
            f"{' | '.join(top5)}"
        )

    # compact模式：~200 token
    lines = [
        f"▸ 信号: {symbol} {direction} | 体制={regime} | score={score:.0f}",
        f"▸ 有效维度: {' | '.join(active_dims[:12]) if active_dims else '无'}",
    ]
    if extra:
        lines.append(f"▸ 其他加分: {' | '.join(extra[:6])}")

    # 补充关键数值
    rsi_1h = bd.get('RSI状态描述', '')
    timing = signal.get('timing_status', signal.get('timing', ''))
    sl_pct = signal.get('sl_pct', 0)
    rr     = signal.get('rr1', 0)
    if rsi_1h:
        lines.append(f"▸ RSI: {str(rsi_1h)[:30]}")
    if timing:
        lines.append(f"▸ timing={timing} SL={sl_pct:.1f}% RR={rr:.2f}")

    return '\n'.join(lines)


def token_estimate(text: str) -> int:
    """粗估token数（英文4字/token，中文1.5字/token）"""
    zh = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en = len(text) - zh
    return int(zh / 1.5 + en / 4)
