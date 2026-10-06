#!/usr/bin/env python3
"""
multi_voice_rewriter.py — 三账号差异化改写引擎 v3.0
[2026-10-03 苏摩111 v3升级]

核心思路：不是改写原文，而是「同一个市场洞察，三种人格自述」
  姓赵不宣：原文不动（苏摩已认可质量）
  蓝桉：提取核心结论，用高频交易员自己的话重说一遍
  牛来PRO：提取核心逻辑，用宏观教育视角讲清楚背后为什么

禁忌：
  - 蓝桉/牛来PRO 不得出现：梵天 姓赵不宣 设计院
  - 不得是纯数据堆砌
  - 不得有AI腔（根据以上分析/综合来看/建议投资者）
"""
import re, random, time, sys
from typing import Optional
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent.parent))

# [封印 2026-10-06 苏摩111] Chutes LLM改写增强：用DeepSeek-V3.2生成人性化内容
def _llm_rewrite(original: str, persona: str, max_tokens: int = 250) -> str:
    """调用Chutes LLM生成差异化内容，失败则返回空字符串走模板兜底"""
    try:
        from free_llm_client import chat as _fc
        prompt = (
            f'你是{persona}。\n'
            f'根据以下原帖内容，用你自己的口吻改写成一条新帖（不是翻译，是用你的人格重新叙述）：\n\n'
            f'{original[:800]}\n\n'
            f'要求：150-300字，有观点，有数字，结尾不加品牌签名，不得出现"梵天""姓赵不宣""设计院"'
        )
        result = _fc(prompt, task='vip', max_tokens=max_tokens)
        return result.strip() if result and len(result) > 50 else ''
    except Exception:
        return ''

_KEY1_FORBIDDEN = ['梵天', '姓赵不宣', '设计院', '量化系统', 'brahma']
_KEY2_FORBIDDEN = ['梵天', '姓赵不宣', '设计院', '量化系统', 'brahma', '蓝桉', '释怀鸟']

# [2026-10-03 苏摩111] 三账号统一签名尾区：邀请码+折扣+仅供参考+$BTC $ETH
_SHARED_TAIL = (
    '关注我，每晚21:00直播+SMC教学\n'
    '注册享20%手续费折扣 🔗 www.bsmkweb.cc/register?ref=XZBX666\n'
    '{brand_line}\n'
    '$BTC $ETH #BTC #ETH #合约交易 #永续合约'
)

_VOICE = {
    0: {'name': '姓赵不宣',     'suffix': '🌿 姓赵不宣 | 仅供参考',         'forbidden': []},
    1: {'name': '蓝桉VS释怀鸟', 'suffix': '💙 蓝桉VS释怀鸟 | 仅供参考',     'forbidden': _KEY1_FORBIDDEN},
    2: {'name': '牛来PRO',      'suffix': '🐂 牛来PRO | 仅供参考',           'forbidden': _KEY2_FORBIDDEN},
}


def _clean_ip(text: str, forbidden: list) -> str:
    for w in forbidden:
        text = text.replace(w+'系统', '我的系统').replace(w+'分析', '分析').replace(w, '')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _get_tags(text: str) -> str:
    tags = re.findall(r'#\S+', text)
    return ' '.join(tags[:3]) if tags else ''


def _extract_conclusion(text: str) -> str:
    """提取原帖的核心结论（最后一个有观点的句子）"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in reversed(lines):
        if (len(l) > 8 and
            not l.startswith('#') and not l.startswith('🌿') and
            not l.startswith('关注') and not l.startswith('━')):
            return l
    return ''


def _extract_key_insight(text: str) -> str:
    """提取最有价值的洞察句（通常是'为什么'）"""
    insight_kw = ['这不是', '背后', '因为', '说明', '意味着', '其实', '真相',
                  '剧本', '逻辑', '规律', '聪明钱', '主力', '散户']
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in lines:
        if any(k in l for k in insight_kw) and len(l) > 10:
            if not l.startswith('#') and not l.startswith('🌿'):
                return l
    return ''


def _extract_price_action(text: str) -> str:
    """提取操作建议句"""
    action_kw = ['不会追', '不追', '等', '挂', '空单', '多单', '出的机会', '入场', '止损']
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in lines:
        if any(k in l for k in action_kw) and len(l) > 6:
            if not l.startswith('#') and not l.startswith('🌿'):
                return l
    return ''


def _detect_topic(text: str) -> dict:
    """分析帖子主题，返回结构化信息"""
    coins = [c for c in ['BTC','ETH','SOL','BNB','SAND','MANA','GALA','AXS','XRP'] if c in text]
    
    topic = 'general'
    if any(k in text for k in ['板块轮动', '游戏板块', 'NFT', '元宇宙']):
        topic = 'rotation'
    elif any(k in text for k in ['暴涨', '涨了', '%']):
        topic = 'pump'
    elif any(k in text for k in ['CHOP', '震荡', '横盘']):
        topic = 'chop'
    elif any(k in text for k in ['BEAR', '判空', '偏空', '做空']):
        topic = 'short'
    elif any(k in text for k in ['BULL', '判多', '偏多', '做多']):
        topic = 'long'
    
    # 提取核心数字（不超过3个，避免数据堆砌）
    prices = re.findall(r'\$[\d,]+(?:\.\d+)?', text)[:3]
    pcts   = re.findall(r'[-+]?\d+\.?\d*%', text)[:3]
    
    return {
        'topic': topic,
        'coins': coins[:3],
        'prices': prices,
        'pcts': pcts,
        'conclusion': _extract_conclusion(text),
        'insight': _extract_key_insight(text),
        'action': _extract_price_action(text),
        'tags': _get_tags(text),
    }


# ── 蓝桉开场模板（按主题） ─────────────────────────────────
_LANHUI_OPENERS = {
    'rotation': [
        "板块轮动来了，但这不是追的信号。",
        "大盘跌，板块涨——这种组合我见过很多次，结局基本一样。",
        "热点来得快，去得更快。追热点的人，往往是最后一批买家。",
    ],
    'pump': [
        "暴涨之后不是机会，是陷阱。",
        "看到大涨数字心动？先问：谁在卖给你。",
        "这种行情，参与者分两种：提前布好局的，和追进去接盘的。",
    ],
    'short': [
        "今天数据给的很清楚，空头占优。",
        "量能没跟上，这波反弹我不信。",
        "大户在减仓，散户还在加——这种分歧历来是空头信号。",
    ],
    'long': [
        "量能开始放大，这是我在等的信号。",
        "支撑位企稳，可以开始布局了。",
        "今天的回调，是昨天没上车的人第二次机会。",
    ],
    'chop': [
        "今天两边都有陷阱，不好做。",
        "量能萎缩，没有方向——最贵的操作是追。",
        "结构不清晰，等端点触发，别在中间猜方向。",
    ],
    'general': [
        "今天的行情说一件事：别追。",
        "市场永远在教同一堂课，只是每次换个标的。",
        "数据说话，情绪靠边。",
    ],
}

# ── 牛来PRO开场模板（按主题） ────────────────────────────────
_NIULAI_OPENERS = {
    'rotation': [
        "板块轮动是市场中最常见的资金游戏，也是最容易被散户误判的信号。",
        "大盘下跌时某个板块突然拉升，这不是机会，这是转移注意力的手法。",
        "热点板块的背后，通常是主力资金的搬家，不是行情的启动。",
    ],
    'pump': [
        "市场总是在最多人追高的时候见顶。今天就是一个教科书级别的案例。",
        "暴涨不是入场信号，是离场信号。这个逻辑很多人懂，但真到了面前还是会追。",
        "看到大涨的第一反应不应该是「怎么买」，而应该是「谁在卖」。",
    ],
    'short': [
        "市场总是在最多人看多的时候开始下跌。今天就是一个典型案例。",
        "每一次下跌前，都有一段「看起来很安全」的时间窗口。",
        "聪明钱不追涨，它等结构。今天来看它在等什么位置。",
    ],
    'long': [
        "每一次真正的底部，都是在绝望中形成的。",
        "市场给了一个经典的回踩机会，问题是你有没有在等它。",
        "支撑结构确认以后，风险收益比开始向多头倾斜。",
    ],
    'chop': [
        "震荡市最考验的不是判断力，是自律。",
        "大多数人亏钱不是因为方向错了，而是在等待中忍不住频繁操作。",
        "没有清晰方向的时候，等待本身就是最好的交易。",
    ],
    'general': [
        "市场有自己的逻辑，大多数时候我们能做的只有顺应，不是对抗。",
        "每一次「这次不一样」的感觉，往往是最危险的时刻。",
        "好的交易员不预测市场，他们等待市场给出确认之后再行动。",
    ],
}

# ── 教育性收尾（牛来PRO） ────────────────────────────────────
_EDU_CLOSERS = [
    "交易本质上是概率游戏。赢的人不是猜得准，是在高概率时下注，低概率时观望。",
    "好的交易员不预测市场，他们等市场给出确认信号后再行动。这一点做到，胜率自然提升。",
    "学会识别「等待信号」和「追单冲动」的区别，这是从亏钱到盈利最关键的转变。",
    "风险管理比入场时机更重要。止损不是失败，是保留继续参与的资格。",
    "大多数人失败在「多做了一笔不该做的单」，而不是「少做了一笔该做的单」。",
]


def _build_lanhui(info: dict, original: str) -> str:
    """蓝桉版：高频交易员，有自己的独立分析，不是原帖的简单转述
    [封印 2026-10-06] 优先Chutes LLM生成，失败走模板兜底
    """
    # 优先LLM
    llm_result = _llm_rewrite(original, '蓝桉VS释怀鸟：数据驱动的高频交易员，机构视角，冷静分析大户行为')
    if llm_result:
        return llm_result
    topic = info['topic']
    coins = info['coins']
    coin_str = '+'.join(coins[:2]) if coins else '行情'
    
    opener = random.choice(_LANHUI_OPENERS.get(topic, _LANHUI_OPENERS['general']))
    insight = info['insight']
    action  = info['action']
    conclusion = info['conclusion']
    
    parts = [opener, '']
    
    # 核心洞察（用高频语气表述）
    if insight:
        hft_insight = (insight
            .replace('主力资金', '大资金')
            .replace('散户没有这种协调性', '散户做不到这种配合')
            .replace('这是板块轮动的经典剧本', '板块轮动，我见过太多次了')
            .replace('主力在等', 'OI数据显示主力没动')
        )
        parts.append(hft_insight)
    
    # 操作结论
    if action:
        parts += ['', f'我的判断：{action}']
    elif conclusion:
        parts += ['', conclusion.replace('今天正确的交易，昨天就该准备好了',
                                          '提前布好局，等市场来找你。')]
    
    # 结尾
    parts += ['', random.choice([
        f'关注我，每天{coin_str}实盘计划，不放空炮。',
        f'跟着做高频，细节决定成败。关注不迷路。',
        f'涨跌都有策略，关注我一起做。',
    ])]
    
    # [2026-10-03] 统一签名区：邀请码+折扣+仅供参考+$BTC $ETH
    tail = _SHARED_TAIL.format(brand_line='💙 蓝桉VS释怀鸟 | 仅供参考')
    # 互动钩子
    hook = random.choice([
        '你现在是持仓等突破，还是在场外观望？评论说说',
        '这个位置你跟还是等结构确认？评论 A（跟）B（等）',
        '你觉得下一个方向是向上还是向下？评论投票',
    ])
    parts += ['', tail, '', hook]
    return '\n'.join(parts)


def _build_niulai(info: dict, original: str) -> str:
    """牛来PRO版：宏观视角，讲清楚背后逻辑，有教育价值"""
    # 优先LLM
    llm_result = _llm_rewrite(original, '牛来PRO：新手友好型教学导师，解释技术信号背后的逻辑，语言简单明了')
    if llm_result:
        return llm_result
    topic = info['topic']
    coins = info['coins']
    coin_str = '/'.join(coins[:2]) if coins else '加密市场'
    
    opener = random.choice(_NIULAI_OPENERS.get(topic, _NIULAI_OPENERS['general']))
    insight = info['insight']
    action  = info['action']
    
    parts = [opener, '']
    
    # 核心逻辑展开（宏观教育视角）
    if insight:
        macro_insight = (insight
            .replace('主力资金', '机构资金')
            .replace('散户没有这种协调性', '这种规模的协调需要机构级别的资金运作')
            .replace('板块轮动的经典剧本', '板块轮动的经典机制')
            .replace('主力在等', '聪明钱选择观望')
        )
        parts.append(macro_insight)
        parts.append('')
    
    # 从宏观角度给出判断
    if action:
        macro_action = (action
            .replace('我不会追', '这个位置追进去的风险远大于收益')
            .replace('不在这里追', '在高位追单是散户最常见的亏损来源')
        )
        parts.append(f'从风险收益角度看：{macro_action}')
        parts.append('')
    
    # 教育性收尾
    parts.append(random.choice(_EDU_CLOSERS))
    parts.append('')
    
    parts.append(random.choice([
        f'关注我，每天一篇{coin_str}深度解读，帮你看懂市场逻辑。',
        f'学会看宏观，比追热点重要得多。关注我，一起成长。',
        f'关注我，{coin_str}交易逻辑从入门到进阶全覆盖。',
    ]))
    
    # [2026-10-03] 统一签名区：邀请码+折扣+仅供参考+$BTC $ETH
    tail = _SHARED_TAIL.format(brand_line='🐂 牛来PRO | 仅供参考')
    hook = random.choice([
        '你犯过同样的错误吗？评论告诉我你当时怎么想的',
        '这个市场逻辑你认同吗？评论 A（认同）B（不认同）说理由',
        '宏观这么看，你的实盘怎么做的？评论分享',
    ])
    parts += ['', tail, '', hook]
    return '\n'.join(parts)


def rewrite_for_account(original: str, key_idx: int,
                        original_key_idx: int = 0) -> Optional[str]:
    if key_idx == original_key_idx:
        return None
    
    info = _detect_topic(original)
    
    if key_idx == 1:
        text = _build_lanhui(info, original)
    elif key_idx == 2:
        text = _build_niulai(info, original)
    else:
        return original
    
    text = _clean_ip(text, _VOICE[key_idx]['forbidden'])
    return text.strip()


def generate_three_versions(original: str) -> dict:
    versions = {0: original}
    for idx in [1, 2]:
        v = rewrite_for_account(original, idx)
        if v:
            versions[idx] = v
    return versions


def post_three_versions(original: str, post_fn, delay_seconds: int = 300) -> dict:
    versions = generate_three_versions(original)
    results = {}
    for idx, content in versions.items():
        try:
            result = post_fn(content, idx)
            results[idx] = result
            link = result.get('data', {}).get('shareLink', '')
            print(f'[multi_voice] {_VOICE[idx]["name"]} ✅ {link}', flush=True)
        except Exception as e:
            results[idx] = {'error': str(e)}
            print(f'[multi_voice] KEY{idx} 失败: {e}', flush=True)
        if idx < max(versions.keys()):
            time.sleep(delay_seconds)
    return results
