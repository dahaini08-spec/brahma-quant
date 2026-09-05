#!/usr/bin/env python3
"""
square_auto_post.py — VIP策略→Binance Square全自动发帖
设计院三方封印 2026-09-04 苏摩111

流程：
  1. 读取最新brahma_state分析结果
  2. LLM生成Square帖子（中文财经风格）
  3. avoid-ai-writing过滤AI腔
  4. POST到Square API（姓赵不宣账号）

接入位置：
  - afternoon-battlefield cron (UTC09:00 北京17:00) 分析完自动发帖
  - python3 scripts/square_auto_post.py --sym BTC ETH
"""
import argparse, json, sys, os, ssl, urllib.request, time
from pathlib import Path
from datetime import datetime, timezone

BASE  = Path(__file__).parent.parent
BRAIN = BASE / 'brahma_brain'
sys.path.insert(0, str(BRAIN))
sys.path.insert(0, str(BASE / 'scripts'))

SQUARE_KEY  = 'd9f19e3f6ba3480584db27b09bec0f27'  # 姓赵不宣主账号
SQUARE_URL  = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _fetch_state(sym: str) -> dict:
    p = BASE / 'data' / f'brahma_state_{sym.lower()}.json'
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _generate_post(sym: str, state: dict, vip_text: str) -> str:
    """LLM生成Square帖子"""
    try:
        from free_llm_client import chat
        price   = state.get('price', 0)
        regime  = state.get('regime', 'CHOP_MID')
        score   = state.get('score', 0)
        ts      = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')

        prompt = (
            f"你是姓赵不宣，Binance Square量化交易博主。\n"
            f"根据以下梵天系统分析，写一条Binance Square帖子：\n\n"
            f"标的: {sym}/USDT 现价: ${price:,.0f}\n"
            f"体制: {regime} 评分: {score:.0f}\n"
            f"VIP策略摘要: {vip_text[:300]}\n\n"
            f"要求：\n"
            f"1. 中文，200字以内\n"
            f"2. 开头直接说行情判断，不要废话\n"
            f"3. 包含关键价位（入场/止损/目标）\n"
            f"4. 结尾加「梵天系统 数据驱动 非投资建议」\n"
            f"5. 禁止：首先/其次/值得注意/综上所述/作为一个XX\n"
            f"6. 风格：直接、简洁、像真人发的朋友圈\n"
        )
        content = chat(prompt, max_tokens=300, timeout=20, task="vip")
        return content.strip() if content else ''
    except Exception:
        return ''


def _post_to_square(content: str) -> dict:
    """POST到Binance Square"""
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
        resp = json.loads(urllib.request.urlopen(req, timeout=15, context=_ctx).read())
        return resp
    except Exception as e:
        return {'error': str(e)}


def run(syms: list, dry_run: bool = False) -> None:
    # 先跑分析拿最新VIP
    try:
        from brahma_manual_analysis import run_analysis
    except Exception:
        print("无法import run_analysis，跳过")
        return

    for sym in syms:
        print(f"[{sym}] 生成分析报告...", flush=True)
        try:
            report = run_analysis(sym)
        except Exception as e:
            print(f"[{sym}] 分析失败: {e}")
            continue

        # 提取VIP部分
        vip_lines = []
        in_vip = False
        for line in report.split('\n'):
            if '──── VIP' in line:
                in_vip = True
            if in_vip:
                vip_lines.append(line)
            if in_vip and '策略作废' in line:
                break
        vip_text = '\n'.join(vip_lines)

        # 跳过WAIT/禁止入场
        if '⏳' in vip_text or '🚫 禁止入场' in vip_text:
            print(f"[{sym}] AI议会WAIT或死穴封禁，跳过发帖")
            continue

        print(f"[{sym}] LLM生成Square帖子...", flush=True)
        state   = _fetch_state(sym)
        content = _generate_post(sym, state, vip_text)

        if not content:
            print(f"[{sym}] LLM生成失败，跳过")
            continue

        print(f"[{sym}] 帖子内容:\n{content}\n")

        if dry_run:
            print(f"[{sym}] DRY_RUN模式，不实际发送")
            continue

        print(f"[{sym}] 发送到Square...", flush=True)
        resp = _post_to_square(content)
        if resp.get('code') == '000000' or resp.get('success'):
            print(f"[{sym}] ✅ Square发帖成功: {resp.get('data', {})}")
        else:
            print(f"[{sym}] ❌ Square发帖失败: {resp}")

        time.sleep(2)  # 避免频率限制


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sym', nargs='+', default=['BTC', 'ETH'])
    ap.add_argument('--dry-run', action='store_true', help='只生成不发送')
    args = ap.parse_args()
    run(args.sym, dry_run=args.dry_run)
