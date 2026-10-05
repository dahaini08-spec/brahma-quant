"""
brahma_constants.py — 梵天系统全局常量 SSOT
[2026-10-05 苏摩111封印]

替代全库散布的路径硬编码和魔法字符串
使用方式: from brahma_constants import DATA_DIR, STATE_BTC, ...
"""
from pathlib import Path as _P

# ── 目录结构 ──────────────────────────────────────────
_HERE         = _P(__file__).resolve().parent
TRADING_ROOT  = _HERE.parent        # trading-system/
SCRIPTS_DIR   = TRADING_ROOT / 'scripts'
BRAIN_DIR     = TRADING_ROOT / 'brahma_brain'
DATA_DIR      = TRADING_ROOT / 'data'
LOGS_DIR      = TRADING_ROOT / 'logs'
OPENCLAW_MEDIA = TRADING_ROOT / 'openclaw-media'

# ── 状态文件 ──────────────────────────────────────────
STATE_BTC           = DATA_DIR / 'brahma_state_btc.json'
STATE_ETH           = DATA_DIR / 'brahma_state_eth.json'
STATE_COMPOSITE     = DATA_DIR / 'brahma_state.json'
AUTO_ANALYSIS       = DATA_DIR / 'auto_analysis_latest.json'
SIGNAL_QUEUE        = DATA_DIR / 'auto_signal_queue.json'
PAPER_ACCOUNT       = DATA_DIR / 'paper_account.json'
VIP_SIGNAL_STATE    = DATA_DIR / 'vip_signal_state.json'
META_COGNITION      = DATA_DIR / 'meta_cognition_state.json'
POSITION_SL_STATE   = DATA_DIR / 'position_sl_state.json'

# ── CVD 实时数据 ──────────────────────────────────────
CVD_BTC = DATA_DIR / 'cvd_realtime_btcusdt.json'
CVD_ETH = DATA_DIR / 'cvd_realtime_ethusdt.json'

# ── 清算热力图 ────────────────────────────────────────
LIQ_BTC = DATA_DIR / 'liq_heatmap_btcusdt.json'
LIQ_ETH = DATA_DIR / 'liq_heatmap_ethusdt.json'

# ── vol/beta ──────────────────────────────────────────
VOL_BETA_STATE = DATA_DIR / 'vol_beta_state.json'
HAR_RV_CACHE   = DATA_DIR / 'har_rv_cache.json'

# ── 便捷函数 ──────────────────────────────────────────
def state_file(sym: str) -> _P:
    """state_file('BTC') → data/brahma_state_btc.json"""
    return DATA_DIR / f'brahma_state_{sym.lower().replace("usdt","")}.json'

def cvd_file(sym: str) -> _P:
    """cvd_file('BTC') → data/cvd_realtime_btcusdt.json"""
    sym_lower = sym.lower().replace('usdt', '')
    return DATA_DIR / f'cvd_realtime_{sym_lower}usdt.json'

# ── 推送配置 ──────────────────────────────────────────
JARVIS_USER_ID   = '73295708'
JARVIS_THREAD_ID = '01a0f312-7e0c-7ae0-ae95-f66915d1d13c'
JARVIS_TARGET    = f'{JARVIS_USER_ID}:thread:{JARVIS_THREAD_ID}'

# ── 交易参数 ──────────────────────────────────────────
CORR_BTC_ETH     = 0.85   # BTC-ETH 相关性
MAX_NAV_SINGLE   = 0.10   # 单仓最大 NAV 10%
ATR_SL_MULTIPLIER = 1.5   # SL ≥ 1.5×ATR1H
MIN_RR           = 1.5    # 最小 Risk/Reward

# ── 体制参数 ──────────────────────────────────────────
REGIME_SCORE_GATE = {
    'CHOP_MID:LONG':      110,
    'CHOP_MID:SHORT':     110,
    'BEAR_EARLY:LONG':    118,
    'BEAR_TREND:LONG':    140,
    'BULL_TREND:SHORT':   130,
    'BEAR_RECOVERY:SHORT':130,
}
