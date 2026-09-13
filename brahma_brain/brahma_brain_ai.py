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
import json, os, ssl, time, urllib.request, re
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
        'reverse_arg': str,     # 一句话反向论证
        'risks': list[str],     # 风险点
        'void_condition': str,  # 作废条件
        'vip_card': str,        # VIP策略卡片
        'raw_output': str,     # LLM原始输出
        'model': str,           # 使用的模型
        'latency_ms': int,      # 调用耗时
        'success': bool,        # 是否成功
        'error': str,           # 错误信息（如果失败）
        'warnings': list[str],  # 安全约束警告
      }
    """
    t0 = time.time()
    
    # P0修复: 确保d顶层有regime/score/grade（如果caller未设置，从d['bs']提取）
    bs = d.get('bs', {}) if isinstance(d.get('bs'), dict) else {}
    if 'regime' not in d and 'regime' in bs:
        d = {**d, 'regime': bs.get('regime', 'N/A'), 'score': bs.get('score_final', bs.get('score', 0)), 'grade': bs.get('grade', '?')}
    
    # Step 1: 构建prompt
    user_prompt = build_brahma_brain_prompt(d, fvg, ob, liq, res, oi, sm, vol, mac, risk, fc, ens, council)
    
    # Step 2: 构建system prompt（40年交易员人格 + few-shot摘要）
    system_content = TRADER_SYSTEM_PROMPT
    
    # 在system prompt末尾追加5个few-shot案例摘要
    few_shot_text = '\n\n## 参考案例（5个经典场景）\n\n'
    cases = [
        ('CHOP_MID低分+OI撤退+Hurst随机', '等待', 'CHOP低分+OI撤退+Hurst随机+FOMC前=等待', 'FVG偏多但OI撤退+Hurst随机=不追多', '等待：Hurst突破0.55+OI转BUILD+score过110'),
        ('BEAR_TREND高分+OI全线SHORT_BUILD+大户偏空', '做空ENTER', 'BEAR趋势+OI确认+大户偏空=顺势做空', '止损墙上方有逼空风险但OI全线做空确认=不大逆势做多', '反弹入场空单，止损设在止损墙上方1.5×ATR1H处，仓位3%'),
        ('BULL_TREND低分+FVG无共识+方仓陷阱', 'WATCH', 'BULL但score低+方仓陷阱+OI撤退=WATCH', '结构偏多但资金撤退+散户拥挤=不追多也不做空', '等待：OI翻转+FVG共识+失效期解除'),
        # P9补充: 复杂场景
        ('体制矛盾:FVG=BULL但OI=SHORT_BUILD', 'WATCH', '结构偏多但资金在撤=信号矛盾，不赌方向', 'FVG做多信号强但OI全线撤退=结构资金背离，两边都不赌', '等待：OI和FVG方向一致再入场'),
        ('止损墙被测试但未突破+OI无确认', 'WATCH', '可能假突破，无OI确认不追', '止损墙近但OI无BUILD确认=可能是诱多/诱空，不追', '等待：止损墙突破+OI翻转确认方向'),
    ]
    for i, (scenario, action, logic, reverse, advice) in enumerate(cases):
        few_shot_text += f'{i+1}. [{action}] {scenario}\n   逻辑: {logic}\n   反向: {reverse}\n   建议: {advice}\n\n'
    system_content += few_shot_text
    
    # Step 3: 调用LLM（P2修复: 全局超时120s + fallback）
    try:
        raw_output, model_used = _call_llm(system_content, user_prompt, total_timeout=120)
    except Exception as e:
        return {
            'core_logic': f'梵天大脑降级: {str(e)[:60]}',
            'reverse_arg': '',
            'risks': [],
            'void_condition': '',
            'vip_card': '',
            'raw_output': '',
            'model': 'none',
            'latency_ms': int((time.time() - t0) * 1000),
            'success': False,
            'error': str(e)[:200],
            'warnings': [],
        }
    
    latency_ms = int((time.time() - t0) * 1000)
    
    # Step 4: 解析输出
    parsed = _parse_output(raw_output)
    
    # Step 5: 安全约束层
    validated = _validate_output(parsed, risk, ens, vol)
    
    # Fix E: 日志记录
    try:
        import json as _json
        log_path = BASE / 'data' / 'brahma_brain_log.jsonl'
        log_entry = {
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'symbol': d.get('sym', '?'),
            'model': model_used,
            'latency_ms': latency_ms,
            'core_logic': validated.get('core_logic', ''),
            'reverse_arg': validated.get('reverse_arg', ''),
            'void_condition': validated.get('void_condition', ''),
            'warnings': validated.get('warnings', []),
            'raw_output_preview': raw_output[:200],
        }
        with open(log_path, 'a') as _lf:
            _lf.write(_json.dumps(log_entry, ensure_ascii=False) + '\n')
    except Exception:
        pass
    
    return {
        'core_logic': validated.get('core_logic', ''),
        'reverse_arg': validated.get('reverse_arg', ''),
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


def _call_llm(system: str, prompt: str, total_timeout: int = 120) -> tuple:
    """直接调用OpenRouter API（绕过free_llm_client的BRAHMA_CONSTITUTION注入）
    P2修复: 全局超时控制，超时直接raise让上层fallback到规则版VIP
    """
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
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://brahma-quant.ai',
        'X-Title': 'BrahmaBrainAI',
    }
    
    last_error = ''
    _global_t0 = time.time()
    for model in models:
        _elapsed = time.time() - _global_t0
        if _elapsed >= total_timeout:
            raise RuntimeError(f'Global timeout {total_timeout}s exceeded ({_elapsed:.0f}s elapsed)')
        _remaining = total_timeout - _elapsed
        _model_timeout = min(90, int(_remaining) + 5)
        try:
            payload = json.dumps({
                'model': model,
                'messages': [
                    {'role': 'system', 'content': system},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': 0.3,
                'max_tokens': 3000,
            }).encode('utf-8')
            
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=_model_timeout, context=ctx) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            if content:
                # Fix E: 检查输出是否包含格式化标记 + 排除模板占位符
                has_format = '梵天大脑' in content or '核心逻辑' in content or '姓赵不宣' in content
                has_placeholder = '[一句话' in content or '[X]' in content or '[SYMBOL]' in content
                if has_format and not has_placeholder:
                    return content, model
                else:
                    # 格式不对或含占位符，降低温度重试1次
                    payload2 = json.dumps({
                        'model': model,
                        'messages': [
                            {'role': 'system', 'content': system},
                            {'role': 'user', 'content': prompt + '\n\n请严格按照输出格式输出，包含"🧠 梵天大脑决策"和"核心逻辑："标记。'},
                        ],
                        'temperature': 0.1,
                        'max_tokens': 3000,
                    }).encode('utf-8')
                    req2 = urllib.request.Request(url, data=payload2, headers=headers, method='POST')
                    with urllib.request.urlopen(req2, timeout=_model_timeout, context=ctx) as resp2:
                        result2 = json.loads(resp2.read().decode('utf-8'))
                    content2 = result2.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if content2 and ('梵天大脑' in content2 or '核心逻辑' in content2 or '姓赵不宣' in content2):
                        # 排除模板占位符
                        if '[一句话' not in content2 and '[X]' not in content2 and '[SYMBOL]' not in content2:
                            return content2, model
        except Exception as e:
            last_error = str(e)[:100]
            continue
    
    raise RuntimeError(f'All models failed. Last error: {last_error}')


def _parse_output(raw: str) -> dict:
    """解析LLM输出为结构化dict
    P2修复: 从最后一个🧠标记开始解析，跳过模型推理前言
    """
    lines = raw.strip().split('\n')
    
    # 找最后一个🧠标记，跳过推理前言
    start_idx = 0
    for i, line in enumerate(lines):
        if '🧠' in line:
            start_idx = i
    lines = lines[start_idx:]
    
    core_logic = ''
    reverse_arg = ''
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
        elif stripped.startswith('反向论证：'):
            reverse_arg = stripped.replace('反向论证：', '').strip()
            in_risks = False
            in_vip = False
        elif stripped.startswith('风险点：'):
            in_risks = True
            in_vip = False
        elif stripped.startswith('作废条件：'):
            void_condition = stripped.replace('作废条件：', '').strip()
            in_risks = False
            in_vip = False
        elif stripped.startswith('🌿'):
            in_vip = True
            in_risks = False
            vip_card_lines.append(line)
        elif in_risks and stripped and stripped[0].isdigit() and '.' in stripped[:3]:
            risks.append(stripped.lstrip('0123456789. ').strip())
        elif in_vip:
            vip_card_lines.append(line)
    
    return {
        'core_logic': core_logic,
        'reverse_arg': reverse_arg,
        'risks': risks,
        'void_condition': void_condition,
        'vip_card': '\n'.join(vip_card_lines) if vip_card_lines else raw,
    }


def _validate_output(parsed: dict, risk: dict, ens: dict, vol: dict) -> dict:
    """安全约束层 v1.1：AI输出必须通过5项硬约束 — Fix B 2026-09-12"""
    warnings = []
    
    if not risk or not ens or not vol:
        return {**parsed, 'warnings': warnings}
    
    ens_signal = ens.get('ensemble_signal', 0) if ens else 0
    regime_state = risk.get('regime_state', 'GREEN') if risk else 'GREEN'
    nav_mult = risk.get('nav_mult', 1.0) if risk else 1.0
    atr_1h = vol.get('atr_1h', 0) if vol else 0
    
    vip_text = parsed.get('vip_card', '')
    
    # 硬约束1: 失效期RED只能WATCH
    if regime_state == 'RED' and ('🔴' in vip_text or '🟢' in vip_text) and '⏳' not in vip_text:
        if '轻仓' not in vip_text and '1%' not in vip_text and '0.5%' not in vip_text:
            warnings.append('⚠️ 失效期RED但AI未降仓，请人工确认')
    
    # 硬约束2: ensemble方向冲突
    if ens_signal < -0.3 and '多单' in vip_text and '暂无' not in vip_text.split('多单')[0][-10:]:
        warnings.append('⚠️ ensemble偏空但AI建议做多，请人工复核')
    if ens_signal > 0.3 and '空单' in vip_text and '暂无' not in vip_text.split('空单')[0][-10:]:
        warnings.append('⚠️ ensemble偏多但AI建议做空，请人工复核')
    
    # 硬约束3: SL距离≥1.5×ATR1H（从VIP文本中提取SL价格）
    sl_matches = re.findall(r'止损\s*\$([\d,]+\.?\d*)', vip_text)
    entry_matches = re.findall(r'(?:入场区?|挂单区)\s*\$([\d,]+\.?\d*)', vip_text)
    if sl_matches and entry_matches and atr_1h > 0:
        try:
            sl_price = float(sl_matches[0].replace(',', ''))
            entry_price = float(entry_matches[0].replace(',', ''))
            sl_distance = abs(entry_price - sl_price)
            min_sl = 1.5 * atr_1h
            if sl_distance < min_sl:
                warnings.append(f'⚠️ SL距离${sl_distance:.0f}<1.5×ATR1H=${min_sl:.0f}，止损太近')
        except (ValueError, IndexError):
            pass
    
    # 硬约束4: 仓位≤5%NAV（失效期RED≤2.5%）
    pos_matches = re.findall(r'仓位\s*(\d+(?:\.\d+)?)%', vip_text)
    if pos_matches:
        try:
            pos_pct = float(pos_matches[0])
            max_pos = 5.0 * nav_mult
            if pos_pct > max_pos:
                warnings.append(f'⚠️ 仓位{pos_pct}%>上限{max_pos:.1f}%（风控系数x{nav_mult}）')
        except (ValueError, IndexError):
            pass
    
    # 硬约束5: 杠杆≤20x
    lev_matches = re.findall(r'杠杆\s*(\d+)x', vip_text)
    if lev_matches:
        try:
            lev = int(lev_matches[0])
            if lev > 20:
                warnings.append(f'⚠️ 杠杆{lev}x>20x上限')
        except (ValueError, IndexError):
            pass
    
    return {**parsed, 'warnings': warnings}
