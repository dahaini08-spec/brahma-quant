#!/usr/bin/env python3
"""
brahma_news_bureau.py — 梵天新闻局统一调度器 v1.0
[设计院封印 2026-08-14 苏摩111]

职责：所有梵天发帖的唯一入口
  - 帖型路由
  - 三道门审核（技术合规 + 结构完整 + 品牌声音）
  - TradFi品种差异化处理
  - 发布 + 日志

用法：
  python3 scripts/brahma_news_bureau.py --type full_analysis --symbol BTCUSDT
  python3 scripts/brahma_news_bureau.py --type battle_record --symbol TUTUSDT --pnl 239
  python3 scripts/brahma_news_bureau.py --type tradfi_insight --symbol SNDKUSDT
  python3 scripts/brahma_news_bureau.py --type hot_tickers
  python3 scripts/brahma_news_bureau.py --dry-run --type full_analysis --symbol ETHUSDT
  python3 scripts/brahma_news_bureau.py --audit   # 展示最近5条+审核状态
"""

import argparse
import json
import sys
import time
import hashlib
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone

# ── 路径 ──────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / 'data'
DOCS_DIR   = BASE_DIR / 'docs'
LOG_FILE   = DATA_DIR / 'square_post_log.jsonl'
DEDUP_FILE = DATA_DIR / 'square_post_dedup.json'

# ── Square API ────────────────────────────────────────────────────
SQUARE_API = 'https://www.binance.com/bapi/composite/v1/public/pgc/openApi/content/add'
SQUARE_KEYS = {
    'A': 'd9f19e3f6ba3480584db27b09bec0f27',   # 主账户 姓赵不宣
    'B': 'c43ebad6a8434d1b91a039dbf43fda29',
    'C': '278f3e81efda4274a1d8e15dbc32ec88',
}

# ── 帖型→账户路由 ─────────────────────────────────────────────────
POST_TYPE_ACCOUNT = {
    'full_analysis':   'A',
    'battle_record':   'A',
    'tradfi_insight':  'A',
    'hot_tickers':     'A',
    'top_gainers':     'A',
    'top_losers':      'A',
    'market_summary':  'A',
    'funding_rate':    'B',
    'smart_money':     'B',
    'pump_alert':      'A',
    'hot_news':        'A',
    'edu':             'C',
}

# ── 三道门审核 ─────────────────────────────────────────────────────

# Gate-1: 技术合规
BLOCKED_WORDS = [
    'BEAR_TREND', 'CHOP_MID', 'BULL_TREND', 'BEAR_EARLY', 'BEAR_RECOVERY',
    'DD1', '仅供内部', '新浪财经',
]
BLOCKED_PUNCT = ['\uff01']  # 全角感叹号！

# Gate-3: 品牌声音禁词（AI风）
AI_WORDS = [
    '系统给出', '引擎', '35个维度', '多模型', 'AI分析', 'AI模型',
    '模型输出', '机器学习', '深度学习', '神经网络', '算法给出',
    '量化体制', '体制识别', '评分矩阵', '信号分析器',
]


def gate1_tech(content: str) -> tuple[bool, list]:
    """Gate-1：技术合规（字数/感叹号/体制代码）"""
    errors = []
    n = len(content)
    if n < 30:
        errors.append(f'字数不足({n}<30)')
    if n > 2000:
        errors.append(f'字数超限({n}>2000)')
    for p in BLOCKED_PUNCT:
        if p in content:
            errors.append(f'含全角感叹号（广场违规）')
    for w in BLOCKED_WORDS:
        if w in content:
            errors.append(f'含体制代码: {w}')
    return len(errors) == 0, errors


def gate2_structure(content: str, post_type: str) -> tuple[bool, list]:
    """Gate-2：结构完整性（价格/入场/止损/目标）"""
    errors = []
    # 操作性帖型必须有完整结构
    op_types = {'full_analysis', 'tradfi_insight', 'hot_tickers',
                'top_gainers', 'top_losers', 'funding_rate', 'pump_alert'}
    if post_type in op_types:
        if '$' not in content:
            errors.append('缺价格数字($)')
    # 战绩帖和全分析帖必须有价位
    if post_type in {'full_analysis', 'battle_record'}:
        if not any(x in content for x in ['止损', '保护', 'SL', 'stop']):
            errors.append('缺止损信息')
    return len(errors) == 0, errors


def gate3_brand(content: str) -> tuple[bool, list]:
    """Gate-3：品牌声音（无AI风词）"""
    errors = []
    for w in AI_WORDS:
        if w in content:
            errors.append(f'含AI风格词: {w}')
    return len(errors) == 0, errors


def run_three_gates(content: str, post_type: str) -> tuple[bool, dict]:
    """运行三道门，返回(全部通过, 详细结果)"""
    g1_ok, g1_err = gate1_tech(content)
    g2_ok, g2_err = gate2_structure(content, post_type)
    g3_ok, g3_err = gate3_brand(content)
    all_ok = g1_ok and g2_ok and g3_ok
    return all_ok, {
        'gate1': {'ok': g1_ok, 'errors': g1_err},
        'gate2': {'ok': g2_ok, 'errors': g2_err},
        'gate3': {'ok': g3_ok, 'errors': g3_err},
        'chars': len(content),
    }


def is_duplicate(content: str) -> bool:
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        now = time.time()
        d = {k: v for k, v in d.items() if now - v < 86400}
        return h in d
    except Exception:
        return False


def mark_posted(content: str):
    h = hashlib.md5(content.encode()).hexdigest()[:12]
    try:
        d = json.loads(DEDUP_FILE.read_text()) if DEDUP_FILE.exists() else {}
        now = time.time()
        d = {k: v for k, v in d.items() if now - v < 86400}
        d[h] = now
        DEDUP_FILE.write_text(json.dumps(d))
    except Exception:
        pass


# ── TradFi品种检查 ─────────────────────────────────────────────────

def check_tradfi_timing(symbol: str) -> tuple[bool, str]:
    """
    TradFi品种时段检查。
    返回 (可发帖, 警告信息)
    """
    try:
        sys.path.insert(0, str(BASE_DIR))
        from brahma_brain.tradfi_router import classify, get_session
        cls = classify(symbol.upper() if 'USDT' in symbol.upper()
                       else symbol.upper() + 'USDT')
        if cls == 'CRYPTO':
            return True, ''
        sess = get_session()
        # A类亚盘禁止发操作性帖子
        if cls == 'A' and sess['session'] == 'ASIA':
            return False, (
                f'[铁律1] {symbol} 属于TradFi-A类，当前亚盘时段，'
                f'不发布操作建议（UTC {sess["utc_min"]//60:02d}:{sess["utc_min"]%60:02d}）'
            )
        if cls == 'A' and not sess['us_open']:
            return True, f'⚠️ TradFi-A类非交易时段，帖子需注明「美股开市后有效」'
        return True, f'TradFi-{cls}类 时段={sess["session"]} ✅'
    except Exception:
        return True, ''


# ── 发布 ──────────────────────────────────────────────────────────

def publish(content: str, post_type: str, symbol: str = '',
            dry_run: bool = False) -> dict:
    """
    统一发布入口。
    返回 {'ok': bool, 'post_id': str, 'gates': dict, 'reason': str}
    """
    # 三道门
    all_ok, gates = run_three_gates(content, post_type)
    if not all_ok:
        errs = []
        for g, v in gates.items():
            if g.startswith('gate') and not v['ok']:
                errs.extend(v['errors'])
        return {'ok': False, 'post_id': '', 'gates': gates,
                'reason': '三道门未通过: ' + ' | '.join(errs)}

    # TradFi时段检查
    if symbol:
        timing_ok, timing_msg = check_tradfi_timing(symbol)
        if not timing_ok:
            return {'ok': False, 'post_id': '', 'gates': gates,
                    'reason': timing_msg}

    # 去重
    if is_duplicate(content):
        return {'ok': False, 'post_id': '', 'gates': gates,
                'reason': '24H内重复内容'}

    # 选账户
    account_key = POST_TYPE_ACCOUNT.get(post_type, 'A')
    api_key = SQUARE_KEYS[account_key]

    if dry_run:
        print(f'\n[DRY-RUN] 账户={account_key} 帖型={post_type}')
        print(f'[DRY-RUN] 字数={len(content)} 三道门=✅')
        print('─' * 50)
        print(content)
        print('─' * 50)
        return {'ok': True, 'post_id': 'DRY_RUN', 'gates': gates, 'reason': ''}

    try:
        resp = requests.post(
            SQUARE_API,
            headers={
                'X-Square-OpenAPI-Key': api_key,
                'Content-Type': 'application/json',
                'clienttype': 'binanceSkill',
            },
            json={'bodyTextOnly': content},
            timeout=20,
        )
        result = resp.json()
        if result.get('code') == '000000':
            post_id = str(result.get('data', {}).get('id', ''))
            mark_posted(content)
            # 写日志
            DATA_DIR.mkdir(exist_ok=True)
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    'ts': time.time(),
                    'post_type': post_type,
                    'symbol': symbol,
                    'account': account_key,
                    'post_id': post_id,
                    'chars': len(content),
                    'preview': content[:60],
                }, ensure_ascii=False) + '\n')
            return {'ok': True, 'post_id': post_id, 'gates': gates, 'reason': ''}
        else:
            return {'ok': False, 'post_id': '', 'gates': gates,
                    'reason': f'API错误: {result.get("message", result)}'}
    except Exception as e:
        return {'ok': False, 'post_id': '', 'gates': gates, 'reason': str(e)}


# ── 审计模式 ──────────────────────────────────────────────────────

def audit_mode():
    """展示最近5条发帖记录+审核状态"""
    print('\n🏛️ 梵天新闻局 — 内容审计')
    print('=' * 55)

    if not LOG_FILE.exists():
        print('暂无发帖记录')
        return

    lines = LOG_FILE.read_text().strip().split('\n')
    recent = lines[-5:]

    print(f'总发帖数: {len(lines)} 条\n')
    for i, line in enumerate(recent, 1):
        try:
            d = json.loads(line)
            ts = datetime.fromtimestamp(d['ts']).strftime('%m-%d %H:%M')
            print(f'[{i}] {ts} | 帖型={d.get("post_type","?")} | '
                  f'账户={d.get("account","A")} | 字数={d.get("chars","?")}')
            print(f'     预览: {d.get("preview","")[:60]}')
        except Exception:
            pass

    print('\n─ 三道门规则速查 ─')
    print('Gate-1 技术合规: 字数30~500 | 无全角！| 无体制代码')
    print('Gate-2 结构完整: 操作帖必须含$价格 | 全分析帖含止损')
    print('Gate-3 品牌声音: 无AI风格词 | 第一人称 | 不吹嘘胜率')

    # SOP文档位置
    sop = DOCS_DIR / 'brahma_news_bureau_sop.md'
    if sop.exists():
        print(f'\nSOP文档: {sop}')


# ── 代理帖型路由（调用现有模块）────────────────────────────────────

def route_post_type(post_type: str, symbol: str = '', **kwargs) -> str:
    """路由到对应帖型生成器，返回内容字符串"""
    try:
        sys.path.insert(0, str(BASE_DIR))

        if post_type in ('hot_tickers', 'top_gainers', 'top_losers',
                         'market_summary', 'funding_rate', 'smart_money',
                         'pump_alert', 'hot_news', 'edu'):
            from scripts.square.square_hot_poster import (
                build_hot_tickers, build_top_gainers, build_top_losers,
                build_market_summary, build_funding_rate, build_smart_money,
                build_pump_alert, build_hot_news, build_education,
            )
            fn_map = {
                'hot_tickers':   build_hot_tickers,
                'top_gainers':   build_top_gainers,
                'top_losers':    build_top_losers,
                'market_summary': build_market_summary,
                'funding_rate':  build_funding_rate,
                'smart_money':   build_smart_money,
                'pump_alert':    build_pump_alert,
                'hot_news':      build_hot_news,
                'edu':           build_education,
            }
            return fn_map[post_type]() or ''

        # 其他帖型需要苏摩提供内容
        return ''

    except Exception as e:
        print(f'[route] 生成失败: {e}', file=sys.stderr)
        return ''


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='梵天新闻局统一发帖调度器')
    parser.add_argument('--type',    default='hot_tickers',
                        help='帖型: hot_tickers/top_gainers/top_losers/market_summary/...')
    parser.add_argument('--symbol',  default='', help='标的符号, 如 BTCUSDT')
    parser.add_argument('--content', default='', help='直接传入帖子内容（跳过自动生成）')
    parser.add_argument('--dry-run', action='store_true', help='预览不发布')
    parser.add_argument('--audit',   action='store_true', help='审计模式')
    parser.add_argument('--pnl',     default='', help='战绩帖盈利百分比')
    args = parser.parse_args()

    if args.audit:
        audit_mode()
        return

    # 生成内容
    content = args.content
    if not content:
        content = route_post_type(args.type, symbol=args.symbol, pnl=args.pnl)

    if not content:
        print(f'[bureau] 帖型 {args.type} 需要传入 --content 或由苏摩提供内容')
        sys.exit(1)

    # 发布
    result = publish(content, args.type, symbol=args.symbol, dry_run=args.dry_run)

    if result['ok']:
        pid = result['post_id']
        if pid == 'DRY_RUN':
            print('[bureau] DRY-RUN完成')
        else:
            print(f'[bureau] ✅ 发布成功 post_id={pid}')
            if pid:
                print(f'https://app.binance.com/uni-qr/cpos/{pid}?r=CRDOE41X&l=zh-CN')
    else:
        print(f'[bureau] ❌ 发布失败: {result["reason"]}')
        # 显示三道门详情
        for g, v in result['gates'].items():
            if g.startswith('gate') and not v['ok']:
                print(f'  {g}: {v["errors"]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
