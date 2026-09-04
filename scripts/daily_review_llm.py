#!/usr/bin/env python3
"""
daily_review_llm.py — 每日复盘LLM总结
设计院三方封印 2026-09-04 苏摩111

每天UTC16:00（北京00:00）运行：
  1. 读取当日brahma_state + capital_alloc日志
  2. LLM生成一段复盘文字
  3. 写入 memory/YYYY-MM-DD.md

接入位置: supercronic crontab (0 16 * * *)
"""
import sys, json, os
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE   = Path(__file__).parent.parent
DATA   = BASE / 'data'
MEMORY = BASE.parent.parent / 'workspace' / 'memory'  # ~/.openclaw/workspace/memory
sys.path.insert(0, str(BASE / 'scripts'))


def _load_today_signals() -> dict:
    """拉当日关键数据"""
    out = {}

    # BTC/ETH 最新state
    for sym in ['btc', 'eth']:
        p = DATA / f'brahma_state_{sym}.json'
        if p.exists():
            try:
                d = json.loads(p.read_text())
                out[sym.upper()] = {
                    'price':  d.get('price', 0),
                    'regime': d.get('regime', '?'),
                    'score':  d.get('score', 0),
                }
            except Exception:
                pass

    # 今日capital_alloc记录
    cap_path = DATA / 'capital_alloc.jsonl'
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    today_trades = []
    if cap_path.exists():
        try:
            for line in cap_path.read_text().splitlines()[-50:]:
                try:
                    d = json.loads(line)
                    if today in d.get('ts', '') or today in str(d.get('time', '')):
                        today_trades.append(d)
                except Exception:
                    pass
        except Exception:
            pass
    out['today_trades'] = today_trades[:10]

    # CVD实时信号
    for sym in ['btcusdt', 'ethusdt']:
        p = DATA / f'cvd_realtime_{sym}.json'
        if p.exists():
            try:
                d = json.loads(p.read_text())
                out[f'cvd_{sym[:3].upper()}'] = d.get('signal', '?')
            except Exception:
                pass

    return out


def _generate_review(data: dict) -> str:
    """LLM生成复盘文字"""
    try:
        from free_llm_client import chat

        ts    = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        btc   = data.get('BTC', {})
        eth   = data.get('ETH', {})
        trades = data.get('today_trades', [])
        cvd_b = data.get('cvd_BTC', '?')
        cvd_e = data.get('cvd_ETH', '?')

        trade_summary = f"{len(trades)}笔信号记录" if trades else "今日无交易记录"

        prompt = (
            f"今日({ts})梵天系统复盘数据：\n"
            f"BTC: 收盘${btc.get('price',0):,.0f} 体制={btc.get('regime','?')} score={btc.get('score',0):.0f}\n"
            f"ETH: 收盘${eth.get('price',0):,.0f} 体制={eth.get('regime','?')} score={eth.get('score',0):.0f}\n"
            f"CVD收盘方向: BTC={cvd_b} ETH={cvd_e}\n"
            f"交易情况: {trade_summary}\n\n"
            f"请用中文写一段今日复盘总结（100字内）：\n"
            f"1. 今日体制和价格趋势\n"
            f"2. 梵天系统信号质量\n"
            f"3. 明日需要关注的关键位\n"
            f"风格：直接简洁，像写日记，不要废话开场"
        )
        return chat(prompt, max_tokens=200, timeout=20)
    except Exception as e:
        return f"LLM复盘生成失败: {e}"


def run() -> None:
    print("每日复盘LLM总结开始...", flush=True)
    data   = _load_today_signals()
    review = _generate_review(data)

    if not review or 'failed' in review.lower():
        print("复盘生成失败，退出")
        return

    # 写入memory文件
    today  = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    MEMORY.mkdir(exist_ok=True)
    mem_file = MEMORY / f'{today}.md'

    # 追加到今日memory文件
    header = f"\n\n## 梵天每日复盘 {today}\n\n"
    entry  = header + review.strip() + '\n'

    if mem_file.exists():
        with open(mem_file, 'a') as f:
            f.write(entry)
    else:
        mem_file.write_text(f"# {today} 梵天日志\n" + entry)

    print(f"✅ 复盘已写入: {mem_file}")
    print(f"内容:\n{review}")


if __name__ == '__main__':
    run()
