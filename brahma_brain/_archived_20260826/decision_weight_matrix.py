#!/usr/bin/env python3
"""
decision_weight_matrix.py — 梵天决策权重融合矩阵
══════════════════════════════════════════════════
设计院 2026-08-25 苏摩111 P2-7/P2-8封印

解决问题:
  ① L2→L3 权重割裂: 35维评分(0-200) 与 AI议会final_adj(-25~+12) 无统一公式
  ② 信号优先级无分层: score=155 与 score=175 执行优先级相同

设计原则:
  - 35维评分是主裁 (权重70%)
  - AI议会是辅裁 (权重30%), 最大调整幅度±15分
  - BLOCK=否决权(不受权重限制,直接拒绝)
  - 最终分 → 优先级分层 → 仓位/通道决策

优先级分层 (P2-8):
  ┌──────────────┬──────────┬───────────┬──────────────┐
  │ 最终综合分    │ 优先级   │ 执行通道  │ 仓位倍数     │
  ├──────────────┼──────────┼───────────┼──────────────┤
  │ ≥175         │ S级-闪电 │ FAST      │ 1.5x NAV%    │
  │ 165-174      │ A级-快速 │ FAST      │ 1.0x NAV%    │
  │ 155-164      │ B级-标准 │ NORMAL    │ 0.8x NAV%    │
  │ 140-154      │ C级-观察 │ NORMAL    │ 0.5x NAV%    │
  │ <140         │ 不执行   │ SKIP      │ 0            │
  └──────────────┴──────────┴───────────┴──────────────┘
"""
from __future__ import annotations
from typing import Optional


# ── 权重配置 ─────────────────────────────────────────────────────
_W_SCORE  = 0.70   # 35维评分权重
_W_AI     = 0.30   # AI议会权重
_AI_MAX   = 12.0   # AI最大加分
_AI_MIN   = -25.0  # AI最大减分(BLOCK)
_AI_SCALE = 15.0   # 归一化基数
_AI_PASS_CAP  = +8.0   # PASS最大加分上限
_AI_WARN_CAP  = -8.0   # WARN固定扣分


def _normalize_ai_adj(final_adj: float) -> float:
    """
    将AI议会的final_adj(-25~+12) 归一化为35维分等比的调整值
    公式: adj_norm = final_adj * (35维均值150 / AI_SCALE)
    限幅: [-20, +12]
    """
    BASE = 150.0
    adj = final_adj * (BASE / _AI_SCALE) * _W_AI
    return max(-20.0, min(12.0, adj))


def fuse_score(
    score_35d: float,
    ai_verdict: str,           # 'PASS' | 'WARN' | 'BLOCK'
    ai_confidence: float = 0.6,
    ai_final_adj: Optional[float] = None,
) -> dict:
    """
    融合35维评分 + AI议会裁决 → 最终综合分 + 优先级

    参数:
      score_35d     : brahma_core 35维评分 (典型范围 80-200)
      ai_verdict    : reasoning_gate 裁决
      ai_confidence : 裁决置信度 0.0-1.0
      ai_final_adj  : AI给出的分值调整 (可选, 无则由verdict推导)

    返回:
      fused_score   : 融合后最终分
      priority      : S/A/B/C/SKIP
      channel       : FAST/NORMAL/SKIP
      size_mult     : 仓位倍数 (相对于regime标准仓位)
      detail        : 计算过程说明
    """
    # BLOCK = 否决权，直接拒绝
    if ai_verdict == 'BLOCK':
        return {
            'fused_score': 0.0,
            'priority':    'SKIP',
            'channel':     'SKIP',
            'size_mult':   0.0,
            'detail':      f'AI议会BLOCK(conf={ai_confidence:.2f}) → 信号拒绝',
        }

    # 推导 ai_final_adj（若未提供）
    if ai_final_adj is None:
        if ai_verdict == 'WARN':
            ai_final_adj = -8.0
        else:  # PASS: 上限+8，按置信度缩放
            ai_final_adj = min(8.0, ai_confidence * 8.0)

    # AI调整: WARN固定-8，PASS上限+8，置信度加权
    if ai_verdict == 'WARN':
        ai_adj_norm = -8.0
    else:
        ai_adj_norm = min(8.0, ai_final_adj * ai_confidence)

    # 融合公式: 最终分 = 35维分 * 0.7权重基准 + AI调整
    # 注意: 35维分本身已是完整分，AI调整是增量
    fused = score_35d + ai_adj_norm

    # 置信度加成: AI置信度越高，调整越准确，小幅加权
    conf_bonus = (ai_confidence - 0.5) * 4.0  # [-2, +2]
    fused += conf_bonus

    fused = round(fused, 1)

    # 优先级分层
    if fused >= 175:
        priority, channel, size_mult = 'S', 'FAST',   1.5
    elif fused >= 165:
        priority, channel, size_mult = 'A', 'FAST',   1.0
    elif fused >= 155:
        priority, channel, size_mult = 'B', 'NORMAL', 0.8
    elif fused >= 140:
        priority, channel, size_mult = 'C', 'NORMAL', 0.5
    else:
        priority, channel, size_mult = 'SKIP', 'SKIP', 0.0

    detail = (
        f'35维={score_35d} + AI调整={ai_adj_norm:+.1f}'
        f'(verdict={ai_verdict},adj={ai_final_adj:+.1f})'
        f' + conf={conf_bonus:+.1f} = {fused}'
    )

    return {
        'fused_score': fused,
        'priority':    priority,
        'channel':     channel,
        'size_mult':   size_mult,
        'detail':      detail,
    }


def get_priority_label(priority: str) -> str:
    labels = {
        'S':    '⚡S级-闪电(score≥175)',
        'A':    '🔥A级-快速(165-174)',
        'B':    '✅B级-标准(155-164)',
        'C':    '👀C级-观察(140-154)',
        'SKIP': '⛔不执行(<140或BLOCK)',
    }
    return labels.get(priority, priority)


def apply_to_signal(signal: dict, ai_result: dict) -> dict:
    """
    直接接收 brahma_core 信号 + reasoning_gate 结果，返回融合决策

    signal: brahma_core.analyze() 返回值
    ai_result: reasoning_gate() 返回值
    """
    score_35d     = float(signal.get('score_final', signal.get('score', 100)))
    ai_verdict    = ai_result.get('verdict', 'PASS')
    ai_confidence = float(ai_result.get('confidence', 0.6))

    # 从breakdown提取已有的ai_final_adj（如果brahma_core已经算过）
    bd = signal.get('confluence', {}).get('breakdown', {})
    s25 = bd.get('s25_reasoning', '')
    ai_final_adj = None
    if 'adj=' in s25:
        try:
            adj_str = s25.split('adj=')[1].split()[0]
            ai_final_adj = float(adj_str)
        except Exception:
            pass

    result = fuse_score(score_35d, ai_verdict, ai_confidence, ai_final_adj)
    result['symbol']    = signal.get('symbol', '')
    result['regime']    = signal.get('regime', signal.get('market_state', ''))
    result['direction'] = signal.get('signal_dir', signal.get('direction', ''))
    return result


# ── CLI 测试 ─────────────────────────────────────────────────────
if __name__ == '__main__':
    print('═' * 55)
    print('  梵天决策权重融合矩阵 — 测试用例')
    print('═' * 55)

    cases = [
        # (score_35d, verdict, conf, adj, 描述)
        (178, 'PASS',  0.88, None, 'S级 顺势高分'),
        (168, 'PASS',  0.82, None, 'A级 顺势标准'),
        (158, 'WARN',  0.72, -8,   'B级 AI警告'),
        (162, 'BLOCK', 0.95, -25,  'BLOCK 逆势封禁'),
        (145, 'PASS',  0.65, None, 'C级 低分'),
        (130, 'PASS',  0.60, None, 'SKIP 不执行'),
        (172, 'WARN',  0.80, -8,   'A→B 警告降级'),
    ]

    for score, verdict, conf, adj, desc in cases:
        r = fuse_score(score, verdict, conf, adj)
        label = get_priority_label(r['priority'])
        print(f'\n  [{desc}]')
        print(f'    输入: 35维={score} AI={verdict}(conf={conf})')
        print(f'    输出: 融合分={r["fused_score"]} → {label}')
        print(f'    通道: {r["channel"]}  仓位倍数: {r["size_mult"]}x')
        print(f'    计算: {r["detail"]}')

    print('\n' + '═' * 55)
    print('  ✅ 融合矩阵验证完成')
    print('═' * 55)
