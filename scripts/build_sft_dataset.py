#!/usr/bin/env python3
"""
build_sft_dataset.py — 梵天SFT训练数据集构造器
设计院封印 2026-09-05 苏摩111

职责:
  从梵天历史数据构造 SFT（监督微调）训练样本
  输出格式: data/brahma_sft_train.jsonl
  用途: 训练梵天专属小模型（Qwen2.5-1.5B LoRA微调）

数据来源:
  1. data/hcme_expanded_index.json  — 3890条历史案例（主力）
  2. data/live_signal_log.jsonl     — 实盘信号记录
  3. data/learning_log.jsonl        — LLM每日复盘lesson
  4. data/wr_matrix_live.json       — 实时WR矩阵

输出格式（Alpaca风格）:
  {
    "instruction": "市场状态描述",
    "input": "详细信号数据",
    "output": "ACTION:LONG|SHORT|WAIT  CONF:HIGH|MED|LOW  REASON:xxx"
  }

接入位置: 独立脚本，训练前运行一次即可
"""
import json, random, sys
from pathlib import Path
from datetime import datetime, timezone

BASE  = Path(__file__).parent.parent
DATA  = BASE / 'data'
OUT_F = DATA / 'brahma_sft_train.jsonl'

# ── 梵天宪法铁律（注入训练数据）────────────────────────────────────────
IRON_RULES = {
    ('BEAR_TREND', 'LONG'):      ('AVOID', 'BEAR_TREND做多WR=45%封禁'),
    ('BULL_TREND', 'SHORT'):     ('AVOID', 'BULL_TREND做空WR=38%封禁'),
    ('BEAR_RECOVERY', 'SHORT'):  ('AVOID', 'BEAR_RECOVERY做空WR=0%封禁'),
    ('BULL_CORRECTION', 'LONG'): ('AVOID', 'BULL_CORRECTION做多封禁'),
}

# 体制→方向→期望动作映射
REGIME_BIAS = {
    'BULL_TREND':    {'LONG': 'ENTER', 'SHORT': 'AVOID'},
    'BEAR_TREND':    {'SHORT': 'ENTER', 'LONG': 'AVOID'},
    'BEAR_RECOVERY': {'LONG': 'ENTER', 'SHORT': 'AVOID'},
    'BULL_EARLY':    {'LONG': 'ENTER', 'SHORT': 'WAIT'},
    'BEAR_EARLY':    {'SHORT': 'ENTER', 'LONG': 'WAIT'},
    'CHOP_MID':      {'LONG': 'WAIT',  'SHORT': 'WAIT'},
}


def outcome_to_action(outcome: str, is_win: bool, regime: str, direction: str) -> tuple:
    """把历史结局转化为训练标签"""
    key = (regime, direction)
    # 铁律优先
    if key in IRON_RULES:
        action, reason = IRON_RULES[key]
        return action, 'LOW', reason

    if outcome in ('TP1', 'TP2'):
        bias = REGIME_BIAS.get(regime, {}).get(direction, 'WAIT')
        conf = 'HIGH' if outcome == 'TP2' else 'MED'
        return bias if bias != 'AVOID' else 'ENTER', conf, f'{regime}顺势{direction}，TP达标'
    elif outcome == 'SL':
        return 'WAIT', 'LOW', f'SL触发，信号质量不足'
    elif outcome == 'TIMEOUT':
        return 'WAIT', 'LOW', '超时未触达，方向不明'
    else:
        return 'WAIT', 'MED', '信号中性等待'


def score_to_conf(score: float) -> str:
    if score >= 155: return 'HIGH'
    elif score >= 138: return 'MED'
    else: return 'LOW'


def build_from_hcme() -> list:
    """从HCME历史案例构造训练样本"""
    samples = []
    p = DATA / 'hcme_expanded_index.json'
    if not p.exists():
        print('[build] HCME文件不存在，跳过')
        return samples

    raw = json.loads(p.read_text())
    cases = raw if isinstance(raw, list) else raw.get('cases', raw.get('index', []))
    print(f'[build] HCME案例: {len(cases)}条')

    for c in cases:
        regime    = c.get('regime', 'UNKNOWN')
        direction = c.get('direction', 'LONG')
        outcome   = c.get('outcome', 'UNKNOWN')
        is_win    = c.get('is_win', False)
        score     = float(c.get('score', 0) or 0)
        symbol    = c.get('symbol', 'BTCUSDT').replace('USDT', '')
        pnl       = float(c.get('pnl_pct', 0) or 0)

        # 跳过无效数据
        if regime == 'UNKNOWN' or direction not in ('LONG', 'SHORT'):
            continue

        action, conf, reason = outcome_to_action(outcome, is_win, regime, direction)

        # 构造instruction
        instruction = (
            f"{symbol}/USDT {regime}体制 "
            f"梵天score={score:.0f} "
            f"方向偏好={direction} "
            f"给出操作建议"
        )

        # 构造input（补充信号细节）
        input_text = (
            f"体制={regime} "
            f"score={score:.0f} "
            f"direction={direction} "
            f"outcome_history={outcome} "
            f"pnl_history={pnl:+.2f}%"
        )

        # 构造output
        output_text = f"ACTION:{action}  CONF:{conf}  REASON:{reason}"

        samples.append({
            'instruction': instruction,
            'input':       input_text,
            'output':      output_text,
            'source':      'hcme',
            'regime':      regime,
            'direction':   direction,
        })

    return samples


def build_from_live_signals() -> list:
    """从实盘信号记录构造训练样本"""
    samples = []
    p = DATA / 'live_signal_log.jsonl'
    if not p.exists():
        print('[build] live_signal_log不存在，跳过')
        return samples

    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    # 只用已结算的信号
    settled = [l for l in lines if l.get('outcome') in ('TP1', 'TP2', 'SL')]
    print(f'[build] 实盘已结算信号: {len(settled)}条')

    for sig in settled:
        regime    = sig.get('regime', 'UNKNOWN')
        direction = sig.get('direction', 'LONG')
        outcome   = sig.get('outcome', 'UNKNOWN')
        score     = float(sig.get('score_final') or sig.get('score') or 0)
        symbol    = sig.get('symbol', 'BTCUSDT').replace('USDT', '')
        pnl       = float(sig.get('pnl_pct', 0) or 0)
        fvg_dir   = sig.get('fvg_direction', 'NONE')
        oi_sig    = sig.get('oi_signal', 'MIXED')

        if regime == 'UNKNOWN':
            continue

        action, conf, reason = outcome_to_action(outcome, outcome in ('TP1','TP2'), regime, direction)

        instruction = (
            f"{symbol}/USDT {regime}体制 "
            f"score={score:.0f} FVG={fvg_dir} OI={oi_sig} "
            f"给出操作建议"
        )
        input_text = (
            f"regime={regime} score={score:.0f} "
            f"direction={direction} fvg={fvg_dir} oi={oi_sig}"
        )
        output_text = f"ACTION:{action}  CONF:{conf}  REASON:{reason}"

        samples.append({
            'instruction': instruction,
            'input':       input_text,
            'output':      output_text,
            'source':      'live',
            'regime':      regime,
            'direction':   direction,
        })

    return samples


def build_constitution_samples() -> list:
    """构造梵天宪法铁律强化样本（每条铁律扩充100个变体）"""
    samples = []

    templates = [
        # BEAR_TREND做多封禁
        {
            'instruction': 'BTC/USDT BEAR_TREND体制 score={s} 是否做多？',
            'input': 'regime=BEAR_TREND direction=LONG score={s}',
            'output': 'ACTION:AVOID  CONF:HIGH  REASON:BEAR_TREND做多WR=45%，梵天宪法铁律封禁',
            'scores': [85, 100, 115, 130, 142, 155, 160, 170],
        },
        # BULL_TREND做空封禁
        {
            'instruction': 'ETH/USDT BULL_TREND体制 score={s} 是否做空？',
            'input': 'regime=BULL_TREND direction=SHORT score={s}',
            'output': 'ACTION:AVOID  CONF:HIGH  REASON:BULL_TREND做空WR=38%，梵天宪法铁律封禁',
            'scores': [90, 110, 125, 138, 150, 162],
        },
        # CHOP体制等待
        {
            'instruction': 'BTC/USDT CHOP_MID体制 score={s} 方向{d} 是否入场？',
            'input': 'regime=CHOP_MID direction={d} score={s}',
            'output': 'ACTION:WAIT  CONF:MED  REASON:CHOP_MID震荡体制，双向封禁，等待突破',
            'scores': [100, 115, 125],
            'dirs': ['LONG', 'SHORT'],
        },
        # 无共振点等待
        {
            'instruction': 'BTC/USDT {r}体制 score={s} 无FVG+OB+清算共振点 是否入场？',
            'input': 'regime={r} score={s} triple_resonance=False fvg=NONE',
            'output': 'ACTION:WAIT  CONF:MED  REASON:无FVG+OB+清算三因子共振，不给入场价，等待结构形成',
            'regimes': ['BULL_TREND', 'BEAR_TREND', 'BULL_EARLY'],
            'scores': [140, 150, 160],
        },
    ]

    for tmpl in templates:
        scores = tmpl.get('scores', [120, 140])
        dirs   = tmpl.get('dirs', ['LONG'])
        regimes = tmpl.get('regimes', ['BULL_TREND'])
        syms   = ['BTC', 'ETH', 'SOL', 'BNB']

        for score in scores:
            for sym in syms:
                for d in dirs:
                    for r in regimes:
                        inst = tmpl['instruction'].format(s=score, d=d, r=r).replace('BTC', sym)
                        inp  = tmpl['input'].format(s=score, d=d, r=r)
                        out  = tmpl['output']
                        samples.append({
                            'instruction': inst,
                            'input':       inp,
                            'output':      out,
                            'source':      'constitution',
                        })

    print(f'[build] 宪法铁律强化样本: {len(samples)}条')
    return samples


def build_from_learning_log() -> list:
    """从LLM复盘lesson构造训练样本"""
    samples = []
    p = DATA / 'learning_log.jsonl'
    if not p.exists():
        return samples

    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    print(f'[build] learning_log: {len(lines)}条')

    for entry in lines:
        lesson = entry.get('lesson', '')
        wr     = entry.get('wr', 0)
        if not lesson or len(lesson) < 10:
            continue
        samples.append({
            'instruction': f'本批信号WR={wr:.0%}，总结交易教训',
            'input':       entry.get('summary', '')[:100],
            'output':      lesson[:100],
            'source':      'learning_log',
        })

    return samples


def main():
    print('=== 梵天SFT数据集构造器 ===')
    print(f'时间: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    print()

    all_samples = []

    # 1. HCME历史案例（主力）
    all_samples += build_from_hcme()

    # 2. 实盘信号
    all_samples += build_from_live_signals()

    # 3. 宪法铁律强化
    all_samples += build_constitution_samples()

    # 4. learning_log
    all_samples += build_from_learning_log()

    # 去重 + 打乱
    seen = set()
    deduped = []
    for s in all_samples:
        key = s['instruction'][:50] + s['output'][:30]
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    random.shuffle(deduped)

    # 写入
    DATA.mkdir(parents=True, exist_ok=True)
    with open(OUT_F, 'w', encoding='utf-8') as f:
        for s in deduped:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')

    # 统计
    sources = {}
    for s in deduped:
        src = s.get('source', 'unknown')
        sources[src] = sources.get(src, 0) + 1

    print(f'\n✅ 完成！总样本: {len(deduped)}条')
    print('来源分布:')
    for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
        print(f'  {src:20s}: {cnt}条')
    print(f'\n输出文件: {OUT_F}')
    print(f'文件大小: {OUT_F.stat().st_size / 1024:.1f} KB')

    # 展示3条样本
    print('\n--- 样本预览（前3条）---')
    for s in deduped[:3]:
        print(f'[{s["source"]}]')
        print(f'  instruction: {s["instruction"][:60]}')
        print(f'  output:      {s["output"][:60]}')
        print()


if __name__ == '__main__':
    main()
