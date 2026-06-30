"""
research_bridge.py · 研究增强层桥接器 v0.1
====================================================
职责：
  1. 接收外部研究信号（QuantDinger / TradingAgents / Kronos）
  2. 验证格式、TTL、权重上限
  3. 安全注入 brahma_core 评分层（≤8分/150分）

架构位置：
  外部研究容器 → research_bridge.py → brahma_brain/external_signal.py → brahma_core

设计原则（STAR.md L0）：
  - 外部信号最高权重 8分（≤5.3%），失败归零
  - TTL=30分钟，过期自动归零，不阻塞核心引擎
  - 研究容器禁止持有交易所 API Key
  - 所有注入有审计日志

作者：设计院 · 2026-06-17
"""

import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

BASE_DIR = Path(__file__).parent

# external_signal 缓存接口（brahma_core 读取端）
try:
    import sys as _sys_es
    _sys_es.path.insert(0, str(BASE_DIR / 'brahma_brain'))
    from external_signal import write as _ext_write, clear as _ext_clear, status as _ext_status
    _EXT_OK = True
except Exception as _es_e:
    _EXT_OK = False
    _ext_write = _ext_clear = _ext_status = None
DATA_DIR = BASE_DIR / "data"
BRIDGE_LOG = DATA_DIR / "research_bridge_log.jsonl"
BRIDGE_STATE = DATA_DIR / "research_bridge_state.json"

# ============================================================
# 常量 — 权重上限（STAR.md §三 研究增强层）
# ============================================================
MAX_WEIGHT_PER_SOURCE = {
    "quantdinger_research": 8,   # 多智能体叙事研究
    "trading_agents":       6,   # Bull/Bear 辩论
    "kronos":               4,   # 价格方向预测
    "last30days":           0,   # 纯文本附注，不注入分数
}
SIGNAL_TTL_MINUTES = 30

# ============================================================
# 格式校验
# ============================================================
REQUIRED_FIELDS = ["source", "symbol", "direction", "confidence", "score_delta", "generated_at"]

def validate_signal(signal: Dict[str, Any]) -> tuple[bool, str]:
    """校验研究信号格式（STAR.md §四 输出格式规范）"""
    for f in REQUIRED_FIELDS:
        if f not in signal:
            return False, f"缺少必要字段: {f}"

    # 来源白名单
    if signal["source"] not in MAX_WEIGHT_PER_SOURCE:
        return False, f"未知来源: {signal['source']}，拒绝注入"

    # 方向合法性
    if signal["direction"] not in ("LONG", "SHORT", "NEUTRAL"):
        return False, f"非法方向: {signal['direction']}"

    # 权重上限检查
    max_w = MAX_WEIGHT_PER_SOURCE[signal["source"]]
    if signal.get("score_delta", 0) > max_w:
        signal["score_delta"] = max_w  # 截断，不拒绝
        signal["_capped"] = True

    # 置信度范围
    conf = signal.get("confidence", 0)
    if not (0.0 <= conf <= 1.0):
        return False, f"置信度超范围: {conf}"

    return True, "OK"


def is_expired(signal: Dict[str, Any]) -> bool:
    """检查 TTL（STAR.md L0：30分钟过期归零）"""
    try:
        gen_at = datetime.fromisoformat(signal["generated_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - gen_at) > timedelta(minutes=SIGNAL_TTL_MINUTES)
    except Exception:
        return True  # 解析失败视为过期


def check_regime_block(signal: Dict[str, Any], current_regime: str) -> tuple[bool, str]:
    """
    检查 STAR.md L1 死穴封禁
    当前体制 × 信号方向 是否命中死穴
    """
    BLOCKED_COMBOS = {
        ("BEAR_TREND", "LONG"),
        ("BULL_TREND", "SHORT"),
        ("BEAR_RECOVERY", "SHORT"),
        ("BULL_CORRECTION", "LONG"),
    }
    combo = (current_regime, signal.get("direction"))
    if combo in BLOCKED_COMBOS:
        return True, f"[BLOCKED] 死穴方向 {current_regime}×{signal['direction']}，WR<48%，拒绝注入"
    return False, "OK"


# ============================================================
# 核心注入接口
# ============================================================

def inject_research_signal(
    signal: Dict[str, Any],
    current_regime: str,
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    主入口：验证 + 注入研究信号
    
    Args:
        signal: 研究信号 JSON（STAR.md §四 格式）
        current_regime: 当前梵天体制（从 brahma_state.json 读取）
        dry_run: True = 只记录不注入（安全默认）
    
    Returns:
        {
            "injected": bool,
            "score_delta": int,  # 0 = 归零/拒绝
            "reason": str,
            "signal_id": str
        }
    """
    signal_id = f"rb_{int(time.time())}_{signal.get('source', 'unk')[:8]}"
    result = {
        "injected": False,
        "score_delta": 0,
        "reason": "",
        "signal_id": signal_id,
        "dry_run": dry_run,
    }

    # Step 1: 格式校验
    ok, msg = validate_signal(signal)
    if not ok:
        result["reason"] = f"格式校验失败: {msg}"
        _audit_log(signal_id, signal, result)
        return result

    # Step 2: TTL 检查
    if is_expired(signal):
        result["reason"] = "信号已过期（TTL=30min），归零"
        _audit_log(signal_id, signal, result)
        return result

    # Step 3: 死穴封禁检查（STAR.md L1）
    blocked, block_msg = check_regime_block(signal, current_regime)
    if blocked:
        result["reason"] = block_msg
        _audit_log(signal_id, signal, result)
        return result

    # Step 4: CHOP 体制降权（STAR.md L2）
    if current_regime.startswith("CHOP"):
        result["reason"] = f"CHOP 体制，研究信号自动归零"
        _audit_log(signal_id, signal, result)
        return result

    # Step 5: 权重注入
    score_delta = int(signal.get("score_delta", 0))
    max_w = MAX_WEIGHT_PER_SOURCE.get(signal["source"], 0)
    score_delta = min(score_delta, max_w)  # 二次截断确保安全

    result["injected"] = not dry_run
    result["score_delta"] = score_delta if not dry_run else 0
    result["reason"] = f"{'[DRY_RUN] ' if dry_run else ''}注入 +{score_delta}分 from {signal['source']}"

    # ── 写入 external_signal 缓存（brahma_core 读取端）────────────────
    if not dry_run and _EXT_OK and _ext_write and score_delta != 0:
        try:
            _ext_write(signal.get('symbol',''), signal.get('direction',''), {
                'source':       signal.get('source', 'unknown'),
                'score_delta':  score_delta,
                'confidence':   signal.get('confidence', 0.5),
                'narrative':    signal.get('narrative', ''),
                'generated_at': signal.get('generated_at', ''),
            })
        except Exception as _ew:
            pass  # 写缓存失败不影响审计日志

    _audit_log(signal_id, signal, result)
    return result


def _audit_log(signal_id: str, signal: Dict[str, Any], result: Dict[str, Any]):
    """追加审计日志（append-only，STAR.md §一 合规要求）"""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "signal_id": signal_id,
            "source": signal.get("source"),
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "score_delta_requested": signal.get("score_delta", 0),
            "score_delta_applied": result.get("score_delta", 0),
            "injected": result.get("injected", False),
            "reason": result.get("reason", ""),
            "dry_run": result.get("dry_run", True),
        }
        with open(BRIDGE_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.warning(f"[research_bridge] 审计日志写入失败: {e}")


# ============================================================
# 状态查询
# ============================================================

def get_current_regime() -> str:
    """从 brahma_state.json 读取当前体制"""
    try:
        state_path = BASE_DIR / "data" / "brahma_state.json"
        state = json.loads(state_path.read_text())
        return state.get("regime", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def get_bridge_stats() -> Dict[str, Any]:
    """读取最近 50 条注入日志摘要"""
    try:
        if not BRIDGE_LOG.exists():
            return {"total": 0, "injected": 0, "blocked": 0, "expired": 0}
        entries = []
        with open(BRIDGE_LOG) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except:
                    pass
        recent = entries[-50:]
        return {
            "total": len(recent),
            "injected": sum(1 for e in recent if e.get("injected")),
            "blocked": sum(1 for e in recent if "[BLOCKED]" in e.get("reason", "")),
            "expired": sum(1 for e in recent if "过期" in e.get("reason", "")),
            "total_score_injected": sum(e.get("score_delta_applied", 0) for e in recent),
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# CLI 测试接口
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="research_bridge 测试")
    parser.add_argument("--test", action="store_true", help="运行自检测试")
    parser.add_argument("--stats", action="store_true", help="查看注入统计")
    args = parser.parse_args()

    if args.stats:
        stats = get_bridge_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))

    if args.test:
        regime = get_current_regime()
        print(f"当前体制: {regime}")

        # 测试1：正常信号
        test_signal = {
            "source": "quantdinger_research",
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "confidence": 0.75,
            "score_delta": 6,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "narrative": "测试信号：BEAR_TREND 空头叙事",
        }
        result = inject_research_signal(test_signal, regime, dry_run=True)
        print(f"[测试1 正常信号] {result}")

        # 测试2：死穴封禁
        blocked_signal = dict(test_signal)
        blocked_signal["direction"] = "LONG"
        result2 = inject_research_signal(blocked_signal, "BEAR_TREND", dry_run=True)
        print(f"[测试2 死穴封禁] {result2}")

        # 测试3：过期信号
        expired_signal = dict(test_signal)
        expired_signal["generated_at"] = "2026-06-17T00:00:00Z"
        result3 = inject_research_signal(expired_signal, regime, dry_run=True)
        print(f"[测试3 过期信号] {result3}")

        # 测试4：权重截断
        overweight_signal = dict(test_signal)
        overweight_signal["score_delta"] = 99  # 超出上限
        result4 = inject_research_signal(overweight_signal, regime, dry_run=True)
        print(f"[测试4 权重截断] {result4}")
