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
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

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
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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
                except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
        except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
    out['today_trades'] = today_trades[:10]

    # CVD实时信号
    for sym in ['btcusdt', 'ethusdt']:
        p = DATA / f'cvd_realtime_{sym}.json'
        if p.exists():
            try:
                d = json.loads(p.read_text())
                out[f'cvd_{sym[:3].upper()}'] = d.get('signal', '?')
            except Exception as _e: print(f'[WARN] {__name__}: {_e}', file=sys.stderr)
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

        # [C-3蒸馏 苏摩111] 议会教训包注入（零API成本，本地工件）
        # 接入位置：scripts/learning_loop.py council_context() → daily_review prompt
        _council_ctx = ''
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from learning_loop import council_context as _cc
            _council_ctx = _cc()
        except Exception:
            _council_ctx = ''

        prompt = (
            f"今日({ts})梵天系统复盘数据：\n"
            f"BTC: 收盘${btc.get('price',0):,.0f} 体制={btc.get('regime','?')} score={btc.get('score',0):.0f}\n"
            f"ETH: 收盘${eth.get('price',0):,.0f} 体制={eth.get('regime','?')} score={eth.get('score',0):.0f}\n"
            f"CVD收盘方向: BTC={cvd_b} ETH={cvd_e}\n"
            f"交易情况: {trade_summary}\n"
            + (f"\n{_council_ctx}\n" if _council_ctx else '')
            + "\n请用中文写一段今日复盘总结（100字内）：\n"
            f"1. 今日体制和价格趋势\n"
            f"2. 梵天系统信号质量\n"
            f"3. 明日需要关注的关键位\n"
            f"风格：直接简洁，像写日记，不要废话开场"
        )
        # [9.26接线 苏摩111] 40年交易员人格SSOT接入：brahma_brain_prompt.TRADER_SYSTEM_PROMPT
        # 接入位置：scripts/daily_review_llm.py → 每日复盘LLM system人格
        _trader_persona = ''
        try:
            _sys_path = Path(__file__).resolve().parent.parent / 'brahma_brain'
            sys.path.insert(0, str(_sys_path))
            from brahma_brain_prompt import TRADER_SYSTEM_PROMPT as _trader_persona
        except Exception:
            pass
        return chat(prompt, max_tokens=200, timeout=20, task="review", system=_trader_persona)
    except Exception as e:
        return f"LLM复盘生成失败: {e}"


def _llm_channel_down() -> bool:
    """[恢복 2026-10-10] Groq있으면 항상 채널 정상"""
    try:
        import sys as _dr; _dr.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
        from free_llm_client import GROQ_KEY
        if GROQ_KEY:
            return False  # Groq있으면 채널 정상
    except Exception:
        pass
    # fallback: 기존 체크
    try:
        from pathlib import Path as _P
        import json as _j, time as _t
        f = _P(__file__).parent.parent / 'data' / 'llm_channel_state.json'
        if not f.exists():
            return False
        d = _j.loads(f.read_text())
        bu = float(d.get('backoff_until', 0) or 0)
        return _t.time() < bu
    except Exception:
        return False

def _local_review_fallback(data: dict) -> str:
    """[9.29 P1 苏摩111] 复盘LLM双通道全灭时的本地规则复盘（不依赖任何LLM）。
    蓝图铁律4.2-4「降级路径显式」：LLM不可用→出本地降级复盘+降级标记，不静默装正常。
    数据源与LLM版完全一致（_load_today_signals），口径=四层方法论的压缩版。
    """
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    btc = data.get('BTC') or {}
    eth = data.get('ETH') or {}
    trades = data.get('today_trades', []) or []
    cvd_b = data.get('cvd_BTC', '?')
    cvd_e = data.get('cvd_ETH', '?')
    lines = [
        f"【本地降级复盘 LLM不可用】 {ts}",
        f"事实层: BTC=${btc.get('price',0):,.0f} 体制={btc.get('regime','?')} score={btc.get('score',0):.0f} CVD={cvd_b} | "
        f"ETH=${eth.get('price',0):,.2f} 体制={eth.get('regime','?')} score={eth.get('score',0):.0f} CVD={cvd_e}",
        f"交易层: 今日信号{len(trades)}条，纸面单见paper_daily_review(23:30北京)",
        f"关注层: 等待FVG磁铁+止损墙结构变化，vip卡片见auto_analysis_latest",
        f"来源: daily_review_llm本地降级路径(free_llm+master双通道均不可用)",
    ]
    return '\n'.join(lines)


def run() -> None:
    print("每日复盘LLM总结开始...", flush=True)
    data   = _load_today_signals()
    review = _generate_review(data)

    if not review or 'failed' in review.lower():
        # [9.29 P1 苏摩111] 双通道全灭→本地规则复盘兜底（降级路径显式，不再空转退出）
        review = _local_review_fallback(data)
        print("LLM双通道全灭 → 本地降级复盘已生成", flush=True)
        try:
            from push_hub import push_jarvis
            push_jarvis(
                "🤖⚠️ 每日复盘LLM双通道失败→本地降级复盘已写入（free+master均不可用，详见data/llm_channel_state.json）",
                priority='P1', dedup_key='daily_review_llm_fail', dedup_ttl=86400)
        except Exception as _e:
            print(f"告警推送失败: {_e}", file=sys.stderr)
    else:
        # 成功路径但通道24h内有失败记录 → P3提醒（退避中可能自愈）
        if _llm_channel_down():
            try:
                from push_hub import push_jarvis
                push_jarvis(
                    "🤖ℹ️ daily_review成功但LLM通道24h内有失败记录（429退避中，UTC明0点重置自愈）",
                    priority='P3', dedup_key='llm_channel_warn', dedup_ttl=86400)
            except Exception:
                pass

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
