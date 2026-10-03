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
import json, time, sys, os, subprocess, urllib.request, ssl
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
sys.path.insert(0, str(BASE / 'brahma_brain'))
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
    except Exception: pass

_CTX = ssl.create_default_context()

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
        except:
            pass
    
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
    """把复盘关键结论写入MEMORY.md的动态交易记忆区"""
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

    mem_path.write_text(src)
    print(f'[postmortem] Dreaming写入MEMORY.md ✅ ({len(lines)-1}个标的)', flush=True)
