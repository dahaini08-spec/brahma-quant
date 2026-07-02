#!/usr/bin/env python3
"""
brahma_brain/headroom.py — Token 压缩与上下文管理
Brahma-Quant Open Source v3.0 | 设计院封印 2026-07-02

解决多 Agent 长上下文场景的 Token 消耗问题。

核心功能：
  1. 市场状态压缩 (ms dict → 精简 string，压缩率 60-80%)
  2. 信号卡片压缩 (推送内容去冗余)
  3. Agent 上下文裁剪 (保留最相关的 K 条历史)
  4. Token 预算追踪

参考：Headroom (2024) — 通过智能压缩降低 LLM 上下文成本

用法：
    from brahma_brain.headroom import compress_ms, compress_signal_card, TokenBudget

    compressed = compress_ms(ms_dict)           # 压缩市场状态
    card = compress_signal_card(signal_dict)    # 压缩信号卡片
    budget = TokenBudget(limit=4000)            # Token 预算管理
"""
import os
import json
import time
import logging
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# ── 默认配置 ─────────────────────────────────────────────────────
DEFAULT_BUDGET     = int(os.environ.get('BRAHMA_TOKEN_BUDGET',    '4000'))
MS_MAX_FIELDS      = int(os.environ.get('BRAHMA_MS_MAX_FIELDS',   '12'))
HISTORY_MAX_TURNS  = int(os.environ.get('BRAHMA_HISTORY_MAX',     '6'))

# ms 字段优先级（高优先级保留，低优先级在预算紧时裁剪）
_MS_FIELD_PRIORITY = {
    'regime':     10,   # 最重要，必须保留
    'price':      10,
    'signal_bias': 9,
    'momentum':   9,    # RSI 等核心动量
    'structure':  8,    # CHoCH/BOS
    'trend':      8,
    'atr':        7,
    'volume':     6,
    'sentiment':  5,
    'key_levels': 4,
    'bb_15m':     3,
    'wave':       3,
    'valid':      2,
    'error':      1,
}


# ════════════════════════════════════════════════════════════════
# 模块1: 市场状态压缩
# ════════════════════════════════════════════════════════════════

def compress_ms(ms: Dict, max_fields: int = MS_MAX_FIELDS) -> str:
    """
    将 ms (market state) 字典压缩为紧凑字符串
    压缩率：约 60-80%（相比 json.dumps）

    Args:
        ms:         市场状态字典
        max_fields: 最多保留的顶级字段数

    Returns:
        压缩后的字符串（可直接注入 Agent prompt）

    示例：
        {regime: BEAR_TREND | price: 60000 | rsi_1h: 35.2 | ...}
    """
    if not ms or not isinstance(ms, dict):
        return '{}'

    try:
        # 按优先级排序字段
        sorted_fields = sorted(
            ms.items(),
            key=lambda kv: _MS_FIELD_PRIORITY.get(kv[0], 0),
            reverse=True
        )[:max_fields]

        parts = []
        for key, val in sorted_fields:
            if val is None or val == '':
                continue
            compressed_val = _compress_value(key, val)
            if compressed_val:
                parts.append(f"{key}:{compressed_val}")

        return '{' + ' | '.join(parts) + '}'

    except Exception as e:
        logger.warning(f"[Headroom] compress_ms 失败: {e}")
        return f"{{regime:{ms.get('regime','?')} price:{ms.get('price','?')}}}"


def _compress_value(key: str, val: Any) -> str:
    """递归压缩值"""
    if isinstance(val, float):
        return f"{val:.2f}" if abs(val) < 10000 else f"{val:.0f}"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, str):
        return val[:20]  # 字符串截断
    if isinstance(val, dict):
        # 嵌套 dict：只取最重要的几个键
        if key == 'momentum':
            rsi = val.get('rsi_1h', val.get('rsi', '?'))
            return f"rsi={_fmt(rsi)}"
        if key == 'trend':
            h1 = val.get('1h', {})
            if isinstance(h1, dict):
                return f"1h:{h1.get('direction','?')}"
            return str(h1)[:15]
        if key == 'structure':
            grade = val.get('grade', '?')
            choch = '✓CHoCH' if val.get('has_choch') else ''
            return f"grade={grade}{choch}"
        if key == 'volume':
            ratio = val.get('ratio', '?')
            return f"ratio={_fmt(ratio)}"
        if key == 'sentiment':
            fr = val.get('funding_rate', '?')
            return f"fr={_fmt(fr)}"
        # 通用 dict 压缩
        items = [f"{k}={_fmt(v)}" for k, v in list(val.items())[:3]]
        return ','.join(items)
    if isinstance(val, list):
        return f"[{len(val)}]"
    if isinstance(val, bool):
        return 'T' if val else 'F'
    return str(val)[:20]


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)[:10]


# ════════════════════════════════════════════════════════════════
# 模块2: 信号卡片压缩
# ════════════════════════════════════════════════════════════════

def compress_signal_card(signal: Dict, mode: str = 'compact') -> str:
    """
    压缩信号卡片为推送友好格式

    Args:
        signal: 信号字典（含 symbol/score/direction/regime 等）
        mode:   'compact'(默认) | 'full' | 'mini'

    Returns:
        压缩后的推送文本

    压缩率对比：
        full:    ~800 chars
        compact: ~200 chars (75% 压缩)
        mini:    ~80  chars (90% 压缩)
    """
    try:
        sym    = signal.get('symbol', '?')
        score  = signal.get('score', signal.get('total', 0))
        dir_   = signal.get('direction', signal.get('signal_dir', '?'))
        regime = signal.get('regime', '?')
        grade  = signal.get('grade', '?')
        sl     = signal.get('sl_pct', signal.get('stop_loss_pct', '?'))
        tp     = signal.get('tp_pct', signal.get('take_profit_pct', '?'))
        timing = signal.get('timing_badge', signal.get('badge', ''))
        ts     = signal.get('timestamp', signal.get('ts', ''))[:16]

        if mode == 'mini':
            return f"[{sym} {dir_} s={score} {regime[:4]}]"

        if mode == 'compact':
            parts = [
                f"📊 {sym} {dir_} | score={score} grade={grade}",
                f"体制:{regime} {timing}",
                f"SL={sl}% TP={tp}% | {ts}",
            ]
            return '\n'.join(p for p in parts if p.strip())

        # full mode — 完整信息（去冗余但保留核心）
        return json.dumps({
            k: v for k, v in signal.items()
            if k not in ('raw_ms', 'debug', 'internal', 'history')
        }, ensure_ascii=False, indent=None)

    except Exception as e:
        logger.warning(f"[Headroom] compress_signal_card 失败: {e}")
        return str(signal)[:200]


# ════════════════════════════════════════════════════════════════
# 模块3: Agent 上下文裁剪
# ════════════════════════════════════════════════════════════════

def trim_agent_history(history: List[Dict],
                       max_turns: int = HISTORY_MAX_TURNS,
                       always_keep_first: bool = True) -> List[Dict]:
    """
    裁剪 Agent 对话历史，保留最相关的轮次

    策略：
      1. 始终保留第一轮（system prompt / 初始上下文）
      2. 始终保留最后 max_turns 轮
      3. 中间部分丢弃（已不相关的历史）

    Args:
        history:           消息列表 [{'role': ..., 'content': ...}]
        max_turns:         最多保留轮数
        always_keep_first: 是否始终保留第一条

    Returns:
        裁剪后的历史
    """
    if not history or len(history) <= max_turns:
        return history

    result = []
    if always_keep_first:
        result.append(history[0])
        tail = history[1:]
    else:
        tail = history

    # 保留最后 max_turns 条
    if len(tail) > max_turns:
        dropped = len(tail) - max_turns
        result.extend(tail[-max_turns:])
        logger.debug(f"[Headroom] 历史裁剪: 丢弃 {dropped} 轮")
    else:
        result.extend(tail)

    return result


def estimate_tokens(text: Union[str, dict, list]) -> int:
    """
    快速 token 数量估算（无需 tiktoken）
    规则：1 token ≈ 4 chars（英文）≈ 2 chars（中文）

    Args:
        text: 字符串或可 JSON 化对象

    Returns:
        估算 token 数
    """
    try:
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)

        # 统计 ASCII 和非 ASCII 字符
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        cjk_chars   = len(text) - ascii_chars

        return int(ascii_chars / 4 + cjk_chars / 2)
    except Exception:
        return len(str(text)) // 3


# ════════════════════════════════════════════════════════════════
# 模块4: Token 预算管理器
# ════════════════════════════════════════════════════════════════

class TokenBudget:
    """
    Token 预算追踪器

    用法：
        budget = TokenBudget(limit=4000)
        with budget.track('market_analysis'):
            result = run_analysis(ms_compressed)
        print(budget.summary())
    """

    def __init__(self, limit: int = DEFAULT_BUDGET):
        self.limit   = limit
        self.used    = 0
        self.records: List[Dict] = []
        self._start  = time.time()

    def add(self, label: str, tokens: int) -> bool:
        """记录消耗，返回是否在预算内"""
        self.used += tokens
        self.records.append({
            'label': label,
            'tokens': tokens,
            'cumulative': self.used,
            'ts': time.time() - self._start,
        })
        if self.used > self.limit:
            logger.warning(
                f"[Headroom] Token超预算: {self.used}/{self.limit} "
                f"(+{tokens} for '{label}')"
            )
            return False
        return True

    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def usage_pct(self) -> float:
        return round(self.used / self.limit * 100, 1) if self.limit else 0

    def summary(self) -> str:
        lines = [
            f"📊 Token Budget: {self.used}/{self.limit} ({self.usage_pct()}%)",
        ]
        for r in self.records[-5:]:
            lines.append(f"  {r['label']:30s} +{r['tokens']:5d}  累计={r['cumulative']}")
        if self.used > self.limit:
            lines.append(f"  ⚠️ 超预算 {self.used - self.limit} tokens")
        return '\n'.join(lines)

    class _Tracker:
        def __init__(self, budget, label):
            self._budget = budget
            self._label  = label
            self._text_in  = []
            self._text_out = []

        def feed_input(self, text): self._text_in.append(text)
        def feed_output(self, text): self._text_out.append(text)

        def __enter__(self): return self
        def __exit__(self, *args):
            total_in  = estimate_tokens(' '.join(str(t) for t in self._text_in))
            total_out = estimate_tokens(' '.join(str(t) for t in self._text_out))
            self._budget.add(self._label, total_in + total_out)

    def track(self, label: str) -> '_Tracker':
        return self._Tracker(self, label)


# ── 快速测试 ─────────────────────────────────────────────────────
if __name__ == '__main__':
    import json as _json

    # 示例 ms
    ms_example = {
        'regime': 'BEAR_TREND',
        'price': 60123.45,
        'signal_bias': 'SHORT',
        'momentum': {'rsi_1h': 62.3, 'rsi_4h': 58.1, 'rsi_1d': 45.0,
                     'bb': {'pos': 0.8, 'width': 0.02}, 'atr_pct': 0.012},
        'trend': {'1h': {'direction': 'down', 'strength': 0.7, 'adx': 32},
                  '4h': {'direction': 'down', 'strength': 0.8, 'adx': 35},
                  'consensus': {'consensus': 'down', 'strength': 0.75}},
        'structure': {'has_choch': True, 'has_bos': True, 'grade': 82},
        'atr': 601.2,
        'volume': {'ratio': 1.35, 'trend': 'increasing'},
        'sentiment': {'funding_rate': -0.012, 'long_short_ratio': 0.82},
        'key_levels': {'fib': [58000, 59000, 61000]},
        'valid': True,
    }

    original = _json.dumps(ms_example, ensure_ascii=False)
    compressed = compress_ms(ms_example)
    original_tokens = estimate_tokens(original)
    compressed_tokens = estimate_tokens(compressed)
    compression_ratio = (1 - compressed_tokens / original_tokens) * 100

    print(f"原始: {len(original)} chars / ~{original_tokens} tokens")
    print(f"压缩: {len(compressed)} chars / ~{compressed_tokens} tokens")
    print(f"压缩率: {compression_ratio:.0f}%")
    print(f"\n压缩结果:\n  {compressed}")

    # Token 预算示例
    budget = TokenBudget(limit=4000)
    budget.add('market_state', compressed_tokens)
    budget.add('signal_analysis', 800)
    budget.add('council_debate', 1200)
    print(f"\n{budget.summary()}")
