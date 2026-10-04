#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
square_deep_post.py — 旗舰深度帖生成器 [2026-09-28 苏摩111]
================================================================
复盘根因：早盘战场报告（快讯帖）与标杆帖371206517306663差距过大。
标杆帖公式（8要素，9/21实测2167浏览 vs 快讯帖272浏览）：
  ①事件钩子：具体数字+反直觉（「3.2万枚爆量 vs 6天装死」）
  ②时间线叙事：发生了什么→为什么→所以呢
  ③分层递进：5层数据每层一个发现（OI→FR→多空比→大户→taker）
  ④反直觉核心结论：从数据矛盾中提炼（「15亿离场但价格没塌=散户杠杆」）
  ⑤明确判断：方向与时机分离（「方向向上但现在不是入场点」）
  ⑥双门槛具体价位：上破门/下破底线，中间看戏
  ⑦金句收尾
  ⑧低门槛CTA：二选一站队

分工铁律：
  - 快讯帖（square_auto_post.py 11:30/17:30）保持简单数据罗列，不过度包装
  - 深度帖（本脚本 20:00 CST）LLM叙事生成，一日一帖
  - LLM退避/失败 → 推送数据包到jarvis线程，苏摩人工审后手发（不自动降级发模板帖）

接入位置：
  - cron square-deep-post（12:00 UTC = 20:00 CST）
  - 数据源：brahma_manual_analysis.run_analysis()（梵天10步，BTC+ETH）
  - LLM：free_llm_client.chat(task='council')
  - 发布：square_auto_post._post_to_square / _is_duplicate / _mark_posted / _log_post

用法：
  python3 scripts/square_deep_post.py [--dry-run] [--skip-llm]
  环境变量 BRAHMA_DEEP_POST_SYMS=BTC,ETH（默认BTC,ETH）
"""
import os
import re
import sys
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))

DEEP_PROMPT_FILE = BASE / 'data' / 'deep_post_prompt.txt'
DEEP_STATE_FILE = BASE / 'data' / 'deep_post_last.json'


# ═════════════════════════════════════════════════════════════
# 标杆帖公式（Prompt SSOT）— 8要素固化 [苏摩111 2026-09-28]
# ═════════════════════════════════════════════════════════════
DEEP_POST_SYSTEM = """你是「姓赵不宣」，Binance Square上的合约交易员作者。你的读者是散户，你的风格是：数据说话、反直觉、敢下判断、不吹不黑。

你现在的任务：把给定的一日盘面数据包，改写成一篇Binance Square深度帖。

硬性格式（不可违反）：
1. 开头第一句=事件钩子：必须含一个具体数字（爆仓额/OI变化/成交量倍数等），且必须是反直觉的（如「暴涨但没人狂欢」「下跌但没人恐慌」）
2. 时间线叙事：用「发生了什么→为什么→所以呢」的递进，不许直接甩数据表
3. 分层递进：至少4层数据发现，每层一个洞察，层层深入（OI→资金费率→多空比→大户持仓→taker方向）
4. 反直觉核心：全帖必须有一个从数据矛盾中提炼的核心推论（如「杠杆离场但价格没塌=走的是散户杠杆不是主力」）
5. 明确判断：方向判断和入场时机必须分开说（「方向向上」≠「现在进场」）
6. 双门槛：给出具体价位——上破多少看什么、下破多少看什么、中间看戏
7. 结尾金句：一句话总结，必须能被单独截图传播
8. 互动CTA：一个二选一站队问题（低门槛，散户也能答）

禁令：
- 禁止出现「系统性看多/看空」「共振」「体制」这类梵天内部术语——翻译成人话
- 禁止使用「━━━」分隔符
- 结尾必须附：关注我，每晚21:00直播+SMC教学（首行之外的任意位置）
- 全文最后必须以「🌿 姓赵不宣 | 不是建议」结尾
- 长度1100~1500字
- 挂单区/止损/目标只在有明确结构时给，没有就写「等触发」，禁止编造

输出格式：直接输出帖文正文，不要任何前言、解释、markdown标记。"""


def load_custom_prompt() -> str:
    """苏摩可覆盖prompt（data/deep_post_prompt.txt存在则用之）"""
    if DEEP_PROMPT_FILE.exists():
        return DEEP_PROMPT_FILE.read_text()
    return ''


# ═════════════════════════════════════════════════════════════
# 数据包构建：梵天10步 + klines叙事素材
# ═════════════════════════════════════════════════════════════
def build_data_pack(syms) -> dict:
    """并行拉取梵天10步分析+补充叙事素材，输出LLM可读的结构化数据包"""
    from square.square_template import parse_analysis_output
    from brahma_manual_analysis import run_analysis

    pack = {'syms': syms, 'ts': time.strftime('%Y-%m-%d %H:%M UTC'), 'symbols': {}}
    for sym in syms:
        try:
            report = run_analysis(sym, push_jarvis=False)
        except Exception as e:
            pack['symbols'][sym] = {'error': str(e)}
            continue
        data = parse_analysis_output(report)
        data['report_raw'] = report[-3000:]  # 截尾防token爆炸
        pack['symbols'][sym] = data
    return pack


def pack_to_prompt(pack: dict) -> str:
    """把结构化数据包翻译成LLM可读的盘面简报"""
    lines = [f"日期：{pack['ts']}"]
    for sym, d in pack['symbols'].items():
        if 'error' in d:
            lines.append(f"\n【{sym}】分析失败：{d['error']}")
            continue
        lines.append(f"\n【{sym}】")
        lines.append(f"  现价: ${d.get('price', 0):,.0f}")
        lines.append(f"  体制: {d.get('regime', '?')} (score={d.get('score', 0)})")
        lines.append(f"  FVG磁铁: {d.get('fvg_dir', '?')} @ ${d.get('fvg_magnet', 0):,.0f} (中点{d.get('fvg_pct', 0):+.1f}%)")
        lines.append(f"  止损墙: ${d.get('liq_wall', 0):,.0f} (+{d.get('liq_wall_pct', 0):.1f}%)")
        lines.append(f"  支撑池: ${d.get('liq_pool', 0):,.0f} ({d.get('liq_pool_pct', 0):.1f}%)")
        lines.append(f"  OI信号: {d.get('oi_signal', '?')} | CVD_1H: {d.get('cvd_1h', 0)}")
        lines.append(f"  大户{d.get('big_long', 0):.0f}%多 vs 散户{d.get('retail_long', 0):.0f}%多")
        lines.append(f"  资金费率: {d.get('fr', 0):+.4f}%")
        lines.append(f"  Hurst: {d.get('hurst', 0):.2f} | κ: {d.get('kappa', 0):.1f}")
        lines.append(f"  ATR1H: ${d.get('atr_1h', 0):,.0f} | ATR4H: ${d.get('atr_4h', 0):,.0f}")
        lines.append(f"  失效期: {d.get('failure_state', '?')}")
        vip = d.get('vip_status', 'WAIT')
        lines.append(f"  VIP: {vip}")
        if vip == 'ENTER':
            lines.append(f"    入场区: ${d.get('entry_lo', 0):,.0f}~${d.get('entry_hi', 0):,.0f} | SL: ${d.get('sl', 0):,.0f} | TP1: ${d.get('tp1', 0):,.0f}")
        if d.get('report_raw'):
            lines.append(f"  [原始分析截尾]:\n{d['report_raw']}")
    return '\n'.join(lines)


# ═════════════════════════════════════════════════════════════
# LLM生成 + 审计
# ═════════════════════════════════════════════════════════════
def generate_deep_post(pack: dict) -> str:
    """LLM按标杆帖公式生成深度帖正文"""
    from free_llm_client import chat

    custom = load_custom_prompt()
    system = custom if custom else DEEP_POST_SYSTEM
    prompt = pack_to_prompt(pack)
    out = chat(prompt, system=system, max_tokens=2400, task='council', timeout=90)
    return out or ''


def build_flagship_proposal(pack: dict) -> str:
    """旗舰帖批准卡片：数据包→苏摩111批准→发布（批准链路，不自动发）
    [2026-09-28 苏摩111] 接入位置：square_deep_post.py run() LLM生成后、发帖前
    铁律：对外观点必须过批准链路；卡片含正文+数据依据+风险自查
    """
    lines = ['📝 旗舰深度帖待批（回111发布/回停止作废）', '']
    lines.append('——— 数据依据 ———')
    for sym in pack.get('syms', []):
        d = pack.get('symbols', {}).get(sym, {})
        if 'error' in d:
            lines.append(f'{sym}: 分析失败')
            continue
        oi_sig = d.get('oi_signal', '?')
        lines.append(
            f'{sym}: {d.get("regime","?")}(score={d.get("score",0)}) '
            f'| OI={oi_sig} | FR={d.get("fr",0):+.4f}% '
            f'| 大户{d.get("big_long",50):.0f}%多/散户{d.get("retail_long",50):.0f}%多 '
            f'| 止损墙${d.get("liq_wall",0):,.0f} 支撑池${d.get("liq_pool",0):,.0f}')
    lines.append('')
    lines.append('——— 正文 ———')
    lines.append(pack.get('draft', '(无正文)'))
    lines.append('')
    lines.append('——— 自查 ———')
    ok, issues = audit_deep_post(pack.get('draft',''), pack)
    lines.append('✅ 8要素审计通过' if ok else '⚠️ 审计问题: ' + '; '.join(issues))
    lines.append('🌿 姓赵不宣 | 不是建议')
    return '\n'.join(lines)


def audit_deep_post(content: str, pack: dict = None) -> tuple:
    """深度帖专用审计：8要素逐项检查
    [2026-09-30 苏摩111 统一审计接线] 8要素后追加四道门统一断言（
    square_template.audit_post，快照帖同源），旗舰帖不过品牌门=不进批准队列。
    """
    issues = []
    if len(content) < 600:
        issues.append(f'长度不足({len(content)}字<600)')
    if '━━━' in content:
        issues.append('使用了禁止的分隔符')
    if not content.rstrip().endswith('🌿 姓赵不宣 | 不是建议'):
        issues.append('结尾缺少品牌签名')
    if '关注我，每晚21:00直播' not in content:
        issues.append('缺少直播引流句')
    # 事件钩子：第一句必须含数字
    first_line = content.split('\n')[0] if content else ''
    if not re.search(r'\d', first_line):
        issues.append('开头无具体数字（事件钩子缺失）')
    # 双门槛：至少两个价位关键词
    if content.count('$') < 3:
        issues.append(f'价位数量不足({content.count("$")}个)')
    # CTA：结尾附近要有站队问题
    tail = content[-400:]
    if not re.search(r'还是|哪个|站|觉得|投', tail):
        issues.append('结尾CTA缺失')
    # 数据矛盾推论：检查是否有「但/反而/却/没」的反转词
    if not re.search(r'但|反而|却|没|不', content):
        issues.append('缺反直觉核心（无反转词）')
    # [统一审计接线] 四道门断言（IP泄漏/违禁词/水印）
    try:
        from square.square_template import audit_post
        ok, brand_issues = audit_post(content)
        if not ok:
            issues.extend(brand_issues)
    except ImportError:
        pass
    return (len(issues) == 0, issues)


# ═════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════
# A线：数据门控 —— 当周有故事才产旗舰帖
# ═══════════════════════════════════════════════════════
GATE_THRESHOLDS = {
    'oi_chg_7d_min': 5.0,      # [Fix2 2026-10-03] OI 7d变化≥5%（原10%，CHOP_MID日常难触发）
    'fr_neg_count_min': 2,     # 资金费率近7天转负≥2次
    'lsr_change_min': 0.20,    # [Fix2 2026-10-03] 散户多空比7d变化≥0.2（原0.3）
    'need_count': 1,           # OR逻辑：1项即触发
}
GATE_STATE_FILE = BASE / 'data' / 'flagship_gate_state.json'


def check_story_gate(syms=('BTCUSDT', 'ETHUSDT')) -> tuple:
    """旗舰帖数据门控：拉OI/费率/多空比 7d变化，≥2项触发才产深度帖
    [2026-09-28 苏摩111] 接入位置：run() 在数据包构建之前调用
    返回 (fired: bool, reasons: list, summary: dict)
    """
    import urllib.request
    import ssl
    _ctx = ssl.create_default_context()

    def _fetch(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=10, context=_ctx).read())

    reasons, summary = [], {}
    for sym in syms:
        s = {}
        # ① OI 7d变化
        try:
            oi = _fetch(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1d&limit=8')
            if isinstance(oi, list) and len(oi) >= 2:
                oi0, oi1 = float(oi[0]['sumOpenInterestValue']), float(oi[-1]['sumOpenInterestValue'])
                if oi0 > 0:
                    s['oi_chg_7d'] = round((oi1 - oi0) / oi0 * 100, 1)
        except Exception:
            pass
        # ② 资金费率近7天转负次数
        try:
            fr = _fetch(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&limit=21')
            if isinstance(fr, list):
                s['fr_neg_count'] = sum(1 for x in fr if float(x['fundingRate']) < 0)
        except Exception:
            pass
        # ③ 散户多空比 7d变化（4h粒度42点）
        try:
            ls = _fetch(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=4h&limit=42')
            if isinstance(ls, list) and len(ls) > 5:
                s['lsr_change'] = round(abs(float(ls[-1]['longShortRatio']) - float(ls[0]['longShortRatio'])), 2)
        except Exception:
            pass
        summary[sym] = s

    # 判定：任一币满足≥2项即触发
    for sym, s in summary.items():
        hit = 0
        if s.get('oi_chg_7d') is not None and abs(s['oi_chg_7d']) >= GATE_THRESHOLDS['oi_chg_7d_min']:
            hit += 1; reasons.append(f"{sym} OI 7d变化{s['oi_chg_7d']:+.1f}%≥{GATE_THRESHOLDS['oi_chg_7d_min']}%")
        if s.get('fr_neg_count', 0) >= GATE_THRESHOLDS['fr_neg_count_min']:
            hit += 1; reasons.append(f"{sym} 费率7d转负{s['fr_neg_count']}次")
        if s.get('lsr_change') is not None and s['lsr_change'] >= GATE_THRESHOLDS['lsr_change_min']:
            hit += 1; reasons.append(f"{sym} 多空比变化{s['lsr_change']:.2f}≥{GATE_THRESHOLDS['lsr_change_min']}")
        if hit >= GATE_THRESHOLDS['need_count']:
            return True, reasons, summary

    # 缺失哪个条件→明确说（同交易系统「等待」理念）
    missing = []
    for sym, s in summary.items():
        if s.get('oi_chg_7d') is not None and abs(s['oi_chg_7d']) < GATE_THRESHOLDS['oi_chg_7d_min']:
            missing.append(f"{sym} OI变化{s['oi_chg_7d']:+.1f}%<阈值")
        if s.get('fr_neg_count', 0) < GATE_THRESHOLDS['fr_neg_count_min']:
            missing.append(f"{sym} 费率转负{s.get('fr_neg_count',0)}次<阈值")
        if s.get('lsr_change') is not None and s['lsr_change'] < GATE_THRESHOLDS['lsr_change_min']:
            missing.append(f"{sym} 多空比变化{s['lsr_change']:.2f}<阈值")
    return False, missing, summary


def _record_gate(state: dict):
    GATE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


# ═══════════════════════════════════════════════════════
# A线：批准队列 —— LLM产出→队列→苏摩111→发布
# ═══════════════════════════════════════════════════════
QUEUE_DIR = BASE / 'data' / 'flagship_pending'


def enqueue_for_approval(content: str, pack: dict, gate_reasons: list) -> str:
    """深度帖入队待批：写flagship_pending/，推批准卡片到jarvis
    [2026-09-28 苏摩111] 接入位置：run() LLM审计通过后（替代直接发布）
    发布走 flush_flagship_queue.py（苏摩回111后触发）
    """
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    fname = f'flagship_{ts}.json'
    item = {
        'ts': ts,
        'content': content,
        'gate_reasons': gate_reasons,
        'pack_summary': {s: {k: v for k, v in (pack.get('symbols', {}).get(s, {}) or {}).items()
                              if k != 'report_raw'} for s in pack.get('syms', [])},
        'status': 'PENDING',
    }
    (QUEUE_DIR / fname).write_text(json.dumps(item, ensure_ascii=False, indent=1))

    # 推送批准卡片
    from push_hub import push_jarvis
    card = build_flagship_proposal({**pack, 'draft': content})
    push_jarvis(card, priority='P1', dedup_key=f'flagship_approve_{ts}', dedup_ttl=21600)
    return fname


# 主流程
# ═════════════════════════════════════════════════════════════
def run(dry_run: bool = False, skip_llm: bool = False):
    syms_env = os.environ.get('BRAHMA_DEEP_POST_SYMS', 'BTC,ETH')
    syms = [s.strip().upper() for s in syms_env.split(',') if s.strip()]

    print(f'[deep-post] syms={syms} dry={dry_run} skip_llm={skip_llm}', flush=True)

    # 0. 数据门控：当周有故事才产旗舰帖（无故事→记录缺失条件→不发）
    gate_syms = [f'{s}USDT' for s in syms]
    fired, reasons, gate_summary = check_story_gate(tuple(gate_syms))
    _record_gate({'ts': time.time(), 'fired': fired, 'reasons': reasons, 'summary': gate_summary})
    if not fired:
        # [2026-10-04 苏摩111] 48h兜底：门控未触发但已超48h未发旗舰帖，强制发一条
        import time as _t2, json as _j2
        _gate_f = BASE / 'data' / 'deep_post_last_ts.json'
        _last_ts = 0
        try:
            _last_ts = _j2.loads(_gate_f.read_text()).get('ts', 0)
        except Exception: pass
        _force = (_t2.time() - _last_ts) > 48 * 3600
        if not _force:
            print(f'[deep-post] 数据门控未触发，距上次{(_t2.time()-_last_ts)/3600:.1f}h<48h，跳过')
            return
        print(f'[deep-post] 48h兜底触发（门控{";".join(reasons[:2])}但已{(_t2.time()-_last_ts)/3600:.0f}h未发）', flush=True)
    print(f'[deep-post] 门控触发：{";".join(reasons)}', flush=True)
    # 记录本次发帖时间（兜底逻辑用）
    import time as _t3, json as _j3
    _gate_f2 = BASE / 'data' / 'deep_post_last_ts.json'
    _gate_f2.write_text(_j3.dumps({'ts': _t3.time()}), encoding='utf-8')

    # 1. 数据包
    pack = build_data_pack(syms)
    ok_syms = [s for s in syms if 'error' not in pack['symbols'].get(s, {'error': 1})]
    if not ok_syms:
        print('[deep-post] 全部币种分析失败，本轮放弃')
        return

    # 2. LLM生成
    if skip_llm:
        content = ''
        print('[deep-post] --skip-llm: 跳过生成，直接推送数据包')
    else:
        content = generate_deep_post(pack)
        if not content:
            print('[deep-post] LLM生成失败（退避/配额烧穿）')

    # 3. 分支：LLM成功→审计→发帖；失败→推送数据包给苏摩
    if content:
        ok, issues = audit_deep_post(content, pack)
        if not ok:
            print(f'[deep-post] 审计未过: {issues} → 改走人工审路径')
            content = ''

    if not content:
        # 兜底：推送数据包到jarvis，苏摩人工审后手发
        from push_hub import push_jarvis
        brief = pack_to_prompt(pack)
        push_jarvis(f'📝 深度帖LLM生成失败，数据包如下（人工审后手发）：\n\n{brief[:3500]}')
        print('[deep-post] 数据包已推送jarvis（人工审路径）')
        return

    # 4. 去重+入批准队列（不自动发布）
    from square_auto_post import _is_duplicate
    if _is_duplicate(content):
        print('[deep-post] 24h内重复，跳过')
        return

    # [2026-09-28 苏摩111] 批准链路：对外观点必须过批准，不直接发
    fname = enqueue_for_approval(content, pack, reasons)
    print(f'[deep-post] ✅ 已入批准队列: {fname}（等待苏摩111）')
    if dry_run:
        print('[deep-post] DRY-RUN：队列文件已写，批准卡片未推（避免dry污染线程）')
    DEEP_STATE_FILE.write_text(json.dumps({
        'ts': time.time(), 'content_len': len(content),
        'pack_syms': ok_syms, 'queued': fname, 'status': 'PENDING_APPROVAL',
    }, ensure_ascii=False))


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-llm', action='store_true')
    args = ap.parse_args()
    run(dry_run=args.dry_run, skip_llm=args.skip_llm)
