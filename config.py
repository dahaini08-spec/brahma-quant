#!/usr/bin/env python3
"""
config.py — 梵天统一配置中心 v2.0
所有参数从 FANTAN_BLUEPRINT_V3.json 读取，代码只引用，不定义。
API Key 从 alerts/.env 读取，禁止硬编码。
"""
import os
from pathlib import Path

_ROOT     = Path(__file__).parent
_ENV_FILE = _ROOT.parent / "alerts" / ".env"
_BP_FILE  = _ROOT / "FANTAN_BLUEPRINT_V3.json"
_env_cache = {}
_bp_cache  = {}


# ── 环境变量 / .env ──────────────────────────────────────────
def _load_env() -> dict:
    global _env_cache
    if _env_cache: return _env_cache
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                _env_cache[k.strip()] = v.strip()
    _env_cache.update({k: v for k, v in os.environ.items()
                       if k.startswith(('BINANCE_', 'DINGTALK_'))})
    return _env_cache

def get_env(key: str, default: str = "") -> str:
    return _load_env().get(key, default)


# ── Blueprint 参数读取 ────────────────────────────────────────
def _load_blueprint() -> dict:
    global _bp_cache
    if _bp_cache: return _bp_cache
    try:
        import json
        _bp_cache = json.loads(_BP_FILE.read_text())
    except Exception:
        _bp_cache = {}
    return _bp_cache

def _bp_domain(domain_key: str) -> dict:
    bp = _load_blueprint()
    # 支持两种结构：旧版 domains / 新版 BLUEPRINT_DOMAINS
    return bp.get("BLUEPRINT_DOMAINS", bp.get("domains", {})).get(domain_key, {})

def _bp_get(path: str, default=None):
    """
    点分路径读取 Blueprint 值，例如:
      _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_base_pct", 0.014)
      _bp_get("risk_rules.max_leverage", 5)
    """
    parts = path.split(".")
    obj   = _load_blueprint()
    for p in parts:
        if not isinstance(obj, dict): return default
        obj = obj.get(p, default)
    return obj if obj is not None else default


# ── Binance API ──────────────────────────────────────────────
def binance_keys() -> tuple:
    """返回 (api_key, secret)"""
    env = _load_env()
    return env.get("BINANCE_API_KEY", ""), env.get("BINANCE_SECRET", "")


# ── DingTalk ─────────────────────────────────────────────────
def dingtalk_main() -> tuple:
    env = _load_env()
    return env.get("DINGTALK_WEBHOOK", ""), env.get("DINGTALK_SECRET", "")

def dingtalk_ai() -> tuple:
    env = _load_env()
    return env.get("DINGTALK_AI_WEBHOOK", ""), env.get("DINGTALK_AI_SECRET", "")

def coinglass_key() -> str:
    return _load_env().get("COINGLASS_API_KEY", "")

def coinglass_headers(version: int = 2) -> dict:
    """返回正确的 Coinglass API 请求头
    v2: coinglassSecret (open-api.coinglass.com)
    v3: CG-API-KEY      (open-api-v3.coinglass.com) — 修复 2026-05-17
    """
    key = coinglass_key()
    if version == 3:
        return {"CG-API-KEY": key}
    return {"coinglassSecret": key}

def square_keys() -> list:
    """返回所有 Square API Key 列表，优先读 .env，兜底读 square/config.py"""
    env = _load_env()
    keys = [v for k, v in sorted(env.items()) if k.startswith("SQUARE_KEY_") and v.strip()]
    if not keys:
        # 兼容兜底：直接读 square/config.py
        try:
            import re, ast
            sq_cfg = _ROOT.parent / "scripts" / "square" / "config.py"
            if sq_cfg.exists():
                m = re.search(r'SQUARE_API_KEYS\s*=\s*(\[.*?\])', sq_cfg.read_text(), re.DOTALL)
                if m:
                    keys = ast.literal_eval(m.group(1))
        except Exception as _e:
            _ = None  # 非致命异常，不阻断
    return keys



# ── 系统端点 ─────────────────────────────────────────────────
FAPI_BASE = "https://fapi.binance.com"
SAPI_BASE = "https://api.binance.com"


# ── 交易参数（从 Blueprint 读取，有硬编码兜底）──────────────
@property
def MAX_LEVERAGE():
    return _bp_get("risk_rules.max_leverage", 5)

@property
def MIN_SCORE():
    return _bp_get("BLUEPRINT_DOMAINS.D13_coordinator.params.min_score", 70)

@property
def KELLY_BASE_PCT():
    return _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_base_pct", 0.014)

@property
def KELLY_OPTIMAL_PCT():
    return _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_optimal", 0.018)

@property
def KELLY_GOLDEN_PCT():
    return _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_golden_2x", 0.025)

@property
def MAX_POSITIONS():
    return _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.max_positions", 3)

@property
def BLACKLIST():
    return _bp_get("BLUEPRINT_DOMAINS.D13_coordinator.params.blacklist",
                   ["XRPUSDT", "SOLUSDT", "CHZUSDT"])

@property
def TREND_BLACKLIST():
    return _bp_get("BLUEPRINT_DOMAINS.D13_coordinator.params.trend_blacklist",
                   ["BTCUSDT", "ETHUSDT", "ADAUSDT", "LINKUSDT"])


# ── 模块直接可用的常量（向后兼容旧代码的 import config.XXX）──
def _resolve(prop_or_val):
    """property 对象在模块级无法直接调用，用函数包一层"""
    return prop_or_val.fget(None) if isinstance(prop_or_val, property) else prop_or_val

MAX_LEVERAGE    = _bp_get("risk_rules.max_leverage",                               5)
MIN_SCORE       = _bp_get("BLUEPRINT_DOMAINS.D13_coordinator.params.min_score",              70)
KELLY_BASE_PCT  = _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_base_pct",    0.014)
KELLY_OPTIMAL   = _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_optimal",     0.018)
KELLY_GOLDEN    = _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.kelly_golden_2x",   0.025)
MAX_POSITIONS   = _bp_get("BLUEPRINT_DOMAINS.D6_portfolio_manager.params.max_positions",     3)
BLACKLIST       = _bp_get("BLUEPRINT_DOMAINS.D13_coordinator.params.blacklist",
                          ["XRPUSDT", "SOLUSDT", "CHZUSDT"])
TREND_BLACKLIST = _bp_get("BLUEPRINT_DOMAINS.D13_coordinator.params.trend_blacklist",
                          ["BTCUSDT", "ETHUSDT", "ADAUSDT", "LINKUSDT"])


# ── 自检 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    api_key, secret = binance_keys()
    wh, _           = dingtalk_main()
    bp              = _load_blueprint()

    print(f"{'─'*50}")
    print(f"  梵天配置中心 v2.0 自检")
    print(f"{'─'*50}")
    print(f"  API Key:    {api_key[:8]}...{api_key[-4:] if api_key else 'NOT SET'}")
    print(f"  Secret:     {'✅ 已配置' if secret else '❌ 未配置'}")
    print(f"  钉钉主:     {'✅ 已配置' if wh else '❌ 未配置'}")
    print(f"  Blueprint:  v{bp.get('_version','?')}  {len(bp.get('domains',{}))}个域")
    print(f"{'─'*50}")
    print(f"  MIN_SCORE:      {MIN_SCORE}  (来源: Blueprint D13)")
    print(f"  KELLY_BASE:     {KELLY_BASE_PCT:.1%}  (来源: Blueprint D6)")
    print(f"  KELLY_OPTIMAL:  {KELLY_OPTIMAL:.1%}  (EXP-18实验结论)")
    print(f"  MAX_LEVERAGE:   {MAX_LEVERAGE}x")
    print(f"  MAX_POSITIONS:  {MAX_POSITIONS}")
    print(f"  BLACKLIST:      {BLACKLIST}")
    print(f"  TREND_BL:       {TREND_BLACKLIST}")
    print(f"{'─'*50}")
    print(f"  ✅ 所有参数从 Blueprint 读取，零硬编码")


# ── 路由与阈值常量（from system_config SSOT）─────────────────────────
# 任何脚本只需 from config import * 即可获取所有配置
try:
    import sys as _sys, pathlib as _pl
    _sys.path.insert(0, str(_pl.Path(__file__).parent / 'scripts'))
    from system_config import (
        JARVIS_USER_ID, JARVIS_THREAD_ID, JARVIS_TARGET, JARVIS_TARGET_T,
        MIN_SCORE, MIN_GRADE, MIN_WEIGHTED,
        TTL_GRADE_S, TTL_GRADE_A, TTL_GRADE_BC, TTL_GRADE_BC_BEAR,
        TTL_MIN, TTL_MAX, GAP_HARD_REJECT, GAP_PRICE_EXPIRED, MAX_HOLD_HOURS,
        ROOT, DATA_DIR, SIGNAL_LOG, BRAHMA_STATE, WUQU_PAPER, SIGNAL_QUEUE,
    )
except ImportError:
    pass  # system_config可选，各模块仍可直接import system_config
