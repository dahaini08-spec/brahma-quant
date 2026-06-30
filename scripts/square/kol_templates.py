#!/usr/bin/env python3
"""
新闻局 KOL 模板库 v1.0  2026-06-03
════════════════════════════════════════════════════════════════════════
SOUL.md 五条不可妥协原则：
  1. 内容第一：宁可不发，不发烂帖
  2. 结构锁死：钩子 + 研判 + 策略，顺序不可变
  3. 数字神圣：价格/止损/目标，一字不改
  4. 人工确认：每次发布前必过人眼（「发」口令）
  5. 风格统一：像同一个顶级交易员在说话

钩子标准（第一句话）：
  ✅ 有数字、有判断、有冲突感
  ✅ 例：「散户74%做多ETH，机构在悄悄卖。这不对称。」
  ❌ 禁止：「分享一个思路」「盯了该币」「大家好」

使用方式：
  from scripts.square.kol_templates import build_kol_post, TEMPLATES
  post = build_kol_post(brahma_data, template_id='BEAR_SHORT')
  # post 输出三个版本，用户选一个说「发」
"""
import random, datetime

# ─────────────────────────────────────────────────────────────────────
# Layer-2 结构验证（发帖前硬检查）
# ─────────────────────────────────────────────────────────────────────
STRUCTURE_RULES = {
    'min_chars':    80,    # 最短字数
    'max_chars':   500,    # 最长字数（广场限制）
    'must_have_price': True,
    'must_have_sl':    True,
    'must_have_tp':    True,
    'hook_min_chars':  20,  # 钩子最少20字
}

GATE_KEYWORDS = ['新浪财经', '根据新浪', 'DD1', '仅供内部']  # 禁止出现在广场帖

# ─────────────────────────────────────────────────────────────────────
# 钩子库（按体制分类）
# ─────────────────────────────────────────────────────────────────────
HOOKS = {
    'BEAR_TREND': [
        "{sym}持续下跌，散户在接刀，机构在卖出。这个不对称是机会。",
        "RSI超卖，但方向是对的——{sym}熊市里超卖可以更超卖。",
        "清算地图显示{sym}下方密集多单，一旦破位，踩踏式下跌开始。",
        "市场共识做多{sym}，但链上数据显示机构在出。与共识反向。",
        "{sym}整理结束，熊市下一浪开始。结构已确认。",
    ],
    'BEAR_EARLY': [
        "{sym}高位拒绝，换手完成，聪明钱已经在离场。",
        "OB区域+RSI背离，{sym}顶部信号成立，做空时机窗口打开。",
        "散户看到的是整理，机构看到的是分发。{sym}当前位置：分发区。",
        "{sym}在关键阻力区三次拒绝，市场在告诉你答案。",
        "成交量萎缩 + 价格滞涨 + OB区域，{sym}教科书级别的做空setup。",
    ],
    'CHOP': [
        "{sym}在区间极端顶部，94%历史WR=75%。这不是预测，是统计。",
        "区间高位，散户追多，专业交易员做空。{sym}极端溢价确认。",
        "{sym}触及区间上轨，历史上这个位置做空胜率7.5成。",
    ],
    'NEUTRAL': [
        "结构已经说话，{sym}入场点就在当前位置附近。",
        "{sym}关键位置，机构OB确认，信号触发中。",
        "不预测，只跟结构。{sym}当前setup：做空条件全部成立。",
    ],
}

# ─────────────────────────────────────────────────────────────────────
# 研判模板
# ─────────────────────────────────────────────────────────────────────
ANALYSIS_TEMPLATES = {
    'SHORT_BEAR': """\
{hook}

📊 {sym}/USDT · 做空信号
体制: {regime} | 评分: {score}/150 | 时间: {ts_cst}

核心研判：
• 熊市体制确认，趋势顺势方向
• OB结构 + {div_str} + {rsi_str}
• 量能{vol_str}，动能{vol_direction}

⚠️ 仅供参考 模拟复盘""",

    'SHORT_RANGE': """\
{hook}

📊 {sym}/USDT · 区间顶部做空
体制: {regime} | 评分: {score}/150 | 时间: {ts_cst}

核心研判：
• 区间溢价区({range_pos:.0%})，历史高WR位置
• OB阻力确认，{rsi_str}
• {vol_str}

⚠️ 仅供参考 模拟复盘""",

    'SHORT_GENERIC': """\
{hook}

📊 {sym}/USDT · 做空
体制: {regime} | 评分: {score}/150

核心研判：
• {regime_desc}
• 结构共振: OB + 动量背离
• 入场区靠近当前价，执行效率高

⚠️ 仅供参考 模拟复盘""",
}

# ─────────────────────────────────────────────────────────────────────
# 策略模板（精确数字，一字不改）
# ─────────────────────────────────────────────────────────────────────
STRATEGY_BLOCK = """\
📌 策略参考
入场区: ${entry_lo:.2f} ~ ${entry_hi:.2f}
保  护: ${sl:.2f} ({sl_pct:.2f}%)
目标一: ${tp1:.2f} (R:R {rr:.1f})
目标二: ${tp2:.2f}

#加密货币 #{sym} #做空 #量化"""


# ─────────────────────────────────────────────────────────────────────
# ICP 三账户受众定位（v2.0 升级核心）
# 账户A：散户共鸣型  — 情绪共鸣 + 我也被套过 + 系统帮我找到出路
# 账户B：机构视角型  — 数据驱动 + 大户在做什么 + 信息不对称
# 账户C：新手友好型  — 解释信号 + 告诉我该怎么做 + 简单明了
# ─────────────────────────────────────────────────────────────────────
ICP_HOOKS = {
    'A_RETAIL': {   # 散户共鸣
        'BEAR_TREND': [
            "被{sym}这波下跌割过的举手 ✋ 今天给大家一个反击的思路。",
            "上次追多{sym}被套的教训告诉我：趋势在，别接刀。",
            "{sym}跌了这么多，散户在补仓，但数据告诉我结构没变。",
        ],
        'BEAR_EARLY': [
            "{sym}高位，我没追进去——因为这个指标告诉我先等等。",
            "很多人觉得{sym}要反弹了，我理解那种心情，但数据不支持。",
        ],
        'DEFAULT': [
            "{sym}的机会出现了，这次我没有靠感觉，靠的是系统。",
            "盯{sym}好几天，终于等到这个位置。",
        ],
    },
    'B_INSTITUTION': {  # 机构视角
        'BEAR_TREND': [
            "大户多空比：多60% 空40%，散户69%做多。大户比散户更谨慎——这是信号。",
            "链上数据：{sym}交易所净流入连续3日为正，机构在卖。",
            "清算地图显示{sym}下方$X亿多单密集，机构不会让这些单子好过。",
        ],
        'BEAR_EARLY': [
            "OB区域+资金流出+RSI背离，机构分发{sym}的三重确认。",
            "持仓数据：{sym}大户减仓，散户接盘。这种结构历史上结局只有一个。",
        ],
        'DEFAULT': [
            "量化信号+链上数据+情绪指标三向共振，{sym}当前是高概率setup。",
            "F&G=12极度恐慌，但散户仍69%做多——情绪底≠价格底，还差一步。",
        ],
    },
    'C_NEWBIE': {  # 新手友好
        'BEAR_TREND': [
            "{sym}做空是什么意思？就是先卖后买，跌了赚钱。",
            "新手看这里：{sym}评分{score}/150，意思是系统认为这是强信号。",
        ],
        'BEAR_EARLY': [
            "{sym}出现了什么信号？简单说：价格涨但动力在减弱，叫背离。",
        ],
        'DEFAULT': [
            "不懂技术分析也没关系，{sym}信号说的是：在这个价格区间入场，止损放这里。",
            "{sym}信号来了，我来帮你解读：入场区/止损/目标分别是什么意思。",
        ],
    },
}


# ── 情绪堆叠型钩子（#26 情感触点）─────────────────────────────
EMOTION_HOOKS = {
    'BEAR_TREND': [
        "你有没有在{sym}上被割过？我被割过，所以才建了这套系统。",
        "看着{sym}跌，心里是什么感觉？我现在反过来做空，感觉很不一样。",
        "{sym}跌破支撑那一刻，99%散户慌了。那1%在做什么？",
        "我曾经在{sym}高位追多，亏了30%。现在系统告诉我：顺势空。",
        "这波{sym}下跌让很多人爆仓了。但熊市里也有人赚钱，方向不同而已。",
    ],
    'DEFAULT': [
        "{sym}的机会来了，不是感觉——是数据。",
        "等了好久，{sym}终于出现了一个值得入场的位置。",
    ],
}

# ── 10个标准钩子备用池（供随机扩充）──────────────────────────
EXTENDED_HOOKS_BEAR = [
    "{sym}空头格局成立，我用38个引擎确认了一遍。",
    "市场在下跌，但做空{sym}不是随便空——等这个位置。",
    "{sym}反弹就是空机会，这是当前体制给的节奏。",
    "熊市里亏钱的人有一个共同错误：在{sym}每次反弹时做多。",
    "F&G=12，但{sym}散户还69%做多。这个不对称是关键。",
    "{sym}当前位置，数据说做空，我听数据的。",
    "不需要预测{sym}要跌多少，只需要知道：现在顺势是空。",
    "做交易15年，{sym}这种形态我见过很多次，结局都一样。",
]


ICP_ANALYSIS = {
    'A_RETAIL': """\
{hook}

我用系统扫出来的 {sym}/USDT 做空机会：
体制: {regime_cn} | 评分: {score}/150 | {ts_cst}

为什么这里做空？
• {regime_desc}，趋势方向明确
• {rsi_str}，但趋势里超卖可以更超卖
• {vol_str}，动能{vol_direction}
• 置信度 {confidence}%

⚠️ 仅供参考 模拟复盘""",

    'B_INSTITUTION': """\
{hook}

{sym}/USDT 量化信号 · 机构视角
体制: {regime} | 评分: {score}/150 | {ts_cst}

数据说了什么：
• 体制共识: {regime} 趋势确认
• {rsi_str} + {vol_str}
• 置信度 {confidence}% | 反向触发条件: {bull_trigger}
• {second_order_tp}

⚠️ 仅供参考 模拟复盘""",

    'C_NEWBIE': """\
{hook}

{sym}/USDT 信号解读（新手版）
评分 {score}/150 意味着系统认为这是{'强' if score>=140 else '中等'}信号

三个关键数字：
• 入场区：这个价格段进 → ${entry_lo:.2f}~${entry_hi:.2f}
• 止损：跌破这个就离场 → ${sl:.2f}
• 目标：预期到达这里  → ${tp1:.2f} (+{rr:.1f}倍回报)

方向：{dir_cn} | 体制：{regime_cn}
⚠️ 仅供参考 模拟复盘""",
}


def build_kol_post(brahma_data: dict, n_versions: int = 3,
                  icp_mode: bool = True) -> list:
    """
    输入梵天分析结果，输出 n 个 KOL 风格帖子版本。
    用户从中选一个说「发」。

    brahma_data 字段：
      symbol, regime, score, entry_lo, entry_hi, stop_loss,
      tp1, tp2, sl_pct, rr, rsi_1h, vol_ratio, has_div,
      range_position（可选）
    """
    sym      = brahma_data.get('symbol','').replace('USDT','')
    regime   = brahma_data.get('regime', 'NEUTRAL')
    score    = brahma_data.get('score', 0)
    entry_lo = brahma_data.get('entry_lo', 0)
    entry_hi = brahma_data.get('entry_hi', 0)
    sl       = brahma_data.get('stop_loss', 0)
    tp1      = brahma_data.get('tp1', 0)
    tp2      = brahma_data.get('tp2', 0)
    sl_pct   = brahma_data.get('sl_pct', 0)
    rr       = brahma_data.get('rr', 2.5)
    rsi_v    = brahma_data.get('rsi_1h', 50)
    vol_ratio= brahma_data.get('vol_ratio', 1.0)
    has_div  = brahma_data.get('has_div', False)
    range_pos= brahma_data.get('range_position', 0.5)

    # 时间戳
    ts_cst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%m/%d %H:%M')

    # 辅助描述
    regime_key = 'BEAR_TREND' if 'BEAR' in regime else ('CHOP' if 'CHOP' in regime else 'NEUTRAL')
    div_str    = '看空背离确认' if has_div else 'RSI趋势顺势'
    rsi_str    = f'RSI={rsi_v:.0f}({"超卖区" if rsi_v<35 else ("中性" if rsi_v<60 else "超买区")})'
    vol_str    = f'量能×{vol_ratio:.1f}x({"放量" if vol_ratio>1.3 else ("缩量" if vol_ratio<0.8 else "正常")})'
    vol_dir    = '加速' if vol_ratio > 1.3 else ('减弱' if vol_ratio < 0.8 else '平稳')
    regime_desc= {'BEAR_TREND':'熊市趋势顺势做空','BEAR_EARLY':'顶部反转做空',
                  'CHOP_HIGH':'区间高位反转','NEUTRAL':'中性做空'}.get(regime, regime)

    # 策略块（数字不可变）
    strategy = STRATEGY_BLOCK.format(
        entry_lo=entry_lo, entry_hi=entry_hi,
        sl=sl, sl_pct=sl_pct, tp1=tp1, tp2=tp2,
        rr=rr, sym=sym
    )

    # 辅助字段
    confidence  = brahma_data.get('confidence', int(min(score/150*100, 95)))
    bull_case   = brahma_data.get('bull_case', {})
    bull_trigger= bull_case.get('trigger', '日线止跌K线+量能放大')[:22]
    second_order= brahma_data.get('second_order', {})
    so_tp       = second_order.get('if_tp1_hit', '触TP1后减仓50%锁定利润')[:28]
    dir_raw     = str(brahma_data.get('direction', brahma_data.get('signal_dir', 'SHORT')))
    dir_cn      = '做空' if dir_raw.upper() in ('SHORT','空','做空') else '做多'
    regime_cn   = {'BEAR_TREND':'熊市下行','BEAR_EARLY':'顶部反转',
                   'CHOP_MID':'震荡整理','CHOP_HIGH':'区间高位',
                   'BULL_TREND':'牛市上行','NEUTRAL':'中性'}.get(regime, regime)
    hook_pool   = HOOKS.get(regime_key, HOOKS['NEUTRAL'])
    template_key= ('SHORT_BEAR' if 'BEAR' in regime else
                   ('SHORT_RANGE' if 'CHOP' in regime else 'SHORT_GENERIC'))

    # ICP 三账户差异化模式（n_versions>=3时启用）
    if n_versions >= 3:
        versions = []
        icp_list = [
            ('A_RETAIL',      'BEAR_TREND' if 'BEAR' in regime else 'DEFAULT'),
            ('B_INSTITUTION', 'BEAR_TREND' if 'BEAR' in regime else 'DEFAULT'),
            ('C_NEWBIE',      'DEFAULT'),
        ]
        for icp_key, rkey in icp_list:
            hook_src = (ICP_HOOKS.get(icp_key, {}).get(rkey)
                        or ICP_HOOKS.get(icp_key, {}).get('DEFAULT', []))
            if not hook_src:
                hook_src = hook_pool
            hook = random.choice(hook_src).format(sym=sym, score=score)
            tmpl = ICP_ANALYSIS.get(icp_key, ICP_ANALYSIS['A_RETAIL'])
            try:
                analysis = tmpl.format(
                    hook=hook, sym=sym, regime=regime, score=score,
                    ts_cst=ts_cst, div_str=div_str, rsi_str=rsi_str,
                    vol_str=vol_str, vol_direction=vol_dir,
                    regime_desc=regime_desc, regime_cn=regime_cn,
                    confidence=confidence, bull_trigger=bull_trigger,
                    second_order_tp=so_tp, dir_cn=dir_cn,
                    entry_lo=entry_lo, entry_hi=entry_hi,
                    sl=sl, tp1=tp1, rr=rr, range_pos=range_pos,
                )
            except (KeyError, ValueError):
                analysis = hook + f'\n\n{sym}/USDT | 评分 {score}/150 | {ts_cst}'
            versions.append(analysis + '\n\n' + strategy)
        return versions

    # 安全回落：原有逐应模式
    random.shuffle(hook_pool)
    versions = []
    for i in range(min(n_versions, len(hook_pool))):
        hook = hook_pool[i].format(sym=sym)
        analysis = ANALYSIS_TEMPLATES[template_key].format(
            hook=hook, sym=sym, regime=regime, score=score,
            ts_cst=ts_cst, div_str=div_str, rsi_str=rsi_str,
            vol_str=vol_str, vol_direction=vol_dir,
            range_pos=range_pos, regime_desc=regime_desc,
        )
        versions.append(analysis + '\n\n' + strategy)
    return versions


def validate_post(content: str) -> tuple:
    """
    Layer-2 结构验证。
    返回 (ok: bool, errors: list)
    """
    errors = []

    # Gate-1: 禁词
    for kw in GATE_KEYWORDS:
        if kw in content:
            errors.append(f'禁词: {kw}')

    # Gate-2: 结构完整性
    if len(content) < STRUCTURE_RULES['min_chars']:
        errors.append(f'字数不足({len(content)}<{STRUCTURE_RULES["min_chars"]})')
    if len(content) > STRUCTURE_RULES['max_chars']:
        errors.append(f'字数超限({len(content)}>{STRUCTURE_RULES["max_chars"]})')
    if '$' not in content:
        errors.append('缺少价格数字（$）')
    if '入场' not in content and 'entry' not in content.lower():
        errors.append('缺少入场区')
    if not any(x in content for x in ['保护', '止损', 'SL', '保  护']):
        errors.append('缺少止损')
    if '目标' not in content and 'TP' not in content:
        errors.append('缺少目标')

    return (len(errors) == 0), errors


# ─────────────────────────────────────────────────────────────────────
# CLI 测试
# ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys, json

    # 模拟梵天输出数据
    sample = {
        'symbol': 'ETHUSDT',
        'regime': 'BEAR_TREND',
        'score': 162,
        'entry_lo': 1918.60,
        'entry_hi': 1930.99,
        'stop_loss': 1976.74,
        'tp1': 1794.94,
        'tp2': 1715.00,
        'sl_pct': 2.8,
        'rr': 2.5,
        'rsi_1h': 51.0,
        'vol_ratio': 1.4,
        'has_div': True,
        'range_position': 0.95,
    }

    if len(sys.argv) > 1:
        try:
            sample = json.loads(sys.argv[1])
        except:
            pass

    versions = build_kol_post(sample, n_versions=3)
    print(f"=== KOL 帖子预览（{len(versions)}个版本）===\n")
    for i, v in enumerate(versions, 1):
        ok, errs = validate_post(v)
        status = '✅ 通过验证' if ok else f'❌ {errs}'
        print(f"── 版本{i} {status} ({len(v)}字) ──")
        print(v)
        print()

# ══════════════════════════════════════════════════════
# 身份叙事模板 — 课程顶级思维落地 #马斯洛尊重层+归属层
# 「用系统的人 vs 靠感觉的人」身份标签强化
# ══════════════════════════════════════════════════════
IDENTITY_NARRATIVE = {
    'WIN': [
        "用系统的人赢了感觉派。数字不骗人。",
        "散户靠感觉，系统靠结构。这次结构赢了。",
        "当69%散户做多时，38个引擎在做空。结果说明一切。",
    ],
    'WAIT': [
        "系统今天不出手。这也是一种赢法——知道什么时候不该进。",
        "不追，不抢，等系统给的位置。纪律就是优势。",
        "最难的不是进场，是忍住不进场。系统今天选择观望。",
    ],
    'LOSS': [
        "止损不是失败，是纪律。下一个信号等我们。",
        "系统止损了。这一单亏损被控制在计划内——这才是重要的。",
    ],
}

# 一句话价值锚点 — 梵天的「核心价值主张」
VALUE_ANCHOR = "帮被市场割过的交易者，在反弹时找到有入场区/止损/目标的机会，并告诉你信号可能错在哪里。"

