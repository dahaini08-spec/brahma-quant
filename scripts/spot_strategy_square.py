#!/usr/bin/env python3
"""
spot_strategy_square.py — 拳头二：现货策略→Square发帖+Jarvis推送
设计院三方封印 2026-09-10 苏摩111
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import sys, os, json, ssl, urllib.request, time, subprocess, re
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))

SQUARE_KEY = 'd9f19e3f6ba3480584db27b09bec0f27'
SQUARE_URL = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
JARVIS_TO = '73295708:thread:01a0d79b-fea4-71b1-9f2a-c02a9844b4ed'
OPENROUTER_KEY = os.environ.get('OPENROUTER_API_KEY', '')
# 从free_llm_client读取API key
try:
    from free_llm_client import API_KEY
    OPENROUTER_KEY = API_KEY
except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
_ctx = ssl.create_default_context()
_ctx.check_hostname = True
_ctx.verify_mode = ssl.CERT_REQUIRED

BLOCKED_WORDS = [
    'BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY',
    '梵天', 'FVG', 'Kronos', '体制识别',
    'brahma', 'brahma_', '量化系统', '量化引擎',
]


def _load_last_card_state(sym: str) -> dict:
    """读取上次发布的卡片关键数字 [P0-3 2026-09-27 接入位置: spot_strategy_square.py run()]"""
    path = BASE / 'data' / f'spot_card_last_{sym}.json'
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _should_post(sym: str, rec: dict) -> tuple:
    """P0-3降频门控：数字变动>2%才发（复盘实锤：BTC/ETH卡片一字不差重发=重复内容降权+读者拿旧信息）

    规则：
    - 首次发：放行
    - entry_lo/entry_hi/sl/tp1/tp2 任一变动>2%：放行并更新快照
    - bias变化：放行并更新快照
    - 其余：跳过
    """
    last = _load_last_card_state(sym)
    if not last:
        return True, '首次发布'

    # bias变化优先
    if last.get('bias') != rec.get('bias'):
        return True, f"bias变化 {last.get('bias')}→{rec.get('bias')}"

    # 关键数字变动>2%
    for k in ('entry_lo', 'entry_hi', 'sl', 'tp1', 'tp2'):
        old_v, new_v = last.get(k), rec.get(k)
        if old_v is None or new_v is None:
            continue
        try:
            old_f, new_f = float(old_v), float(new_v)
        except (TypeError, ValueError):
            continue
        if old_f and abs(new_f - old_f) / old_f * 100 > 2.0:
            return True, f'{k}变动{abs(new_f-old_f)/old_f*100:.1f}%'

    return False, '数字变动<2%，24h内不发'


def run_spot(symbol: str) -> dict:
    subprocess.run(
        ['python3', str(BASE / 'scripts' / 'spot_strategy_runner.py'),
         '--symbol', symbol, '--quiet'],
        capture_output=True, text=True, timeout=30, cwd=str(BASE)
    )
    cache = BASE / 'data' / f'spot_strategy_{symbol}USDT.json'
    if cache.exists():
        return json.loads(cache.read_text())
    return {}


def rewrite_for_square(draft: str) -> str:
    """[恢复 2026-10-10 苏摩111] Groq免费，重新启用LLM重写"""
    if not draft or len(draft) < 30:
        return draft
    try:
        import sys as _sr; _sr.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
        from free_llm_client import chat as _gc
        _r = _gc(
            '用40年顶级现货交易员语气重写以下分析帖，保留数字，去掉废话，禁止AI腔：\n\n' + draft[:600],
            max_tokens=500, task='vip', timeout=20
        )
        if _r and len(_r) > 60:
            return _r.strip()
    except Exception:
        pass
    return draft

    # ── 品牌包装（2026-09-12 苏摩111封印）──
    BRAND_PREFIX = ''
    BRAND_SUFFIX = '🌿 姓赵不宣 | 不是建议'
    # 顶端不加前缀
    if '姓赵不宣' not in draft:
        draft = f'{draft}\n\n{BRAND_SUFFIX}'
    return draft
    # ── 以下LLM重写已废弃 ──
    if not OPENROUTER_KEY:
        return draft
    try:
        prompt = (
            f'用40年顶级交易员视角重写以下现货策略帖。'
            f'保留所有数字，禁止Markdown格式，禁止AI腔，'
            f'直接输出重写结果，不要解释：\n\n{draft}'
        )
        payload = json.dumps({
            'model': 'meta-llama/llama-3.3-70b-instruct:free',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 400,
            'temperature': 0.2,
        }).encode()
        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions', data=payload,
            headers={
                'Authorization': f'Bearer {OPENROUTER_KEY}',
                'Content-Type': 'application/json',
            },
        )
        resp = json.loads(urllib.request.urlopen(req, timeout=20, context=_ctx).read())
        result = resp.get('choices', [{}])[0].get('message', {}).get('content', '')
        if not result or len(result) < 80:
            return draft
        for w in BLOCKED_WORDS:
            result = result.replace(w, '')
        result = re.sub(r'\*\*(.+?)\*\*', r'【\1】', result)
        result = re.sub(r'\*(.+?)\*', r'\1', result)
        result = re.sub(r'^#{1,6}\s+', '', result, flags=re.MULTILINE)
        return result.strip()
    except Exception:
        return draft


def post_to_square(content: str) -> dict:
    payload = json.dumps({'bodyTextOnly': content}).encode()
    req = urllib.request.Request(
        SQUARE_URL, data=payload,
        headers={
            'X-Square-OpenAPI-Key': SQUARE_KEY,
            'Content-Type': 'application/json',
            'clienttype': 'binanceSkill',
        },
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
    except Exception as e:
        return {'error': str(e)}


def push_jarvis(message: str):
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', JARVIS_TO,
             '--message', message],
            capture_output=True, text=True, timeout=10
        )
    except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
def run(symbols: list, dry_run: bool = False):
    for sym in symbols:
        print(f'[{sym}] 运行现货策略...', flush=True)
        rec = run_spot(sym)
        if not rec:
            print(f'[{sym}] 策略生成失败，跳过')
            continue

        card = rec.get('card', '')
        if not card or len(card) < 50:
            print(f'[{sym}] 策略内容为空，跳过')
            continue

        # [P0-3 2026-09-27] 降频门控：数字变动>2%才发
        should_post, reason = _should_post(sym, rec)
        print(f'[{sym}] 降频门控: {reason}')
        if not should_post:
            continue

        print(f'[{sym}] LLM重写...', flush=True)
        content = rewrite_for_square(card)

        if dry_run:
            print(f'[DRY-RUN] {sym} 内容预览:')
            print('-' * 50)
            print(content[:400])
            print('-' * 50)
            continue

        print(f'[{sym}] 发送Square...', flush=True)
        result = post_to_square(content)
        if result.get('code') == '000000':
            post_id = result.get('data', {}).get('id', '')
            print(f'[{sym}] ✅ Square发布成功 id={post_id}')
            push_jarvis(f'📢 拳头二现货策略发帖成功\n\n{sym} id={post_id}')
            # [P0-3] 更新已发布快照
            snap = {k: rec.get(k) for k in ('bias', 'entry_lo', 'entry_hi', 'sl', 'tp1', 'tp2')}
            snap['ts'] = rec.get('ts', '')
            (BASE / 'data' / f'spot_card_last_{sym}.json').write_text(
                json.dumps(snap, ensure_ascii=False, indent=2))
        else:
            print(f'[{sym}] ❌ Square发布失败: {result}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='+', default=['BTC', 'ETH'])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args.symbols, dry_run=args.dry_run)
