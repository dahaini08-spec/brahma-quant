"""
safety.py — 梵天全局安全闸 v1.0
设计院 2026-07-08 | 第三方审计P0-0/P0-1修复

用法:
    from brahma_brain.safety import require_live_trading, safety_config
    require_live_trading()  # 执行层必须调用，否则抛出异常

fail-closed原则: 任何配置读取失败，默认禁止执行
"""
import os, sys
from pathlib import Path

_BASE = Path(__file__).parent.parent
_SAFETY_FILE = _BASE / 'config' / 'safety.yaml'

# ── 读取配置 ──────────────────────────────────────────────────────
def _load_safety_config() -> dict:
    """读取 safety.yaml，失败时 fail-closed"""
    defaults = {
        'global': {
            'signal_only': False,
            'paper_only': False,
            'live_trading_enabled': False,  # fail-closed: 默认禁止
            'fail_closed': True,
        },
        'execution': {
            'allow_import_executor': False,
            'allow_market_order': False,
            'allow_limit_order': False,
            'require_risk_approval': True,
        },
        'risk': {
            'max_single_nav_pct': 0.05,
            'max_daily_loss_pct': 0.03,
            'max_concurrent_positions': 2,
            'min_score_threshold': 135,
        },
    }
    try:
        import yaml  # type: ignore
        if _SAFETY_FILE.exists():
            with open(_SAFETY_FILE) as f:
                loaded = yaml.safe_load(f) or {}
            # 深度合并
            for section, vals in loaded.items():
                if section in defaults and isinstance(vals, dict):
                    defaults[section].update(vals)
                else:
                    defaults[section] = vals
    except ImportError:
        # yaml未安装：从环境变量读取关键字段
        live_env = os.environ.get('BRAHMA_LIVE_TRADING_ENABLED', 'false').lower()
        defaults['global']['live_trading_enabled'] = (live_env == 'true')
        defaults['execution']['allow_import_executor'] = (live_env == 'true')
    except Exception:
        pass  # fail-closed: 返回保守默认值
    return defaults

# 单例缓存
_cfg = None

def safety_config() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = _load_safety_config()
    return _cfg

def reset_cache():
    """测试用：清除缓存"""
    global _cfg
    _cfg = None


# ── 安全门 API ───────────────────────────────────────────────────
def is_live_trading_enabled() -> bool:
    """检查实盘交易是否启用"""
    cfg = safety_config()
    env_override = os.environ.get('BRAHMA_LIVE_TRADING_ENABLED', '').lower()
    if env_override:
        return env_override == 'true'
    return bool(cfg.get('global', {}).get('live_trading_enabled', False))

def is_paper_only() -> bool:
    cfg = safety_config()
    return bool(cfg.get('global', {}).get('paper_only', True))

def require_live_trading(caller: str = ''):
    """
    执行层必须调用此函数。
    如果实盘未启用，抛出 RuntimeError。
    """
    if not is_live_trading_enabled():
        raise RuntimeError(
            f"[SafetyGate] 实盘交易未启用 — "
            f"{'caller='+caller+' ' if caller else ''}"
            f"设置 BRAHMA_LIVE_TRADING_ENABLED=true 或 config/safety.yaml live_trading_enabled: true"
        )

def require_api_keys():
    """检查API密钥是否从环境变量正确加载（非空）"""
    key = os.environ.get('BINANCE_API_KEY', '')
    sec = os.environ.get('BINANCE_SECRET', '')
    if not key or not sec:
        raise RuntimeError(
            "[SafetyGate] BINANCE_API_KEY / BINANCE_SECRET 未配置 — "
            "请在环境变量或 .env 文件中设置，禁止硬编码"
        )
    # 检查是否仍是泄露的旧密钥（前8位特征）
    LEAKED_PREFIXES = ['sDqoRAye', 'hXQnzQco']
    for prefix in LEAKED_PREFIXES:
        if key.startswith(prefix) or sec.startswith(prefix):
            raise RuntimeError(
                "[SafetyGate] 🚨 检测到已泄露的API密钥！"
                "请立即撤销旧密钥并生成新密钥"
            )

def get_max_nav_pct(symbol: str = '') -> float:
    """获取单笔最大NAV比例"""
    cfg = safety_config()
    return float(cfg.get('risk', {}).get('max_single_nav_pct', 0.05))

def get_min_score() -> float:
    cfg = safety_config()
    return float(cfg.get('risk', {}).get('min_score_threshold', 135))


# ── 安全报告 ──────────────────────────────────────────────────────
def safety_report() -> dict:
    """返回当前安全状态报告"""
    cfg = safety_config()
    key = os.environ.get('BINANCE_API_KEY', '')
    sec = os.environ.get('BINANCE_SECRET', '')
    LEAKED = ['sDqoRAye', 'hXQnzQco']

    return {
        'live_trading_enabled': is_live_trading_enabled(),
        'paper_only':           is_paper_only(),
        'api_key_set':          bool(key) and not any(key.startswith(p) for p in LEAKED),
        'api_secret_set':       bool(sec) and not any(sec.startswith(p) for p in LEAKED),
        'leaked_key_detected':  any(key.startswith(p) or sec.startswith(p) for p in LEAKED),
        'fail_closed':          bool(cfg.get('global', {}).get('fail_closed', True)),
        'max_nav_pct':          get_max_nav_pct(),
        'min_score':            get_min_score(),
        'safety_file_exists':   _SAFETY_FILE.exists(),
        'env_mode':             cfg.get('environment', {}).get('mode', 'unknown'),
    }


if __name__ == '__main__':
    import json
    report = safety_report()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print()
    if report['leaked_key_detected']:
        print("🚨 警告: 检测到已泄露的API密钥！请立即撤销！")
    elif report['live_trading_enabled'] and report['api_key_set']:
        print("✅ 实盘模式：密钥OK，safety.yaml配置正常")
    else:
        print("🟡 信号/论文模式（无实盘风险）")
