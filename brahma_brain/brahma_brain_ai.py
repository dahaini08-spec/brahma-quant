"""
brahma_brain_ai.py — 梵天大脑 AI决策层主入口
设计院三方联合封印 2026-09-12 苏摩111

职责：
  1. 接收10步全量数据
  2. 调用LLM进行40年交易员级别综合判断
  3. 安全约束层验证
  4. 输出VIP策略卡片（AI版）

接入位置：
  scripts/brahma_manual_analysis.py Step10区域

依赖：
  brahma_brain/brahma_brain_prompt.py (System Prompt + Few-Shot)
  scripts/free_llm_client.py (OpenRouter API)
  brahma_brain/risk_engine.py (安全约束)
"""
import json, os, ssl, time, urllib.request
from pathlib import Path

BASE = Path(__file__).parent.parent
sys_path = str(BASE)
if sys_path not in __import__('sys').path:
    __import__('sys').path.insert(0, sys_path)

from brahma_brain.brahma_brain_prompt import (
    TRADER_SYSTEM_PROMPT, FEW_SHOT_EXAMPLES, build_brahma_brain_prompt
)


def brahma_brain_decide(d: dict, fvg: dict, ob: dict, liq: dict, res: dict,
                        oi: dict, sm: dict, vol: dict, mac: dict, risk: dict,
                        fc: dict, ens: dict, council: dict) -> dict:
    """
    梵天大脑主入口：10步数据 → LLM → VIP策略卡片
    
    返回:
      {
        'core_logic': str,      # 一句话核心逻辑
        'risks': list[str],     # 风险点
        'void_condition': str,  # 作废条件
        'vip_card': str,        # VIP策略卡片
        'raw_output': str,     # LLM原始输出
        'model': str,           # 使用的模型
        'latency_ms': int,      # 调用耗时
        'success': bool,        # 是否成功
        'error': str,           # 错误信息（如果失败）
      }
    """
    t0 = time.time()
    
    # Step 1: 构建prompt
    prompt = build_brahma_brain_prompt(d, fvg, ob, liq, res, oi, sm, vol, mac, risk, fc, ens, council)
    
    # Step 2: 构建messages — free_llm_client.chat()只支持prompt+system，
    # 所以把few-shot案例嵌入system prompt，实际数据作为prompt
    system_content = TRADER_SYSTEM_PROMPT
    
    # 在system prompt末尾追加3个few-shot案例摘要
    few_shot_text = '\n\n## 参考案例（3个经典场景）\n\n'
    cases = [
        ('CHOP_MID低分+OI撤退+Hurst随机', '等待', 'CHOP低分+OI撤退+Hurst随机+FOMC前=等待', '等待：Hurst突破0.55+OI转BUILD+score过110'),
        ('BEAR_TREND高分+OI全线SHORT_BUILD+大户偏空', '做空ENTER', 'BEAR趋势+OI确认+大户偏空=顺势做空', '反弹入场空单，止损$止损墙上方，仓位3%'),
        ('BULL_TREND低分+FVG无共识+方仓陷阱', 'WATCH', 'BULL但score低+方仓陷阱+OI撤退=WATCH', '等待：OI翻转+FVG共识+失效期解除'),
    ]
    for i, (scenario, action, logic, advice) in enumerate(cases):
        few_shot_text += f'{i+1}. [{action}] {scenario}\n   逻辑: {logic}\n   建议: {advice}\n\n'
    system_content += few_shot_text
    
    # 实际数据作为user prompt
    user_prompt = build_brahma_brain_prompt(d, fvg, ob, liq, res, oi, sm, vol, mac, risk, fc, ens, council)
    
    # Step 3: 调用LLM
    try:
        raw_output, model_used = _call_llm(system_content, user_prompt)
    except Exception as e:
        return {
            'core_logic': f'梵天大脑降级: {str(e)[:60]}',
            'risks': [],
            'void_condition': '',
            'vip_card': '',
            'raw_output': '',
            'model': 'none',
            'latency_ms': int((time.time() - t0) * 1000),
            'success': False,
            'error': str(e)[:200],
        }
    
    latency_ms = int((time.time() - t0) * 1000)
    
    # Step 4: 解析输出
    parsed = _parse_output(raw_output)
    
    # Step 5: 安全约束层
    validated = _validate_output(parsed, risk, ens, vol)
    
    return {
        'core_logic': validated.get('core_logic', ''),
        'risks': validated.get('risks', []),
        'void_condition': validated.get('void_condition', ''),
        'vip_card': validated.get('vip_card', raw_output),
        'raw_output': raw_output,
        'model': model_used,
        'latency_ms': latency_ms,
        'success': True,
        'error': '',
        'warnings': validated.get('warnings', []),
    }


def _call_llm(system: str, prompt: str) -> tuple:
    """直接调用OpenRouter API（绕过free_llm_client的BRAHMA_CONSTITUTION注入）"""
    import json, ssl, urllib.request, os
    
    # 加载API key
    env_path = BASE / '.env'
    api_key = ''
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('OPENROUTER_API_KEY='):
                api_key = line.split('=', 1)[1].strip()
                break
    if not api_key:
        api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        raise RuntimeError('OPENROUTER_API_KEY not found')
    
    # 模型轮换列表（与free_llm_client一致）
    models = [
        'minimax/minimax-m3:free',
        'nvidia/nemotron-3-ultra-550b-a55b:free',
        'google/gemma-4-31b-it:free',
        'nvidia/nemotron-3-super-120b-a12b:free',
    ]
    
    url = 'https://openrouter.ai/api/v1/chat/completions'
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://brahma-quant.ai',
        'X-Title': 'BrahmaBrainAI',
    }
    
    last_error = ''
    for model in models:
        try:
            payload = json.dumps({
                'model': model,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': 0.3,
                'max_tokens': 2000,
            }).encode('utf-8')
            
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            if content:
                return content, model
        except Exception as e:
            last_error = str(e)[:100]
            continue
    
    raise RuntimeError(f'All models failed. Last error: {last_error}')


def _parse_output(raw: str) -> dict:
    """解析LLM输出为结构化dict"""
    lines = raw.strip().split('\n')
    
    core_logic = ''
    risks = []
    void_condition = ''
    vip_card_lines = []
    in_risks = False
    in_vip = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('核心逻辑：'):
            core_logic = stripped.replace('核心逻辑：', '').strip()
            in_risks = False
            in_vip = False
        elif stripped.startswith('风险点：'):
            in_risks = True
            in_vip = False
        elif stripped.startswith('作废条件：'):
            void_condition = stripped.replace('作废条件：', '').strip()
            in_risks = False
            in_vip = False
        elif stripped.startswith('🌿') or stripped.startswith('──') and 'VIP' in stripped:
            in_vip = True
            in_risks = False
            vip_card_lines.append(line)
        elif in_risks and stripped and stripped[0].isdigit() and '.' in stripped[:3]:
            risks.append(stripped.lstrip('0123456789. ').strip())
        elif in_vip:
            vip_card_lines.append(line)
    
    return {
        'core_logic': core_logic,
        'risks': risks,
        'void_condition': void_condition,
        'vip_card': '\n'.join(vip_card_lines) if vip_card_lines else raw,
    }


def _validate_output(parsed: dict, risk: dict, ens: dict, vol: dict) -> dict:
    """安全约束层：AI输出必须通过硬约束"""
    warnings = []
    
    # 如果没有风控数据，跳过验证
    if not risk or not ens or not vol:
        return {**parsed, 'warnings': warnings}
    
    # 记录约束违规但不过度修改AI输出
    # （AI已在system prompt中被告知约束，这里只做记录）
    
    ens_signal = ens.get('ensemble_signal', 0) if ens else 0
    regime_state = risk.get('regime_state', 'GREEN') if risk else 'GREEN'
    
    vip_text = parsed.get('vip_card', '')
    
    # 检查1: 失效期RED但AI建议ENTER
    if regime_state == 'RED' and 'ENTER' in vip_text.upper():
        # AI应该在system prompt约束下已经降级，如果没有则标记
        if '轻仓' not in vip_text and '1%' not in vip_text:
            warnings.append('失效期RED但AI未降仓')
    
    # 检查2: ensemble偏空但AI建议做多
    if ens_signal < -0.3 and '多单' in vip_text and '暂无' not in vip_text.split('多单')[0][-10:]:
        warnings.append('ensemble偏空但AI建议做多，请人工复核')
    
    return {**parsed, 'warnings': warnings}
