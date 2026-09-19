#!/usr/bin/env python3
"""Phase 0模拟: Jev预筛层 vs 纯LLM基线对比"""
import time, json, sys, os
sys.path.insert(0, ".")
sys.path.insert(0, "brahma_brain")

from reasoning_client import reasoning_gate

# === 规则预筛函数(模拟Jev Choice题型) ===
def jev_prescreen(result):
    """模拟Jev: 基于铁律规则快速判断PASS/WARN/BLOCK"""
    regime = result.get("regime", "CHOP_MID")
    signal_dir = result.get("signal_dir", "LONG")
    score = float(result.get("score_final", result.get("score", 0)))

    # 铁律硬阻断
    iron_rules = {
        ("BEAR_TREND", "LONG"): "BLOCK",
        ("BULL_TREND", "SHORT"): "BLOCK",
        ("CHOP_MID", "LONG"): "BLOCK",
        ("BEAR_RECOVERY", "SHORT"): "BLOCK",
    }
    if (regime, signal_dir) in iron_rules:
        return {"verdict": "BLOCK", "confidence": 0.95, "reason": f"{regime}{signal_dir}铁律封禁"}

    if score < 60:
        return {"verdict": "WARN", "confidence": 0.7, "reason": f"score={score}偏低"}

    return {"verdict": "PASS", "confidence": 0.7, "reason": "规则预筛通过"}

# === 测试场景 ===
scenarios = [
    {"name": "BULL_TREND LONG s=140", "symbol": "BTCUSDT", "regime": "BULL_TREND", "signal_dir": "LONG", "score_final": 140, "confluence": {"breakdown": {"RSI_1H": 45, "bb_width": 0.005, "fg": 72}}},
    {"name": "BEAR_TREND SHORT s=120", "symbol": "BTCUSDT", "regime": "BEAR_TREND", "signal_dir": "SHORT", "score_final": 120, "confluence": {"breakdown": {"RSI_1H": 25, "bb_width": 0.008, "fg": 22}}},
    {"name": "CHOP_MID LONG s=80", "symbol": "ETHUSDT", "regime": "CHOP_MID", "signal_dir": "LONG", "score_final": 80, "confluence": {"breakdown": {"RSI_1H": 50, "bb_width": 0.012, "fg": 55}}},
    {"name": "BULL_TREND LONG s=100", "symbol": "BTCUSDT", "regime": "BULL_TREND", "signal_dir": "LONG", "score_final": 100, "confluence": {"breakdown": {"RSI_1H": 50, "bb_width": 0.007, "fg": 65}}},
    {"name": "BULL_TREND LONG s=50", "symbol": "BTCUSDT", "regime": "BULL_TREND", "signal_dir": "LONG", "score_final": 50, "confluence": {"breakdown": {"RSI_1H": 30, "bb_width": 0.003, "fg": 40}}},
    {"name": "BEAR_RECOVERY LONG s=130", "symbol": "ETHUSDT", "regime": "BEAR_RECOVERY", "signal_dir": "LONG", "score_final": 130, "confluence": {"breakdown": {"RSI_1H": 35, "bb_width": 0.006, "fg": 30}}},
    {"name": "BEAR_TREND SHORT s=50", "symbol": "BTCUSDT", "regime": "BEAR_TREND", "signal_dir": "SHORT", "score_final": 50, "confluence": {"breakdown": {"RSI_1H": 20, "bb_width": 0.01, "fg": 15}}},
    {"name": "CHOP_MID SHORT s=120", "symbol": "ETHUSDT", "regime": "CHOP_MID", "signal_dir": "SHORT", "score_final": 120, "confluence": {"breakdown": {"RSI_1H": 60, "bb_width": 0.009, "fg": 45}}},
]

print("=" * 70)
print("Phase 0模拟: Jev预筛层 vs 纯LLM基线对比")
print("=" * 70)

# === 基线: 纯LLM ===
print("\n--- 基线: 纯LLM(当前) ---")
llm_results = []
for sc in scenarios:
    name = sc["name"]
    sc_copy = {k: v for k, v in sc.items() if k != "name"}
    t0 = time.time()
    r = reasoning_gate(sc_copy, inject_context=True)
    e = round(time.time() - t0, 2)
    v = r["verdict"]
    c = r["confidence"]
    re = r["reason"]
    llm_results.append({"name": name, "verdict": v, "conf": c, "elapsed": e, "reason": re})
    print(f"  {name}: verdict={v} conf={c} elapsed={e}s reason={re}")

llm_total_time = sum(r["elapsed"] for r in llm_results)
llm_calls = len(scenarios)

# === Phase 0: Jev预筛 ===
print("\n--- Phase 0: Jev预筛(模拟) ---")
jev_results = []
jev_llm_calls = 0
jev_total_time = 0
for sc in scenarios:
    name = sc["name"]
    sc_copy = {k: v for k, v in sc.items() if k != "name"}

    # Step 1: Jev预筛
    t0 = time.time()
    jev_r = jev_prescreen(sc_copy)
    jev_time = round(time.time() - t0, 4)

    if jev_r["verdict"] == "PASS":
        # 直接放行
        v = jev_r["verdict"]
        c = jev_r["confidence"]
        re = jev_r["reason"]
        e = jev_time
        src = "Jev放行"
    else:
        # WARN/BLOCK → 调LLM细判
        jev_llm_calls += 1
        t1 = time.time()
        r = reasoning_gate(sc_copy, inject_context=True)
        llm_time = round(time.time() - t1, 2)
        jev_total_time += llm_time
        v = r["verdict"]
        c = r["confidence"]
        re = r["reason"]
        e = llm_time
        src = "Jev=" + jev_r["verdict"] + "→LLM"

    jev_results.append({"name": name, "verdict": v, "conf": c, "elapsed": e, "reason": re, "src": src})
    print(f"  {name}: {src} → verdict={v} conf={c} elapsed={e}s reason={re}")

# === 对比 ===
print("\n" + "=" * 70)
print("对比结果")
print("=" * 70)
print(f"  总场景: {len(scenarios)}")
print(f"  基线LLM调用: {llm_calls} | 总耗时: {llm_total_time}s")
print(f"  Jev预筛后LLM调用: {jev_llm_calls} | LLM耗时: {jev_total_time}s | Jev耗时: ~0s")
print(f"  降本: {(1-jev_llm_calls/llm_calls)*100:.0f}% (LLM调用减少{llm_calls-jev_llm_calls}/{llm_calls})")
print(f"  降速: {(1-jev_total_time/llm_total_time)*100:.0f}% (总耗时减少)")

# 一致性检查
print("\n--- 一致性检查 ---")
consistent = 0
for i in range(len(scenarios)):
    llm_v = llm_results[i]["verdict"]
    jev_v = jev_results[i]["verdict"]
    match = "✅" if llm_v == jev_v else "❌"
    if llm_v == jev_v:
        consistent += 1
    print(f"  {scenarios[i]['name']}: LLM={llm_v} Jev={jev_v} {match}")
print(f"\n  一致率: {consistent}/{len(scenarios)} = {consistent/len(scenarios)*100:.0f}%")

# Phase 1-3 预估
print("\n" + "=" * 70)
print("Phase 1-3 预估")
print("=" * 70)
print("Phase 1 Context动态裁剪:")
print("  当前注入: ~800-1100 chars")
print("  裁剪后: ~400-500 chars (减半)")
print("  LLM判断速度预计提升20-30% (上下文更短)")
print("  判断准确率预计提升5-10% (减少不相关记忆干扰)")
print()
print("Phase 2 执行前风险Hook:")
print("  当前: 无执行前检查")
print("  改进: Noul概率检查(重复下单/超仓位/违反铁律)")
print("  预计拦截: 1-3%异常命令(observe模式100条估算)")
print()
print("Phase 3 WR反哺标准化:")
print("  当前: WR反哺每日统计，无Jev校准")
print("  改进: 每次分析保存Jev判断→结算后对比")
print("  3个月后: Jev准确率评估数据集")
