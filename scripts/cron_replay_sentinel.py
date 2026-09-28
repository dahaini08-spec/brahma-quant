#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron_replay_sentinel.py — Cron打断重放哨兵
[2026-09-28 苏摩111] Harness缺口补齐：进程层有process_resurrect，cron层此前没有断点重放。
gateway重启会打断正在运行的cron job，留下 status=error / error="cron: job interrupted
by gateway restart" / delivered=false 的记录，且任务本体未执行——内容管道静默断流。

职责：
  扫描 ~/.openclaw/cron/runs/*.jsonl 中「被打断且未交付」的最近记录，
  按幂等窗口（同一job只重放最近一次，重放标记文件去重）自动重触发。

四闸防重放风暴：
  1. 重放标记文件（data/cron_replay_done.json）——同一run只重放一次，幂等
  2. 时间窗口（--window-min，默认180min）——只追最近的打断，不翻旧账
  3. 距打断最小间隔（--grace-min，默认12min）——确认不是正在跑，且躲开gateway重启后的抖动窗口
  4. 黑名单（--exclude，默认进程恢复类+守护类job）——重复job不重放，避免覆盖真实状态

用法：
  python3 scripts/cron_replay_sentinel.py             # 扫描+重放（默认干跑打印，--apply才真触发）
  python3 scripts/cron_replay_sentinel.py --apply     # 实际重触发
  python3 scripts/cron_replay_sentinel.py --apply --quiet   # 静默（cron模式）
接入位置：brahma_crontab.txt（15分钟一档，错开*/10哨兵簇）；输出接logs/cron_replay.log
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
RUNS_DIR = HOME / '.openclaw' / 'cron' / 'runs'
STATE_DIR = Path(__file__).parent.parent / 'data'
DONE_FILE = STATE_DIR / 'cron_replay_done.json'
LOG_PREFIX = '[cron-replay]'

INTERRUPT_PATTERNS = (
    'interrupted by gateway restart',
    'interrupted by shutdown',
)
# 守护/进程恢复类job不重放：它们的"运行"就是恢复动作本身，重放会叠加副作用
DEFAULT_EXCLUDE = {
    'process-resurrect-bridge',   # every 5m，重启后自会跑
    'chop-breakout-watch',        # every 2h，gap超出语义窗口，重放过期信号有害
    'square-trade-loop',          # every 12h，重复运行有真实副作用风险
    'auto-analysis',              # every 3h，等下一轮即新鲜数据
}


def load_done() -> dict:
    try:
        return json.loads(DONE_FILE.read_text())
    except Exception:
        return {}


def save_done(done: dict) -> None:
    now = time.time()
    # 保留7天内记录，防无限膨胀
    done = {k: v for k, v in done.items() if now - float(v.get('ts', 0)) < 7 * 86400}
    DONE_FILE.write_text(json.dumps(done, ensure_ascii=False, indent=2))


def scan_interrupted(window_min: float, grace_min: float, exclude: set) -> list:
    """返回候选重放列表：[(job_id, job_name, run_key, ts_iso, error)]"""
    now_ms = time.time() * 1000
    window_ms = window_min * 60 * 1000
    grace_ms = grace_min * 60 * 1000
    jobs_meta = load_jobs_meta()
    candidates = []
    if not RUNS_DIR.exists():
        return candidates
    for runs_file in RUNS_DIR.glob('*.jsonl'):
        job_id = runs_file.stem
        if job_id in exclude:
            continue
        name = jobs_meta.get(job_id, {}).get('name', job_id)
        if name in exclude or jobs_meta.get(job_id, {}).get('kind') == 'process':
            continue
        # 只读文件尾部（打断记录必然是最近的事件），大文件不整读
        try:
            lines = runs_file.read_text().strip().splitlines()[-50:]
        except Exception:
            continue
        for line in reversed(lines):  # 从最新往回扫
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts_ms = e.get('ts', 0)
            if not ts_ms or now_ms - ts_ms > window_ms:
                break  # 超窗口，更老的更不关心
            if e.get('action') != 'finished' or e.get('status') != 'error':
                continue
            err = (e.get('deliveryError') or e.get('error') or '')
            if not any(p in err for p in INTERRUPT_PATTERNS):
                continue
            if e.get('delivered'):
                continue  # 已交付的不重放
            if now_ms - ts_ms < grace_ms:
                break  # 还在grace窗口内（可能正在跑/抖动），跳过这个job本轮不管
            run_key = f'{job_id}:{ts_ms}'
            candidates.append({
                'job_id': job_id, 'name': name, 'run_key': run_key,
                'ts_iso': datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat(),
                'error': err[:80],
            })
            break  # 每个job只取最近一次打断
    return candidates


def load_jobs_meta() -> dict:
    meta = {}
    try:
        jobs = json.loads((HOME / '.openclaw' / 'cron' / 'jobs.json').read_text())
        for j in jobs.get('jobs', []):
            meta[j.get('id')] = {'name': j.get('name'), 'kind': j.get('kind')}
    except Exception:
        pass
    return meta


def main() -> None:
    args = sys.argv[1:]
    apply = '--apply' in args
    quiet = '--quiet' in args
    window_min = 180.0
    grace_min = 12.0
    if '--window-min' in args:
        window_min = float(args[args.index('--window-min') + 1])
    if '--grace-min' in args:
        grace_min = float(args[args.index('--grace-min') + 1])
    exclude = set(DEFAULT_EXCLUDE)

    done = load_done()
    candidates = scan_interrupted(window_min, grace_min, exclude)
    if not candidates:
        if not quiet:
            print(f'{LOG_PREFIX} 无待重放记录 (window={window_min}min, grace={grace_min}min)')
        return
    if not quiet:
        print(f'{LOG_PREFIX} 发现 {len(candidates)} 条打断记录:')
    for c in candidates:
        replayed = c['run_key'] in done
        if not quiet:
            print(f'  - {c["name"]} @ {c["ts_iso"]} ({c["error"]})'
                  f' → {"已重放过,跳过" if replayed else ("将重放" if apply else "干跑(未重放)")}')
        if replayed or not apply:
            continue
        try:
            r = subprocess.run(
                ['openclaw', 'cron', 'run', c['job_id']],
                capture_output=True, text=True, timeout=60,
            )
            ok = '"ok": true' in r.stdout or '"ok":true' in r.stdout
            done[c['run_key']] = {
                'ts': time.time(),
                'name': c['name'],
                'replay_ok': ok,
                'orig_ts': c['ts_iso'],
            }
            print(f'{LOG_PREFIX} 重放 {c["name"]}: {"已触发" if ok else "触发失败: " + (r.stdout + r.stderr)[:120]}')
        except Exception as e:
            done[c['run_key']] = {'ts': time.time(), 'name': c['name'], 'replay_ok': False, 'err': str(e)[:100]}
            print(f'{LOG_PREFIX} 重放 {c["name"]} 异常: {str(e)[:120]}')
    if apply:
        save_done(done)
        if not quiet:
            print(f'{LOG_PREFIX} 标记文件已更新: {DONE_FILE}')


if __name__ == '__main__':
    main()
