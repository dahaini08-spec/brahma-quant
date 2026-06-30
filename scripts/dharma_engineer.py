#!/usr/bin/env python3
"""
dharma_engineer.py — 達摩院繁體審核工程師 v1.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
職責：
  全流程系統穩定性審核
  所有升級/優化以達摩院意見實訓標準執行
  檢測系統工程正常 / 衔接 / 穩定性

審核層級（L0~L6）：
  L0  進程層      — 關鍵進程存活
  L1  連接層      — Binance API / WS 連接
  L2  資料層      — 數據新鮮度 / 快取完整性
  L3  資產層      — 帳戶餘額 / 持倉對齊
  L4  信號層      — 信號鏈路 / dd1佇列 / 冷卻狀態
  L5  參數層      — v7 WFV 參數對齊 / 體制矩陣
  L6  排程層      — Cron 任務健康 / 無死任務

輸出：
  正常 → HEARTBEAT_OK（靜默）
  異常 → 繁體中文告警報告
"""
import os, sys, json, time, hmac, hashlib, subprocess
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
JARVIS  = "73295708:thread:019f1797-6c60-7541-ad72-ec34ed14dfc4"
VERSION = "v1.0"

sys.path.insert(0, str(BASE))
try:
    from config import binance_keys
    _KEY, _SEC = binance_keys()
except:
    _KEY, _SEC = "", ""

# ── 工具函數 ───────────────────────────────────────────────────────
def _ts():
    return datetime.now(timezone.utc).strftime('%m-%d %H:%M UTC')

def _binance_get(path, params=""):
    ts = int(time.time()*1000)
    qs = f"timestamp={ts}" + (f"&{params}" if params else "")
    sig = hmac.new(_SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"https://fapi.binance.com{path}?{qs}&signature={sig}"
    req = urllib.request.Request(url, headers={"X-MBX-APIKEY": _KEY})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())

def _pub_get(url):
    return json.loads(urllib.request.urlopen(url, timeout=6).read())

def _state():
    return json.load(open(BASE/"data"/"brahma_state.json"))

def _runtime():
    return json.load(open(BASE/"data"/"dharma_runtime.json"))

# ── L0 進程層 ─────────────────────────────────────────────────────
def check_L0_process():
    issues = []
    procs = {
        "watchdog_guardian": "watchdog守護進程",
        "crond":             "系統排程器",
    }
    for name, label in procs.items():
        r = subprocess.run(["pgrep","-f",name], capture_output=True)
        if not r.stdout.strip():
            issues.append(f"❌ L0 [{label}] 未運行")
    return issues

# ── L1 連接層 ─────────────────────────────────────────────────────
def check_L1_connection():
    issues = []
    t0 = time.time()
    try:
        _pub_get("https://fapi.binance.com/fapi/v1/ping")
        latency = (time.time()-t0)*1000
        if latency > 2000:
            issues.append(f"⚠️ L1 fapi延遲過高 {latency:.0f}ms（閾值2000ms）")
    except Exception as e:
        issues.append(f"❌ L1 Binance fapi連接失敗: {e}")
    
    try:
        fr = _pub_get("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")
        price = float(fr.get("markPrice",0))
        if price < 10000 or price > 200000:
            issues.append(f"❌ L1 BTC價格異常: ${price:,.2f}")
    except Exception as e:
        issues.append(f"❌ L1 市場數據獲取失敗: {e}")
    
    return issues

# ── L2 資料層 ─────────────────────────────────────────────────────
def check_L2_data():
    issues = []
    now = time.time()
    cache_dir = BASE/"data"/"brahma_cache"
    
    state = _state()
    updated = state.get("updated_at","")
    try:
        from datetime import datetime, timezone
        ua = datetime.fromisoformat(updated.replace("Z","+00:00"))
        age_min = (datetime.now(timezone.utc)-ua).total_seconds()/60
        if age_min > 60:
            issues.append(f"⚠️ L2 brahma_state 已 {age_min:.0f} 分鐘未更新（閾值60min）")
    except:
        issues.append(f"⚠️ L2 brahma_state 時間解析失敗")
    
    # 核心標的快取新鮮度
    syms = ["BTCUSDT","ETHUSDT"]
    for sym in syms:
        for tf in ["1h_250","4h_250","15m_250"]:
            f = cache_dir/f"{sym}_{tf}.json"
            if f.exists():
                age = (now - f.stat().st_mtime)/60
                limit = 120 if "1h" in tf else (480 if "4h" in tf else 60)
                if age > limit:
                    issues.append(f"⚠️ L2 {sym} {tf} 快取過期 {age:.0f}min（閾值{limit}min）")
            else:
                issues.append(f"❌ L2 {sym} {tf} 快取檔案不存在")
    
    return issues

# ── L3 資產層 ─────────────────────────────────────────────────────
def check_L3_asset():
    issues = []
    if not _KEY:
        return ["❌ L3 API金鑰未設定，無法驗證資產"]
    
    try:
        balances = _binance_get("/fapi/v2/balance")
        usdt = next((b for b in balances if b["asset"]=="USDT"), None)
        if not usdt:
            issues.append("❌ L3 USDT餘額查詢失敗")
        else:
            bal = float(usdt["balance"])
            avail = float(usdt["availableBalance"])
            
            # 與 brahma_state 對比
            state = _state()
            state_nav = float(state.get("nav") or state.get("paper_nav") or 0)
            state_avail = float(state.get("available_balance") or 0)
            
            nav_diff = abs(bal - state_nav)
            if nav_diff > 5:
                issues.append(f"⚠️ L3 NAV對齊偏差: 鏈上=${bal:.2f} vs state=${state_nav:.2f} 差{nav_diff:.2f}U")
            
            if abs(state_avail - avail) > 5:
                issues.append(f"⚠️ L3 可用餘額對齊偏差: 鏈上=${avail:.2f} vs state=${state_avail:.2f}")
        
        # 持倉核對
        positions = _binance_get("/fapi/v2/positionRisk")
        open_pos = [p for p in positions if float(p.get("positionAmt",0)) != 0]
        state = _state()
        state_pos = [p for p in state.get("positions",[]) if p.get("status")=="OPEN"]
        
        if len(open_pos) != len(state_pos):
            issues.append(f"⚠️ L3 持倉數量不一致: 鏈上={len(open_pos)} vs state={len(state_pos)}")
    
    except Exception as e:
        issues.append(f"❌ L3 資產查詢異常: {e}")
    
    return issues

# ── L4 信號層 ─────────────────────────────────────────────────────
def check_L4_signal():
    issues = []
    
    # dd1_pending 積壓
    dd1 = BASE/"data"/"dd1_pending.json"
    if dd1.exists():
        q = json.loads(dd1.read_text())
        pending = [x for x in q if x.get("status")=="PENDING"]
        if len(pending) > 5:
            issues.append(f"⚠️ L4 dd1_pending積壓 {len(pending)} 條PENDING（閾值5）")
        if len(q) > 20:
            issues.append(f"⚠️ L4 dd1_pending總計 {len(q)} 條（含歷史），建議清理")
    
    # queue_state 積壓
    qs = BASE/"data"/"queue_state.json"
    if qs.exists():
        q_data = json.loads(qs.read_text())
        queue = q_data.get("queue",[])
        if len(queue) > 10:
            issues.append(f"⚠️ L4 queue_state積壓 {len(queue)} 條（閾值10）")
    
    # 信號冷卻過長（超過24H的標的）
    if qs.exists():
        cooldowns = q_data.get("cooldowns",{})
        now = time.time()
        from datetime import datetime, timezone
        stale_cools = []
        for sym, cd in cooldowns.items():
            try:
                ts = datetime.fromisoformat(cd["ts"]).timestamp()
                age_h = (now-ts)/3600
                if age_h > 48:
                    stale_cools.append(f"{sym}({age_h:.0f}H)")
            except: pass
        if len(stale_cools) > 10:
            issues.append(f"⚠️ L4 {len(stale_cools)} 個標的冷卻超48H，建議清理: {stale_cools[:3]}...")
    
    return issues

# ── L5 參數層 ─────────────────────────────────────────────────────
def check_L5_params():
    issues = []
    try:
        rt = _runtime()
        version = rt.get("system_version","?")
        if version != "v7.0":
            issues.append(f"⚠️ L5 系統版本 {version}（預期v7.0）")
        
        sp = rt.get("sym_params",{})
        expected = {
            "BTCUSDT": {"thr":100,"sl_mult":2.527,"mh":17},
            "ETHUSDT": {"thr":100,"sl_mult":2.8,  "mh":18},
        }
        for sym, exp in expected.items():
            actual = sp.get(sym,{})
            for k, v in exp.items():
                av = actual.get(k)
                if av is None:
                    issues.append(f"❌ L5 {sym}.{k} 缺失")
                elif abs(float(av)-v) > 0.01:
                    issues.append(f"⚠️ L5 {sym}.{k} 偏差: 實際={av} 預期={v}")
        
        # WR矩陣核心條目
        wrv7 = rt.get("wr_matrix_v7",{})
        required = {
            "BTCUSDT": ["BEAR_EARLY_SHORT","BULL_EARLY_LONG"],
            "ETHUSDT": ["BEAR_EARLY_SHORT","BULL_EARLY_LONG"],
        }
        for sym, keys in required.items():
            for key in keys:
                if key not in wrv7.get(sym,{}):
                    issues.append(f"❌ L5 WR矩陣缺失 {sym}.{key}")
    
    except Exception as e:
        issues.append(f"❌ L5 參數審核異常: {e}")
    
    return issues

# ── L6 排程層 ─────────────────────────────────────────────────────
def check_L6_cron():
    issues = []
    
    # 檢查 /etc/cron.d/
    cron_d = Path("/etc/cron.d")
    brahma_crons = list(cron_d.glob("brahma-*"))
    if not brahma_crons:
        issues.append("⚠️ L6 /etc/cron.d/ 無 brahma-* 任務（clean-stale可能丟失）")
    
    # 檢查 noai_runner 日誌最近執行
    log = BASE/"logs"/"noai_runner.log"
    if log.exists():
        lines = log.read_text().split("\n")[-5:]
        last_line = [l for l in lines if l.strip()]
        # 找最後一條帶時間戳的記錄
        import re
        for line in reversed(last_line):
            m = re.match(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
            if m:
                from datetime import datetime
                last_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                age_h = (datetime.utcnow()-last_ts).total_seconds()/3600
                if age_h > 2:
                    issues.append(f"⚠️ L6 noai_runner 最後執行 {age_h:.1f}H前（閾值2H）")
                break
    else:
        issues.append("⚠️ L6 noai_runner.log 不存在，cron任務可能未執行")
    
    return issues

# ── 主入口 ────────────────────────────────────────────────────────
def run_full_audit(verbose=False):
    all_issues = []
    report_lines = []
    
    checks = [
        ("L0 進程層",    check_L0_process),
        ("L1 連接層",    check_L1_connection),
        ("L2 資料層",    check_L2_data),
        ("L3 資產層",    check_L3_asset),
        ("L4 信號層",    check_L4_signal),
        ("L5 參數層",    check_L5_params),
        ("L6 排程層",    check_L6_cron),
    ]
    
    layer_results = {}
    for label, fn in checks:
        try:
            issues = fn()
        except Exception as e:
            issues = [f"❌ {label} 審核異常: {e}"]
        layer_results[label] = issues
        all_issues.extend(issues)
    
    if not all_issues:
        print("HEARTBEAT_OK")
        return
    
    # 分級
    errors   = [i for i in all_issues if i.startswith("❌")]
    warnings = [i for i in all_issues if i.startswith("⚠️")]
    
    grade = "🔴 ERROR" if errors else "🟡 WARNING"
    
    now_str = _ts()
    lines = [
        f"🧑‍💻 達摩院繁體審核工程師 {VERSION}",
        f"📋 全系統審核報告 · {now_str}",
        f"評級：{grade}  ❌{len(errors)}項 ⚠️{len(warnings)}項",
        "─"*40,
    ]
    
    for label, issues in layer_results.items():
        if issues:
            lines.append(f"\n【{label}】")
            for i in issues:
                lines.append(f"  {i}")
        elif verbose:
            lines.append(f"【{label}】✅ 正常")
    
    lines += [
        "─"*40,
        "請依序處理 ❌ 後再處理 ⚠️",
        f"⚜️ 達摩院 · 梵天量化 · 品質第一",
    ]
    
    report = "\n".join(lines)
    print(report)
    return report


if __name__ == "__main__":
    import sys
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    run_full_audit(verbose=verbose)
