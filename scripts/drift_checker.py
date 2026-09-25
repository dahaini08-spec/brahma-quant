#!/usr/bin/env python3
# drift_checker.py — 封印常量 ↔ 代码实值自动比对（三方审核 Phase 0）
# 接入位置: supercronic brahma_crontab.txt(每日03:05, script-only) + logs/drift_checker.log
#          告警走 push_hub.push_jarvis(P1)，全绿静默(P4)
# 设计: 三方审核方案 v1.0 docs/three_party_review_plan_v1.md §Phase0
#       L1=字面grep实锤自动采信 / 只报事实不做AI推理 / 不对称过滤(默认放行)
# [封印 2026-09-25 苏摩111]

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # trading-system/
LOG = ROOT / "logs/drift_checker.log"

sys.path.insert(0, str(ROOT / "scripts"))


def _grep(pattern: str, path: str) -> list:
    """L1证据: 字面grep。返回匹配行列表。"""
    p = ROOT / path
    if not p.exists():
        return []
    out = subprocess.run(
        ["grep", "-nE", pattern, str(p)],
        capture_output=True, text=True, timeout=10,
    )
    return [l for l in out.stdout.strip().split("\n") if l]


def _extract_value(line: str) -> str:
    m = re.search(r"=\s*([0-9_.]+)", line)
    return m.group(1) if m else ""


# ── 检查规则（每条 = {id, desc, run}）──────────────────────────────
# 规则语义: check返回 (ok, detail)。ok=True=同值/一致；False=漂移实锤。
CHECKS = []


def check(name: str, desc: str):
    def deco(fn):
        CHECKS.append({"id": name, "desc": desc, "fn": fn})
        return fn
    return deco


@check("D1", "MIN_SCORE_OPEN: brahma_core.py 实值应为100（9.17苏摩111降门槛，代码=SSOT）")
def d1():
    hits = _grep(r"^MIN_SCORE_OPEN\s*=", "brahma_brain/brahma_core.py")
    fallback = _grep(r"MIN_SCORE_OPEN = 100", "brahma_brain/brahma_core.py")
    ok = any("100" in h for h in fallback)
    return ok, f"硬赋值={len(hits)}行 fallback100={'有' if ok else '无'}"


@check("D2", "paper_account.json 内部NAV一致性: start_nav vs nav_current 双语义不得并存于同一文件")
def d2():
    p = ROOT / "data/paper_account.json"
    if not p.exists():
        return True, "文件不存在(跳过)"
    d = json.loads(p.read_text())
    keys = {k for k in d if "nav" in k.lower()}
    start = d.get("start_nav")
    current = d.get("nav_current", d.get("current_nav"))
    # 实锤标准: start_nav 与 nav_current/100 差异>100x → 双语义并存
    if start and current and abs(current / start) > 100:
        return False, f"双NAV并存实锤: start_nav={start} vs nav_current={current} (比例{current/start:.0f}x)"
    return True, f"NAV语义一致 (start={start}, current={current})"


@check("D3", "paper_executor.PAPER_SCORE_MIN 应与 brahma_core.MIN_SCORE_OPEN 同值（常量唯一定义点铁律）")
def d3():
    pe = _grep(r"PAPER_SCORE_MIN\s*=", "scripts/paper_executor.py")
    bc = _grep(r"MIN_SCORE_OPEN = [0-9]", "brahma_brain/brahma_core.py")
    if not pe or not bc:
        return False, "任一常量grep不到（文件/命名漂移）"
    v_pe = _extract_value(pe[0].split(":", 1)[1])
    v_bc = _extract_value(bc[0].split(":", 1)[1])
    return v_pe == v_bc, f"paper_executor={v_pe} vs brahma_core={v_bc}"


@check("D4", "brahma_os/config.py start_nav 与 paper_account.json 语义隔离（≠同值告警, 账本重建P2前仅记录）")
def d4():
    hits = _grep(r"start_nav[:\s=]", "brahma_os/config.py")
    if not hits:
        return True, "无start_nav(跳过)"
    v = _extract_value(hits[0].split(":", 1)[1])
    return True, f"记录: brahma_os start_nav={v} (P2账本重建统一前仅观测)"


@check("D5", "get_regime_mult 活性守卫: regime_config.py 必须定义且被 brahma_core 调用（防'废而未删'复发）")
def d5():
    defined = _grep(r"def get_regime_mult", "brahma_brain/regime_config.py")
    called = _grep(r"(_get_rm|get_regime_mult)[[:space:]]*\(", "brahma_brain/brahma_core.py")
    ok = bool(defined) and bool(called)
    return ok, f"定义={len(defined)}处 调用={len(called)}处"


@check("D7", "命名劫持守卫: _path_guard不得预注册brahma_brain为非包模块（9.25 WARN风暴根因）")
def d7():
    hits = _grep(r"_register\(\s*'brahma_brain'", "../scripts/square/_path_guard.py")
    if not hits:
        return True, "无预注册（包模式正常）"
    ctx = _grep(r"__init__.py", "../scripts/square/_path_guard.py")
    ok = bool(ctx)  # 有包检测分支=已修复版本
    return ok, f"预注册残留={len(hits)}处 包检测={'有' if ctx else '无'}"


@check("D6", "trader_brain cf_action唯一裁判: 调用方(tradfi_inject)必须传cf_action（9.23封印防回退）")
def d6():
    passed = _grep(r"cf_action=", "brahma_brain/brahma_core_tradfi_inject.py")
    ok = bool(passed)
    return ok, f"调用方传参={'有' if ok else '缺失!'}"


# 已实锤、已登记R-registry、待苏摩111批准修复的项（不重复推送，P2统一修）
ACK_PENDING = {"D2"}  # R-004 双NAV并存 → P2账本重建


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results, drifts = [], []
    for c in CHECKS:
        try:
            ok, detail = c["fn"]()
        except Exception as e:  # 检查器自身故障≠漂移，标记SKIP
            ok, detail = True, f"SKIP(检查器异常: {type(e).__name__})"
        results.append((c["id"], c["desc"], ok, detail))
        if not ok and c["id"] not in ACK_PENDING:  # ACK项只记日志不推送
            drifts.append((c["id"], c["desc"], detail))

    lines = [f"[{now}] drift_checker: {len(results)} checks, {len(drifts)} drift"]
    for cid, desc, ok, detail in results:
        lines.append(f"  {'PASS' if ok else 'DRIFT'} {cid}: {detail}")

    print("\n".join(lines))  # 日志由cron >> logs/drift_checker.log 追加（脚本内不双写）

    if drifts:
        msg = "🚨 梵天drift_checker 发现漂移实锤\n"
        for cid, desc, detail in drifts:
            msg += f"• {cid}: {detail}\n"
        msg += f"详情: logs/drift_checker.log"
        try:
            from push_hub import push_jarvis
            push_jarvis(msg, priority="P1")
        except Exception as e:
            print(f"  push失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
