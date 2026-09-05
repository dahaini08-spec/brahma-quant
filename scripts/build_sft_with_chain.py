#!/usr/bin/env python3
"""
build_sft_with_chain.py — 梵天蒸馏数据生成器（带推理链）
设计院封印 2026-09-05 苏摩111

职责:
  用 Claude 4.6 Sonnet 作 Teacher，为每条训练样本生成<think>推理链
  把 data/brahma_sft_train.jsonl (400条) 升级为带CoT推理的蒸馏版本
  输出: data/brahma_sft_chain.jsonl (目标2000条)

原理（来自《动手学大模型》Chapter 4）:
  原始数据: instruction → ACTION:AVOID
  蒸馏数据: instruction → <think>体制BEAR_TREND，铁律1封禁...</think>ACTION:AVOID

  带推理链的训练数据让小模型"真正理解宪法"而不是死记硬背

接入位置: 独立脚本，训练前运行一次
用法: python3 scripts/build_sft_with_chain.py [--limit 50]
"""
import json, time, sys, argparse, ssl, urllib.request
from pathlib import Path

BASE   = Path(__file__).parent.parent
DATA   = BASE / 'data'
IN_F   = DATA / 'brahma_sft_train.jsonl'
OUT_F  = DATA / 'brahma_sft_chain.jsonl'

# OpenRouter API（Teacher模型）
API_KEY  = None  # 从.env加载
BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'

def _load_key() -> str:
    env_path = BASE / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('OPENROUTER_API_KEY='):
                return line.split('=', 1)[1].strip()
    import os
    return os.environ.get('OPENROUTER_API_KEY', '')

# 梵天宪法（Teacher也要遵守）
BRAHMA_SYSTEM = """你是梵天量化系统的信号裁决专家（Teacher模型）。
梵天宪法铁律：
1. BEAR_TREND做多WR=45% → AVOID
2. BULL_TREND做空WR=38% → AVOID
3. SL<1.5×ATR1H → WAIT
4. 无FVG+OB+清算共振 → WAIT

你的任务：为每个信号生成详细的推理过程，格式严格如下：
<think>
第一步：识别体制（BEAR_TREND/BULL_TREND/CHOP_MID等）
第二步：检查铁律（是否触发封禁？）
第三步：分析信号（OI方向/大户仓位/FVG磁铁）
第四步：得出结论（ENTER/AVOID/WAIT/SHORT）
</think>
ACTION:XXX  CONF:HIGH|MED|LOW  REASON:一句话核心逻辑"""

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

def generate_chain(instruction: str, input_text: str, output: str) -> str:
    """调用Teacher模型生成推理链"""
    global API_KEY
    if not API_KEY:
        API_KEY = _load_key()

    user_prompt = f"""请为以下梵天信号生成完整的推理过程：

信号: {instruction}
数据: {input_text if input_text else '(无额外数据)'}
正确答案: {output}

请生成带<think>推理链的完整回答："""

    payload = json.dumps({
        'model':       'minimax/minimax-m3:free',
        'messages': [
            {'role': 'system', 'content': BRAHMA_SYSTEM},
            {'role': 'user',   'content': user_prompt},
        ],
        'max_tokens':  200,
        'temperature': 0.3,
    }).encode()

    req = urllib.request.Request(
        BASE_URL, data=payload,
        headers={
            'Authorization': f'Bearer {API_KEY}',
            'Content-Type':  'application/json',
        },
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=20, context=_ctx).read())
        content = resp['choices'][0]['message']['content'].strip()
        # 确保包含<think>标签
        if '<think>' in content:
            return content
        else:
            # 包装成think格式
            return f"<think>\n分析: {content}\n</think>\n{output}"
    except Exception as e:
        print(f'  Teacher调用失败: {e}')
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0, help='限制处理条数(0=全部)')
    parser.add_argument('--skip-existing', action='store_true', help='跳过已生成的条目')
    args = parser.parse_args()

    # 读取原始数据
    if not IN_F.exists():
        print(f'原始数据不存在: {IN_F}')
        print('请先运行: python3 scripts/build_sft_dataset.py')
        sys.exit(1)

    raw_samples = [json.loads(l) for l in IN_F.read_text().splitlines() if l.strip()]
    print(f'=== 梵天蒸馏数据生成器 ===')
    print(f'原始样本: {len(raw_samples)} 条')

    # 加载已生成的（断点续传）
    existing = set()
    if args.skip_existing and OUT_F.exists():
        for l in OUT_F.read_text().splitlines():
            if l.strip():
                d = json.loads(l)
                existing.add(d.get('instruction', '')[:50])
        print(f'已有蒸馏样本: {len(existing)} 条（跳过）')

    # 过滤
    to_process = [s for s in raw_samples
                  if s.get('instruction', '')[:50] not in existing]
    if args.limit:
        to_process = to_process[:args.limit]

    print(f'待处理: {len(to_process)} 条')
    print(f'预计时间: 约 {len(to_process) * 8 // 60} 分钟（每条~8秒）')
    print()

    # 优先处理宪法铁律样本（最重要）
    constitution = [s for s in to_process if s.get('source') == 'constitution']
    others       = [s for s in to_process if s.get('source') != 'constitution']
    to_process   = constitution + others

    success = 0
    failed  = 0

    with open(OUT_F, 'a', encoding='utf-8') as f:
        for i, sample in enumerate(to_process):
            inst   = sample['instruction']
            inp    = sample.get('input', '')
            output = sample['output']

            print(f'[{i+1}/{len(to_process)}] {inst[:50]}...')

            chain = generate_chain(inst, inp, output)
            if chain:
                new_sample = {
                    **sample,
                    'output':         chain,      # 替换为带推理链的输出
                    'output_original': output,    # 保留原始输出
                    'has_chain':      True,
                }
                f.write(json.dumps(new_sample, ensure_ascii=False) + '\n')
                f.flush()
                success += 1
                print(f'  ✅ 推理链生成成功 ({len(chain)}字符)')
            else:
                # Teacher失败：保留原始样本（无推理链）
                fallback = {**sample, 'has_chain': False}
                f.write(json.dumps(fallback, ensure_ascii=False) + '\n')
                f.flush()
                failed += 1
                print(f'  ⚠️ Teacher失败，保留原始输出')

            # 冷却防限速
            time.sleep(3)

    total = success + failed
    print(f'\n=== 完成 ===')
    print(f'总处理: {total} 条')
    print(f'推理链: {success} 条 ({success/total*100:.0f}%)')
    print(f'原始版: {failed} 条 ({failed/total*100:.0f}%)')
    print(f'输出文件: {OUT_F}')
    print()
    print('下一步: 上传 brahma_sft_chain.jsonl 到 Google Drive → 跑 brahma_mini_train.ipynb')


if __name__ == '__main__':
    main()
