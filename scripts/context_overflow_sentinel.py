#!/usr/bin/env python3
"""
context_overflow_sentinel.py — 上下文溢出/压缩死锁哨兵（零AI成本）
[9.27 苏摩111 事故复盘] 根因: compaction三参数全100k数学死锁 → 54次预检拦截+2次会话重置

职责：
1. 扫描当日 gateway 日志的 [context-overflow-precheck] 行（overflowTokens>0 才算真命中）
2. 同一 sessionKey 在60s窗口内的多行合并为1个事件簇（重试链=1簇）
3. 分级: 当日1-2簇→P2 / ≥3簇→P1 / 检测到"Context limit exceeded"(会话重置)→P0
4. dedup: 同一 sessionKey 同一级别 12h 内只推一次（防轰炸）
5. 状态落盘 data/context_overflow_state.json

接入位置: brahma_crontab.txt (*/10)
依赖: scripts/push_hub.py (push_jarvis)
exit 0 恒定（永不阻塞 cron 链）
"""
# [2026-10-05 P1 苏摩111] 统一路径管理，替代裸 sys.path.insert
try:
    import brahma_path_setup  # noqa
except ImportError:
    pass  # 兜底：原有 sys.path.insert 仍保留

import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE_FILE = BASE / "data" / "context_overflow_state.json"
LOG_DIR = Path("/tmp/openclaw")
PUSH = None  # lazy import

DEDUP_TTL = 12 * 3600  # 秒


def _log(msg: str):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def _today_log_path() -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"openclaw-{day}.log"


def _parse_precheck(line: str, ts: str):
    m = re.search(
        r"\[context-overflow-precheck\] sessionKey=(\S+) provider=(\S+) route=(\S+) "
        r"estimatedPromptTokens=(\d+) promptBudgetBeforeReserve=(\d+) overflowTokens=(\d+)",
        line,
    )
    if not m:
        return None
    return {
        "session": m.group(1),
        "provider": m.group(2),
        "route": m.group(3),
        "est": int(m.group(4)),
        "budget": int(m.group(5)),
        "overflow": int(m.group(6)),
        "ts": ts,  # ISO time from JSON envelope
    }


def _iter_log_lines(logp: Path):
    """Yield (iso_time, message) from JSON-lines gateway log; fallback to raw text."""
    for line in logp.read_text(errors="ignore").splitlines():
        raw = line
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                msg = obj.get("message", "")
                if not isinstance(msg, str):
                    msg = str(msg)
                yield obj.get("time", "")[:19], msg, raw
                continue
            except Exception:
                pass
        yield raw[11:19], raw, raw


def _cluster(events: list) -> dict:
    """90s窗口内同session多行=1簇（重试链）。ts=ISO时间戳。"""
    clusters = {}
    for e in sorted(events, key=lambda x: x["ts"]):
        try:
            secs = datetime.fromisoformat(e["ts"]).timestamp()
        except Exception:
            secs = 0
        key = e["session"]
        c = clusters.get(key)
        if c is None or secs - c["last_sec"] > 60:
            clusters[key] = {"last_sec": secs, "count": 1,
                             "max_overflow": e["overflow"], "max_est": e["est"],
                             "budget": e["budget"], "first_ts": e["ts"]}
        else:
            c["last_sec"] = secs
            c["count"] += 1
            c["max_overflow"] = max(c["max_overflow"], e["overflow"])
            c["max_est"] = max(c["max_est"], e["est"])
    return clusters


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "days": {}}


def _save_state(state: dict):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    # 只保留7天历史防膨胀
    days = state.get("days", {})
    if len(days) > 7:
        for k in sorted(days)[: len(days) - 7]:
            days.pop(k, None)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def push(priority: str, msg: str) -> bool:
    global PUSH
    if PUSH is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from push_hub import push_jarvis
            PUSH = push_jarvis
        except Exception as e:
            _log(f"push_hub import failed: {e}")
            PUSH = False
    if not PUSH:
        return False
    try:
        return PUSH(msg, priority=priority, dedup_key=f"ctxovf-{priority}", dedup_ttl=DEDUP_TTL) is not False
    except Exception as e:
        _log(f"push failed: {e}")
        return False


def _is_today_file(f: Path) -> bool:
    """文件mtime是今天（UTC粗判，足够哨兵用）"""
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        return f.stat().st_mtime >= day_start
    except Exception:
        return False


def _evidence_error_sessions() -> None:
    """[9.28 苏摩111 盲区2修复] 扫今日trajectory的error终局，落盘留证JSONL。
    增量去重：以session文件名+ended行hash为键，避免重复写入。
    背景：gateway log无error详情，error上下文只在trajectory的session.ended行。
    """
    import hashlib
    sess_dir = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    out = BASE / "data" / "error_session_evidence.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if out.exists():
        for line in out.read_text().strip().splitlines():
            try:
                seen.add(json.loads(line).get("evidence_key", ""))
            except Exception:
                pass
    n_new = 0
    for f in sess_dir.glob("*.trajectory.jsonl"):
        if not _is_today_file(f):
            continue
        try:
            lines = f.read_text(errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            if '"session.ended"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            data = d.get("data", {})
            if data.get("status") != "error":
                continue
            reason = str(data.get("reason", ""))[:200]
            # 抽该文件里最后一个model.completed的promptError（中断根因）
            last_pe = ""
            for j in range(i, -1, -1):
                if '"model.completed"' in lines[j]:
                    m = re.search(r'"promptError":\s*"([^"]{0,120})', lines[j])
                    if m:
                        last_pe = m.group(1)
                    break
            ek = hashlib.sha1(f"{f.name}:{reason}".encode()).hexdigest()[:16]
            if ek in seen:
                continue
            rec = {"ts": time.time(), "ts_iso": datetime.now(timezone.utc).isoformat(),
                   "evidence_key": ek, "session_file": f.name[:60],
                   "reason": reason, "last_model_promptError": last_pe}
            try:
                with open(out, "a") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_new += 1
            except Exception:
                pass
    if n_new:
        _log(f"error-session evidence: +{n_new} records")


def main() -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logp = _today_log_path()
    if not logp.exists():
        return 0

    precheck_events = []
    resets = []
    try:
        for ts, msg, _raw in _iter_log_lines(logp):
            if "[context-overflow-precheck]" in msg:
                ev = _parse_precheck(msg, ts)
                if ev and ev["overflow"] > 0:
                    precheck_events.append(ev)
            elif "Context limit exceeded" in msg:
                # 会话重置提示（平台恢复消息，=已发生重置）
                resets.append(ts)
    except Exception as e:
        _log(f"log read failed: {e}")
        return 0

    clusters = _cluster(precheck_events)
    state = _load_state()
    daystate = state.setdefault("days", {}).setdefault(day, {
        "clusters": {}, "reset_alerted": False, "p1_alerted": {}
    })

    # ---- [9.28 苏摩111 盲区2修复] error session终局留证 ----
    _evidence_error_sessions()

    # ---- P0: 会话重置（用户已见报错）----
    if resets and not daystate.get("reset_alerted"):
        daystate["reset_alerted"] = True
        push("P0",
             f"🚨 上下文重置事故实锤 | {day} UTC\n"
             f"Context limit exceeded 已触发会话重置（{len(resets)}次）\n"
             f"根因: compaction三参数全100k数学死锁（reserve+keep>窗口）\n"
             f"修复: 平台层调低 reserveTokens/keepRecentTokens至40k/60k\n"
             f"哨兵: 每10min扫描，死锁再现即告警")

    # ---- P1/P2: 溢出簇 ----
    for sess, c in clusters.items():
        short = sess.split(":")[-1][:12] if ":" in sess else sess[:12]
        n = c["count"]
        prev = daystate["clusters"].get(sess, {})
        prev_alerted = prev.get("level")
        if n >= 3:
            level = "P1"
        elif n >= 1:
            level = "P2"
        else:
            level = None
        if level and prev_alerted != level:
            daystate["clusters"][sess] = {"level": level, "ts": c["first_ts"],
                                          "count": n, "max_overflow": c["max_overflow"]}
            est_k = c["max_est"] / 1000
            bud_k = c["budget"] / 1000
            if level == "P1":
                push("P1",
                     f"⚠️ 压缩死锁活跃 | session {short}\n"
                     f"溢出簇 {n}次 est={est_k:.0f}k 预算={bud_k:.0f}k "
                     f"(超出{c['max_overflow']}tok)\n"
                     f"压缩后仍超→重试循环风险→若3次烧完将重置会话\n"
                     f"对策: 平台层调低reserve/keep参数")
            else:
                # P2静默落盘（不打扰），只有转P1才推
                _log(f"P2 cluster: {short} count={n} ovf={c['max_overflow']}")

    # ---- [9.28 苏摩111] 线程水位预警（P1）：主线程est>150k→提醒换线程/压缩 ----
    # 机制：overflow precheck的est字段=当前prompt估算token，>150k=逼近200k窗口
    # 不等死锁发生，提前预警——AI层唯一可做的主动治理
    TH_WATERMARK = 150_000
    wm_alerted = daystate.setdefault("wm_alerted", {})
    hot_sessions = []
    for e in precheck_events:
        if e.get("est", 0) >= TH_WATERMARK:
            hot_sessions.append(e)
    if hot_sessions:
        hot_by_sess = {}
        for e in hot_sessions:
            hot_by_sess[e["session"]] = max(e["est"], hot_by_sess.get(e["session"], 0))
        for sess, est in hot_by_sess.items():
            if wm_alerted.get(sess, {}).get("ts", 0) and \
               time.time() - wm_alerted[sess].get("ts", 0) < DEDUP_TTL:
                continue  # 12h内已提醒
            wm_alerted[sess] = {"ts": time.time(), "est": est}
            push("P1",
                 f"📊 线程水位预警 | {sess.split(':')[-1][:12]}\n"
                 f"估算prompt={est/1000:.0f}k（超150k阈值）\n"
                 f"继续对话将频发overflow→建议：换新对话线程或主动压缩\n"
                 f"根因：compaction三参数死锁（平台层待修）")

    # 统计落盘（即使无告警也记录，供daily审计）
    daystate["summary"] = {
        "precheck_hits": len(precheck_events),
        "clusters": {k[:40]: v["count"] for k, v in clusters.items()},
        "resets_seen_in_user_msgs": len(resets),
        "watermark_150k": {s: e for s, e in wm_alerted.items()} if wm_alerted else {},
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"fatal: {e}")
        sys.exit(0)
