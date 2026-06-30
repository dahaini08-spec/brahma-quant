#!/usr/bin/env python3
"""
新闻局品牌声音规范 v1.0  — #36
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
品牌定位：专业量化交易员 × 系统思维 × 数据说话
目标读者：有一定交易经验，被市场割过，想找系统方法的加密玩家

【三账户人设】
主账户(A)  散户共鸣型 — "我也被割过，系统帮我找到出路"
           语气：第一人称，有温度，有失败经历，有方法论
           禁止：过于专业的术语，冷漠的数字堆砌

账户2(B)   机构视角型 — "数据告诉我大户在做什么"
           语气：数据驱动，信息不对称，冷静分析
           禁止：情绪化表达，主观臆断

账户3(C)   新手友好型 — "这个信号是什么意思，我该怎么做"
           语气：教学风格，解释术语，简单明了
           禁止：假设读者懂技术分析

【统一风格规则】
✅ 第一句话：必须有冲突感/数字/判断（钩子）
✅ 核心数字：价格/止损/目标，绝对不改
✅ 结尾：⚠️ 仅供参考 模拟复盘（统一）
✅ 字数：150-350字（主账户），100-220字（新手版）
❌ 禁止："大家好""分享一个""盯了很久"
❌ 禁止：吹嘘胜率，保证盈利
❌ 禁止：体制代码（BEAR_TREND等）直接出现在帖子正文
"""

BRAND_RULES = {
    'A_RETAIL': {
        'persona': '被市场割过但找到系统方法的交易员',
        'tone': '第一人称，有温度，方法论导向',
        'char_range': (150, 350),
        'forbidden': ['大家好', '分享一个思路', '盯了', 'BEAR_TREND', 'CHOP_MID'],
    },
    'B_INSTITUTION': {
        'persona': '数据驱动的量化分析师',
        'tone': '客观冷静，信息密度高，强调数据',
        'char_range': (150, 350),
        'forbidden': ['我感觉', '可能会', '大家好', 'BEAR_TREND'],
    },
    'C_NEWBIE': {
        'persona': '交易导师，专注帮新手理解信号',
        'tone': '教学风，解释术语，步骤清晰',
        'char_range': (100, 220),
        'forbidden': ['OB结构', 'confluence', 'ATR', 'EMA', 'BEAR_TREND'],
    },
}

def check_brand_voice(content: str, account: str = 'A_RETAIL') -> tuple:
    """检查内容是否符合品牌声音规范，返回(ok, violations)"""
    rules = BRAND_RULES.get(account, BRAND_RULES['A_RETAIL'])
    violations = []
    lo, hi = rules['char_range']
    if len(content) < lo:
        violations.append(f'字数{len(content)}<{lo}')
    if len(content) > hi:
        violations.append(f'字数{len(content)}>{hi}')
    for w in rules['forbidden']:
        if w in content:
            violations.append(f'禁用词: {w}')
    return len(violations) == 0, violations

if __name__ == '__main__':
    print('品牌声音规范加载完毕')
    for acc, r in BRAND_RULES.items():
        print(f"  {acc}: {r['persona']}")

VALUE_ANCHOR = "帮被市场割过的交易者，在反弹时找到有入场区/止损/目标的机会，并告诉你信号可能错在哪里。"
