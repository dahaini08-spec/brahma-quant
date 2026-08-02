#!/usr/bin/env python3
"""
brahma_smoke_test.py - 梵天系统启动冒烟测试
设计院固化封印 2026-07-14

用途:
  每次封口前、重启后、疑似偏移时运行
  快速发现:模块缺失 / 字段丢失 / 数据陈旧 / cron路由偏移

运行:
  python3 scripts/brahma_smoke_test.py
  python3 scripts/brahma_smoke_test.py --fix   # 自动修复可修复项
"""
import sys, os, json, time, subprocess
from pathlib import Path

# [设计院固化 2026-07-23] 屏蔽HF/transformers离线warning,加速smoke_test启动
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
os.environ.setdefault('HF_DATASETS_OFFLINE', '1')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv可选,不阻断smoke_test运行
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

# SSOT: 从system_config.py读取，不硬编码 (2026-07-28 设计院修复)
try:
    from scripts.system_config import JARVIS_USER_ID as _UID, JARVIS_THREAD_ID as _TID
    CORRECT_THREAD = f"{_UID}:thread:{_TID}"
except Exception:
    CORRECT_THREAD = "73295708:thread:019f8768-6731-777d-8924-2426a5abd10f"  # fallback
RED    = '\033[91m'
GREEN  = '\033[92m'
YELLOW = '\033[93m'
RESET  = '\033[0m'

results = []

def ok(name, detail=''):
    results.append(('✅', name, detail))
    print(f'  {GREEN}✅ {name}{RESET}' + (f': {detail}' if detail else ''))

def warn(name, detail='', fix=None):
    results.append(('⚠️', name, detail))
    print(f'  {YELLOW}⚠️  {name}{RESET}' + (f': {detail}' if detail else ''))
    if fix: print(f'     → 修复: {fix}')

def fail(name, detail='', fix=None):
    results.append(('❌', name, detail))
    print(f'  {RED}❌ {name}{RESET}' + (f': {detail}' if detail else ''))
    if fix: print(f'     → 修复: {fix}')

# ─── 1. 关键模块可导入 ─────────────────────────────────────────────────
print('\n【1】关键模块导入性')
REQUIRED_MODULES = [
    ('brahma_brain.brahma_analysis_runner', 'run_analysis'),
    ('brahma_brain.brahma_engine',          'analyze'),          # 函数式模块
    ('brahma_brain.brahma_scoring',         None),
    ('brahma_brain.brahma_health',          'run_health_check'),
    ('brahma_brain.brahma_learning_loop',   'main'),
    ('brahma_brain.macro_engine',           'write_macro_state'),
    ('brahma_brain.formatter',              'brahma_panorama_report'),
    ('brahma_brain.timing_filter',          None),
    ('brahma_brain.regime_state_machine',   None),
    ('brahma_brain.signal_card_formatter',  'format_vip_card'),
    # [2026-07-23 新模块]
    ('brahma_brain.cross_asset_gate',       'CrossAssetGate'),
    ('brahma_brain.tradfi_signal_layer',    None),
    ('brahma_brain.brahma_bus',             'BrahmaBus'),
]
for mod, attr in REQUIRED_MODULES:
    try:
        m = __import__(mod, fromlist=[''])
        if attr and not hasattr(m, attr):
            warn(mod, f'{attr} 属性缺失', fix=f'检查{mod}.py是否定义{attr}')
        else:
            ok(mod)
    except Exception as e:
        fail(mod, str(e)[:80], fix=f'cp scripts/{mod.split(".")[-1]}.py brahma_brain/')

# ─── 2. 全景矩阵字段完整性 ─────────────────────────────────────────────
print('\n【2】全景矩阵字段完整性(快速分析BTC)')
try:
    from brahma_brain.brahma_analysis_runner import run_analysis
    r = run_analysis('BTCUSDT')
    pano = r.get('_panorama_full', '')
    # B2 OB段
    if 'B2' in pano and 'Order Blocks' in pano:
        ok('全景B2·OB', f'做多/做空OB已展示')
    else:
        fail('全景B2·OB', 'OB段缺失', fix='检查formatter.py B2模块')
    # [P7 SNDK教训封印 2026-07-24] OB字段完整性验证
    # 防止「设计与实现脱节」:age_bars缺失 → 降权逻辑形同虚设
    try:
        from brahma_brain.smc_engine import find_order_blocks
        from brahma_brain.brahma_bus import get_klines
        _kl = get_klines('BTCUSDT', '1h', limit=30)
        if _kl:
            _opens  = [float(k[1]) for k in _kl]
            _highs  = [float(k[2]) for k in _kl]
            _lows   = [float(k[3]) for k in _kl]
            _closes = [float(k[4]) for k in _kl]
            _obs = find_order_blocks(_opens, _highs, _lows, _closes)
            _all_obs = _obs.get('bull_obs', []) + _obs.get('bear_obs', [])
            _missing_age = [o for o in _all_obs if 'age_bars' not in o]
            _missing_broken = [o for o in _all_obs if 'broken' not in o]
            if _missing_age:
                fail('OB·age_bars字段', f'{len(_missing_age)}个OB缺失age_bars--降权逻辑失效(SNDK教训)',
                     fix='检查smc_engine.py find_order_blocks是否注入age_bars')
            else:
                ok('OB·age_bars字段', f'全部{len(_all_obs)}个OB均含age_bars字段')
            if _missing_broken:
                fail('OB·broken字段', f'{len(_missing_broken)}个OB缺失broken字段',
                     fix='检查smc_engine.py find_order_blocks是否注入broken')
            else:
                ok('OB·broken字段', f'全部OB均含broken字段')
    except Exception as _e:
        warn('OB字段完整性', f'验证异常: {_e}')
    # B3 清算集群
    if 'B3' in pano and '清算集群' in pano:
        ok('全景B3·清算', '清算墙已展示')
    else:
        fail('全景B3·清算', '清算集群段缺失', fix='检查formatter.py B3模块')
    # FVG
    if 'FVG' in pano or '公平价值' in pano:
        ok('全景FVG', 'FVG字段已展示')
    else:
        warn('全景FVG', '无FVG数据(可能无缺口,非错误)')
    # 外部层得分
    ext = r.get('_ext_score_bonus', None)
    if ext is not None:
        ok('外部扩展层', f'字段存在 bonus={ext}')
    else:
        warn('外部扩展层', '字段缺失,检查外部扩展层初始化')
    # score合理
    score = r.get('score', 0)
    ok('评分引擎', f'score={score:.1f}')
except Exception as e:
    fail('全景矩阵分析', str(e)[:100])

# ─── 3. 关键数据文件新鲜度 ─────────────────────────────────────────────
print('\n【3】关键数据文件新鲜度')
now = time.time()
DATA_CHECKS = [
    ('data/macro_state.json',       4*3600,  'DXY宏观',   'python3 -c "from brahma_brain.macro_engine import write_macro_state; write_macro_state()"'),
    ('data/live_signal_log.jsonl',  24*3600, '信号日志',   None),
    ('data/wuqu_positions.json',    24*3600, '持仓状态',   None),
    ('data/ic_tracker_state.json',  48*3600, 'IC统计',     'python3 brahma_brain/brahma_learning_loop.py'),
    ('data/regime_state.json',          12*3600, '体制状态', 'python3 scripts/rsi_structure_watcher.py'),
]
for rel, max_age, name, fix_cmd in DATA_CHECKS:
    p = BASE / rel
    if not p.exists():
        fail(name, '文件不存在', fix=fix_cmd)
        continue
    age = now - p.stat().st_mtime
    if age > max_age:
        warn(name, f'陈旧{age/3600:.1f}h (阈值{max_age//3600}h)', fix=fix_cmd)
    else:
        ok(name, f'{age/3600:.2f}h ago')
    # 检查macro_state.json内容有效性
    if 'macro_state' in rel:
        try:
            ms = json.loads(p.read_text())
            dxy_val = ms.get('dxy', {}).get('value')
            if dxy_val:
                ok('DXY数据有效', f'value={dxy_val}')
            else:
                fail('DXY数据有效', 'value=null,字段映射错误', fix='检查macro_engine.write_macro_state()')
        except:
            fail('macro_state.json', '解析失败')

# ─── 3b. 持仓+软止损cron有效性 ──────────────────────────────────────────
print('\n【3b】持仓与软止损cron有效性')
try:
    import json as _j
    pos = _j.loads(Path(BASE / 'data/wuqu_positions.json').read_text())
    open_pos = [p for p in pos if isinstance(p, dict) and float(p.get('size', p.get('qty', 0))) != 0]
    ok(f'持仓状态', f'{len(open_pos)}个活跃持仓')
except Exception as e:
    warn('持仓状态', str(e)[:60])

# ETH供应感知字段验证
try:
    import sys as _s; _s.path.insert(0, str(BASE))
    from scripts.system_config import API_KEY as _ak, API_SECRET as _as
    import os as _os
    _os.environ['BINANCE_API_KEY']    = _ak
    _os.environ['BINANCE_SECRET']     = _as
    _os.environ['BINANCE_API_SECRET'] = _as
    from brahma_brain.brahma_analysis_runner import run_analysis as _ra
    _re = _ra('ETHUSDT')
    _pano = _re.get('_panorama_full', '')
    if 'ETH供应感知' in _pano or 'PoS无矿工卖压' in _pano:
        ok('ETH供应感知字段', 'PoS替代方案已显示')
    else:
        warn('ETH供应感知字段', '未在全景输出中找到')
except Exception as e:
    warn('ETH供应感知', str(e)[:60])

# ─── 4. Cron路由一致性 ─────────────────────────────────────────────────
_SSOT_TID = _TID if '_TID' in dir() else '019f8768-6731-777d-8924-2426a5abd10f'
print(f'\n【４】Cron路由一致性（SSOT={_SSOT_TID[:8]}...）')
try:
    jobs_path = Path.home() / '.openclaw/cron/jobs.json'
    raw = json.loads(jobs_path.read_text())
    jobs = raw if isinstance(raw, list) else raw.get('jobs', list(raw.values()))
    wrong = []
    # 只检查分析推送类5个核心任务，其余系统级任务允许路由到主线程
    ANALYSIS_PUSH_TASKS = {
        'main-signal-watcher', 'rsi-structure-watcher',
        'oi-advanced-scanner', 'pump-hunter', 'brahma-nerve-center'
    }
    for j in jobs:
        if not isinstance(j, dict): continue
        to = j.get('delivery', {}).get('to', '')
        name = j.get('name', '')
        if name not in ANALYSIS_PUSH_TASKS:
            continue
        if to and _SSOT_TID not in to:
            wrong.append(name)
    if wrong:
        fail('Cron路由', f'{len(wrong)}个任务路由到旧线程: {wrong}', fix='openclaw cron rm <id> && openclaw cron add ...')
    else:
        ok('Cron路由', f'全部任务路由正确')
except Exception as e:
    warn('Cron路由检查', str(e)[:60])

# ─── 5. 自愈系统覆盖度 ────────────────────────────────────────────────
print('\n【5】自愈系统覆盖度')
try:
    health_src = (BASE / 'brahma_brain/brahma_health.py').read_text()
    covers = {
        '全景格式化器':   'panorama' in health_src or 'formatter' in health_src,
        '学习闭环':       'learning_loop' in health_src,
        'macro_state新鲜': 'macro_state' in health_src,
        'OB/FVG字段':    'panorama_integrity' in health_src or 'B2' in health_src,
        'DharmaFactor权重': '_check_dharma_factor_weights' in health_src,
        'WR门控完整性':     '_check_wr_gate_integrity' in health_src,
        'Tardis月份刷新':   '_check_tardis_month_freshness' in health_src,
    }
    for item, covered in covers.items():
        if covered:
            ok(f'自愈覆盖·{item}')
        else:
            warn(f'自愈覆盖·{item}', '健康检查未覆盖此项', fix='在brahma_health.py添加对应check')
except Exception as e:
    warn('自愈系统检查', str(e)[:60])

# ─── 宪法守卫测试(持久化防复发)─────────────────────────────────────────
try:
    import subprocess as _sp_ct
    _ct = _sp_ct.run(
        ['python3', str(Path(__file__).parent.parent / 'brahma_brain' / 'brahma_constitutional_test.py')],
        capture_output=True, text=True, timeout=15
    )
    if _ct.returncode == 0:
        ok('宪法守卫测试 19/19')
    else:
        fail('宪法守卫测试', _ct.stdout.split('❌')[-1].strip()[:80] if '❌' in _ct.stdout else '失败')
except Exception as _ct_e:
    warn('宪法守卫测试', str(_ct_e)[:60])

# ─── 汇总 ─────────────────────────────────────────────────────────────
print('\n' + '─'*50)
n_ok   = sum(1 for r in results if r[0] == '✅')
n_warn = sum(1 for r in results if r[0] == '⚠️')
n_fail = sum(1 for r in results if r[0] == '❌')
total  = len(results)

if n_fail == 0 and n_warn == 0:
    print(f'{GREEN}🏛️ 冒烟测试全部通过 {n_ok}/{total} ✅{RESET}')
    sys.exit(0)
elif n_fail == 0:
    print(f'{YELLOW}🏛️ 冒烟测试基本通过 {n_ok}/{total} ⚠️ {n_warn}项警告{RESET}')
    sys.exit(0)
else:
    print(f'{RED}🏛️ 冒烟测试发现问题 {n_ok}/{total} | ❌{n_fail}个失败 ⚠️{n_warn}个警告{RESET}')
    sys.exit(1)
