#!/usr/bin/env python3
"""
intent_injector.py — 意图识别 + 规则注入器
设计院 · 防御纵深框架 Layer 0
2026-05-28

核心思想：
  消息到达 AI 推理之前，精准识别意图关键词，
  把最相关的强制规则注入到消息顶部，确保 AI
  推理时必然面对约束，而不是靠「记忆」激活。

  SOUL.md 是背景知识 → 概率激活（不可靠）
  intent_injector 注入 → 出现在推理上下文里 → 必然激活

使用方式：
  # 在 cron message 生成 / AI 推理前调用
  from guardrails.intent_injector import inject
  enriched_message = inject(original_message)
"""

import re
from typing import List, Tuple

# ═══════════════════════════════════════════════════════
# 意图规则表
# 格式：(pattern_list, rules_list, priority)
# priority 高的先注入（相关规则放最上面）
# ═══════════════════════════════════════════════════════

INTENT_RULES: List[Tuple[List[str], List[str], int]] = [

    # ─── 钉钉1 / DD1（最高优先级，历史上最多犯错）───────
    (
        ['钉钉1', 'DD1', 'dd1', '根据新浪财经'],
        [
            '🔴 【强制规则 · 钉钉1】',
            '  钉钉1格式 = 钉钉专属，物理隔离于广场。',
            '  ✅ 唯一合法路径：route("send_dd1", {"text": ...})',
            '  ❌ 绝对禁止：post_to_square() / auto_poster / 任何广场API',
            '  ❌ 绝对禁止：把含「根据新浪财经公开数据」的内容发往广场',
            '  违反此规则 → action_router 硬拦截，同时写入 error_registry',
        ],
        100,
    ),

    # ─── 发帖 / 广场相关 ──────────────────────────────
    (
        ['发帖', '广场', 'square', 'post', '发布', '新闻局'],
        [
            '🟠 【强制规则 · 发帖】',
            '  所有发帖操作必须经过 action_router.route("post_square", ...)',
            '  发帖前自动检查：① 无DD1签名 ② 无禁词 ③ 非钉钉1格式',
            '  如内容来自用户粘贴 → 先判断是否含「根据新浪财经公开数据」',
            '  含 → 走钉钉1路径，不含 → 才允许走广场路径',
        ],
        90,
    ),

    # ─── 分析 / 信号 / 看多看空 ───────────────────────
    (
        ['分析', '看多', '看空', '合约', '信号', '研判', '怎么走', '方向'],
        [
            '🔵 【强制规则 · 分析】',
            '  所有分析必须调用梵天系统：action_router.route("run_analysis", {"symbol": ...})',
            '  ❌ 禁止手写 EMA/RSI/MACD 临时脚本',
            '  ❌ 禁止手动拼接 /fapi/v1/klines 自行判断',
            '  ✅ 标准调用：python3 brahma_analyze.py ETH --brief',
        ],
        80,
    ),

    # ─── 交易 / 下单 / 开仓 ───────────────────────────
    (
        ['下单', '开仓', '平仓', '止损', '买入', '卖出', '做多', '做空', '实盘'],
        [
            '🔴 【强制规则 · 交易】',
            '  实盘交易必须经过：paper → CONFIRM门 → watcher，不可跳过',
            '  任何自动下单必须有 --dry-run 验证步骤',
            '  涉及资金操作 → 输出 CONFIRM 门等待人工确认',
        ],
        95,
    ),

    # ─── 钉钉2 / 系统告警 ─────────────────────────────
    (
        ['钉钉2', '系统告警', 'send_dd2', '通知'],
        [
            '🟡 【强制规则 · 钉钉2】',
            '  钉钉2用于系统通知/日常推送，不需要888确认',
            '  合规用语：不含「梵天/达摩院/brahma」等内部词',
            '  调用：push_hub.send_dd2(title, text)',
        ],
        70,
    ),

    # ─── 系统修改 / 升级 ──────────────────────────────
    (
        ['升级', '修改', '删除', '清理', 'rm ', 'drop', '重构'],
        [
            '⚠️ 【强制规则 · 系统修改】',
            '  修改 brahma_brain/ 任何文件前，必须先运行：',
            '  python3 brahma_analyze.py ETH --brief 确认评分未下降',
            '  ❌ 禁止以「清理幽灵模块」为由移走 brahma_brain/ 下的 .py 文件',
            '  破坏性操作（rm/删除）必须输出警告并等待确认',
        ],
        85,
    ),
]

# ═══════════════════════════════════════════════════════
# 注入函数
# ═══════════════════════════════════════════════════════

def inject(message: str, max_rules: int = 3) -> str:
    """
    识别消息意图，把最相关的强制规则注入到消息顶部。

    Args:
        message:   原始消息
        max_rules: 最多注入几条规则块（避免注入过多干扰推理）

    Returns:
        注入后的消息（如无匹配规则，返回原始消息）
    """
    matched = []

    for patterns, rules, priority in INTENT_RULES:
        for p in patterns:
            if p.lower() in message.lower():
                matched.append((priority, rules))
                break

    if not matched:
        return message

    # 按优先级排序，取最多 max_rules 条
    matched.sort(key=lambda x: -x[0])
    selected = matched[:max_rules]

    injected_blocks = []
    for _, rules in selected:
        injected_blocks.append('\n'.join(rules))

    header = (
        '╔══════════════════════════════════════════════╗\n'
        '║  ⚡ 设计院强制规则注入（自动检测，必须遵守） ║\n'
        '╚══════════════════════════════════════════════╝\n'
    )
    footer = '─' * 48 + '\n'

    injection = header + '\n\n'.join(injected_blocks) + '\n\n' + footer

    return injection + message


def detect_intents(message: str) -> List[str]:
    """
    仅检测命中哪些意图，不注入（用于日志/调试）。
    """
    hit = []
    for patterns, rules, priority in INTENT_RULES:
        for p in patterns:
            if p.lower() in message.lower():
                # 取第一行作为意图名
                hit.append(rules[0].strip())
                break
    return hit


# ═══════════════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════════════

if __name__ == '__main__':
    tests = [
        '设计院 新闻局 钉钉1格式 发帖',
        '分析 ETH 看多还是看空',
        '发帖到广场 今日行情',
        '今天天气不错',   # 无匹配
        '下单做空BTC 止损7.5万',
    ]

    for msg in tests:
        intents = detect_intents(msg)
        print(f'消息: 「{msg}」')
        print(f'  识别意图: {intents if intents else ["无匹配"]}')
        if intents:
            result = inject(msg)
            # 只显示注入头部
            lines = result.split('\n')
            print(f'  注入行数: {len(lines)} 行（原 {len(msg.split(chr(10)))} 行）')
        print()
