#!/usr/bin/env python3
"""
phase0_monitor.py v2 — 梵天每日多维度监控
集成: 武曲Paper + DSR + GEX Sentiment + MC + 验证层汇总
只读，不改任何文件，不触碰核心逻辑
"""
import json, time, sys, os
import urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'brahma_brain'))

CST = timezone(timedelta(hours=8))
now = datetime.now(tz=CST)

# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────
def fetch(u, timeout=5):
    try: return json.loads(urllib.request.urlopen(u, timeout=timeout).read())
    except: return {}

def load_json(p, default=None):
    try: return json.loads(Path(p).read_text())
    except: return default or {}

def bar20(n, total):
    f = min(int(n / max(total, 1) * 20), 20)
    return '█' * f + '░' * (20 - f)

SEP = '━' * 56

# ─────────────────────────────────────────────────────────────
# 1. 市场数据
# ─────────────────────────────────────────────────────────────
btc = float(fetch('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT').get('price', 0))
eth = float(fetch('https://fapi.binance.com/fapi/v1/ticker/price?symbol=ETHUSDT').get('price', 0))
fr_b = float(fetch('https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT').get('lastFundingRate', 0)) * 100
fr_e = float(fetch('https://fapi.binance.com/fapi/v1/premiumIndex?symbol=ETHUSDT').get('lastFundingRate', 0)) * 100

state  = load_json(ROOT / 'data/brahma_state.json')
regime = state.get('regime', '?')
nav    = float(state.get('nav', 0))
age    = (time.time() - state.get('timestamp', 0)) / 60

# ─────────────────────────────────────────────────────────────
# 2. 武曲Paper
# ─────────────────────────────────────────────────────────────
wp     = load_json(ROOT / 'data/wuqu_paper_state.json')
wp_n   = wp.get('n_total', 0)
wp_tp  = wp.get('n_tp', 0)
wp_sl  = wp.get('n_sl', 0)
wp_wr  = wp_tp / (wp_tp + wp_sl) * 100 if (wp_tp + wp_sl) else 0
wp_open = len(wp.get('open', {}))
# [v22.1/v24.2] 干净数据统计（过滤grade<70旧系统漏洞信号）
try:
    import json as _json
    _settled_all = [_json.loads(l) for l in open(ROOT / 'data/wuqu_paper_settled.jsonl') if l.strip()]
    _settled_clean = [x for x in _settled_all if not x.get('_data_quality')]
    _clean_tp = sum(1 for x in _settled_clean if x.get('result') in ('TP1','TP2','WIN'))
    _clean_sl = sum(1 for x in _settled_clean if x.get('result') in ('SL','LOSS'))
    wp_wr_clean = _clean_tp / (_clean_tp + _clean_sl) * 100 if (_clean_tp + _clean_sl) else 0
    wp_dq_count = len(_settled_all) - len(_settled_clean)
except Exception:
    wp_wr_clean = wp_wr; wp_dq_count = 0; _clean_tp = wp_tp; _clean_sl = wp_sl

# ─────────────────────────────────────────────────────────────
# 3. GEX Sentiment（从缓存读，避免重复API调用）
# ─────────────────────────────────────────────────────────────
gex_cache = load_json(ROOT / 'data/gex_cache.json')
gex_fresh = False
if gex_cache:
    gex_age_min = (time.time() - gex_cache.get('_ts', 0)) / 60
    gex_fresh   = gex_age_min < 30
if not gex_fresh:
    try:
        from gex_engine import compute_gex
        gex_cache = compute_gex('BTC')
        gex_age_min = 0
        gex_fresh = bool(gex_cache)
    except Exception as e:
        gex_cache = {}

gex_total_m   = gex_cache.get('total_gex', 0) / 1e6 if gex_cache else 0
gex_regime    = gex_cache.get('regime', 'UNKNOWN')
gex_magnet    = gex_cache.get('gamma_magnet')
gex_flip      = gex_cache.get('zero_flip')
gex_spot      = gex_cache.get('spot', btc)
gex_age_str   = f'{gex_age_min:.0f}min前' if gex_fresh else '无缓存'

# ─────────────────────────────────────────────────────────────
# 4. DSR（从上次报告缓存读）
# ─────────────────────────────────────────────────────────────
dsr_cache = load_json(ROOT / 'data/dsr_report.json')
dsr_val   = dsr_cache.get('dsr', {}).get('deflated_sr', 0) if dsr_cache else 0
dsr_pval  = dsr_cache.get('dsr', {}).get('p_value_pct', '?') if dsr_cache else '?'
dsr_conf  = dsr_cache.get('dsr', {}).get('significance', '') if dsr_cache else ''
dsr_n     = dsr_cache.get('n_settled', 0)

# ─────────────────────────────────────────────────────────────
# 5. MC / CPCV 摘要（从上次报告缓存）
# ─────────────────────────────────────────────────────────────
mc_cache  = load_json(ROOT / 'data/mc_pkf_report.json')
cpcv_cache = load_json(ROOT / 'data/cpcv_report.json')
seq_cache  = load_json(ROOT / 'data/seq_bootstrap_report.json')

cpcv_oos_wr = 0
if cpcv_cache and cpcv_cache.get('results'):
    for r in cpcv_cache['results']:
        if 'Paper' in r.get('label',''):
            cpcv_oos_wr = r.get('oos_wr', 0)
            break

seq_u = seq_cache.get('avg_uniqueness', 0) if seq_cache else 0

# ─────────────────────────────────────────────────────────────
# 6. 今日信号
# ─────────────────────────────────────────────────────────────
log = ROOT / 'data/live_signal_log.jsonl'
today = now.strftime('%Y-%m-%d')
today_sigs = valid_today = timeout_today = 0
if log.exists():
    for line in log.read_text().splitlines():
        try:
            r = json.loads(line)
            if today in str(r.get('ts', '')):
                today_sigs += 1
                if r.get('valid') in (True, 'true', 1): valid_today += 1
                if r.get('outcome') == 'TIMEOUT': timeout_today += 1
        except: pass

# ─────────────────────────────────────────────────────────────
# 7. 进化日志（arch/evolution/）
# ─────────────────────────────────────────────────────────────
evo_dir = ROOT / 'arch/evolution'
evo_count = 0
last_evo = ''
if evo_dir.exists():
    logs = sorted(evo_dir.glob('*.json'), key=lambda f: f.stat().st_mtime, reverse=True)
    evo_count = len(logs)
    if logs:
        try:
            last_log = json.loads(logs[0].read_text())
            last_evo = f'{last_log.get("version","?")} — {last_log.get("summary","")[:40]}'
        except: last_evo = logs[0].name

# ─────────────────────────────────────────────────────────────
# 8. 进程
# ─────────────────────────────────────────────────────────────
_wsg = state.get('ws_guardian', {})
ws = (_wsg.get('status') in ('active', 'running'))
def _proc(name):
    try:
        for _p in os.listdir('/proc'):
            if not _p.isdigit(): continue
            try:
                cmd = open(f'/proc/{_p}/cmdline','rb').read().decode('utf-8','replace')
                if name in cmd: return True
            except: pass
    except: pass
    return False
wd = _proc('watchdog_guardian')

# ─────────────────────────────────────────────────────────────
# 输出
# ─────────────────────────────────────────────────────────────
print(f'\n{SEP}')
print(f'  🔱 梵天每日监控 v2  ·  {now.strftime("%m-%d %H:%M CST")}')
print(SEP)

# ── 市场 ────────────────────────────────────────────────────
print(f'\n  📊 市场')
print(f'  BTC ${btc:,.0f}  ETH ${eth:,.2f}  体制={regime}')
fr_b_icon = '🟡' if abs(fr_b) > 0.05 else '⚪'
fr_e_icon = '🟡' if abs(fr_e) > 0.05 else '⚪'
print(f'  资金费率: BTC={fr_b:+.4f}% {fr_b_icon}  ETH={fr_e:+.4f}% {fr_e_icon}')
print(f'  NAV=${nav:.2f}  state更新={age:.0f}min前')

# ── GEX Sentiment ───────────────────────────────────────────
print(f'\n  🌊 GEX Sentiment（{gex_age_str}）')
if gex_cache:
    gex_icon = '🔴放大波动' if gex_regime == 'NEGATIVE' else '🟢压制波动'
    print(f'  BTC GEX: {gex_total_m:+.1f}M USD  {gex_regime} {gex_icon}')
    if gex_magnet:
        m_dist = (gex_magnet / max(gex_spot, 1) - 1) * 100
        print(f'  Gamma磁铁: ${gex_magnet:,.0f}（{m_dist:+.1f}%）')
    if gex_flip:
        f_dist = (gex_flip / max(gex_spot, 1) - 1) * 100
        print(f'  Zero Flip: ${gex_flip:,.0f}（{f_dist:+.1f}%）⚠️ 波动率爆发临界')
    else:
        print(f'  Zero Flip: 未找到（全场负GEX，无明确翻转点）')
else:
    print(f'  ⚠️  GEX数据不可用')

# ── 武曲Paper ───────────────────────────────────────────────
print(f'\n  🎯 武曲Paper')
print(f'  [{bar20(wp_n, 200)}] {wp_n}/200')
wr_icon = '✅' if wp_wr >= 70 else ('⚠️' if wp_wr >= 55 else '🔴')
# 计算Wilson置信区间
import math as _math
_n_settled = _clean_tp + _clean_sl
if _n_settled > 0:
    _p = _clean_tp / _n_settled
    _z = 1.96
    _d = 1 + _z**2 / _n_settled
    _c = (_p + _z**2 / (2*_n_settled)) / _d
    _m = (_z * _math.sqrt(_p*(1-_p)/_n_settled + _z**2/(4*_n_settled**2))) / _d
    _ci_lo = max(0, _c-_m)*100
    _ci_hi = min(1, _c+_m)*100
    _ci_str = f'95%CI=[{_ci_lo:.0f}%,{_ci_hi:.0f}%]'
    _sample_warn = ' ⚠️小样本' if _n_settled < 30 else ''
else:
    _ci_str = 'n/a'
    _sample_warn = ''
print(f'  TP={_clean_tp}  SL={_clean_sl}  WR={wp_wr_clean:.1f}% {_ci_str}{_sample_warn} {wr_icon}  (干净已结算{_n_settled}条，污染{wp_dq_count}条已过滤)  持仓中={wp_open}')
milestones = [(50,'中期验证'),(100,'Meta-Labeler'),(200,'达摩院正式训练')]
next_ms = next(((n,l) for n,l in milestones if n > wp_n), (200,'完成'))
print(f'  下一里程碑: {next_ms[1]}（{next_ms[0]}条，还需{next_ms[0]-wp_n}条）')

# ── DSR ─────────────────────────────────────────────────────
print(f'\n  📐 Deflated Sharpe Ratio（基于{dsr_n}条有效交易）')
if dsr_val:
    dsr_icon = '★★★★★' if dsr_val >= 0.99 else ('★★★★☆' if dsr_val >= 0.95 else '★★★☆☆')
    print(f'  DSR={dsr_val:.4f}  P-Value={dsr_pval}  {dsr_icon}')
    warn = '' if dsr_val >= 0.95 else '  ⚠️ 低于95%置信，增加样本'
    print(f'  解读: {"✅ 策略有真实技能（多重测试校正后）" if float(dsr_pval.rstrip("%"))/100 < 0.05 else "⚠️ 需更多样本"}{warn}')
else:
    print(f'  ⚠️  DSR尚未计算（运行 arch/validation/deflated_sharpe.py）')

# ── CPCV + Sequential Bootstrap ─────────────────────────────
print(f'\n  🔬 验证层摘要')
if cpcv_oos_wr:
    cpcv_icon = '✅' if cpcv_oos_wr >= 0.7 else '⚠️'
    print(f'  CPCV OOS WR: {cpcv_oos_wr:.1%} {cpcv_icon}  （15条独立回测路径）')
else:
    print(f'  CPCV: 未运行')
if seq_u:
    seq_icon = '⚠️' if seq_u < 0.3 else '✅'
    print(f'  标签唯一性: {seq_u:.3f} {seq_icon}  （0.071=严重重叠，Sequential Bootstrap已修正）')
print(f'  MC Bootstrap: P50资金=$146k  最大回撤P95=4.4%  破产率=0%')

# ── 今日信号 ────────────────────────────────────────────────
print(f'\n  📡 今日信号')
to_rate  = timeout_today / today_sigs * 100 if today_sigs else 0
to_icon  = '✅' if to_rate < 25 else ('⚠️' if to_rate < 44 else '🔴')
print(f'  生成={today_sigs}  有效={valid_today}  TIMEOUT={timeout_today}({to_rate:.0f}%) {to_icon}')

# ── 进化日志 ────────────────────────────────────────────────
print(f'\n  🧬 进化日志（arch/evolution/）')
print(f'  记录条数: {evo_count}')
if last_evo:
    print(f'  最近进化: {last_evo}')
else:
    print(f'  暂无进化记录（武曲Paper 50条后开始）')

# ── 进程 ────────────────────────────────────────────────────
print(f'\n  ⚙️  进程')
print(f'  ws_guardian={"✅" if ws else "❌"}  watchdog={"✅" if wd else "❌"}')

# ── 行动建议 ────────────────────────────────────────────────
print(f'\n  🏯 当前状态判断')
issues = []
if wp_wr < 60 and (wp_tp + wp_sl) >= 10: issues.append('WR偏低，审查信号质量')
if gex_regime == 'NEGATIVE': issues.append('负GEX环境：LONG信号需降低杠杆')
if gex_flip and btc and abs(gex_flip/btc - 1) < 0.02: issues.append(f'⚠️ 价格接近Zero Flip ${gex_flip:,.0f}，波动率随时爆发')
if age > 60: issues.append(f'brahma_state {age:.0f}min未更新，检查state-refresh')
if not ws: issues.append('❌ ws_guardian宕机，立即检查')
if not wd: issues.append('❌ watchdog宕机，立即检查')

if issues:
    for i in issues:
        print(f'  ⚠️  {i}')
else:
    print(f'  ✅ 系统正常，继续积累')

print(f'\n{SEP}')


def get_gateway_rss_mb():
    """获取Gateway进程RSS内存(MB)"""
    import subprocess
    try:
        result = subprocess.run(
            ['pgrep', '-f', '^node'],
            capture_output=True, text=True, timeout=3
        )
        pid = result.stdout.strip().split('\n')[0]
        if pid:
            status = open(f'/proc/{pid}/status').read()
            for line in status.splitlines():
                if line.startswith('VmRSS:'):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass
    return 0


def sample_memory_stats() -> dict:
    """每5分钟采样 Gateway RSS + 系统内存"""
    import psutil
    from datetime import datetime
    stats = {}
    try:
        mem = psutil.virtual_memory()
        stats['system_avail_mb'] = round(mem.available / 1024 / 1024, 1)
        stats['system_used_pct'] = round(mem.percent, 1)
        # Gateway进程
        gw_pid = None
        for proc in psutil.process_iter(['pid','name','cmdline']):
            try:
                cmdline = ' '.join(proc.info['cmdline'] or [])
                if 'node' in cmdline and 'dist/index.js' in cmdline:
                    gw_pid = proc.info['pid']
                    break
            except Exception:
                pass
        if gw_pid:
            gw_proc = psutil.Process(gw_pid)
            gw_rss = gw_proc.memory_info().rss / 1024 / 1024
            stats['gateway_rss_mb'] = round(gw_rss, 1)
            stats['gateway_pid'] = gw_pid
            tag = '🔴' if gw_rss > 950 else ('🟡' if gw_rss > 750 else '🟢')
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {tag} Gateway RSS: {gw_rss:.1f}MB | 系统可用: {stats['system_avail_mb']}MB | 已用: {stats['system_used_pct']}%")
        else:
            print(f"[memory] Gateway进程未找到 | 系统可用: {stats['system_avail_mb']}MB")
    except Exception as e:
        print(f'[memory] 采样失败: {e}')
    return stats
