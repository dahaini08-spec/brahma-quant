#!/usr/bin/env python3
"""
zhao_bu_xuan_ip.py — 姓赵不宣 IP标准体系 v1.0
[设计院封印 2026-08-14 苏摩111]

核心定义：
  姓赵不宣 = 梵天系统对外的唯一形象
  所有发帖 = 同一个人说话，同一种风格，统一标准

三大内容支柱：
  P1 IP打造帖   — 建立认知，吸引关注（专业帖+热度帖）
  P2 币种分析帖 — 苏摩指令触发，梵天全能力支撑
  P3 其他内容帖 — 战绩/教育/市场洞察
"""

# ═══════════════════════════════════════════════════════════════════
# 核心人设定义（唯一标准）
# ═══════════════════════════════════════════════════════════════════

PERSONA = {
    'name':       '姓赵不宣',
    'tag':        '梵天系统唯一对外标志',
    'identity':   '曾被市场割过，靠系统方法找到出路的职业交易员',
    'expertise':  '合约量化 | 体制识别 | 方向判断',
    'tone':       '直接，有数据，有立场，不废话，不鸡汤',
    'forbidden':  [
        # AI/系统类
        'AI分析', 'AI模型', '多模型', '系统给出', '引擎输出',
        '35个维度', '量化体制', '梵天', '设计院', '方仓',
        # 态度类
        '大家好', '分享一下', '盯了很久', '感觉', '可能会',
        '大概', '仅供参考以上内容', '保证盈利', '稳赚',
        # 格式类（全角符号）
        '！',  # 全角感叹号，广场违规
        # 体制代码（绝不出现在正文）
        'BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
        'BEAR_RECOVERY', 'CHOP_LOW', 'CHOP_HIGH',
    ],
    'signature':  '⚠️ 仅供参考 模拟复盘',
    'hashtags': {
        'analysis':    ['#合约', '#量化交易', '#BTC', '#加密货币'],
        'tradfi':      ['#美股代币', '#合约', '#TradFi'],
        'battle':      ['#实盘', '#战绩', '#合约'],
        'education':   ['#交易方法', '#新手必看', '#量化'],
        'hotspot':     ['#热点', '#合约', '#加密货币'],
    },
}

# ═══════════════════════════════════════════════════════════════════
# 三大支柱定义
# ═══════════════════════════════════════════════════════════════════

PILLARS = {

    # ── P1：IP打造帖 ─────────────────────────────────────────────
    'P1_IP': {
        'desc': 'IP打造，建立认知，吸引关注',
        'sub_types': {

            'P1A_PROFESSIONAL': {
                'name': '专业帖',
                'desc': '展示分析深度，建立专业形象',
                'trigger': '苏摩指令 或 重大行情节点',
                'examples': [
                    '双轨对比（今日感知层 vs 全能力结论反差）',
                    '体制识别解读（用人话解释为什么不入场）',
                    '五周期共振讲解（不露体制代码）',
                ],
                'structure': [
                    '钩子：一句反直觉的判断',
                    '铺垫：表面数据 vs 深层逻辑',
                    '核心：我看到了什么数据/结论',
                    '立场：当前怎么操作（或为什么不操作）',
                    '收尾：一句有记忆点的总结',
                ],
                'char_range': (280, 480),
                'key_element': '必须有一个反直觉的观点',
            },

            'P1B_HOTSPOT': {
                'name': '热度帖',
                'desc': '借热点话题引流，触达更广受众',
                'trigger': '广场热议标的 / 今日涨跌幅榜 / 重大新闻',
                'examples': [
                    '今日涨幅榜：为什么SNDK涨了22%（我的判断）',
                    '今日跌幅榜：TUT跌了47%，要不要抄底',
                    '热议币种：广场在讨论XXX，我的立场是',
                ],
                'structure': [
                    '钩子：直接说热点标的+我的判断',
                    '背景：一句话说明为什么火（不废话）',
                    '判断：基于什么做出这个判断（1~2个关键数据）',
                    '操作：如果做，怎么做（或为什么不做）',
                    '风险：一句话说这个逻辑什么时候失效',
                ],
                'char_range': (180, 320),
                'key_element': '必须有明确立场，不两边押注',
            },

            'P1C_INSIGHT': {
                'name': '洞察帖',
                'desc': '分享独特视角，塑造思维领先感',
                'trigger': '市场出现反常现象 / 散户和数据背离 / 今日重要结论',
                'examples': [
                    'RSI=23不等于抄底，这是散户最常犯的错误',
                    '为什么77%的人在做空SNDK，反而是看多信号',
                    '看得越清楚，越知道什么时候不做',
                ],
                'structure': [
                    '钩子：一个让人想不到的判断',
                    '逻辑：为什么大多数人的直觉是错的',
                    '数据：用一个数据支撑这个判断',
                    '结论：正确的思路应该是什么',
                ],
                'char_range': (160, 300),
                'key_element': '观点要反直觉，但有数据支撑',
            },
        },
    },

    # ── P2：币种分析帖 ───────────────────────────────────────────
    'P2_ANALYSIS': {
        'desc': '苏摩指令触发，梵天全能力输出',
        'rule': '强制使用梵天全能力（brahma_1hao_analysis），禁止感知层替代',
        'sub_types': {

            'P2A_FULL_ANALYSIS': {
                'name': '完整分析帖',
                'desc': '全能力35维输出，有明确操作建议',
                'trigger': '梵天裁决=ENTER 且 grade≥80',
                'required_data': [
                    'HCME历史WR + 置信度',
                    'Kronos p_up',
                    'HAR-RV波动率状态',
                    '精确清算集群',
                    '进场区/止损/目标',
                ],
                'structure': [
                    '钩子：这个标的为什么值得关注（用数字）',
                    '多空力量：一句话定性（空头多？多头拥挤？）',
                    '历史验证：类似形态历史上怎样（翻译HCME）',
                    '操作参数：标准价位表',
                    '失效条件：什么情况止损离场',
                ],
                'char_range': (250, 450),
                'price_block': True,   # 必须有标准价位表
            },

            'P2B_SKIP_ANALYSIS': {
                'name': 'SKIP洞察帖',
                'desc': '梵天裁决=SKIP，不给操作建议，给分析洞察',
                'trigger': '梵天裁决=SKIP 或 grade<80',
                'required_data': ['HCME结论', 'Kronos方向', '为什么不入场'],
                'structure': [
                    '钩子：这个标的今天市场在讨论什么',
                    '表面数据：看起来像什么机会',
                    '深层数据：为什么实际上不是',
                    '结论：等什么条件才入场',
                ],
                'char_range': (200, 380),
                'price_block': False,  # 不给操作建议
                'key_element': '让读者理解「等待」也是一种能力',
            },

            'P2C_TRADFI': {
                'name': 'TradFi专项帖',
                'desc': 'TradFi品种分析，区分时段有效性',
                'trigger': 'COIN/SNDK/NVDA/TSLA等TradFi品种分析',
                'special_rules': [
                    'A类（NVDA/MSFT等）：必须注明「美股13:30 UTC开市后有效」',
                    'B类（COIN/MSTR）：可直接发，说明双引擎逻辑',
                    'C类（SNDK/TSLA）：全时段有效，强调个股催化逻辑',
                    '禁止在亚盘发布A类操作建议',
                ],
                'char_range': (200, 400),
            },

            'P2D_WATCH': {
                'name': '关注位帖',
                'desc': '梵天裁决=WATCH，告知读者等待什么信号',
                'trigger': '梵天score在WATCH区间，条件未成熟',
                'structure': [
                    '当前在等什么位置',
                    '触发做多/做空的具体条件',
                    '目前不入场的原因',
                ],
                'char_range': (150, 280),
            },
        },
    },

    # ── P3：其他内容 ─────────────────────────────────────────────
    'P3_OTHER': {
        'desc': '战绩/教育/市场洞察/复盘',
        'sub_types': {

            'P3A_BATTLE': {
                'name': '战绩帖',
                'desc': '实盘盈利记录，建立信任',
                'trigger': '单笔盈利≥50% 或 战绩值得分享',
                'structure': [
                    '结果先行：直接说盈利多少（不要铺垫）',
                    '入场逻辑：当时为什么进（简洁）',
                    '验证：哪些数据给了支撑',
                    '反思：这笔交易的局限在哪里（真实感）',
                    '关键价位：给读者留存',
                ],
                'char_range': (200, 380),
                'key_element': '必须真实，不夸大，要有反思',
            },

            'P3B_EDUCATION': {
                'name': '教育帖',
                'desc': '交易方法论，帮新手建立框架',
                'trigger': '定时（每周1~2次）或 苏摩指令',
                'topics': [
                    '为什么超卖不等于反弹',
                    '资金费率是什么，怎么用',
                    '止损为什么是保护而不是失败',
                    '怎么判断一个标的是趋势还是震荡',
                    '清算层是什么，为什么机构知道你的止损在哪',
                ],
                'char_range': (200, 380),
                'key_element': '简单，有画面感，不堆术语',
            },

            'P3C_MARKET_DAILY': {
                'name': '市场日报',
                'desc': '每日收盘复盘，建立固定节奏',
                'trigger': '每日 22:00 CST 自动',
                'structure': [
                    'BTC今日一句话定性',
                    '今日最值得注意的1件事',
                    '明日关注的关键价位',
                ],
                'char_range': (180, 300),
            },

            'P3D_REVIEW': {
                'name': '复盘帖',
                'desc': '深度复盘一个已完成的交易或行情',
                'trigger': '苏摩指令 或 行情有重要复盘价值',
                'key_element': '必须有「当时判断」vs「实际结果」的对比',
                'char_range': (250, 450),
            },
        },
    },
}


# ═══════════════════════════════════════════════════════════════════
# 统一格式标准
# ═══════════════════════════════════════════════════════════════════

FORMAT_STANDARD = {

    # 价位表标准格式（P2A必须使用）
    'PRICE_BLOCK': """
━━━━━━━━━━━━━━━━━━━━━━
🎯 关键价位

入场区：${entry_lo} ~ ${entry_hi}
止损：  ${sl}（-{sl_pct}%）
目标一：${tp1}（R:R={rr}）
目标二：${tp2}
━━━━━━━━━━━━━━━━━━━━━━""",

    # 分隔线（统一用这个，不用=====或-----）
    'DIVIDER': '━━━━━━━━━━━━━━━━━━━━━━',

    # 免责声明（统一结尾）
    'DISCLAIMER': '⚠️ 仅供参考 模拟复盘',

    # 字数控制
    'CHAR_LIMITS': {
        'min_all':   100,    # 所有帖子最低字数
        'max_all':   500,    # Square字数限制
        'ideal_P1':  (200, 380),
        'ideal_P2':  (250, 450),
        'ideal_P3':  (180, 380),
    },

    # 开头禁用（前5字）
    'FORBIDDEN_STARTS': [
        '大家好', '今天给大家', '分享一个',
        '最近我在', '盯了', '很多人',
    ],

    # 结尾规范
    'REQUIRED_END': '⚠️ 仅供参考 模拟复盘',
}


# ═══════════════════════════════════════════════════════════════════
# Gate-4：IP一致性检查（新增，在三道门之后）
# ═══════════════════════════════════════════════════════════════════

def gate4_ip_consistency(content: str, post_type: str) -> tuple:
    """
    Gate-4：姓赵不宣IP一致性检查
    在三道门通过后执行最后一道人设门控
    """
    errors = []

    # 检查AI/梵天相关词（扩展版）
    for word in PERSONA['forbidden']:
        if word in content:
            errors.append(f'违反IP：含「{word}」')

    # 检查开头
    first_10 = content[:10]
    for bad_start in FORMAT_STANDARD['FORBIDDEN_STARTS']:
        if content.startswith(bad_start):
            errors.append(f'开头违规：「{bad_start}」')

    # P2A完整分析帖必须有价位表
    if post_type == 'P2A_FULL_ANALYSIS':
        if '$' not in content or '止损' not in content:
            errors.append('P2A帖缺少价位表（必须含$和止损）')

    # 检查结尾（宽松匹配，有仅供参考即可）
    if '仅供参考' not in content:
        errors.append('缺少「⚠️ 仅供参考 模拟复盘」结尾')

    # TradFi A类时段警告
    if post_type == 'P2C_TRADFI':
        if '美股' not in content and '13:30' not in content:
            errors.append('TradFi帖建议注明有效时段')

    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════════════
# 帖型路由速查
# ═══════════════════════════════════════════════════════════════════

def route_post_type(scenario: str) -> dict:
    """
    根据场景快速路由到对应帖型
    场景示例：
      '苏摩分析BTC梵天裁决ENTER'  → P2A
      '苏摩分析SNDK梵天裁决SKIP'  → P2B
      '苏摩分析NVDA'              → P2C TradFi
      '广场热议TUT'               → P1B hotspot
      '战绩TUT+239%'             → P3A battle
      '今日收盘复盘'              → P3C market_daily
    """
    scenario_lower = scenario.lower()

    # P2：苏摩指令触发的币种分析
    if '梵天' in scenario or '分析' in scenario:
        if 'enter' in scenario_lower or '入场' in scenario:
            return {'pillar': 'P2', 'type': 'P2A_FULL_ANALYSIS',
                    'name': '完整分析帖', 'price_block': True}
        if 'skip' in scenario_lower or '不入场' in scenario:
            return {'pillar': 'P2', 'type': 'P2B_SKIP_ANALYSIS',
                    'name': 'SKIP洞察帖', 'price_block': False}
        if 'watch' in scenario_lower:
            return {'pillar': 'P2', 'type': 'P2D_WATCH',
                    'name': '关注位帖', 'price_block': False}
        # TradFi品种检测
        tradfi_syms = ['NVDA','MSFT','AAPL','COIN','MSTR','SNDK','TSLA','BABA',
                       'AMZN','GOOGL','META','HOOD','QQQ','SPY']
        for sym in tradfi_syms:
            if sym.lower() in scenario_lower:
                return {'pillar': 'P2', 'type': 'P2C_TRADFI',
                        'name': 'TradFi专项帖', 'price_block': True}
        return {'pillar': 'P2', 'type': 'P2A_FULL_ANALYSIS',
                'name': '完整分析帖（默认）', 'price_block': True}

    # P3A：战绩
    if '战绩' in scenario or 'pnl' in scenario_lower or '%' in scenario:
        return {'pillar': 'P3', 'type': 'P3A_BATTLE', 'name': '战绩帖'}

    # P3C：日报
    if '收盘' in scenario or '日报' in scenario or '复盘' in scenario:
        return {'pillar': 'P3', 'type': 'P3C_MARKET_DAILY', 'name': '市场日报'}

    # P1B：热点
    if '热议' in scenario or '榜' in scenario or '热点' in scenario:
        return {'pillar': 'P1', 'type': 'P1B_HOTSPOT', 'name': '热度帖'}

    # 默认P1洞察
    return {'pillar': 'P1', 'type': 'P1C_INSIGHT', 'name': '洞察帖'}


# ═══════════════════════════════════════════════════════════════════
# 四道门完整审核（三道门 + Gate-4 IP一致性）
# ═══════════════════════════════════════════════════════════════════

def run_four_gates(content: str, post_type: str) -> tuple:
    """
    完整四道门审核。
    返回 (all_ok: bool, details: dict)
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.brahma_news_bureau import run_three_gates

    three_ok, three_detail = run_three_gates(content, post_type)
    g4_ok, g4_errors = gate4_ip_consistency(content, post_type)

    all_ok = three_ok and g4_ok
    three_detail['gate4'] = {'ok': g4_ok, 'errors': g4_errors}
    return all_ok, three_detail


if __name__ == '__main__':
    # 打印完整体系摘要
    print('🔱 姓赵不宣 IP标准体系 v1.0')
    print('=' * 50)
    print(f'身份：{PERSONA["identity"]}')
    print(f'风格：{PERSONA["tone"]}')
    print()
    for pk, pv in PILLARS.items():
        print(f'【{pk}】{pv["desc"]}')
        for sk, sv in pv['sub_types'].items():
            print(f'  {sk}: {sv["name"]} — {sv["desc"]}')
    print()
    print(f'四道门：Gate1技术 | Gate2结构 | Gate3品牌 | Gate4 IP一致性')
    print(f'统一结尾：{PERSONA["signature"]}')
