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
OR_MODEL   = 'xingchen-agi/xing4.0-29b-a4b:free'
OR_API_URL = 'https://openrouter.ai/api/v1/chat/completions'

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
            _groq_backoff_until = time.time() + 300
            print('[groq] 429 backoff 5min', file=sys.stderr)
        elif code in (402, 401):
            print(f'[groq] {code} key问题', file=sys.stderr)
        return ''

# ── OpenRouter备用 ────────────────────────────────────────────────────────

_or_backoff_until = 0.0

def _or_chat(messages: list, max_tokens: int = 200, timeout: int = 25) -> str:
    """OpenRouter备用通道：xing4.0-29b，Groq故障时接管"""
    global _or_backoff_until
    if not OR_KEY:
        return ''
    if time.time() < _or_backoff_until:
        return ''
    try:
        payload = json.dumps({
            'model': OR_MODEL,
            'messages': messages,
            'max_tokens': max_tokens,
            'temperature': 0.2,
        }).encode()
        req = urllib.request.Request(OR_API_URL, data=payload, headers={
            'Authorization': f'Bearer {OR_KEY}',
            'Content-Type': 'application/json',
        })
        kw = {'timeout': timeout}
        if _ctx: kw['context'] = _ctx
        with urllib.request.urlopen(req, **kw) as r:
            resp = json.loads(r.read())
        choices = resp.get('choices') or []
        if not choices:
            return ''
        content = (choices[0].get('message') or {}).get('content', '') or ''
        return content.strip()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _or_backoff_until = time.time() + 300
            print('[or] 429 backoff 5min', file=sys.stderr)
        return ''
    except Exception:
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
    三层路由: Groq(0.24s) → OR备用 → 主AI兜底
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

    # 3. 主AI兜底
    print(f'[llm] 免费池全灭，主AI接管 task={task}', file=sys.stderr)
    result = _master_chat(messages, max_tokens, timeout)
    if result:
        _mark_master_success(task)
        return result

    return ''

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

    votes = {}
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(chat, p, task='council', max_tokens=10): role
                   for role, p in prompts.items()}
        for fut in _cf.as_completed(futures):
            role = futures[fut]
            try:
                v = (fut.result() or 'WAIT').strip().upper()
                v = v if v in ('LONG', 'SHORT', 'WAIT') else 'WAIT'
                votes[role] = v
            except Exception:
                votes[role] = 'WAIT'

    # 多数投票
    from collections import Counter
    counts = Counter(votes.values())
    winner = counts.most_common(1)[0][0]
    agree  = counts[winner]
    conf   = 'HIGH' if agree == 3 else ('MED' if agree == 2 else 'LOW')

    return {
        'verdict': winner,
        'confidence': conf,
        'votes': votes,
        'agree': agree,
    }
