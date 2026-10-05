#!/usr/bin/env python3
"""
daily_postmortem.py — 24小时复盘量化分析引擎
[设计院封印 2026-10-01 苏摩111]

核心能力：
  1. OI变化趋势（价格涨+OI跌=派发信号）
  2. LSR连续漂移方向（大户减多速率）
  3. K线量能结构（放量阴线=机构出货）
  4. 信号漂移频率（方向翻转次数=体制置信度指标）
  5. 24h博弈格局综合评分 → 下一时段方向概率

接入位置：brahma_crontab.txt 每日 23:30 UTC（日复盘）
输出：推送苏摩线程 + 写 data/postmortem_latest.json
"""
from __future__ import annotations
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, time, sys, os, subprocess, urllib.request, ssl
from pathlib import Path
from datetime import datetime, timezone

# push_hub统一入口 [Fix 2026-10-03 苏摩111]
try:
    import push_hub
except ImportError:
    import sys as _ph_sys, pathlib as _ph_pl
    _ph_sys.path.insert(0, str(_ph_pl.Path(__file__).parent))
    import push_hub


BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

try:
    from dotenv import dotenv_values
    _env = dotenv_values(BASE / 'alerts' / '.env')
except Exception:
    _env = {}
_USER   = os.environ.get('JARVIS_USER_ID', _env.get('JARVIS_USER_ID', '73295708'))
_THREAD = os.environ.get('JARVIS_THREAD_ID', _env.get('JARVIS_THREAD_ID', ''))
if not _THREAD:
    try:
        import re as _re
        _cs = (BASE / 'brahma_brain' / 'brahma_cpu.py').read_text()
        _m = _re.search(r"_JARVIS_THREAD\s*=\s*'([^']{30,})'", _cs)
        if _m: _THREAD = _m.group(1)
    except Exception as _te:
        import sys; print(f'[WARN] daily_postmortem thread detect: {_te}', file=sys.stderr)

def _get(url: str, timeout: int = 8):
    try:
        return json.loads(urllib.request.urlopen(url, timeout=timeout, context=_CTX).read())
    except Exception as e:
        print(f'[postmortem] API失败 {url[:50]}: {e}', file=sys.stderr)
        return None


# ══════════════════════════════════════════════
# 核心分析函数
# ══════════════════════════════════════════════

def analyze_oi_pattern(oi_hist: list[dict]) -> dict:
    """
    OI变化模式分析
    价格涨+OI跌 = 多头获利离场（派发）
    价格涨+OI涨 = 新多入场（真突破）
    价格跌+OI涨 = 新空入场（做空）
    价格跌+OI跌 = 空头获利离场
    """
    if len(oi_hist) < 2:
        return {'pattern': 'UNKNOWN', 'bias': 'NEUTRAL'}
    
    oi_vals = [float(o['sumOpenInterest']) for o in oi_hist]
    oi_chg_total = oi_vals[-1] - oi_vals[0]
    oi_chg_pct = oi_chg_total / oi_vals[0] * 100
    
    # 连续减仓计数
    consecutive_down = 0
    for i in range(len(oi_vals)-1, 0, -1):
        if oi_vals[i] < oi_vals[i-1]:
            consecutive_down += 1
        else:
            break
    
    pattern = 'DISTRIBUTION' if oi_chg_pct < -0.5 else \
              'ACCUMULATION' if oi_chg_pct > 0.5 else 'NEUTRAL'
    bias = 'BEARISH' if consecutive_down >= 3 else \
           'BULLISH' if consecutive_down == 0 and oi_chg_pct > 0.3 else 'NEUTRAL'
    
    return {
        'pattern': pattern, 'bias': bias,
        'oi_start': round(oi_vals[0], 1),
        'oi_end': round(oi_vals[-1], 1),
        'oi_chg_pct': round(oi_chg_pct, 3),
        'consecutive_down': consecutive_down,
    }


def analyze_lsr_drift(lsr_hist: list[dict], role: str = 'big') -> dict:
    """
    LSR连续漂移方向分析
    大户连续减多 = 派发信号
    散户连续加多 > 70% = 猎杀临界
    """
    key = 'longAccount'
    vals = [float(l[key]) * 100 for l in lsr_hist]
    if not vals:
        return {'drift': 'UNKNOWN', 'crowded': False}
    
    drift_total = vals[-1] - vals[0]
    # 单调性检测
    direction_changes = sum(1 for i in range(1, len(vals)) if (vals[i]-vals[i-1]) * (vals[i-1]-vals[i-2] if i>1 else 0) < 0)
    
    crowded = vals[-1] > 70.0 if role == 'retail' else False
    exhausted = vals[-1] < 50.0 and drift_total < -3.0  # 大户快速减多
    
    return {
        'start': round(vals[0], 1),
        'end': round(vals[-1], 1),
        'drift_total': round(drift_total, 2),
        'direction_changes': direction_changes,
        'crowded': crowded,
        'exhausted': exhausted,
        'trend': 'REDUCING_LONG' if drift_total < -2 else
                 'ADDING_LONG'   if drift_total > 2  else 'STABLE',
    }


def analyze_kline_structure(klines: list) -> dict:
    """
    K线量能结构分析
    放量阴线 = 机构出货
    缩量横盘 = 等待方向
    放量阳线 = 真实突破
    """
    if not klines:
        return {'structure': 'UNKNOWN'}
    
    vols = [float(k[5]) for k in klines]
    avg_vol = sum(vols) / len(vols)
    latest_vol = vols[-1]
    
    bodies = [(float(k[4]) - float(k[1])) / float(k[1]) * 100 for k in klines]
    
    # 找放量阴线（机构出货信号）
    distribution_bars = [(i, v, bodies[i]) for i, v in enumerate(vols)
                         if v > avg_vol * 1.5 and bodies[i] < -0.3]
    
    # 最近K线量能比
    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 0
    
    # 价格结构：高点是否递进
    highs = [float(k[2]) for k in klines]
    higher_high = highs[-1] > highs[-2] if len(highs) >= 2 else False
    
    structure = 'DISTRIBUTION' if distribution_bars and len(distribution_bars) >= 2 else \
                'CONSOLIDATION' if vol_ratio < 0.3 else \
                'BREAKOUT' if vol_ratio > 2.0 and bodies[-1] > 0.3 else \
                'NORMAL'
    
    return {
        'structure': structure,
        'vol_ratio': round(vol_ratio, 2),
        'avg_vol': round(avg_vol, 0),
        'latest_vol': round(latest_vol, 0),
        'distribution_bars': len(distribution_bars),
        'higher_high': higher_high,
        'latest_body_pct': round(bodies[-1], 3),
    }


def analyze_signal_drift(symbol: str) -> dict:
    """
    24h信号方向漂移频率
    翻转次数 > 6 = CHOP_MID置信（正确等待）
    翻转次数 < 2 = 方向坚定（可能即将突破）
    """
    sig_f = DATA / 'live_signal_log.jsonl'
    if not sig_f.exists():
        return {'flips': 0, 'last_dir': 'UNKNOWN', 'consistency': 'UNKNOWN'}
    
    cutoff = time.time() - 86400
    dirs = []
    for line in sig_f.read_text().splitlines()[-500:]:
        try:
            e = json.loads(line)
            if e.get('symbol') == symbol and e.get('ts', 0) > cutoff:
                d = e.get('signal_dir', 'UNKNOWN')
                if d not in ('UNKNOWN', 'NONE', ''):
                    dirs.append(d)
        except Exception:
            pass  # json parse skip — expected for malformed/empty lines
    
    flips = sum(1 for i in range(1, len(dirs)) if dirs[i] != dirs[i-1])
    last_dir = dirs[-1] if dirs else 'UNKNOWN'
    # 最后3个信号一致性
    last3 = dirs[-3:] if len(dirs) >= 3 else dirs
    consistency = 'HIGH' if len(set(last3)) == 1 and len(last3) >= 3 else \
                  'LOW' if flips > 5 else 'MED'
    
    return {
        'total_signals': len(dirs),
        'flips': flips,
        'last_dir': last_dir,
        'last3': last3,
        'consistency': consistency,
    }


def compute_next_session_bias(sym: str, oi_r: dict, lsr_big: dict, lsr_retail: dict,
                               kline_r: dict, sig_r: dict, price: float,
                               liq_short: float, liq_long: float) -> dict:
    """
    综合评分 → 下一时段方向概率
    40年实战逻辑量化版
    """
    bear_score = 0
    bull_score = 0
    reasons = []

    # OI派发信号
    if oi_r.get('pattern') == 'DISTRIBUTION':
        bear_score += 2
        reasons.append(f'OI持续减仓{oi_r["oi_chg_pct"]:.2f}%=多头派发')
    if oi_r.get('consecutive_down', 0) >= 4:
        bear_score += 2
        reasons.append(f'OI连续{oi_r["consecutive_down"]}h下降=资金撤退')

    # 大户减多
    if lsr_big.get('trend') == 'REDUCING_LONG':
        bear_score += 3
        reasons.append(f'大户多头{lsr_big["start"]}%→{lsr_big["end"]}%连续减多')
    if lsr_big.get('exhausted'):
        bear_score += 2
        reasons.append('大户多头<50%=聪明钱已撤')

    # 散户极度拥挤
    if lsr_retail.get('crowded'):
        bear_score += 3
        reasons.append(f'散户多头{lsr_retail["end"]:.1f}%极度拥挤=猎杀条件')

    # K线放量阴线
    if kline_r.get('distribution_bars', 0) >= 2:
        bear_score += 2
        reasons.append(f'放量阴线{kline_r["distribution_bars"]}根=机构出货')

    # 量能萎缩（横盘等待，不算看空）
    if kline_r.get('vol_ratio', 1) < 0.3:
        reasons.append(f'量能萎缩{kline_r["vol_ratio"]:.2f}x=等待方向')

    # 信号一致性
    if sig_r.get('consistency') == 'HIGH' and sig_r.get('last_dir') == 'SHORT':
        bear_score += 2
        reasons.append(f'信号连续SHORT一致性HIGH')
    elif sig_r.get('flips', 0) > 6:
        reasons.append(f'信号翻转{sig_r["flips"]}次=CHOP_MID确认')

    # 距离止损墙/支撑池
    dist_wall = (liq_short - price) / price * 100 if liq_short > price else 0
    dist_pool = (price - liq_long) / price * 100 if liq_long < price else 0
    if dist_wall < 2.5:
        bear_score += 1
        reasons.append(f'距止损墙{dist_wall:.1f}%=空单临近射程')
    if dist_pool < 2.5:
        bull_score += 1
        reasons.append(f'距支撑池{dist_pool:.1f}%=多单临近射程')

    total = bear_score + bull_score
    bear_pct = round(bear_score / max(total, 1) * 100)
    bull_pct = 100 - bear_pct

    return {
        'bear_score': bear_score,
        'bull_score': bull_score,
        'bear_prob': bear_pct,
        'bull_prob': bull_pct,
        'bias': 'BEAR' if bear_pct > 60 else 'BULL' if bull_pct > 60 else 'NEUTRAL',
        'reasons': reasons,
        'dist_wall_pct': round(dist_wall, 2),
        'dist_pool_pct': round(dist_pool, 2),
    }


# ══════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════

def run_postmortem():
    ts_utc = datetime.now(timezone.utc).strftime('%m/%d %H:%M UTC')
    results = {}

    for sym, sym_l in [('BTC', 'btc'), ('ETH', 'eth')]:
        sym_full = f'{sym}USDT'

        # 实时价格
        p_data = _get(f'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={sym_full}')
        if not p_data:
            continue
        price    = float(p_data['lastPrice'])
        chg_24h  = float(p_data['priceChangePercent'])
        hi_24h   = float(p_data['highPrice'])
        lo_24h   = float(p_data['lowPrice'])

        # 4H K线（最近8根）
        klines = _get(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym_full}&interval=4h&limit=8') or []

        # OI历史（1h，8节点）
        oi_hist = _get(f'https://fapi.binance.com/futures/data/openInterestHist?symbol={sym_full}&period=1h&limit=8') or []

        # LSR大户（1h，8节点）
        lsr_big_hist  = _get(f'https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={sym_full}&period=1h&limit=8') or []
        # LSR散户（global）
        lsr_ret_hist  = _get(f'https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym_full}&period=1h&limit=8') or []

        # liq_heatmap
        liq_f = DATA / f'liq_heatmap_{sym_l}usdt.json'
        liq_short = liq_long = 0.0
        if liq_f.exists():
            ld = json.loads(liq_f.read_text())
            liq_short = float(ld.get('nearest_short_liq', price * 1.02) or price * 1.02)
            liq_long  = float(ld.get('nearest_long_liq',  price * 0.98) or price * 0.98)

        # 各维度分析
        oi_r      = analyze_oi_pattern(oi_hist)
        lsr_big_r = analyze_lsr_drift(lsr_big_hist, 'big')
        lsr_ret_r = analyze_lsr_drift(lsr_ret_hist, 'retail')
        kline_r   = analyze_kline_structure(klines)
        sig_r     = analyze_signal_drift(sym_full)
        bias_r    = compute_next_session_bias(
            sym, oi_r, lsr_big_r, lsr_ret_r, kline_r, sig_r,
            price, liq_short, liq_long
        )

        results[sym] = dict(
            price=price, chg_24h=chg_24h,
            hi_24h=hi_24h, lo_24h=lo_24h,
            range_24h_pct=round((hi_24h - lo_24h) / lo_24h * 100, 2),
            oi=oi_r, lsr_big=lsr_big_r, lsr_retail=lsr_ret_r,
            kline=kline_r, signal=sig_r, bias=bias_r,
            liq_short=liq_short, liq_long=liq_long,
        )

    # ── 写入 postmortem_latest.json ──
    out = {'ts': ts_utc, 'timestamp': time.time(), 'results': results}
    tmp = DATA / 'postmortem_latest.json.tmp'
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    tmp.rename(DATA / 'postmortem_latest.json')

    # Dreaming循环：复盘结论→MEMORY.md
    try:
        _write_dreaming_to_memory(results)
    except Exception as _de:
        print(f'[postmortem] Dreaming写入失败: {_de}', flush=True)

    # ── 生成推送文本 ──
    lines = [f'📊 **梵天24h复盘** | {ts_utc}\n']
    for sym, r in results.items():
        b = r['bias']
        lines.append(f'**——— {sym} ${r["price"]:,.1f} ({r["chg_24h"]:+.2f}%) ———**')
        lines.append(f'24h区间: ${r["lo_24h"]:,.1f}~${r["hi_24h"]:,.1f} ({r["range_24h_pct"]:.1f}%)')
        lines.append(f'OI: {r["oi"]["pattern"]} ({r["oi"]["oi_chg_pct"]:+.2f}%) 连续降{r["oi"]["consecutive_down"]}h')
        lines.append(f'大户LSR: {r["lsr_big"]["start"]}%→{r["lsr_big"]["end"]}% ({r["lsr_big"]["trend"]})')
        lines.append(f'散户LSR: {r["lsr_retail"]["end"]:.1f}% {"🔴拥挤" if r["lsr_retail"]["crowded"] else "正常"}')
        lines.append(f'K线: {r["kline"]["structure"]} 量能{r["kline"]["vol_ratio"]:.2f}x 出货棒{r["kline"]["distribution_bars"]}根')
        lines.append(f'信号: 翻转{r["signal"]["flips"]}次 最后={r["signal"]["last_dir"]} 一致={r["signal"]["consistency"]}')
        lines.append(f'**下一时段偏向: {b["bias"]} (空{b["bear_prob"]}% 多{b["bull_prob"]}%)**')
        for reason in b['reasons'][:4]:
            lines.append(f'  → {reason}')
        lines.append(f'止损墙${r["liq_short"]:,.1f}(+{b["dist_wall_pct"]:.1f}%) 支撑池${r["liq_long"]:,.1f}(-{b["dist_pool_pct"]:.1f}%)')
        lines.append('')

    msg = '\n'.join(lines)
    print(msg)

    # ── 推送 ──
    target = f'{_USER}:thread:{_THREAD}' if _THREAD else _USER
    try:
        subprocess.Popen([
            'openclaw', 'infer',
            '--channel', 'jarvis',
            '--to', target,
            '--message', msg,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f'[postmortem] 推送完成 → {target[:30]}')
    except Exception as e:
        print(f'[postmortem] 推送失败: {e}', file=sys.stderr)

    return out


if __name__ == '__main__':
    import signal as _sig
    _sig.signal(_sig.SIGALRM, lambda s, f: sys.exit(1))
    _sig.alarm(55)
    run_postmortem()


# ══ [Dreaming循环 2026-10-03 苏摩111] 复盘结论→MEMORY.md自动更新 ══
# 对标 Anthropic Agent架构：夜里整理记忆，第二天开工比昨天聪明
def _write_dreaming_to_memory(results: dict) -> None:
    """
    把复盘关键结论写入MEMORY.md的动态交易记忆区
    [2026-10-03 苏摩111] 并发保护 + 可审计写入日志
    Lamis架构：多Agent写同一份记忆要版本追溯
    """
    import re
    from datetime import datetime, timezone

    mem_path = Path('/root/.openclaw/workspace/MEMORY.md')
    if not mem_path.exists():
        return

    ts   = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    src  = mem_path.read_text()

    # 生成今日交易记忆快照
    lines = [f'\n## 🌙 Dreaming {ts} UTC']
    for sym, r in results.items():
        if not isinstance(r, dict): continue
        bias    = r.get('bias', {})
        oi_pat  = r.get('oi', {}).get('pattern', '?')
        lsr_r   = r.get('lsr_retail', {})
        kline   = r.get('kline', {})
        liq_s   = r.get('liq_short', 0)
        liq_l   = r.get('liq_long', 0)
        bias_str= bias.get('bias', 'NEUTRAL')
        bull_p  = bias.get('bull_prob', 50)
        bear_p  = bias.get('bear_prob', 50)
        crowd   = lsr_r.get('crowded', False)
        vol_r   = kline.get('vol_ratio', 1.0)

        lesson = ''
        if crowd and bias_str in ('BEARISH','NEUTRAL'):
            lesson = f'散户拥挤({lsr_r.get("end",0):.0f}%)→偏空'
        elif oi_pat == 'REVERSAL':
            lesson = 'OI反转→关注入场'
        elif vol_r < 0.3:
            lesson = '量能萎缩→等方向确认'

        lines.append(
            f'- **{sym}** 偏向={bias_str} 多{bull_p}%空{bear_p}% '
            f'OI={oi_pat} 散户拥挤={crowd} '
            f'墙=${liq_s:,.0f} 池=${liq_l:,.0f}'
            + (f' → {lesson}' if lesson else '')
        )

    snapshot = '\n'.join(lines)

    # 替换或追加到MEMORY.md的Dreaming区块
    marker = '## 🌙 Dreaming'
    if marker in src:
        # 替换上次的dreaming区块
        src = re.sub(r'\n## 🌙 Dreaming.*?(?=\n## |\Z)', snapshot, src, flags=re.DOTALL)
    else:
        # 首次追加（在文件末尾）
        src = src.rstrip() + '\n' + snapshot + '\n'

    # 并发保护：flock + 原子写（tmp→rename）
    import fcntl
    lock_f = mem_path.with_suffix('.lock')
    with open(str(lock_f), 'w') as _lf:
        fcntl.flock(_lf, fcntl.LOCK_EX)
        try:
            tmp = mem_path.with_suffix('.tmp')
            tmp.write_text(src)
            tmp.rename(mem_path)
        finally:
            fcntl.flock(_lf, fcntl.LOCK_UN)

    # 可审计写入日志（Lamis：哪次会话、谁提议、为什么改）
    audit_f = Path('/root/.openclaw/workspace/memory') / f'dreaming_audit.jsonl'
    audit_f.parent.mkdir(exist_ok=True)
    audit_entry = {
        'ts': time.time(),
        'date': ts,
        'source': 'daily_postmortem',
        'symbols': list(results.keys()),
        'btc_bias': results.get('BTC',{}).get('bias',{}).get('bias','?'),
        'eth_bias': results.get('ETH',{}).get('bias',{}).get('bias','?'),
        'mem_size': len(src),
    }
    with open(str(audit_f), 'a') as _af:
        _af.write(json.dumps(audit_entry) + '\n')

    print(f'[postmortem] Dreaming写入MEMORY.md ✅ ({len(lines)-1}个标的)', flush=True)

    # Diff推送苏摩：「昨天我改了什么，为什么改」
    # Lamis规范：人审变更，不改权重
    try:
        btc_r = results.get('BTC', {})
        eth_r = results.get('ETH', {})
        btc_b = btc_r.get('bias', {}).get('bias', '?')
        eth_b = eth_r.get('bias', {}).get('bias', '?')
        eth_crowd = eth_r.get('lsr_retail', {}).get('crowded', False)
        btc_oi = btc_r.get('oi', {}).get('pattern', '?')
        diff_msg = (
            f'🌙 **Dreaming完成** | {ts_utc}\n\n'
            f'**MEMORY.md已更新** — 今晚市场结构快照：\n'
            f'BTC 偏向={btc_b} OI={btc_oi}\n'
            f'ETH 偏向={eth_b} 散户拥挤={eth_crowd}\n\n'
            f'> 审计日志：memory/dreaming_audit.jsonl\n'
            f'> 回复「确认」接受 / 「撤销」回滚'
        )
        import importlib.util as _ilu, pathlib as _pl
        _spec = _ilu.spec_from_file_location('push_hub',
            _pl.Path(__file__).parent / 'push_hub.py')
        _ph = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_ph)
        _ph.push_jarvis(diff_msg, priority='P3',
            dedup_key=f'dreaming_{ts_utc[:10]}', dedup_ttl=86400)
        print('[postmortem] Dreaming diff推送苏摩 ✅', flush=True)
    except Exception as _diff_e:
        print(f'[postmortem] diff推送失败: {_diff_e}', flush=True)

def run_loss_attribution(trade: dict) -> dict:
    """
    亏损归因5问引擎 [2026-10-03 苏摩111 梵天自进化Phase1]
    接入位置: daily_postmortem.py → meta_cognition_state.json

    对每笔亏损单自动回答5个问题，更新对应维度的IC计数
    """
    import json, time
    from pathlib import Path

    attr = {
        "trade_id": trade.get("id", ""),
        "symbol": trade.get("symbol", ""),
        "direction": trade.get("direction", ""),
        "pnl": trade.get("pnl", 0),
        "ts": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "questions": {}
    }

    # Q1: 体制判断错了吗？
    regime = trade.get("regime", "")
    actual_move = trade.get("actual_move_pct", 0)
    q1_wrong = (
        ("CHOP" in regime and abs(actual_move) > 2.0) or
        ("BULL" in regime and actual_move < -1.5) or
        ("BEAR" in regime and actual_move > 1.5)
    )
    attr["questions"]["Q1_regime_wrong"] = q1_wrong

    # Q2: 入场区错了吗？（追高/追低）
    entry = trade.get("entry_price", 0)
    entry_lo = trade.get("entry_lo", 0)
    entry_hi = trade.get("entry_hi", 0)
    q2_chased = False
    if entry_lo and entry_hi and entry:
        zone_mid = (entry_lo + entry_hi) / 2
        deviation = abs(entry - zone_mid) / zone_mid if zone_mid > 0 else 0
        q2_chased = deviation > 0.005  # 偏离入场区超0.5%
    attr["questions"]["Q2_entry_chased"] = q2_chased

    # Q3: FVG方向错了吗？
    fvg_dir = trade.get("fvg_consensus", "NONE")
    signal_dir = trade.get("direction", "")
    q3_fvg_conflict = (
        (fvg_dir == "BULL" and signal_dir == "SHORT") or
        (fvg_dir == "BEAR" and signal_dir == "LONG")
    )
    attr["questions"]["Q3_fvg_conflict"] = q3_fvg_conflict

    # Q4: OI信号误导了吗？
    oi_signal = trade.get("oi_signal", "")
    q4_oi_misled = (
        (oi_signal == "SHORT_BUILD" and signal_dir == "LONG") or
        (oi_signal == "LONG_BUILD" and signal_dir == "SHORT")
    )
    attr["questions"]["Q4_oi_misled"] = q4_oi_misled

    # Q5: 时段错了吗？
    hour_utc = trade.get("hour_utc", -1)
    q5_bad_timing = hour_utc in [2, 3, 4, 5, 6, 7]  # 亚盘低流动性时段
    attr["questions"]["Q5_bad_timing"] = q5_bad_timing

    # 更新 meta_cognition_state.json
    mc_path = Path(__file__).parent.parent / "data" / "meta_cognition_state.json"
    try:
        mc = json.loads(mc_path.read_text()) if mc_path.exists() else {}
        dim = mc.get("dimension_scores", {})

        is_win = trade.get("pnl", 0) > 0
        key = "wins" if is_win else "losses"

        # 更新各维度计数（归因到对应维度）
        dim_map = {
            "Q1_regime_wrong": "regime",
            "Q3_fvg_conflict": "fvg",
            "Q4_oi_misled": "oi",
            "Q5_bad_timing": "timing",
        }
        for q_key, dim_key in dim_map.items():
            if dim_key in dim:
                dim[dim_key][key] = dim[dim_key].get(key, 0) + 1
                total = dim[dim_key]["wins"] + dim[dim_key]["losses"]
                if total >= 10:
                    wr = dim[dim_key]["wins"] / total
                    dim[dim_key]["ic"] = round(wr - 0.5, 4)  # IC = WR - 0.5基准

        mc["dimension_scores"] = dim
        mc["total_trades"] = mc.get("total_trades", 0) + 1
        mc.setdefault("attribution_log", []).append(attr)
        mc["attribution_log"] = mc["attribution_log"][-200:]  # 只保留最近200条
        mc["updated_at"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        mc_path.write_text(json.dumps(mc, ensure_ascii=False, indent=2))
    except Exception as _e:
        print(f"[WARN] meta_cognition update failed: {_e}")

    return attr


def _generate_daily_edu_post(postmortem_data: dict) -> str:
    """
    每日教育帖生成器 [2026-10-04 自主决策封印]
    接入位置: daily_postmortem.py run_postmortem()末尾
    从复盘数据蒸馏出一篇有教学价值的帖子
    """
    import time
    ts = time.strftime('%m/%d', time.gmtime())
    
    btc_bias  = postmortem_data.get('btc_bias', 'NEUTRAL')
    eth_bias  = postmortem_data.get('eth_bias', 'NEUTRAL')
    eth_hurst = postmortem_data.get('eth_hurst', 0)
    btc_hurst = postmortem_data.get('btc_hurst', 0)
    key_lesson = postmortem_data.get('key_lesson', '')
    
    # 选择今日教学主题（轮转）
    day_of_week = int(time.strftime('%w'))
    topics = {
        0: 'hurst',      # 周日：趋势确认
        1: 'fvg',        # 周一：FVG结构
        2: 'oi',         # 周二：OI+资金流
        3: 'lsr',        # 周三：聪明钱分歧
        4: 'clearance',  # 周四：清算地图
        5: 'risk',       # 周五：风控体系
        6: 'review',     # 周六：本周复盘
    }
    topic = topics.get(day_of_week, 'fvg')
    
    templates = {
        'hurst': f"""今天 BTC Hurst={btc_hurst:.3f}，ETH Hurst={eth_hurst:.3f}。

Hurst指数是我每天必看的第一个数字。

不是因为它最准，而是因为它最诚实。

━━━ Hurst告诉你什么 ━━━

H < 0.5 → 均值回归，昨天涨今天跌是常态
H = 0.5 → 随机游走，抛硬币的市场
H > 0.6 → 趋势性，昨天的方向今天大概率延续
H > 0.7 → 强趋势，追涨是对的，抄底是错的

今天ETH {eth_hurst:.2f}——{"已进趋势区，顺势操作" if eth_hurst > 0.6 else "随机游走，谨慎入场"}。

━━━ 大多数人怎么用错了 ━━━

他们盯着K线问「这里能抄底吗」。
Hurst在说「这里的趋势方向是X，逆势成功率只有40%」。

数据和直觉，你选哪个？

关注我，每晚21:00直播+SMC教学
🌿 姓赵不宣 | 不是建议
#量化交易 #技术分析 #Hurst #合约交易""",

        'fvg': f"""今天 ETH FVG共识：{"BEAR 偏空" if "BEAR" in str(eth_bias) else "BULL 偏多" if "BULL" in str(eth_bias) else "中性"}。

FVG（公允价值缺口）是我最依赖的入场工具。

但90%的人用错了。

━━━ 错误用法 ━━━

「FVG在这里，我在这里买」
→ 这是在等价格回到过去

━━━ 正确用法 ━━━

FVG不是支撑，是磁铁。
价格会被吸引到FVG中点，然后决定方向。

多周期共识才是信号：
  15M BEAR + 1H BEAR + 4H BEAR = 三周期共振做空
  任何一个周期方向不一致 = 降低仓位或等待

今天ETH三短周期FVG全部指向同一方向。
这是我信号质量分类里的最高级别。

关注我，每晚21:00直播+SMC教学
🌿 姓赵不宣 | 不是建议
#FVG #SMC交易 #技术分析 #合约交易""",
    }
    
    post = templates.get(topic, templates['fvg'])
    return post


def push_daily_edu_post(postmortem_data: dict) -> None:
    """推送每日教育帖到广场"""
    import sys, time
    from pathlib import Path as _P
    try:
        sys.path.insert(0, str(_P(__file__).parent))
        sys.path.insert(0, str(_P(__file__).parent / 'square'))
        from square_auto_post import _post_to_square, _post_multi_voice
        post = _generate_daily_edu_post(postmortem_data)
        r = _post_to_square(post)
        if r.get('data', {}).get('shareLink'):
            print(f'[EduPost] ✅ 教育帖发布成功: {r["data"]["shareLink"]}')
            time.sleep(5)
            _post_multi_voice(post)
        else:
            print(f'[EduPost] 发布失败: {r}')
    except Exception as _e:
        print(f'[WARN] daily_postmortem: 教育帖推送失败: {_e}')
