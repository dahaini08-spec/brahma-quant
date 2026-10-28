#!/usr/bin/env python3
"""
blood_chip_scanner.py — 带血筹码监控器 v2
[2026-10-19 苏摩111] 升级：42标的 + 方仓历史验证 + 三维决策输出
[2026-09-15 苏摩111] 原版：把"找带血筹码"变成长期运行的流程

触发条件：
  1. 折价率 ≤ -20%（当前价 vs 1年高点）
  2. vs 200日均价 ≤ -5%（跌破均线）
  3. Crypto恐慌指数 ≤ 25（Extreme Fear）→ 触发方仓历史验证

三维输出：
  维度1：价格折价（折价率 / vs200d / 2年回撤位置）
  维度2：方仓历史（n案例 / 30日WR / 中位回报 / 体制分布）
  维度3：当前体制（Regime / Hurst / 与方仓体制匹配度）

接入位置：
  - OpenClaw cron → 每12h运行（06:15/18:15 UTC）→ 推送到Jarvis
  - 手动触发：python3 scripts/blood_chip_scanner.py
"""

import sys, os, json, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'
WATCHLIST    = DATA / 'watchlist_42.json'   # v2: 42标的
WATCHLIST_v1 = DATA / 'watchlist.json'      # 兜底
FANGCANG_DB  = DATA / 'fangcang_merged_v2.json'  # 40913案例

# ── 数据获取 ────────────────────────────────────────────────

def fetch_binance_klines(symbol: str, interval: str = '1d', limit: int = 365) -> list:
    """从Binance API获取K线数据"""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[blood_chip] Binance API error for {symbol}: {e}", file=sys.stderr)
        return []

def fetch_crypto_fear_greed() -> dict:
    """从Alternative.me获取Crypto恐慌贪婪指数 — v2增加历史数据"""
    url = "https://api.alternative.me/fng/?limit=7"  # 取7天历史
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            v = data['data'][0]
            history = data['data'][1:]
            return {
                'value': int(v['value']),
                'classification': v['value_classification'],
                'timestamp': v['timestamp'],
                'history': [{'value': int(d['value']), 'classification': d['value_classification']} for d in history]
            }
    except Exception as e:
        print(f"[blood_chip] Fear&Greed API error: {e}", file=sys.stderr)
        return {'value': -1, 'classification': 'UNKNOWN', 'history': [], 'error': str(e)}

def fetch_yahoo_stock(symbol: str) -> dict:
    """从Yahoo Finance获取美股数据"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            result = data['chart']['result'][0]
            quotes = result['indicators']['quote'][0]
            closes = [c for c in quotes['close'] if c is not None]
            if not closes:
                return {}
            current = closes[-1]
            high_1y = max(closes)
            avg_200d = sum(closes[-200:]) / len(closes[-200:]) if len(closes) >= 200 else sum(closes) / len(closes)
            return {
                'symbol': symbol,
                'current': current,
                'high_1y': high_1y,
                'avg_200d': avg_200d,
                'discount_pct': ((current - high_1y) / high_1y * 100),
                'vs_200d_pct': ((current - avg_200d) / avg_200d * 100),
                'source': 'yahoo'
            }
    except Exception as e:
        print(f"[blood_chip] Yahoo Finance error for {symbol}: {e}", file=sys.stderr)
        return {}

# ── 방창 역사 검증층 (v2 추가) ────────────────────────────────────────────
# [2026-10-19 苏摩111] 접입 위치: analyze_crypto_asset() 위에 삽입

_FANGCANG_CACHE = None  # 지연 로드, 싱글톤 캐시

def _load_fangcang_db() -> list:
    """lazy load fangcang_merged_v2.json, 프로세스 내 1회만 읽기"""
    global _FANGCANG_CACHE
    if _FANGCANG_CACHE is None:
        try:
            _FANGCANG_CACHE = json.loads(FANGCANG_DB.read_text())
        except Exception as e:
            print(f'[blood_chip] 방창DB 로드 실패: {e}', file=sys.stderr)
            _FANGCANG_CACHE = []
    return _FANGCANG_CACHE


def query_fangcang_ev(fangcang_key: str, discount_pct: float, fg_value: int) -> dict:
    """
    방창 역사 검증: 유사 압축구조 역사 케이스 조회
    반환: {n, wr_30d, median_return_30d, top_regimes, note}
    """
    if not fangcang_key:
        return {'n': 0, 'note': '해당 표적 방창 데이터 없음'}

    db = _load_fangcang_db()
    if not db:
        return {'n': 0, 'note': '방창DB 불가용'}

    matched = []
    for c in db:
        sym = c.get('symbol', '').upper()
        if sym != fangcang_key.upper():
            continue
        ret = c.get('future_return') or c.get('future_return_24h')
        if ret is None:
            continue
        # BBW 압축도로 필터: 압축 구조만 선택
        bbw = c.get('min_bb_width', c.get('compress_bbw_min', 9999))
        if bbw > 0.08:
            continue
        matched.append({'ret': float(ret), 'regime': c.get('regime', '?'), 'direction': c.get('direction', '?')})

    if not matched:
        return {'n': 0, 'note': f'{fangcang_key} 유사 압축 케이스 없음'}

    n = len(matched)
    rets = sorted([m['ret'] for m in matched])
    median_ret = rets[n // 2]
    wr = len([r for r in rets if r > 0]) / n * 100

    from collections import Counter
    regime_dist = Counter(m['regime'] for m in matched)
    top_regimes = [(r, cnt, round(cnt/n*100)) for r, cnt in regime_dist.most_common(3)]

    return {
        'n': n,
        'wr_30d': round(wr, 1),
        'median_return_30d': round(median_ret * 100, 2),
        'top_regimes': top_regimes,
        'note': f'{n}개 압축구조 역사 케이스 매칭'
    }


def get_current_regime(symbol_base: str) -> dict:
    """brahma_state에서 현재 체제와 Hurst 읽기"""
    if not symbol_base:
        return {'regime': 'UNKNOWN', 'hurst': 0.5, 'signal': 'NONE'}
    key = symbol_base.upper()
    for path in [DATA / f'brahma_state_{key.lower()}.json',
                 DATA / f'brahma_state_light_{key.lower()}.json']:
        try:
            st = json.loads(path.read_text())
            return {
                'regime': st.get('regime', 'UNKNOWN'),
                'hurst':  float(st.get('hurst', 0.5) or 0.5),
                'signal': st.get('signal_dir', 'NONE'),
            }
        except:
            continue
    return {'regime': 'UNKNOWN', 'hurst': 0.5, 'signal': 'NONE'}


def stars_rating(n: int, wr: float, regime_match: bool) -> str:
    """삼차원 종합 평가 → ⭐⭐⭐⭐⭐"""
    score = 0
    if n >= 20: score += 2
    elif n >= 10: score += 1
    if wr >= 65: score += 2
    elif wr >= 55: score += 1
    if regime_match: score += 1
    return '⭐' * min(score, 5) or '—'


# ── 核心逻辑 ────────────────────────────────────────────────

def analyze_crypto_asset(symbol: str) -> dict:
    """分析单个Crypto资产 — v2增加2年最大回撤"""
    klines = fetch_binance_klines(symbol, '1d', 730)  # 2年
    if not klines or not isinstance(klines, list) or len(klines) < 30:
        return {'symbol': symbol, 'error': 'insufficient data'}
    
    closes = [float(k[4]) for k in klines]
    current = closes[-1]
    high_1y = max(closes[-365:]) if len(closes) >= 365 else max(closes)
    high_2y = max(closes)
    avg_200d = sum(closes[-200:]) / len(closes[-200:]) if len(closes) >= 200 else sum(closes) / len(closes)
    low_2y = min(closes[-730:]) if len(closes) >= 730 else min(closes)
    discount_pct = ((current - high_1y) / high_1y * 100)
    vs_200d_pct = ((current - avg_200d) / avg_200d * 100)
    max_drawdown_2y = ((low_2y - high_2y) / high_2y * 100)  # 2年最大回撤
    
    return {
        'symbol': symbol,
        'current': current,
        'high_1y': high_1y,
        'high_2y': high_2y,
        'low_2y': low_2y,
        'avg_200d': avg_200d,
        'discount_pct': round(discount_pct, 2),
        'vs_200d_pct': round(vs_200d_pct, 2),
        'max_drawdown_2y': round(max_drawdown_2y, 2),
        'source': 'binance',
        'data_points': len(closes)
    }

def check_triggers(asset: dict, fear_greed: dict, rules: dict) -> dict:
    """检查触发条件 — v2增加先行指标"""
    triggers = []
    
    if 'discount_pct' in asset:
        if asset['discount_pct'] <= rules.get('discount_threshold', -20):
            triggers.append(f"折价{asset['discount_pct']:.1f}% ≤ {rules['discount_threshold']}%阈值")
    
    if 'vs_200d_pct' in asset:
        if asset['vs_200d_pct'] <= rules.get('vs_200d_threshold', -5):
            triggers.append(f"vs200d={asset['vs_200d_pct']:+.1f}% ≤ {rules['vs_200d_threshold']}%阈值")
    
    fg_value = fear_greed.get('value', -1)
    if fg_value > 0 and fg_value <= rules.get('fear_greed_extreme', 25):
        triggers.append(f"恐慌指数{fg_value} ≤ {rules['fear_greed_extreme']}(Extreme Fear)")
    elif fg_value > 0 and fg_value <= rules.get('fear_greed_fear', 45):
        triggers.append(f"恐慌指数{fg_value} ≤ {rules['fear_greed_fear']}(Fear)")
    
    # v2先行指标：折价百分位（当前折价 vs 2年最大回撤）
    if 'discount_pct' in asset and 'max_drawdown_2y' in asset:
        max_dd = asset['max_drawdown_2y']
        if max_dd < 0:
            percentile = asset['discount_pct'] / max_dd * 100  # 0=高点, 100=最低点
            asset['drawdown_percentile'] = round(percentile, 1)
            if percentile > 80:
                triggers.append(f"回撤百分位{percentile:.0f}% > 80%（接近2年最低）")
    
    # v2先行指标：恐慌持续时间
    fg_history = fear_greed.get('history', [])
    if fg_history:
        consecutive_fear = sum(1 for d in fg_history if d.get('value', 100) <= 25)
        if consecutive_fear >= 3:
            triggers.append(f"恐慌持续{consecutive_fear}天（连续Extreme Fear）")
    
    return {
        'triggered': len(triggers) > 0,
        'triggers': triggers,
        'needs_deep_research': fg_value > 0 and fg_value <= rules.get('fear_greed_extreme', 25)
    }

def generate_report(watchlist: dict, crypto_results: list, stock_results: list,
                    fear_greed: dict, trigger_results: dict,
                    fangcang_results: dict = None) -> str:
    """生成三维报告 v2：价格折价 + 方仓历史 + 当前体制"""
    beijing = timezone(timedelta(hours=8))
    now = datetime.now(beijing)
    fangcang_results = fangcang_results or {}

    lines = []
    lines.append(f"🩸 带血筹码雷达 v2 | {now.strftime('%m-%d %H:%M')} 北京时间")
    lines.append("")

    # 恐慌指数
    fg = fear_greed
    fg_emoji = "🟢" if fg['value'] > 55 else "🟡" if fg['value'] > 45 else "🟠" if fg['value'] > 25 else "🔴"
    lines.append(f"市场情绪: {fg_emoji} Crypto恐慌指数 = {fg['value']} ({fg['classification']})")
    lines.append("")

    # 触发标的 (三维输出)
    triggered = [(r, trigger_results.get(r['symbol'], {})) for r in crypto_results
                 if 'error' not in r and trigger_results.get(r['symbol'], {}).get('triggered')]

    if triggered:
        lines.append(f"━━━ ⚠️ 触发标的 ({len(triggered)}/{len(crypto_results)}) ━━━")
        for r, tr in triggered:
            sym = r['symbol']
            fc_key = r.get('fangcang_key', '')
            fc_ev  = fangcang_results.get(sym, {})
            regime = r.get('regime_info', {})

            lines.append(f"")
            lines.append(f"━━━ {sym} ${r['current']:,.2f} ━━━")
            # 维度1: 价格折价
            lines.append(f"  【维度1 价格折价】")
            lines.append(f"  折价1y高点：{r['discount_pct']:+.1f}%  vs 200d均：{r['vs_200d_pct']:+.1f}%")
            lines.append(f"  2y最大回撤：{r.get('max_drawdown_2y', 0):+.1f}%  2y低点：${r.get('low_2y', 0):,.2f}")

            # 维度2: 方仓历史
            lines.append(f"  【维度2 方仓历史验证】")
            if fc_ev.get('n', 0) > 0:
                reg_str = ' / '.join([f"{rg}({pct}%)" for rg, _, pct in fc_ev.get('top_regimes', [])[:2]])
                lines.append(f"  n={fc_ev['n']}案例  WR={fc_ev['wr_30d']}%  中位回报={fc_ev['median_return_30d']:+.2f}%")
                lines.append(f"  历史主要体制: {reg_str}")
            else:
                lines.append(f"  {fc_ev.get('note', '暂无方仓数据')}")

            # 维度3: 当前体制
            lines.append(f"  【维度3 当前体制】")
            if regime.get('regime', 'UNKNOWN') != 'UNKNOWN':
                reg_now = regime['regime']
                hurst   = regime.get('hurst', 0.5)
                # 体制匹配度
                top_hist_regimes = [rg for rg, _, _ in fc_ev.get('top_regimes', [])]
                regime_match = reg_now in top_hist_regimes
                match_str = '✅ 体制匹配' if regime_match else '⚠️ 体制偏差'
                lines.append(f"  当前: {reg_now}  Hurst={hurst:.3f}  {match_str}")
            else:
                lines.append(f"  体制数据不可用")
                regime_match = False

            # 综合评级
            fc_wr = fc_ev.get('wr_30d', 0)
            fc_n  = fc_ev.get('n', 0)
            rating = stars_rating(fc_n, fc_wr, regime_match)
            lines.append(f"  综合评级: {rating}")

        lines.append("")
    else:
        lines.append("—— ✅ 无触发（{}/{}）| 继续等待 ——".format(0, len(crypto_results)))
        lines.append("")

    # 观察池简报（未触发标的简要列出）
    not_triggered = [r for r in crypto_results
                     if 'error' not in r and not trigger_results.get(r['symbol'], {}).get('triggered')]
    if not_triggered:
        lines.append("━━━ 观察池（未触发） ━━━")
        for r in not_triggered:
            disc = r['discount_pct']
            disc_emoji = "⚠️" if disc <= -10 else "🟢"
            lines.append(f"  {disc_emoji} {r['symbol']}: ${r['current']:,.2f} 折价={disc:+.1f}%")
        lines.append("")

    lines.append("📊 梵天带血筹码×方仓引擎 v2 | 不是建议")

    return "\n".join(lines)

# ── 主入口 ────────────────────────────────────────────────

def main():
    # 加载观察池
    if not WATCHLIST.exists():
        print(f"[blood_chip] watchlist.json not found at {WATCHLIST}", file=sys.stderr)
        return
    
    watchlist = json.loads(WATCHLIST.read_text())
    rules = watchlist.get('trigger_rules', {})
    crypto_assets = watchlist.get('crypto', [])

    # 중복 제거 (watchlist_42.json에 BNB가 2개)
    seen_syms = set()
    deduped = []
    for a in crypto_assets:
        sym = a.get('symbol', '')
        if sym not in seen_syms:
            seen_syms.add(sym)
            deduped.append(a)
    crypto_assets = deduped

    print(f'[blood_chip v2] {len(crypto_assets)}개 표적 스캔 시작...', file=sys.stderr)

    # 並行 가격 데이터 수집 + 체제 읽기
    crypto_results = []
    for asset in crypto_assets:
        sym = asset.get('symbol', '')
        r = analyze_crypto_asset(sym)
        r['name']         = asset.get('name', sym)
        r['fangcang_key'] = asset.get('fangcang_key') or ''
        r['tier']         = asset.get('tier', 3)
        # 체제 읽기 (BTC/ETH만 지원, 나머지 UNKNOWN)
        fc_key = r['fangcang_key']
        r['regime_info']  = get_current_regime(fc_key) if fc_key in ('BTC','ETH') else {'regime': 'UNKNOWN', 'hurst': 0.5}
        crypto_results.append(r)

    # 恐慌指数
    fear_greed = fetch_crypto_fear_greed()

    # 触发条件 검사
    trigger_results = {}
    for r in crypto_results:
        if 'error' not in r:
            trigger_results[r['symbol']] = check_triggers(r, fear_greed, rules)

    # 방창 역사 검증 (触发标的만 실행, 무거우므로 필요할 때만)
    fangcang_results = {}
    triggered_syms = [r for r in crypto_results
                      if 'error' not in r and trigger_results.get(r['symbol'], {}).get('triggered')]
    if triggered_syms:
        print(f'[blood_chip v2] {len(triggered_syms)}개 触发 → 방창 조회 시작...', file=sys.stderr)
        for r in triggered_syms:
            fc_key = r.get('fangcang_key', '')
            disc   = r.get('discount_pct', 0)
            fg_val = fear_greed.get('value', 50)
            fangcang_results[r['symbol']] = query_fangcang_ev(fc_key, disc, fg_val)

    # 三维 보고서 생성
    report = generate_report(watchlist, crypto_results, [], fear_greed, trigger_results, fangcang_results)
    print(report)
    
    # 保存结果到文件
    output = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'fear_greed': fear_greed,
        'crypto': crypto_results,
        'triggers': trigger_results,
        'fangcang': fangcang_results,
        'report': report
    }
    
    out_file = DATA / 'blood_chip_scan.json'
    out_file.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    
    # 追加历史记录
    history_file = DATA / 'blood_chip_history.jsonl'
    with open(history_file, 'a') as f:
        f.write(json.dumps(output, ensure_ascii=False) + '\n')
    
    return output

if __name__ == '__main__':
    main()
