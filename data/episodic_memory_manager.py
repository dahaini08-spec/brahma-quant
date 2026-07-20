#!/usr/bin/env python3
"""
episodic_memory.py — 梵天情景记忆层
设计院封印 2026-07-20 | 苏摩111批准

功能：
  1. 写入每次分析/交易结果到 per_asset_history.json
  2. 查询历史情境（用于六方推理注入上下文）
  3. 更新 pattern_library.json

激活条件：该币种该体制 n>=3 时注入六方推理上下文
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
EPISODIC_DIR = BASE_DIR / 'episodic_memory'
ASSET_HISTORY_FILE = EPISODIC_DIR / 'per_asset_history.json'
EXPERT_CONCLUSIONS_FILE = EPISODIC_DIR / 'expert_conclusions.json'
PATTERN_LIBRARY_FILE = EPISODIC_DIR / 'pattern_library.json'

EPISODIC_DIR.mkdir(exist_ok=True)


def _load_json(path: Path) -> dict:
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_json(path: Path, data: dict):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_signal_result(symbol: str, regime: str, direction: str,
                         score: float, grade: float,
                         result: str, pnl_pct: float = None,
                         key_dims: dict = None, notes: str = None):
    """
    写入信号结果到情景记忆。
    在信号出场后（signal_settler/ws_guardian）调用。
    """
    data = _load_json(ASSET_HISTORY_FILE)
    if symbol not in data:
        data[symbol] = []

    entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'regime': regime,
        'direction': direction,
        'score': score,
        'grade': grade,
        'result': result,
        'pnl_pct': pnl_pct,
        'key_dims': key_dims or {},
        'notes': notes or ''
    }
    data[symbol].append(entry)

    # 只保留最近100条
    if len(data[symbol]) > 100:
        data[symbol] = data[symbol][-100:]

    _save_json(ASSET_HISTORY_FILE, data)
    print(f'[EpisodicMemory] 写入 {symbol} 记录: {result} pnl={pnl_pct}% (total={len(data[symbol])})')


def get_context_for_analysis(symbol: str, regime: str, direction: str, min_n: int = 3) -> str:
    """
    查询历史情境，用于六方推理注入上下文。
    返回结构化字符串，若样本不足返回空字符串。
    """
    data = _load_json(ASSET_HISTORY_FILE)
    symbol_history = data.get(symbol, [])

    # 按体制+方向过滤
    relevant = [e for e in symbol_history
                if e.get('regime') == regime and e.get('direction') == direction]

    if len(relevant) < min_n:
        return ''  # 样本不足，不注入

    wins = [e for e in relevant if e.get('result', '').startswith('WIN')]
    losses = [e for e in relevant if e.get('result') == 'LOSS']
    wr = len(wins) / len(relevant) if relevant else 0
    avg_pnl = sum(e.get('pnl_pct', 0) or 0 for e in relevant) / len(relevant)

    context_lines = [
        f'📚 历史情境记忆 [{symbol} · {regime} · {direction}] n={len(relevant)}',
        f'  胜率: {wr:.0%}  均值收益: {avg_pnl:+.2f}%',
        f'  WIN: {len(wins)}笔  LOSS: {len(losses)}笔',
    ]

    # 最近3条记录
    recent = relevant[-3:]
    context_lines.append('  最近3条:')
    for e in recent:
        ts_short = e.get('ts', '')[:10]
        context_lines.append(
            f'    [{ts_short}] {e.get("result","")} {e.get("pnl_pct",0):+.1f}% '
            f'score={e.get("score",0):.0f}'
        )

    return '\n'.join(context_lines)


def write_expert_conclusion(signal_id: str, symbol: str, regime: str,
                             direction: str, score: float, grade: float,
                             experts: dict, final_verdict: str):
    """写入六方推理结论存档"""
    data = _load_json(EXPERT_CONCLUSIONS_FILE)
    data[signal_id] = {
        'symbol': symbol,
        'ts': datetime.now(timezone.utc).isoformat(),
        'regime': regime,
        'direction': direction,
        'score': score,
        'grade': grade,
        'experts': experts,
        'final_verdict': final_verdict,
        'outcome': None  # 事后回填
    }
    _save_json(EXPERT_CONCLUSIONS_FILE, data)
    print(f'[EpisodicMemory] 专家结论存档: {signal_id}')


def update_outcome(signal_id: str, result: str, pnl_pct: float = None):
    """回填信号结果到专家结论存档"""
    data = _load_json(EXPERT_CONCLUSIONS_FILE)
    if signal_id in data:
        data[signal_id]['outcome'] = {'result': result, 'pnl_pct': pnl_pct}
        _save_json(EXPERT_CONCLUSIONS_FILE, data)


if __name__ == '__main__':
    # 测试写入
    print('测试 episodic_memory...')
    write_signal_result(
        symbol='BTCUSDT', regime='BEAR_TREND', direction='SHORT',
        score=162.0, grade=85.3, result='WIN_T1', pnl_pct=2.1,
        key_dims={'L2+贝叶斯+宏观': 12, '鲸鱼+跨市场+微观': 8},
        notes='BEAR_TREND体制空单，CHoCH确认后入场'
    )
    ctx = get_context_for_analysis('BTCUSDT', 'BEAR_TREND', 'SHORT', min_n=1)
    print(ctx if ctx else '(样本不足，无上下文)')
    print('✅ episodic_memory 测试完成')
