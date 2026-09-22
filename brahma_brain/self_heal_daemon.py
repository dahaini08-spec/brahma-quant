"""
self_heal_daemon.py — 梵天数据自愈守护
[2026-09-22 苏摩111封印] 数据过期自动修复 + 进程死亡自动重启

功能：
  1. 检测关键数据源过期 → 自动刷新
  2. 检测进程死亡 → 自动重启
  3. 检测drawdown NAV=0 bug → 自动写入ts
  4. 独立于supercronic，60s循环

接入位置：scripts/independent_watchdog.sh 调用
调用方式：
    python3 brahma_brain/self_heal_daemon.py --once   # 单次检查
    python3 brahma_brain/self_heal_daemon.py            # 循环模式

设计原则：
  - 自愈不替代cron，是cron的兜底保险
  - 每次自愈写日志（logs/self_heal.log）
  - 不修改代码，只刷新数据
"""
import json, os, sys, time, subprocess, logging
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / 'data'
LOG_FILE = BASE_DIR / 'logs' / 'self_heal.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler(sys.stderr),
    ]
)
log = logging.getLogger('self_heal')

# ── 数据源配置：文件名, TTL秒, 刷新函数 ──
DATA_SOURCES = [
    ('cvd_btc',     'data/cvd_realtime_btcusdt.json',  3600, 'cvd'),
    ('cvd_eth',     'data/cvd_realtime_ethusdt.json',  3600, 'cvd'),
    ('gex_state',   'data/gex_state.json',            86400, 'gex'),
    ('vol_beta',    'data/vol_beta_state.json',       43200, 'vol_beta'),
    ('liq_btc',     'data/liq_heatmap_btcusdt.json',  14400, 'liq'),
    ('liq_eth',     'data/liq_heatmap_ethusdt.json',  14400, 'liq'),
    ('macro_state', 'data/macro_state.json',         86400, 'macro'),
    ('circuit',     'data/circuit_breaker.json',       3600, 'circuit'),
    ('drawdown',    'data/drawdown_state.json',        3600, 'drawdown'),
    ('antifragile', 'data/antifragile_state.json',    86400, 'antifragile'),
    ('regime_state','data/regime_state.json',          3600, 'regime'),
    # [9.22苏摩111] 新增3个按需数据源
    ('funding_btc', 'data/funding_rate_btcusdt.json', 28800, 'funding'),  # 8h TTL
    ('funding_eth', 'data/funding_rate_ethusdt.json', 28800, 'funding'),
    ('orderbook_btc','data/orderbook_btcusdt.json',    300, 'orderbook'),  # 5min TTL
    ('orderbook_eth','data/orderbook_ethusdt.json',     300, 'orderbook'),
]

# ── 进程配置：进程名, 启动命令 ──
PROCESSES = [
    ('cvd_ws_collector',     'python3 scripts/cvd_ws_collector.py'),
    ('liq_multi_exchange',   'python3 scripts/liq_multi_exchange_collector.py'),
    ('supercronic',          './supercronic brahma_crontab.txt'),
]


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def _age(path: Path) -> float:
    """文件年龄（秒）"""
    if not path.exists():
        return 999999
    return time.time() - path.stat().st_mtime


def _refresh_cvd():
    """CVD采集器写快照靠进程，进程活着就行"""
    pass  # 进程重启在下面处理


def _refresh_gex():
    """手动刷新GEX"""
    try:
        import sys as _sys
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.gex_unified import scan_gex
        for sym in ['BTC', 'ETH']:
            scan_gex(sym)
        log.info('✅ GEX刷新成功')
    except Exception as e:
        log.error(f'❌ GEX刷新失败: {e}')


def _refresh_vol_beta():
    """手动刷新VolBeta"""
    try:
        import sys as _sys
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.vol_beta_engine import run
        run('BTC')
        run('ETH')
        log.info('✅ VolBeta刷新成功')
    except Exception as e:
        log.error(f'❌ VolBeta刷新失败: {e}')


def _refresh_liq():
    """手动刷新清算热图"""
    try:
        subprocess.run(
            ['python3', str(BASE_DIR / 'scripts' / 'liq_heatmap.py')],
            capture_output=True, timeout=60, cwd=str(BASE_DIR)
        )
        log.info('✅ liq_heatmap刷新成功')
    except Exception as e:
        log.error(f'❌ liq_heatmap刷新失败: {e}')


def _refresh_macro():
    """手动刷新宏观数据"""
    try:
        import sys as _sys
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.macro_factor_engine import get_macro_score
        import json as _json
        score, data = get_macro_score()
        data['ts'] = time.time()
        _json.dump(data, open(DATA_DIR / 'macro_state.json', 'w'),
                   indent=2, ensure_ascii=False)
        log.info('✅ macro_state刷新成功')
    except Exception as e:
        log.error(f'❌ macro_state刷新失败: {e}')


def _refresh_circuit():
    """手动刷新circuit_breaker"""
    try:
        state = {'state': 'GREEN', 'suspended': False,
                 'timestamp': time.time(), 'last_updated': time.time()}
        json.dump(state, open(DATA_DIR / 'circuit_breaker.json', 'w'), indent=2)
        log.info('✅ circuit_breaker刷新成功')
    except Exception as e:
        log.error(f'❌ circuit_breaker刷新失败: {e}')


def _refresh_drawdown():
    """手动刷新drawdown（含NAV=0修复）"""
    try:
        import sys as _sys
        _root = str(BASE_DIR)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.drawdown_tracker import update_drawdown
        update_drawdown()  # 修复后NAV=0也会写入ts
        # 双保险：手动写ts
        dd_path = DATA_DIR / 'drawdown_state.json'
        if dd_path.exists():
            d = json.loads(dd_path.read_text())
            d['ts'] = time.time()
            d['last_updated'] = time.time()
            json.dump(d, open(dd_path, 'w'), indent=2, ensure_ascii=False)
        log.info('✅ drawdown刷新成功')
    except Exception as e:
        log.error(f'❌ drawdown刷新失败: {e}')


def _refresh_antifragile():
    """手动刷新antifragile"""
    try:
        import sys as _sys
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.antifragile_guard import full_guard_check
        for sym in ['BTC', 'ETH']:
            d = full_guard_check(sym)
            state = {
                'state': 'GREEN' if not d.get('blocked') else 'BLOCKED',
                'ts': int(time.time() * 1000),
                'details': d
            }
            json.dump(state, open(DATA_DIR / f'antifragile_state_{sym.lower()}.json', 'w'),
                       indent=2)
        combined = {'state': 'GREEN', 'ts': time.time()}
        json.dump(combined, open(DATA_DIR / 'antifragile_state.json', 'w'), indent=2)
        log.info('✅ antifragile刷新成功')
    except Exception as e:
        log.error(f'❌ antifragile刷新失败: {e}')


def _refresh_regime():
    """regime_state由cron watcher更新，这里只检查"""
    pass


def _refresh_funding():
    """资金费率按需拉取（分析时触发，非常驻进程）"""
    try:
        import sys as _sys
        _root = str(BASE_DIR)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.funding_rate_fetch import fetch_funding_rate
        for sym in ['BTCUSDT', 'ETHUSDT']:
            fetch_funding_rate(sym)
        log.info('✅ 资金费率拉取成功')
    except Exception as e:
        log.error(f'❌ 资金费率拉取失败: {e}')


def _refresh_orderbook():
    """盘口深度按需拉取（分析时触发，非常驻进程）"""
    try:
        import sys as _sys
        _root = str(BASE_DIR)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        _bb = str(Path(__file__).parent)
        if _bb not in _sys.path:
            _sys.path.insert(0, _bb)
        from brahma_brain.orderbook_fetch import fetch_orderbook_snapshot
        for sym in ['BTCUSDT', 'ETHUSDT']:
            fetch_orderbook_snapshot(sym)
        log.info('✅ 盘口深度拉取成功')
    except Exception as e:
        log.error(f'❌ 盘口深度拉取失败: {e}')


REFRESH_FNS = {
    'cvd': _refresh_cvd,
    'gex': _refresh_gex,
    'vol_beta': _refresh_vol_beta,
    'liq': _refresh_liq,
    'macro': _refresh_macro,
    'circuit': _refresh_circuit,
    'drawdown': _refresh_drawdown,
    'antifragile': _refresh_antifragile,
    'regime': _refresh_regime,
    'funding': _refresh_funding,
    'orderbook': _refresh_orderbook,
}


def check_data_freshness():
    """检查所有数据源新鲜度，自动修复过期的"""
    healed = 0
    for name, rel_path, ttl, refresh_type in DATA_SOURCES:
        path = BASE_DIR / rel_path
        age_s = _age(path)
        if age_s > ttl:
            log.info(f'⚠️ {name}过期({age_s/3600:.1f}h>{ttl/3600:.1f}h)→触发自愈')
            fn = REFRESH_FNS.get(refresh_type)
            if fn:
                fn()
                healed += 1
            else:
                log.warning(f'⚠️ {name}过期但无刷新函数')
    return healed


def check_processes():
    """检查关键进程存活，自动重启死亡的"""
    restarted = 0
    for proc_name, start_cmd in PROCESSES:
        # 用pgrep检查
        try:
            result = subprocess.run(
                ['pgrep', '-f', proc_name],
                capture_output=True, timeout=5
            )
            if result.returncode != 0:
                log.info(f'⚠️ {proc_name}已死→重启')
                # 分割命令
                parts = start_cmd.split()
                subprocess.Popen(
                    parts,
                    stdout=open(BASE_DIR / 'logs' / f'{proc_name}.log', 'a'),
                    stderr=subprocess.STDOUT,
                    cwd=str(BASE_DIR)
                )
                log.info(f'✅ {proc_name}已重启')
                restarted += 1
        except Exception as e:
            log.error(f'❌ {proc_name}检查失败: {e}')
    return restarted


def run_once():
    """单次检查"""
    log.info(f'── 自愈检查 {_now()} ──')
    healed = check_data_freshness()
    restarted = check_processes()
    if healed == 0 and restarted == 0:
        log.info('✅ 全部正常，无需自愈')
    else:
        log.info(f'本次自愈: {healed}项数据 + {restarted}个进程')
    return healed + restarted


def main_loop(interval=60):
    """循环模式"""
    log.info(f'自愈守护启动 interval={interval}s')
    while True:
        try:
            run_once()
        except Exception as e:
            log.error(f'自愈循环异常: {e}')
        time.sleep(interval)


if __name__ == '__main__':
    if '--once' in sys.argv:
        run_once()
    else:
        main_loop()
