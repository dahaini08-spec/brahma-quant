#!/usr/bin/env python3
"""
multi_voice_rewriter.py — 三账号差异化改写引擎
[2026-10-03 苏摩111封印]

核心设计：同源异声
  同一个市场判断 → 三种声音/风格/视角
  零LLM依赖：纯规则改写，0ms延迟，0漂移风险

账号身份：
  KEY_0 姓赵不宣    → 10年老交易员，冷静犀利，量化背景
  KEY_1 蓝桉VS释怀鸟 → 高频交易者，数字直白，散户友好（禁梵天字样）
  KEY_2 牛来PRO      → 独立分析师，宏观教育，温和（禁梵天/姓赵不宣）

接入位置：
  scripts/square_auto_post.py → _post_multi_voice()
  scripts/square/square_deep_post.py → 旗舰帖三账号分发
"""
import re
import time
import random
from typing import Optional

# ── IP隔离规则 ──────────────────────────────────────────────
# KEY_1 禁止出现的词（蓝桉独立IP）
_KEY1_FORBIDDEN = ['梵天', '姓赵不宣', '设计院', '量化系统', 'brahma']
# KEY_2 禁止出现的词（牛来PRO独立IP）
_KEY2_FORBIDDEN = ['梵天', '姓赵不宣', '设计院', '量化系统', 'brahma',
                   '蓝桉', '释怀鸟']

# ── 各账号风格参数 ────────────────────────────────────────────
_VOICE_CONFIG = {
    0: {
        'name':    '姓赵不宣',
        'suffix':  '🌿 姓赵不宣 | 不是建议',
        'style':   'veteran',    # 老交易员：冷静犀利有沧桑感
        'forbidden': [],
    },
    1: {
        'name':    '蓝桉VS释怀鸟',
        'suffix':  '💙 蓝桉VS释怀鸟 | 高频视角',
        'style':   'hft',        # 高频交易：数字直白，节奏快
        'forbidden': _KEY1_FORBIDDEN,
    },
    2: {
        'name':    '牛来PRO',
        'suffix':  '🐂 牛来PRO | 独立观点',
        'style':   'macro',      # 宏观教育：温和叙事
        'forbidden': _KEY2_FORBIDDEN,
    },
}

# ── 风格词汇替换表 ────────────────────────────────────────────
# veteran（姓赵不宣）→ hft（蓝桉）→ macro（牛来PRO）
_STYLE_REPLACEMENTS = {
    # 老交易员表达 → 高频表达
    'hft': [
        ('见过太多这种行情了', '数据看过了'),
        ('这不是意外，这是剧本', '结构很清楚'),
        ('主力在等', 'OI在减'),
        ('今天正确的交易，发生在昨天', '入场点要提前规划'),
        ('记住这一次', '记录一下'),
        ('沧桑', '高频'),
        ('等待结构', '等量能确认'),
        ('做单', '挂单'),
        ('不在这里追', '当前位置性价比低'),
    ],
    # 老交易员表达 → 宏观教育表达
    'macro': [
        ('见过太多这种行情了', '这种走势在历史上出现过多次'),
        ('这不是意外，这是剧本', '市场有其自己的逻辑'),
        ('主力在等', '聪明钱在观望'),
        ('今天正确的交易，发生在昨天', '好的机会需要提前布局'),
        ('记住这一次', '这是一个值得学习的案例'),
        ('不追', '保持耐心'),
        ('做单', '参与'),
        ('追空', '做空'),
        ('追多', '做多'),
    ],
}

# ── 开场句改写 ────────────────────────────────────────────────
_OPENER_REWRITES = {
    'hft': [
        lambda orig: orig,  # 保持原句
        lambda orig: orig.replace('。', '，数据说话。') if '。' in orig[:20] else orig,
    ],
    'macro': [
        lambda orig: orig,
        lambda orig: f'今天来聊一个大家都关心的问题。\n\n{orig}',
    ],
}

# ── 结尾钩子（各账号独立） ────────────────────────────────────
_CLOSERS = {
    0: [  # 姓赵不宣
        '关注我，每晚21:00直播+SMC教学',
        '点个关注，持续更新交易思路',
    ],
    1: [  # 蓝桉
        '跟我一起做高频，关注不迷路',
        '每天更新行情，关注见证真实交易',
    ],
    2: [  # 牛来PRO
        '关注我，每天一篇市场深度解读',
        '学会看结构，比追热点重要得多',
    ],
}


def _clean_ip(text: str, forbidden: list) -> str:
    """清除IP污染词"""
    for word in forbidden:
        # 直接替换为空或通用词
        text = text.replace(word + '系统', '我的系统')
        text = text.replace(word + '分析', '分析')
        text = text.replace(word, '')
    # 清理多余空格/换行
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def _apply_style(text: str, style: str) -> str:
    """应用风格词汇替换"""
    if style not in _STYLE_REPLACEMENTS:
        return text
    for old, new in _STYLE_REPLACEMENTS[style]:
        text = text.replace(old, new)
    return text


def _replace_suffix(text: str, old_suffix: str, new_suffix: str) -> str:
    """替换结尾品牌标识"""
    if old_suffix in text:
        return text.replace(old_suffix, new_suffix)
    # 找最后一行替换
    lines = text.rstrip().splitlines()
    if lines and ('不是建议' in lines[-1] or '姓赵' in lines[-1] or
                  '🌿' in lines[-1]):
        lines[-1] = new_suffix
        return '\n'.join(lines)
    return text + '\n\n' + new_suffix


def _add_closer(text: str, key_idx: int) -> str:
    """在结尾品牌标识前插入互动钩子"""
    closers = _CLOSERS.get(key_idx, [])
    if not closers:
        return text
    closer = random.choice(closers)
    lines = text.rstrip().splitlines()
    # 找最后一个品牌标识行，在它之前插入
    for i in range(len(lines) - 1, -1, -1):
        if '🌿' in lines[i] or '💙' in lines[i] or '🐂' in lines[i]:
            lines.insert(i, f'\n{closer}')
            return '\n'.join(lines)
    return text + f'\n\n{closer}'


def rewrite_for_account(original: str, key_idx: int,
                        original_key_idx: int = 0) -> Optional[str]:
    """
    将原帖内容改写为目标账号的风格版本。

    Args:
        original: 原始帖子内容（姓赵不宣版）
        key_idx: 目标账号索引（0/1/2）
        original_key_idx: 原始帖子的账号索引（默认0）

    Returns:
        改写后的内容，None表示跳过（不需要改写）
    """
    if key_idx == original_key_idx:
        return None  # 同账号不需要改写

    cfg = _VOICE_CONFIG[key_idx]
    orig_cfg = _VOICE_CONFIG[original_key_idx]

    text = original

    # Step1: 替换结尾品牌标识
    text = _replace_suffix(text, orig_cfg['suffix'], cfg['suffix'])

    # Step2: 应用风格词汇替换
    text = _apply_style(text, cfg['style'])

    # Step3: 清除IP污染词
    text = _clean_ip(text, cfg['forbidden'])

    # Step4: 添加互动钩子（各账号独立）
    text = _add_closer(text, key_idx)

    # Step5: 验证IP隔离
    for word in cfg['forbidden']:
        if word in text:
            # 强制再清一遍
            text = text.replace(word, '')

    return text.strip()


def generate_three_versions(original: str) -> dict:
    """
    从姓赵不宣版原帖，生成三个账号的差异化版本。

    Returns:
        {0: 姓赵版, 1: 蓝桉版, 2: 牛来PRO版}
    """
    versions = {0: original}
    for idx in [1, 2]:
        rewritten = rewrite_for_account(original, idx, original_key_idx=0)
        if rewritten:
            versions[idx] = rewritten
    return versions


def post_three_versions(original: str, post_fn,
                        delay_seconds: int = 300) -> dict:
    """
    发布三个账号的差异化版本。

    Args:
        original: 姓赵不宣版原始内容
        post_fn: 发帖函数，签名 post_fn(content, key_idx) -> dict
        delay_seconds: 账号间发帖间隔（默认5分钟，避免同时触发）

    Returns:
        {0: result0, 1: result1, 2: result2}
    """
    versions = generate_three_versions(original)
    results = {}

    for idx, content in versions.items():
        try:
            result = post_fn(content, idx)
            results[idx] = result
            link = result.get('data', {}).get('shareLink', '')
            name = _VOICE_CONFIG[idx]['name']
            print(f'[multi_voice] {name} ✅ {link}', flush=True)
        except Exception as e:
            results[idx] = {'error': str(e)}
            print(f'[multi_voice] KEY{idx} 失败: {e}', flush=True)

        # 账号间间隔（KEY_0已发，KEY_1等5min，KEY_2再等5min）
        if idx < max(versions.keys()):
            print(f'[multi_voice] 等待{delay_seconds}s → 下一账号...', flush=True)
            time.sleep(delay_seconds)

    return results
