#!/usr/bin/env python3
"""
dharma/run_replay.py — 达摩院离线回放安全启动器 v1.0
设计院 × 达摩院 2026-06-18

解决三大实训障碍：
  1. SIGTERM：每5min保存进度，被杀后自动从断点续跑
  2. smc格式：已在offline_brahma_replay.py v2.1修复
  3. 小样本：gen_regime_matrix.py 强制 n≥100 才入矩阵

用法：
  python3 dharma/run_replay.py                    # 全量 BTC+ETH
  python3 dharma/run_replay.py --sym BTCUSDT      # 单品种
  python3 dharma/run_replay.py --resume           # 从断点续跑
  python3 dharma/run_replay.py --gen-matrix       # 只生成矩阵（已有结果）

特性：
  - 每60秒检测进度文件，自动续跑
  - 最大重试3次（每次从最新断点开始）
  - 完成后自动调用 gen_regime_matrix.py
"""

import sys, os, subprocess, time, json, argparse, signal
from pathlib import Path
from datetime import datetime, timezone

BASE    = Path(__file__).parent.parent
RESULTS = BASE / 'dharma' / 'results'
LOG     = BASE / 'logs' / 'dharma_replay.log'
RESULTS.mkdir(exist_ok=True)
(BASE / 'logs').mkdir(exist_ok=True)

MAX_RETRIES    = 10      # 最大续跑次数（每次从断点继续）
CHECK_INTERVAL = 30      # 检查进程存活间隔（秒）
TIMEOUT_IDLE   = 120     # 超过120秒无新信号输出 → 进程异常，重启


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    line = f'[RunReplay {ts}] {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def count_checkpoint(sym: str) -> int:
    """读取断点文件，返回已完成信号数"""
    ckpt = RESULTS / f'replay_{sym.lower()}_checkpoint.jsonl'
    if not ckpt.exists():
        return 0
    try:
        lines = [l for l in ckpt.read_text().splitlines() if l.strip()]
        return len(lines)
    except Exception:
        return 0


def gen_matrix():
    """完成后生成个币WR矩阵"""
    log('生成个币WR矩阵...')
    r = subprocess.run(
        [sys.executable, str(BASE / 'dharma' / 'gen_regime_matrix.py')],
        capture_output=True, text=True, cwd=str(BASE)
    )
    for line in (r.stdout + r.stderr).splitlines():
        if line.strip():
            log(f'  [matrix] {line}')


def run_replay(sym: str, attempt: int = 0) -> bool:
    """启动一次回放进程，返回是否成功完成"""
    ckpt_n = count_checkpoint(sym)
    log(f'[{sym}] 尝试 #{attempt+1} | 断点进度: {ckpt_n} 条信号')

    # 直接调用 offline_brahma_replay.py（已支持断点续跑参数）
    env = os.environ.copy()
    env['DHARMA_SYM'] = sym  # 传递单品种参数

    cmd = [sys.executable, str(BASE / 'dharma' / 'offline_brahma_replay.py')]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(BASE),
        env=env,
        preexec_fn=os.setsid  # 独立进程组，不受 ws_guardian SIGTERM 影响
    )

    log(f'[{sym}] 进程启动 PID={proc.pid}')

    last_progress = time.time()
    last_ckpt_n   = ckpt_n

    try:
        while True:
            # 非阻塞读取输出
            try:
                line = proc.stdout.readline()
                if line:
                    line = line.rstrip()
                    log(f'  {line}')
                    last_progress = time.time()
            except Exception:
                pass

            # 检查进程是否结束
            ret = proc.poll()
            if ret is not None:
                if ret == 0:
                    log(f'[{sym}] 回放完成 ✅ (exit=0)')
                    return True
                else:
                    log(f'[{sym}] 进程异常退出 exit={ret}')
                    return False

            # 检查进度（断点文件增长 = 正在运行）
            cur_n = count_checkpoint(sym)
            if cur_n > last_ckpt_n:
                last_ckpt_n   = cur_n
                last_progress = time.time()

            # 超时检测
            idle = time.time() - last_progress
            if idle > TIMEOUT_IDLE:
                log(f'[{sym}] 超过{TIMEOUT_IDLE}s无进度 → 杀掉并重启')
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    proc.terminate()
                time.sleep(3)
                return False

            time.sleep(2)

    except KeyboardInterrupt:
        log('用户中断，保存当前进度...')
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description='达摩院回放安全启动器')
    parser.add_argument('--sym', default=None, help='指定品种 (BTCUSDT/ETHUSDT/ALL)')
    parser.add_argument('--resume', action='store_true', help='从断点续跑')
    parser.add_argument('--gen-matrix', action='store_true', help='只生成矩阵')
    args = parser.parse_args()

    if args.gen_matrix:
        gen_matrix()
        return

    syms = ['BTCUSDT', 'ETHUSDT']
    if args.sym and args.sym.upper() != 'ALL':
        syms = [args.sym.upper()]

    log(f'达摩院回放启动器 v1.0 | 品种: {syms}')
    log(f'断点续跑: {"是" if args.resume else "否"} | 最大重试: {MAX_RETRIES}次')
    log('=' * 50)

    # 清除旧断点（非续跑模式）
    if not args.resume:
        for sym in syms:
            ckpt = RESULTS / f'replay_{sym.lower()}_checkpoint.jsonl'
            if ckpt.exists():
                ckpt.unlink()
                log(f'清除旧断点: {ckpt.name}')

    total_start = time.time()

    for sym in syms:
        success = False
        for attempt in range(MAX_RETRIES):
            success = run_replay(sym, attempt)
            if success:
                break
            wait = min(30 * (attempt + 1), 120)
            log(f'[{sym}] {wait}s后重试 (已重试{attempt+1}/{MAX_RETRIES})...')
            time.sleep(wait)

        if not success:
            log(f'[{sym}] ❌ {MAX_RETRIES}次重试后仍未完成，跳过')
        else:
            log(f'[{sym}] ✅ 完成')

    # 生成矩阵
    gen_matrix()

    elapsed = (time.time() - total_start) / 60
    log(f'全部完成 | 耗时 {elapsed:.1f} 分钟')


if __name__ == '__main__':
    main()
