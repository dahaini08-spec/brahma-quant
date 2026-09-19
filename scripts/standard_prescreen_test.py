#!/usr/bin/env python3
"""openclaw infer standard 模型预筛测试 — 对比LLM基线"""
import time, json, sys, subprocess, re, os
sys.path.insert(0, ".")
sys.path.insert(0, "brahma_brain")

from reasoning_client import reasoning_gate

scenarios = [
    {"name": "BULL LONG s=140", "symbol": "BTCUSDT", "regime": "BULL_TREND", "dir": "LONG", "score": 140, "rsi": 45, "bb": 0.005, "fg": 72},
    {"name": "BEAR SHORT s=120", "symbol": "BTCUSDT", "regime": "BEAR_TREND", "dir": "SHORT", "score": 120, "rsi": 25, "bb": 0.008, "fg": 22},
    {"name": "CHOP LONG s=80", "symbol": "ETHUSDT", "regime": "CHOP_MID", "dir": "LONG", "score": 80, "rsi": 50, "bb": 0.012, "fg": 55},
    {"name": "BULL LONG s=100", "symbol": "BTCUSDT", "regime": "BULL_TREND", "dir": "LONG", "score": 100, "rsi": 50, "bb": 0.007, "fg": 65},
    {"name": "BULL LONG s=50", "symbol": "BTCUSDT", "regime": "BULL_TREND", "dir": "LONG", "score": 50, "rsi": 30, "bb": 0.003, "fg": 40},
    {"name": "BEAR_REC LONG s=130", "symbol": "ETHUSDT", "regime": "BEAR_RECOVERY", "dir": "LONG", "score": 130, "rsi": 35, "bb": 0.006, "fg": 30},
    {"name": "BEAR SHORT s=50", "symbol": "BTCUSDT", "regime": "BEAR_TREND", "dir": "SHORT", "score": 50, "rsi": 20, "bb": 0.01, "fg": 15},
    {"name": "CHOP SHORT s=120", "symbol": "ETHUSDT", "regime": "CHOP_MID", "dir": "SHORT", "score": 120, "rsi": 60, "bb": 0.009, "fg": 45},
]

print("=== 基线: openclaw infer advanced (当前生产) ===")
llm_results = []
for sc in scenarios:
    name = sc["name"]
    sc_copy = {k: v for k, v in sc.items() if k not in ["name", "dir", "rsi", "bb", "fg"]}
    sc_copy["signal_dir"] = sc["dir"]
    sc_copy["score_final"] = sc["score"]
    sc_copy["confluence"] = {"breakdown": {"RSI_1H": sc["rsi"], "bb_width": sc["bb"], "fg": sc["fg"]}}
    
    t0 = time.time()
    r = reasoning_gate(sc_copy, inject_context=True)
    elapsed = round(time.time() - t0, 2)
    v = r["verdict"]
    c = r["confidence"]
    re_ = r["reason"]
    llm_results.append({"name": name, "verdict": v, "conf": c, "elapsed": elapsed})
    print(f"  {name}: verdict={v} conf={c} elapsed={elapsed}s reason={re_}")

llm_total = sum(r["elapsed"] for r in llm_results)

print()
print("=== Phase 0: openclaw infer standard (预筛层) ===")
std_results = []
for sc in scenarios:
    name = sc["name"]
    prompt = (
        f"你是梵天风控预筛层。快速判断：\n"
        f"标的={sc['symbol']} 体制={sc['regime']} 方向={sc['dir']} 评分={sc['score']}\n"
        f"RSI_1H={sc['rsi']} BB宽度={sc['bb']} Fear&Greed={sc['fg']}\n\n"
        f"铁律: BEAR_TREND+LONG=BLOCK BULL_TREND+SHORT=BLOCK CHOP_MID+LONG=BLOCK BEAR_RECOVERY+SHORT=BLOCK\n\n"
        f"请直接输出JSON(无其他文字):\n"
        f'{{"verdict":"PASS|WARN|BLOCK","confidence":0.0~1.0,"reason":"<20字"}}\n'
        f"PASS=正常放行 WARN=降分8 BLOCK=拒绝执行"
    )
    
    t0 = time.time()
    try:
        result = subprocess.run(
            ["openclaw", "infer", "model", "run", "--model", "standard", "--prompt", prompt, "--max-tokens", "100"],
            capture_output=True, text=True, timeout=15
        )
        elapsed = round(time.time() - t0, 2)
        output = result.stdout.strip()
        m = re.search(r'\{[^}]+\}', output, re.DOTALL)
        if m:
            data = json.loads(m.group())
            v = data.get("verdict", "?")
            c = data.get("confidence", "?")
            r = data.get("reason", "?")
            std_results.append({"name": name, "verdict": v, "conf": c, "elapsed": elapsed})
            print(f"  {name}: verdict={v} conf={c} elapsed={elapsed}s reason={r}")
        else:
            std_results.append({"name": name, "verdict": "PARSE_FAIL", "conf": 0, "elapsed": elapsed})
            print(f"  {name}: PARSE_FAIL elapsed={elapsed}s raw={output[:60]}")
    except subprocess.TimeoutExpired:
        std_results.append({"name": name, "verdict": "TIMEOUT", "conf": 0, "elapsed": 15})
        print(f"  {name}: TIMEOUT >15s")
    except Exception as e:
        std_results.append({"name": name, "verdict": "ERROR", "conf": 0, "elapsed": 0})
        print(f"  {name}: ERROR {e}")

std_total = sum(r["elapsed"] for r in std_results)

print()
print("=== 对比结果 ===")
print(f"  总场景: {len(scenarios)}")
print(f"  基线(advanced) 总耗时: {llm_total:.1f}s | 平均: {llm_total/len(scenarios):.1f}s/次")
print(f"  预筛(standard) 总耗时: {std_total:.1f}s | 平均: {std_total/len(scenarios):.1f}s/次")
print(f"  降速: {(1-std_total/llm_total)*100:.0f}%")

print()
print("=== 一致性检查 ===")
consistent = 0
for i in range(len(scenarios)):
    llm_v = llm_results[i]["verdict"]
    std_v = std_results[i]["verdict"]
    match = "YES" if llm_v == std_v else "NO"
    if llm_v == std_v:
        consistent += 1
    print(f"  {scenarios[i]['name']}: adv={llm_v} std={std_v} {match}")
print(f"\n  一致率: {consistent}/{len(scenarios)} = {consistent/len(scenarios)*100:.0f}%")
