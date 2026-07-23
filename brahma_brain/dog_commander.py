"""
犬系统 · Layer 4 指挥中心
dog_commander.py

统一入口 · 优先级调度 · 告警路由 · 自愈历史记录
subprocess守护（比纯async更健壮）
[2026-07-23 设计院×达摩院×六方联合 封印]

运行方式：
  python3 -m brahma_brain.dog_commander         # 前台运行
  python3 -m brahma_brain.dog_commander --daemon # 后台守护
"""

import os
import sys
import json
import time
import argparse
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 常量 ────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).resolve().parent.parent
HEAL_LOG     = BASE_DIR / 'data' / 'dog_heal_history.jsonl'
SSOT_USER    = '73295708'
SSOT_THREAD  = '019f8768-6731-777d-8924-2426a5abd10f'
PROBE_INTERVAL  = 30      # 秒
MAX_HEAL_TRIES  = 3       # 超出则熔断+上报
ALERT_DEDUP_TTL = 86400   # 24H去重（ws_guardian除外，每次都报）
NO_DEDUP_PROBES = {'ws_guardian'}  # 这些探针每次失败都告警


# ── 自愈历史 ─────────────────────────────────────────────────
class HealHistory:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, probe_name, level, symptom, heal_action, success, duration_s, escalated):
        # 统计24H内复发次数
        recurrence = self._count_recent(probe_name, hours=24)
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'probe': probe_name,
            'level': level,
            'symptom': symptom,
            'heal_action': heal_action,
            'success': success,
            'duration_s': round(duration_s, 2),
            'recurrence_24h': recurrence + 1,
            'escalated': escalated,
        }
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return entry

    def _count_recent(self, probe_name, hours=24):
        if not self.path.exists():
            return 0
        cutoff = time.time() - hours * 3600
        count = 0
        try:
            with open(self.path, encoding='utf-8') as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        if d.get('probe') == probe_name:
                            ts = datetime.fromisoformat(d['ts']).timestamp()
                            if ts > cutoff:
                                count += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return count

    def high_frequency_faults(self, threshold=5):
        """返回24H内复发超阈值的探针"""
        from collections import Counter
        if not self.path.exists():
            return []
        cutoff = time.time() - 86400
        names = []
        try:
            with open(self.path, encoding='utf-8') as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        ts = datetime.fromisoformat(d['ts']).timestamp()
                        if ts > cutoff:
                            names.append(d['probe'])
                    except Exception:
                        pass
        except Exception:
            pass
        return [p for p, c in Counter(names).items() if c >= threshold]


# ── 告警去重 ─────────────────────────────────────────────────
class AlertDedup:
    def __init__(self):
        self._last: dict[str, float] = {}

    def should_alert(self, probe_name: str, level: str) -> bool:
        if probe_name in NO_DEDUP_PROBES:
            return True  # ws_guardian 每次都报
        if level == 'P0':
            ttl = 1800   # P0 每30min最多一次
        elif level == 'P1':
            ttl = 3600
        else:
            ttl = ALERT_DEDUP_TTL
        last = self._last.get(probe_name, 0)
        if time.time() - last > ttl:
            self._last[probe_name] = time.time()
            return True
        return False


# ── Jarvis推送 ────────────────────────────────────────────────
def send_jarvis(msg: str, urgent: bool = False):
    """通过openclaw CLI推送到SSOT线程"""
    to = f'{SSOT_USER}:thread:{SSOT_THREAD}'
    try:
        subprocess.run(
            ['openclaw', 'message', 'send',
             '--channel', 'jarvis',
             '--to', to,
             '--message', msg],
            timeout=10, capture_output=True
        )
    except Exception as e:
        _log(f'[dog_commander] Jarvis推送失败: {e}')


# ── 熔断器 ────────────────────────────────────────────────────
class CircuitBreaker:
    """同一探针自愈连续失败MAX_HEAL_TRIES次 → 熔断，停止自愈"""
    def __init__(self):
        self._fails: dict[str, int] = {}
        self._tripped: set[str] = set()

    def is_tripped(self, name: str) -> bool:
        return name in self._tripped

    def record_heal_result(self, name: str, success: bool):
        if success:
            self._fails.pop(name, None)
            self._tripped.discard(name)
        else:
            self._fails[name] = self._fails.get(name, 0) + 1
            if self._fails[name] >= MAX_HEAL_TRIES:
                self._tripped.add(name)
                _log(f'[dog_commander] 熔断触发: {name} 自愈失败{MAX_HEAL_TRIES}次')

    def reset(self, name: str):
        self._fails.pop(name, None)
        self._tripped.discard(name)


# ── 日志 ──────────────────────────────────────────────────────
def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


# ── 主指挥循环 ────────────────────────────────────────────────
class DogCommander:
    def __init__(self):
        self.history  = HealHistory(HEAL_LOG)
        self.dedup    = AlertDedup()
        self.breaker  = CircuitBreaker()
        self._pending_p1p2: list[str] = []   # P1/P2聚合后一起推
        self._last_pending_flush = time.time()
        PENDING_FLUSH_INTERVAL = 3600        # P1/P2每小时聚合一次

    def run_once(self):
        """运行一次完整探针周期"""
        from brahma_brain.dog_probes import run_all_probes
        results = run_all_probes()

        for r in results:
            if r.passed:
                self.breaker.reset(r.name)
                continue

            _log(f'[{r.level}] {r.name} FAIL: {r.message}')

            # 熔断检查
            if self.breaker.is_tripped(r.name):
                _log(f'[{r.name}] 熔断中，跳过自愈，等待人工介入')
                if self.dedup.should_alert(r.name + '_tripped', r.level):
                    send_jarvis(
                        f'🔴 [犬系统熔断] {r.name} 自愈已失败{MAX_HEAL_TRIES}次，需人工介入\n'
                        f'症状：{r.message}',
                        urgent=True
                    )
                continue

            # 执行自愈
            t0 = time.time()
            escalated = False
            if r.heal_fn:
                success = r.run_heal()
                duration = time.time() - t0
                self.breaker.record_heal_result(r.name, success)
                _log(f'[{r.name}] 自愈{"✅成功" if success else "❌失败"} ({duration:.1f}s)')
            else:
                success = False
                duration = 0.0
                _log(f'[{r.name}] 无自愈方案，直接上报')

            # 记录历史
            entry = self.history.record(
                probe_name=r.name,
                level=r.level,
                symptom=r.message,
                heal_action=r.heal_fn.__name__ if r.heal_fn else 'none',
                success=success,
                duration_s=duration,
                escalated=escalated
            )

            # 告警路由
            if self.dedup.should_alert(r.name, r.level):
                recurrence = entry['recurrence_24h']
                freq_tag = f' ⚠️ 24H内第{recurrence}次' if recurrence > 1 else ''
                msg_body = (
                    f'症状：{r.message}\n'
                    f'自愈：{"成功" if success else "失败"}{freq_tag}'
                )
                if r.level == 'P0' or not success:
                    # P0 或 自愈失败 → 立即推送
                    send_jarvis(
                        f'🚨 [犬系统{r.level}] {r.name}\n{msg_body}',
                        urgent=True
                    )
                    escalated = True
                else:
                    # P1/P2 自愈成功 → 聚合
                    self._pending_p1p2.append(f'  [{r.level}] {r.name}: {r.message} → 已自愈')

        # 聚合推送 P1/P2
        now = time.time()
        if self._pending_p1p2 and (now - self._last_pending_flush > 3600):
            lines = '\n'.join(self._pending_p1p2)
            send_jarvis(f'🐕 [犬系统周期报告]\n{lines}')
            self._pending_p1p2.clear()
            self._last_pending_flush = now

        # 高频故障检查（每6H一次）
        if not hasattr(self, '_last_hf_check'):
            self._last_hf_check = 0
        if now - self._last_hf_check > 21600:
            hf = self.history.high_frequency_faults(threshold=5)
            if hf:
                send_jarvis(f'⚠️ [犬系统高频故障] 24H内复发≥5次的探针：{hf}\n建议根因分析')
            self._last_hf_check = now

    def run_loop(self):
        """主循环：每30秒一次"""
        _log('[dog_commander] 犬系统启动 🐕 探针间隔=30s')
        while True:
            try:
                self.run_once()
            except Exception as e:
                _log(f'[dog_commander] 主循环异常: {e}')
            time.sleep(PROBE_INTERVAL)


# ── 入口 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description='犬系统指挥中心')
    parser.add_argument('--once', action='store_true', help='只运行一次后退出')
    parser.add_argument('--daemon', action='store_true', help='后台守护模式（nohup）')
    args = parser.parse_args()

    if args.daemon:
        log_path = BASE_DIR / 'logs' / 'dog_commander.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pid = os.fork() if hasattr(os, 'fork') else None
        if pid:
            print(f'[dog_commander] 后台启动 PID={pid} 日志={log_path}')
            return
        # 子进程
        sys.stdout = open(log_path, 'a', buffering=1)
        sys.stderr = sys.stdout

    commander = DogCommander()
    if args.once:
        commander.run_once()
        # 打印摘要
        print('\n=== 探针摘要 ===')
        from brahma_brain.dog_probes import run_all_probes
        for r in run_all_probes():
            icon = '✅' if r.passed else '❌'
            print(f'{icon} [{r.level}] {r.name}: {r.message}')
    else:
        commander.run_loop()


if __name__ == '__main__':
    main()
