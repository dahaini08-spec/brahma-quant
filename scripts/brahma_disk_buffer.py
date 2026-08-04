#!/usr/bin/env python3
"""
brahma_disk_buffer.py — 梵天磁盘缓冲层 v1.0
设计院自主决策落地 2026-08-04 | 苏摩111授权

核心职责:
  1. 内存压力监控 — 当 Available < THRESHOLD_MB 时触发磁盘卸载
  2. 对象序列化 — 将大型缓存对象 pickle 到 /tmp/brahma-membuf/
  3. 按需加载   — 需要时从磁盘透明恢复对象到内存
  4. 日志迁移   — 高速增长日志双写到 /tmp/brahma-archive/
  5. Backtest解压 — 按需从 /tmp/brahma-cold/ 解压到 /tmp/brahma-work/

设计原则:
  - /tmp 是 98GB 物理盘，不占 RAM
  - 所有操作幂等，重复调用无副作用
  - 失败静默降级（不影响主链路）
"""

import os, sys, json, gzip, time, pickle, shutil
from pathlib import Path
from datetime import datetime, timezone

BASE        = Path(__file__).parent.parent
DATA        = BASE / 'data'
BUF_DIR     = Path('/tmp/brahma-membuf')
ARCHIVE_DIR = Path('/tmp/brahma-archive')
COLD_DIR    = Path('/tmp/brahma-cold')
WORK_DIR    = Path('/tmp/brahma-work')

# ── 阈值配置 ─────────────────────────────────────────────
THRESHOLD_MB  = 800    # Available低于此值触发卸载
CRITICAL_MB   = 400    # 危险线，强制卸载所有可卸载对象
BUF_TTL_SEC   = 3600   # 磁盘缓存TTL（1小时）

# ── 日志轮转规则（文件名 → 保留行数）───────────────────
LOG_ROTATION = {
    'capital_alloc.jsonl':          500,
    'kronos_bridge_shadow.jsonl':  1000,
    'nerve_alerts.jsonl':           500,
    'signal_bus.jsonl':             300,
    'oi_advanced_signals.jsonl':    500,
    'tradfi_signal_log.jsonl':      500,
    'ev_feedback_log.jsonl':        300,
    'llm_council_shadow_log.jsonl': 300,
}

def _get_available_mb() -> float:
    """获取当前可用内存(MB)"""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return 9999.0

def _ensure_dirs():
    for d in [BUF_DIR, ARCHIVE_DIR, COLD_DIR, WORK_DIR]:
        d.mkdir(parents=True, exist_ok=True)

# ── 磁盘对象缓冲 ─────────────────────────────────────────
def offload(key: str, obj) -> bool:
    """将大对象序列化到磁盘，释放内存"""
    try:
        _ensure_dirs()
        path = BUF_DIR / f'{key}.pkl'
        with open(path, 'wb') as f:
            pickle.dump({'ts': time.time(), 'obj': obj}, f, protocol=4)
        return True
    except Exception as e:
        print(f'[disk_buffer] offload {key} failed: {e}')
        return False

def load_buf(key: str):
    """从磁盘加载对象，超TTL则返回None"""
    try:
        path = BUF_DIR / f'{key}.pkl'
        if not path.exists():
            return None
        with open(path, 'rb') as f:
            data = pickle.load(f)
        if time.time() - data['ts'] > BUF_TTL_SEC:
            path.unlink(missing_ok=True)
            return None
        return data['obj']
    except Exception:
        return None

def auto_manage():
    """内存压力自动管理：低内存时触发磁盘卸载并通知"""
    avail = _get_available_mb()
    actions = []

    if avail < CRITICAL_MB:
        # 强制日志轮转释放内存映射
        freed = rotate_logs()
        actions.append(f'CRITICAL: 强制日志轮转 释放{freed:.1f}MB')

    if avail < THRESHOLD_MB:
        actions.append(f'内存预警: Available={avail:.0f}MB < {THRESHOLD_MB}MB')
        # 清理过期buf缓存
        cleaned = cleanup_buf()
        if cleaned:
            actions.append(f'清理过期buf: {cleaned}个文件')

    return {'available_mb': avail, 'actions': actions}

def cleanup_buf():
    """清理过期的磁盘缓冲文件"""
    if not BUF_DIR.exists():
        return 0
    count = 0
    for f in BUF_DIR.glob('*.pkl'):
        try:
            if time.time() - f.stat().st_mtime > BUF_TTL_SEC:
                f.unlink()
                count += 1
        except Exception:
            pass
    return count

# ── 日志轮转 ──────────────────────────────────────────────
def rotate_logs() -> float:
    """截断高速增长日志，返回释放的MB数"""
    total_freed = 0.0
    for fname, keep in LOG_ROTATION.items():
        fpath = DATA / fname
        if not fpath.exists():
            continue
        try:
            old_size = fpath.stat().st_size
            lines = fpath.read_text(errors='replace').split('\n')
            lines = [l for l in lines if l.strip()]
            if len(lines) <= keep:
                continue
            # 归档旧数据到/tmp
            _archive_log(fname, lines[:-keep])
            # 保留最近N行
            fpath.write_text('\n'.join(lines[-keep:]) + '\n')
            new_size = fpath.stat().st_size
            total_freed += (old_size - new_size) / 1024 / 1024
        except Exception as e:
            print(f'[disk_buffer] rotate {fname} failed: {e}')
    return total_freed

def _archive_log(fname: str, old_lines: list):
    """将旧日志归档到/tmp/brahma-archive/"""
    try:
        _ensure_dirs()
        date_str = datetime.now(timezone.utc).strftime('%Y%m%d')
        archive_path = ARCHIVE_DIR / f'{date_str}_{fname}.gz'
        content = '\n'.join(old_lines)
        with gzip.open(archive_path, 'at') as f:
            f.write(content + '\n')
    except Exception:
        pass

# ── Backtest数据按需解压 ──────────────────────────────────
def get_backtest_data(symbol: str, timeframe: str) -> list | None:
    """
    按需加载backtest数据：
    1. 优先从工作目录读取（已解压）
    2. 其次从/tmp/brahma-cold/解压
    3. 最后回退到workspace/data/backtest/

    调用示例：
        data = get_backtest_data('BTCUSDT', '15m')
    """
    fname = f'{symbol}_{timeframe}.json'
    work_path = WORK_DIR / fname
    cold_path = COLD_DIR / 'backtest' / f'{fname}.gz'
    orig_path = DATA / 'backtest' / fname

    # 1. 工作目录缓存
    if work_path.exists():
        age = time.time() - work_path.stat().st_mtime
        if age < 86400:  # 24h内有效
            try:
                with open(work_path) as f:
                    return json.load(f)
            except Exception:
                work_path.unlink(missing_ok=True)

    # 2. 从/tmp cold解压
    if cold_path.exists():
        try:
            _ensure_dirs()
            with gzip.open(cold_path, 'rt') as f:
                data = json.load(f)
            # 缓存到work目录
            with open(work_path, 'w') as f:
                json.dump(data, f)
            print(f'[disk_buffer] 解压 {fname}: {cold_path} → {work_path}')
            return data
        except Exception as e:
            print(f'[disk_buffer] 解压失败 {fname}: {e}')

    # 3. 回退到原始文件
    if orig_path.exists():
        try:
            with open(orig_path) as f:
                return json.load(f)
        except Exception:
            pass

    return None

# ── /tmp清理策略 ──────────────────────────────────────────
def cleanup_tmp_archives(keep_days: int = 7):
    """清理超过keep_days天的归档文件"""
    if not ARCHIVE_DIR.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    count = 0
    for f in ARCHIVE_DIR.glob('*.gz'):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                count += 1
        except Exception:
            pass
    return count

def cleanup_work_cache(keep_hours: int = 24):
    """清理超时的工作目录缓存"""
    if not WORK_DIR.exists():
        return 0
    cutoff = time.time() - keep_hours * 3600
    count = 0
    for f in WORK_DIR.glob('*.json'):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                count += 1
        except Exception:
            pass
    return count

# ── 状态报告 ──────────────────────────────────────────────
def status() -> dict:
    """输出磁盘缓冲层完整状态"""
    avail_mb = _get_available_mb()

    def dir_size(d: Path) -> str:
        if not d.exists(): return '0MB'
        total = sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
        return f'{total/1024/1024:.1f}MB'

    return {
        'available_ram_mb': round(avail_mb, 1),
        'pressure': 'CRITICAL' if avail_mb < CRITICAL_MB else
                    'WARN' if avail_mb < THRESHOLD_MB else 'OK',
        'buf_dir':     dir_size(BUF_DIR),
        'archive_dir': dir_size(ARCHIVE_DIR),
        'cold_dir':    dir_size(COLD_DIR),
        'work_dir':    dir_size(WORK_DIR),
        'tmp_total':   dir_size(Path('/tmp')),
    }

# ── CLI入口 ───────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='梵天磁盘缓冲层')
    parser.add_argument('--status',  action='store_true', help='显示状态')
    parser.add_argument('--rotate',  action='store_true', help='执行日志轮转')
    parser.add_argument('--cleanup', action='store_true', help='清理过期缓存')
    parser.add_argument('--auto',    action='store_true', help='自动内存管理')
    args = parser.parse_args()

    if args.status:
        s = status()
        print('=== 梵天磁盘缓冲层状态 ===')
        for k, v in s.items():
            print(f'  {k}: {v}')

    elif args.rotate:
        freed = rotate_logs()
        print(f'✅ 日志轮转完成，释放 {freed:.1f}MB')

    elif args.cleanup:
        a = cleanup_tmp_archives()
        w = cleanup_work_cache()
        b = cleanup_buf()
        print(f'✅ 清理完成: archive={a}个 work={w}个 buf={b}个')

    elif args.auto:
        result = auto_manage()
        avail = result['available_mb']
        actions = result['actions']
        if actions:
            print(f'⚠️ 内存管理: {avail:.0f}MB available')
            for a in actions: print(f'  {a}')
        else:
            print(f'HEARTBEAT_OK — Available={avail:.0f}MB')
    else:
        s = status()
        print(json.dumps(s, ensure_ascii=False, indent=2))
