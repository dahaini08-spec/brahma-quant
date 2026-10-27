#!/usr/bin/env python3
"""
free_llm_client.py — 梵天免费LLM客户端
[封印 2026-10-19 苏摩111] 全力Groq重写版

设计原则（三方联合确认）:
  - Groq qwen3.8-27b 配额充足，0.24s/次，全力使用
  - 无规则替代（规则降低分析质量）
  - 无AI缓存（市场实时变化，旧结论无效）
  - 无Chutes（已转付费402，永久移除）
  - 三层路由: Groq → OpenRouter → 主AI兜底

调用链:
  chat(prompt, task) → Groq优先 → OR备用 → 主AI兜底
"""
import os, sys, json, time, urllib.request, ssl
from pathlib import Path

# ── Key加载 ──────────────────────────────────────────────────────────────

def _load_groq_key() -> str:
    for ep in [Path(__file__).parent.parent / 'alerts' / '.env',
               Path(__file__).parent.parent / '.env']:
        if ep.exists():
            for line in ep.read_text().splitlines():
                if line.startswith('GROQ_API_KEY='):
                    return line.split('=', 1)[1].strip()
    return os.environ.get('GROQ_API_KEY', '')

def _load_or_key() -> str:
    for ep in [Path(__file__).parent.parent / '.env',
               Path(__file__).parent.parent / 'alerts' / '.env']:
        if ep.exists():
            for line in ep.read_text().splitlines():
                if line.startswith('OPENROUTER_API_KEY='):
                    return line.split('=', 1)[1].strip()
    return os.environ.get('OPENROUTER_API_KEY', '')

GROQ_KEY = _load_groq_key()
OR_KEY   = _load_or_key()

# ── 模型配置 ──────────────────────────────────────────────────────────────

# Groq: qwen3.8-27b 全任务主力（0.24s，配额充足）
GROQ_MODEL   = 'qwen/qwen3.8-27b'
GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'

# OpenRouter: xing4.0-29b 备用（Groq故障时接管）
OR_MODEL   = 'cohere/north-mini-code:free'          # [修复 2026-10-22 苏摩111 v2] cohere最快0.5s+标准输出
OR_MODEL_2 = 'google/gemma-4-26b-a4b-it:free'      # 备选1: 1.1s 标准输出
OR_MODEL_3 = 'nvidia/nemotron-3.5-lightning:free'  # 备选2: 0.5s但需关键词提取
OR_API_URL = 'https://openrouter.ai/api/v1/chat/completions'

# Cerebras: llama-3.3-70b 第三备用（900 tokens/s，极速，免费）
# [接入 2026-10-27 苏摩111] Groq WAF封锁时的高质量备用链
CEREBRAS_MODEL   = 'llama-3.3-70b'
CEREBRAS_API_URL = 'https://api.cerebras.ai/v1/chat/completions'

def _load_cerebras_key() -> str:
    for ep in [Path(__file__).parent.parent / 'alerts' / '.env',
               Path(__file__).parent.parent / '.env']:
        if ep.exists():
            for line in ep.read_text().splitlines():
                if line.startswith('CEREBRAS_API_KEY='):
                    return line.split('=', 1)[1].strip()
    return os.environ.get('CEREBRAS_API_KEY', '')

CEREBRAS_KEY = _load_cerebras_key()

# Groq退避（429时）
_groq_backoff_until = 0.0

# ── SSL context ───────────────────────────────────────────────────────────

try:
    _ctx = ssl.create_default_context()
except Exception:
    _ctx = None

# ── 梵天宪法 System Prompt ────────────────────────────────────────────────

BRAHMA_CONSTITUTION = """你是梵天量化系统的专项AI分析员。
梵天宪法铁律（绝对不可违反）：
1. BEAR_TREND体制做多WR=45% → 必须输出AVOID，不论score多高
2. BULL_TREND体制做空WR=38% → 必须输出AVOID，不论score多高
3. SL距离必须≥1.5×ATR1H → 不满足则输出WAIT
4. 无FVG+OB+清算三因子共振 → 输出WAIT，不给具体入场价
5. 回答必须简洁精准，禁止废话和重复
你的任何判断都不得违反以上铁律。"""

# ── Groq调用 ──────────────────────────────────────────────────────────────

def _groq_chat(messages: list, max_tokens: int = 200, timeout: int = 20) -> str:
    """Groq主力通道：qwen3.8-27b，0.24s/次，配额充足"""
    global _groq_backoff_until
    if not GROQ_KEY:
        return ''
    if time.time() < _groq_backoff_until:
        return ''
    try:
        from groq import Groq as _GroqClient
        client = _GroqClient(api_key=GROQ_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
            timeout=min(timeout, 25),
        )
        content = (resp.choices[0].message.content or '') if resp.choices else ''
        return content.strip()
    except Exception as e:
        code = getattr(e, 'status_code', 0) or getattr(
            getattr(e, 'response', None), 'status_code', 0)
        if code == 429:
            _groq_backoff_until = time.time() + 30  # 30s快速重试，配额耗尽会持续429
            print('[groq] 429 backoff 30s', file=sys.stderr)
        elif code in (402, 401):
            print(f'[groq] {code} key问题', file=sys.stderr)
        return ''

# ── OpenRouter备用 ────────────────────────────────────────────────────────

_or_backoff_until = 0.0

def _or_chat(messages: list, max_tokens: int = 200, timeout: int = 25) -> str:
    """OpenRouter备用通道：多模型降级链。Groq故障时接管
    [修复 2026-10-22 苏摩111 v3] 多模型降级：cohere(0.5s)→gemma4(1.1s)→nemotron(推理型)
    """
    global _or_backoff_until
    if not OR_KEY:
        return ''
    if time.time() < _or_backoff_until:
        return ''
    import re as _re
    _model_chain = [OR_MODEL, OR_MODEL_2, OR_MODEL_3]
    for _try_model in _model_chain:
        try:
            payload = json.dumps({
                'model': _try_model,
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': 0.2,
            }).encode()
            req = urllib.request.Request(OR_API_URL, data=payload, headers={
                'Authorization': f'Bearer {OR_KEY}',
                'Content-Type': 'application/json',
                'HTTP-Referer': 'https://brahma.ai',
                'X-Title': 'brahma-council',
            })
            kw = {'timeout': timeout}
            if _ctx: kw['context'] = _ctx
            with urllib.request.urlopen(req, **kw) as r:
                resp = json.loads(r.read())
            choices = resp.get('choices') or []
            if not choices:
                continue
            msg = choices[0].get('message') or {}
            raw = msg.get('content') or msg.get('reasoning') or ''
            if raw:
                hits = _re.findall(r'\b(LONG|SHORT|WAIT)\b', raw.upper())
                if hits: return hits[-1]
                if raw.strip(): return raw.strip()
            continue  # 内容为空，尝试下一个模型
        except urllib.error.HTTPError as e:
            if e.code == 429:
                continue  # 该模型429，尝试下一个
            break
        except Exception:
            break
    # 全部失败
    _or_backoff_until = time.time() + 30
    print('[or] 全模型失败 backoff 30s', file=sys.stderr)
    return ''

# ── Cerebras备用 ─────────────────────────────────────────────────────────
# [接入 2026-10-27 苏摩111] 900 tokens/s，llama-3.3-70b免费层
# 注册地址: https://cloud.cerebras.ai

_cerebras_backoff_until = 0.0

def _cerebras_chat(messages: list, max_tokens: int = 200, timeout: int = 15) -> str:
    """Cerebras备用通道：llama-3.3-70b，900 tokens/s，极速"""
    global _cerebras_backoff_until
    if not CEREBRAS_KEY:
        return ''
    if time.time() < _cerebras_backoff_until:
        return ''
    try:
        payload = json.dumps({
            'model': CEREBRAS_MODEL,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': 0.2,
        }).encode()
        req = urllib.request.Request(CEREBRAS_API_URL, data=payload, headers={
            'Authorization': f'Bearer {CEREBRAS_KEY}',
            'Content-Type': 'application/json',
        })
        kw = {'timeout': timeout}
        if _ctx: kw['context'] = _ctx
        with urllib.request.urlopen(req, **kw) as r:
            resp = json.loads(r.read())
        choices = resp.get('choices') or []
        if not choices:
            return ''
        raw = (choices[0].get('message') or {}).get('content') or ''
        return raw.strip()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _cerebras_backoff_until = time.time() + 60
            print('[cerebras] 429 backoff 60s', file=sys.stderr)
        elif e.code in (401, 403):
            print(f'[cerebras] {e.code} key问题', file=sys.stderr)
        return ''
    except Exception as e:
        print(f'[cerebras] error: {e}', file=sys.stderr)
        return ''


# ── 主AI兜底 ─────────────────────────────────────────────────────────────

def _master_chat(messages: list, max_tokens: int, timeout: int) -> str:
    """主AI兜底（OpenClaw内置模型），永远可用"""
    try:
        _mc_path = Path(__file__).parent / 'master_chat.py'
        if _mc_path.exists():
            import importlib.util as _iu
            spec = _iu.spec_from_file_location('master_chat', _mc_path)
            mc = _iu.module_from_spec(spec)
            spec.loader.exec_module(mc)
            return mc.chat(messages, max_tokens=max_tokens) or ''
    except Exception:
        pass
    return ''

# ── 公开接口 ──────────────────────────────────────────────────────────────

def chat(prompt: str, system: str = '', max_tokens: int = 200,
         timeout: int = 20, task: str = 'default') -> str:
    """
    梵天LLM统一入口。
    四层路由: Groq(0.24s) → OR:free → Cerebras(900t/s) → 主AI兜底
    task参数保留兼容性，当前全部走同一模型。
    """
    # 轻量任务省略宪法，节省tokens
    _light = {'oi', 'safety', 'chop'}
    sys_content = '' if (task in _light and not system) else BRAHMA_CONSTITUTION
    if system:
        sys_content = (sys_content + '\n\n' + system).strip()

    messages = []
    if sys_content:
        messages.append({'role': 'system', 'content': sys_content})
    messages.append({'role': 'user', 'content': prompt})

    # 1. Groq主力
    result = _groq_chat(messages, max_tokens, timeout)
    if result:
        return result

    # 2. OR备用
    print(f'[llm] Groq失败，切OR备用 task={task}', file=sys.stderr)
    result = _or_chat(messages, max_tokens, timeout)
    if result:
        return result

    # 3. Cerebras备用（900 tokens/s，llama-3.3-70b）
    # [接入 2026-10-27 苏摩111] OR全灭时的高质量快速备用
    if CEREBRAS_KEY:
        result = _cerebras_chat(messages, max_tokens, timeout)
        if result:
            print(f'[llm] Cerebras接管 task={task}', file=sys.stderr)
            return result

    # 4. 主AI兜底（强制执行，OR backoff不阻断）
    print(f'[llm] 免费池全灭，主AI接管 task={task}', file=sys.stderr)
    result = _master_chat(messages, max_tokens, timeout)
    if result:
        _mark_master_success(task)
        return result

    # 4. 终极兜底：规则直接返回（永不返回空）
    content_lower = (messages[-1].get('content','') if messages else '').lower()
    if any(w in content_lower for w in ['wait','等待','chop']): return 'WAIT'
    if any(w in content_lower for w in ['long','多','bull','上涨']): return 'WATCH'
    if any(w in content_lower for w in ['short','空','bear','下跌']): return 'WATCH'
    return 'WAIT'

def _mark_master_success(task: str = '', transit: bool = False):
    """记录主AI接管事件（用于监控）"""
    try:
        log = Path(__file__).parent.parent / 'data' / 'master_failover.jsonl'
        with open(log, 'a') as f:
            f.write(json.dumps({
                'ts': time.time(), 'task': task, 'transit': transit
            }) + '\n')
    except Exception:
        pass

# ── 兼容性别名（旧代码无缝迁移）────────────────────────────────────────

def chat_with_rule(prompt: str, task: str = 'default',
                   ctx: dict = None, **kwargs) -> str:
    """兼容旧调用，直接透传chat()，无规则替代"""
    return chat(prompt, task=task, **kwargs)

def chat_cached(prompt: str, task: str = 'default',
                use_cache: bool = False, **kwargs) -> str:
    """兼容旧调用，直接透传chat()，无缓存"""
    return chat(prompt, task=task, **kwargs)

# 旧路由表：保留兼容性，值无实际意义
RULE_REPLACEABLE    = {}
TASK_MODEL_MAP      = {t: GROQ_MODEL for t in
    ['council','vip','oi','regime','wr_audit','review','hcme','chop','safety','default']}
GROQ_TASK_MODEL_MAP = TASK_MODEL_MAP
FALLBACK_MODELS     = [OR_MODEL]

# ── 调用统计（轻量，不阻塞）──────────────────────────────────────────────

def get_today_ai_calls() -> dict:
    """今日AI调用统计"""
    log = Path(__file__).parent.parent / 'data' / 'ai_call_log.jsonl'
    if not log.exists():
        return {'total': 0, 'by_task': {}}
    today_start = time.time() - (time.time() % 86400)
    entries = []
    for line in log.read_text(encoding='utf-8').splitlines():
        try:
            e = json.loads(line)
            if e.get('ts', 0) >= today_start:
                entries.append(e)
        except Exception:
            pass
    from collections import Counter
    return {'total': len(entries),
            'by_task': dict(Counter(e.get('task','?') for e in entries))}

# ── AI议会三方投票（council_three_way 兼容接口）──────────────────────────

def council_three_way(
    sym: str, price: float, regime: str, score: float,
    fvg_dir: str, fvg_magnet: float,
    oi_signal: str, sm_signal: str, big_long: float,
    hurst: float, kappa: float, harv: float,
    entry_lo: float, entry_hi: float,
    liq_up: float, liq_dn: float,
    macro_bias: str, fear_greed: int,
) -> dict:
    """
    AI议会三方独立投票（Groq三路并发）
    宏观裁判 / 结构裁判 / 量化裁判 独立调用
    """
    import concurrent.futures as _cf

    base = f"{sym}/USDT ${price:,.0f} {regime}体制(score={score:.0f})"

    prompts = {
        'macro': (
            f"{base}\n宏观裁判：FR={fear_greed}恐贪 DXY={macro_bias}\n"
            f"只输出 LONG / SHORT / WAIT，一个词"
        ),
        'structure': (
            f"{base}\nFVG={fvg_dir}磁铁${fvg_magnet:,.0f} "
            f"清算上=${liq_up:,.0f}下=${liq_dn:,.0f}\n"
            f"只输出 LONG / SHORT / WAIT，一个词"
        ),
        'quant': (
            f"{base}\nOI={oi_signal} 聪明钱={sm_signal} "
            f"大户多头={big_long:.1f}% Hurst={hurst:.3f}\n"
            f"只输出 LONG / SHORT / WAIT，一个词"
        ),
    }

    # [封印 2026-10-22 苏摩111] council真实AI推理+RULE_FALLBACK明确标注
    # 修复：Groq/OR全在backoff时走规则，但需明确标注RULE_FALLBACK，不能假装AI推理
    groq_live = time.time() >= _groq_backoff_until
    or_live   = time.time() >= _or_backoff_until
    free_available = groq_live or or_live
    _is_rule_fallback = False

    votes = {}
    if not free_available:
        # 规则快速判断（<1ms，不走subprocess）— 明确标注RULE_FALLBACK
        _is_rule_fallback = True
        verdict = 'WAIT'
        if oi_signal == 'LONG_BUILD' and fvg_dir == 'BULL': verdict = 'LONG'
        elif oi_signal == 'SHORT_BUILD' and fvg_dir == 'BEAR': verdict = 'SHORT'
        votes = {'macro': verdict, 'structure': verdict, 'quant': verdict}
    else:
        # 真实AI推理：若任意一路返回非WAIT，说明AI真实响应
        ai_responded = False
        with _cf.ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(chat, p, task='council', max_tokens=10): role
                       for role, p in prompts.items()}
            for fut in _cf.as_completed(futures):
                role = futures[fut]
                try:
                    v = (fut.result() or 'WAIT').strip().upper()
                    v = v if v in ('LONG', 'SHORT', 'WAIT') else 'WAIT'
                    votes[role] = v
                    if v != 'WAIT': ai_responded = True
                except Exception:
                    votes[role] = 'WAIT'
        # 若三路全WAIT且AI未真实响应，标注为RULE_FALLBACK
        if not ai_responded:
            _is_rule_fallback = True

    # 多数投票
    from collections import Counter
    counts = Counter(votes.values())
    winner = counts.most_common(1)[0][0]
    agree  = counts[winner]
    # [封印 2026-10-22 苏摩111] 用_is_rule_fallback精确判断，而非free_available
    conf = ('RULE_FALLBACK' if _is_rule_fallback
            else 'HIGH' if agree == 3
            else ('MED' if agree == 2 else 'LOW'))

    return {
        'verdict': winner,
        'confidence': conf,
        'votes': votes,
        'agree': agree,
    }
