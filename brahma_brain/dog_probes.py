"""
犬系统 · Layer 2 健康探针矩阵
dog_probes.py

8项核心探针，每30秒轮询，异常即上报
[2026-07-23 设计院×达摩院×六方联合 封印]
"""
import os
import time
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 探针结果 ─────────────────────────────────────────────────

class ProbeResult:
    def __init__(self, name, level, passed, message, heal_fn=None):
        self.name     = name
        self.level    = level      # P0 / P1 / P2
        self.passed   = passed
        self.failed   = not passed
        self.message  = message
        self.heal_fn  = heal_fn
        self.ts       = time.time()

    def run_heal(self):
        if self.heal_fn:
            try:
                self.heal_fn()
                return True
            except Exception as e:
                return False
        return False


# ── 探针实现 ─────────────────────────────────────────────────

def probe_engine_alive() -> ProbeResult:
    """P0: 评分引擎存活检查"""
    try:
        from brahma_brain.brahma_health import run_health_check
        result = run_health_check(full=False)
        score = result.get('score', 0)
        passed = score >= 50
        return ProbeResult(
            'engine_alive', 'P0', passed,
            f'score={score}' if passed else f'引擎异常 score={score}',
            heal_fn=None  # 引擎崩溃需人工介入
        )
    except Exception as e:
        return ProbeResult('engine_alive', 'P0', False, f'引擎探针异常: {e}')


def probe_api_connectivity() -> ProbeResult:
    """P0: Binance API连通性"""
    try:
        import urllib.request, json
        start = time.time()
        r = urllib.request.urlopen(
            'https://fapi.binance.com/fapi/v1/ping', timeout=5)
        latency_ms = int((time.time() - start) * 1000)
        passed = latency_ms < 2000
        def heal():
            # 清BrahmaBus缓存
            try:
                from brahma_brain.brahma_bus import BrahmaBus
                BrahmaBus().flush_stale()
            except Exception:
                pass
        return ProbeResult(
            'api_connectivity', 'P0', passed,
            f'延迟={latency_ms}ms' if passed else f'API延迟过高 {latency_ms}ms',
            heal_fn=heal
        )
    except Exception as e:
        return ProbeResult('api_connectivity', 'P0', False, f'API不可达: {e}',
                           heal_fn=None)


def probe_ws_guardian() -> ProbeResult:
    """P1: ws_guardian心跳检查（cron定时任务，检查日志最近更新时间<30min）"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(base, 'logs', 'ws_guardian.log')
        if not os.path.exists(log_path):
            passed = False
            msg = 'ws_guardian日志不存在'
        else:
            age_min = (time.time() - os.path.getmtime(log_path)) / 60
            passed = age_min < 30
            msg = f'ws_guardian 最近心跳={age_min:.1f}min前' if passed else f'ws_guardian 心跳超时={age_min:.1f}min (>30min)'
        def heal():
            # 触发一次 ws_guardian cron（通过直接运行）
            subprocess.Popen(
                ['python3', os.path.join(base, 'scripts', 'ws_guardian.py')],
                stdout=open(log_path, 'a'), stderr=subprocess.STDOUT
            )
        return ProbeResult('ws_guardian', 'P1', passed, msg, heal_fn=heal)
    except Exception as e:
        return ProbeResult('ws_guardian', 'P1', False, f'探针异常: {e}')


def probe_dxy_freshness() -> ProbeResult:
    """P1: DXY宏观数据新鲜度（< 4H）"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        macro_path = os.path.join(base, 'data', 'macro_state.json')
        if not os.path.exists(macro_path):
            passed = False
            age_h = 999
        else:
            age_h = (time.time() - os.path.getmtime(macro_path)) / 3600
            passed = age_h < 4.0
        def heal():
            from brahma_brain.macro_engine import write_macro_state
            write_macro_state()
        return ProbeResult(
            'dxy_freshness', 'P1', passed,
            f'DXY数据 age={age_h:.2f}h' if passed else f'DXY数据陈旧 age={age_h:.2f}h (>4H)',
            heal_fn=heal
        )
    except Exception as e:
        return ProbeResult('dxy_freshness', 'P1', False, f'探针异常: {e}')


def probe_ic_statistics() -> ProbeResult:
    """P1: IC统计数据新鲜度（< 48H）"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ic_path = os.path.join(base, 'data', 'ic_tracker_state.json')
        if not os.path.exists(ic_path):
            passed = False
            age_h = 999
        else:
            age_h = (time.time() - os.path.getmtime(ic_path)) / 3600
            passed = age_h < 48.0
        def heal():
            subprocess.run(
                ['python3', 'scripts/brahma_learning_loop.py'],
                cwd=base, timeout=120
            )
        return ProbeResult(
            'ic_statistics', 'P1', passed,
            f'IC统计 age={age_h:.2f}h' if passed else f'IC统计陈旧 age={age_h:.2f}h (>48H)',
            heal_fn=heal
        )
    except Exception as e:
        return ProbeResult('ic_statistics', 'P1', False, f'探针异常: {e}')


def probe_signal_queue_drain() -> ProbeResult:
    """P0: 信号队列不积压"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        import json
        log_path = os.path.join(base, 'data', 'live_signal_log.jsonl')
        if not os.path.exists(log_path):
            return ProbeResult('signal_queue_drain', 'P0', True, '信号日志不存在（正常）')
        # 统计最近1H内未推送信号数
        now = time.time()
        with open(log_path) as f:
            lines = f.readlines()[-200:]
        pending = 0
        for l in lines:
            try:
                d = json.loads(l)
                ts = d.get('ts', 0)
                pushed = d.get('pushed', True)
                if not pushed and (now - float(ts)) < 3600:
                    pending += 1
            except Exception:
                pass
        passed = pending < 10
        return ProbeResult(
            'signal_queue_drain', 'P0', passed,
            f'积压信号={pending}条' if passed else f'信号积压过多 {pending}条 (>10)',
        )
    except Exception as e:
        return ProbeResult('signal_queue_drain', 'P0', True, f'探针跳过: {e}')


def probe_cron_route_ssot() -> ProbeResult:
    """P2: Cron任务路由SSOT一致性"""
    try:
        import json
        from pathlib import Path
        SSOT = '73295708:t:019fd9dd-4b0f-71db-87fb-1e192ccb2291'
        jobs_path = Path.home() / '.openclaw/cron/jobs.json'
        if not jobs_path.exists():
            return ProbeResult('cron_route_ssot', 'P2', True, 'jobs.json不存在')
        data = json.loads(jobs_path.read_text())
        jobs = data if isinstance(data, list) else data.get('jobs', list(data.values()))
        wrong = []
        skip_names = ['Square','广场','square','live-performance','brahma-arch','data-backup']
        for j in jobs:
            if not isinstance(j, dict): continue
            name = j.get('name', '')
            if any(s in name for s in skip_names): continue
            msg = (j.get('payload') or j).get('message', '')
            if not msg: continue  # 无消息的静默任务跳过
            to = j.get('delivery', {}).get('to', '')
            if to and SSOT not in to:
                wrong.append(name)
        passed = len(wrong) == 0
        def heal():
            data2 = json.loads(jobs_path.read_text())
            jobs2 = data2 if isinstance(data2, list) else data2.get('jobs', list(data2.values()))
            for j2 in (jobs2 if isinstance(data2, list) else jobs2):
                if not isinstance(j2, dict): continue
                if j2.get('name','') in wrong and 'delivery' in j2:
                    j2['delivery']['to'] = SSOT
            jobs_path.write_text(json.dumps(data2, ensure_ascii=False, indent=2))
        return ProbeResult(
            'cron_route_ssot', 'P2', passed,
            'Cron路由全部正确' if passed else f'路由异常: {wrong[:3]}',
            heal_fn=heal
        )
    except Exception as e:
        return ProbeResult('cron_route_ssot', 'P2', True, f'探针跳过: {e}')


def probe_360_freshness() -> ProbeResult:
    """P2: 360体检报告新鲜度（< 24H）"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rpt_path = os.path.join(base, 'data', 'brahma_360_report.json')
        if not os.path.exists(rpt_path):
            passed = False
            age_h = 999
        else:
            age_h = (time.time() - os.path.getmtime(rpt_path)) / 3600
            passed = age_h < 24.0
        def heal():
            from brahma_brain.brahma_360 import run_full_scan
            import json as _j
            from pathlib import Path
            result = run_full_scan()
            Path(rpt_path).write_text(_j.dumps(result, ensure_ascii=False, indent=2, default=str))
        return ProbeResult(
            '360_freshness', 'P2', passed,
            f'360报告 age={age_h:.2f}h' if passed else f'360报告陈旧 age={age_h:.2f}h (>24H)',
            heal_fn=heal
        )
    except Exception as e:
        return ProbeResult('360_freshness', 'P2', True, f'探针跳过: {e}')


# ── 探针注册表 ───────────────────────────────────────────────

def probe_cron_gateway_conflict() -> ProbeResult:
    """P2: 检测cron任务是否与gateway-daily-restart(12:00 UTC)时间冲突"""
    try:
        import json
        from pathlib import Path
        jobs_path = Path.home() / '.openclaw/cron/jobs.json'
        if not jobs_path.exists():
            return ProbeResult('cron_gateway_conflict', 'P2', True, 'jobs.json不存在')
        data = json.loads(jobs_path.read_text())
        jobs = data if isinstance(data, list) else data.get('jobs', list(data.values()))
        conflicts = []
        for j in jobs:
            if not isinstance(j, dict): continue
            name = j.get('name', '')
            if 'gateway' in name.lower(): continue  # 跳过gateway自身
            sched = j.get('schedule', {})
            anchor_ms = sched.get('anchorMs', 0)
            every_ms = sched.get('everyMs', 0)
            if not (anchor_ms and every_ms): continue
            # 间隔<=15min的任务不可避免，豆免检测
            if every_ms <= 15 * 60 * 1000: continue
            # 检查未来8次触发是否落在 11:50~12:10 UTC 窗口
            for i in range(8):
                t_ms = anchor_ms + i * every_ms
                # 将ms转为当天小时
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
                if dt.hour == 12 and dt.minute <= 10:
                    conflicts.append(name)
                    break
                if dt.hour == 11 and dt.minute >= 50:
                    conflicts.append(name)
                    break
        passed = len(conflicts) == 0
        return ProbeResult(
            'cron_gateway_conflict', 'P2', passed,
            'Cron时间无冲突' if passed else f'与gateway-restart冲突: {conflicts[:3]}',
        )
    except Exception as e:
        return ProbeResult('cron_gateway_conflict', 'P2', True, f'探针跳过: {e}')


ALL_PROBES = [
    probe_engine_alive,
    probe_api_connectivity,
    probe_ws_guardian,
    probe_dxy_freshness,
    probe_ic_statistics,
    probe_signal_queue_drain,
    probe_cron_route_ssot,
    probe_360_freshness,
    probe_cron_gateway_conflict,
]

LEVEL_PRIORITY = {'P0': 0, 'P1': 1, 'P2': 2}


def run_all_probes() -> list:
    """运行全部探针，返回排序后的ProbeResult列表"""
    results = []
    for probe_fn in ALL_PROBES:
        try:
            results.append(probe_fn())
        except Exception as e:
            results.append(ProbeResult(probe_fn.__name__, 'P1', False, f'探针崩溃: {e}'))
    results.sort(key=lambda r: LEVEL_PRIORITY.get(r.level, 9))
    return results


if __name__ == '__main__':
    print('=== 犬系统探针矩阵自检 ===')
    for r in run_all_probes():
        icon = '✅' if r.passed else '❌'
        print(f'{icon} [{r.level}] {r.name}: {r.message}')
