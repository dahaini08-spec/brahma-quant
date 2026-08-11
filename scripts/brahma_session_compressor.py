#!/usr/bin/env python3
"""
梵天对话压缩工程 — brahma_session_compressor.py
设计院 2026-08-11 封印

功能：
1. 清理旧checkpoint文件（>24h，已无用的压缩快照）
2. 清理7天以上非活跃transcript
3. 监控Node.js内存，超1.5GB时预警
4. 记录压缩日志到 data/session_compress_log.jsonl

STATUS: ACTIVE
触发：每6H一次（brahma-session-compressor cron）
"""
import os, sys, time, json, subprocess
from pathlib import Path

BASE = Path(__file__).parent.parent
SESSIONS_DIR = Path('/root/.openclaw/agents/main/sessions')
LOG_FILE = BASE / 'data' / 'session_compress_log.jsonl'
ACTIVE_THREAD = '019fd9dd-4b0f-71db-87fb-1e192ccb2291'  # 苏摩主线程

# ── 配置 ───────────────────────────────────────────
CHECKPOINT_MAX_AGE_H = 24       # checkpoint保留时长
TRANSCRIPT_MAX_AGE_DAYS = 7     # 非活跃transcript保留时长
NODE_MEM_WARN_MB = 1500         # Node.js内存预警阈值
NODE_MEM_CRITICAL_MB = 2000     # Node.js内存严重阈值


def get_node_rss_mb() -> float:
    """获取OpenClaw Node.js进程RSS内存"""
    try:
        r = subprocess.run(['ps', 'aux', '--no-headers'], capture_output=True, text=True)
        for line in r.stdout.split('\n'):
            if 'node' in line.lower() and 'openclaw' in line.lower():
                parts = line.split()
                if len(parts) > 5:
                    return int(parts[5]) / 1024  # KB -> MB
    except Exception:
        pass
    # fallback: 找最大node进程
    try:
        r = subprocess.run(['ps', 'aux', '--sort=-%mem', '--no-headers'],
                          capture_output=True, text=True)
        for line in r.stdout.split('\n'):
            if 'node' in line.lower():
                parts = line.split()
                if len(parts) > 5:
                    return int(parts[5]) / 1024
    except Exception:
        pass
    return 0.0


def get_available_mb() -> float:
    """获取系统可用内存"""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 9999.0


def clean_sessions() -> dict:
    """清理过期session文件"""
    if not SESSIONS_DIR.exists():
        return {'error': 'sessions dir not found'}

    now = time.time()
    freed_cp = 0
    removed_cp = 0
    freed_tr = 0
    removed_tr = 0
    skipped = []

    for f in SESSIONS_DIR.glob('*.jsonl'):
        try:
            age_h = (now - f.stat().st_mtime) / 3600
            sz = f.stat().st_size

            if '.checkpoint.' in f.name:
                # checkpoint：超过24h清理
                if age_h > CHECKPOINT_MAX_AGE_H:
                    f.unlink()
                    freed_cp += sz
                    removed_cp += 1
            else:
                # 主transcript：保护活跃线程，7天以上清理
                if ACTIVE_THREAD in f.name:
                    skipped.append(f.name[:40])
                    continue
                age_days = age_h / 24
                if age_days > TRANSCRIPT_MAX_AGE_DAYS:
                    f.unlink()
                    freed_tr += sz
                    removed_tr += 1
        except Exception as e:
            pass  # 跳过无法删除的文件

    return {
        'checkpoint_removed': removed_cp,
        'checkpoint_freed_mb': round(freed_cp / 1024 / 1024, 1),
        'transcript_removed': removed_tr,
        'transcript_freed_mb': round(freed_tr / 1024 / 1024, 1),
        'total_freed_mb': round((freed_cp + freed_tr) / 1024 / 1024, 1),
        'skipped': len(skipped),
    }


def check_core_dumps() -> dict:
    """清理core dump文件"""
    cwd = Path('/root/.openclaw/workspace/trading-system')
    cores = list(cwd.glob('core.*'))
    freed = 0
    for c in cores:
        try:
            freed += c.stat().st_size
            c.unlink()
        except Exception:
            pass
    return {'cores_removed': len(cores), 'freed_mb': round(freed / 1024 / 1024, 1)}


def check_tmp_cache() -> dict:
    """清理/tmp过期brahma缓存"""
    now = time.time()
    removed = 0
    freed = 0
    TTL = 7200  # 2h

    # brahma-cold/shared/work klines缓存
    for d in ['/tmp/brahma-cold', '/tmp/brahma-shared', '/tmp/brahma-work']:
        dp = Path(d)
        if not dp.exists():
            continue
        for f in dp.iterdir():
            try:
                age = now - f.stat().st_mtime
                if age > TTL and f.is_file():
                    freed += f.stat().st_size
                    f.unlink()
                    removed += 1
            except Exception:
                pass

    # brahma临时碎片
    for pattern in ['brahma_chunk_*.txt', 'brahma_*.pkl']:
        for f in Path('/tmp').glob(pattern):
            try:
                freed += f.stat().st_size
                f.unlink()
                removed += 1
            except Exception:
                pass

    return {'tmp_removed': removed, 'freed_mb': round(freed / 1024 / 1024, 1)}


def main():
    ts = time.time()
    ts_str = time.strftime('%Y-%m-%d %H:%M CST', time.gmtime(ts + 8 * 3600))

    # 执行清理
    session_result = clean_sessions()
    core_result = check_core_dumps()
    tmp_result = check_tmp_cache()

    total_freed = (session_result['total_freed_mb'] +
                   core_result['freed_mb'] +
                   tmp_result['freed_mb'])

    # 内存状态
    node_mb = get_node_rss_mb()
    avail_mb = get_available_mb()

    # 日志记录
    log_entry = {
        'ts': ts,
        'ts_str': ts_str,
        'session': session_result,
        'core': core_result,
        'tmp': tmp_result,
        'total_freed_mb': round(total_freed, 1),
        'node_rss_mb': round(node_mb, 0),
        'avail_mb': round(avail_mb, 0),
    }
    LOG_FILE.parent.mkdir(exist_ok=True)
    with open(LOG_FILE, 'a') as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

    # 决定是否推送
    need_push = False
    msg_lines = [f'🗜️ 梵天对话压缩工程 {ts_str}']

    if total_freed > 50:
        msg_lines.append(f'✅ 释放 {total_freed:.0f}MB')
        msg_lines.append(f'  checkpoint清理: {session_result["checkpoint_removed"]}个 ({session_result["checkpoint_freed_mb"]}MB)')
        if session_result['transcript_removed'] > 0:
            msg_lines.append(f'  transcript清理: {session_result["transcript_removed"]}个 ({session_result["transcript_freed_mb"]}MB)')
        if core_result['cores_removed'] > 0:
            msg_lines.append(f'  core dump清理: {core_result["cores_removed"]}个 ({core_result["freed_mb"]}MB)')
        need_push = True

    if node_mb > NODE_MEM_WARN_MB:
        level = '🔴 严重' if node_mb > NODE_MEM_CRITICAL_MB else '⚠️ 警告'
        msg_lines.append(f'{level} Node.js RSS={node_mb:.0f}MB (可用{avail_mb:.0f}MB)')
        if node_mb > NODE_MEM_CRITICAL_MB:
            msg_lines.append('→ 建议执行 gateway restart 释放内存')
        need_push = True

    if not need_push:
        # 无重要变化，静默
        print('HEARTBEAT_OK')
        return

    # 推送到苏摩
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from push_hub import _jarvis
        _jarvis('\n'.join(msg_lines))
    except Exception:
        pass

    print('\n'.join(msg_lines))


if __name__ == '__main__':
    main()
