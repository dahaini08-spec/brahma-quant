#!/usr/bin/env python3
"""[9.27wheelhouse 苏摩111·三方审核] 容器层重置(self-healing)自动恢复依赖。

根因：平台overlaybd容器在gateway重启后回滚writable layer → /usr/local
（dist-packages+libgomp）中被"事后安装"的包整批丢失（mcp×4次、tornado、
websocket-client、pytest、lightgbm、libgomp.so.1全中过）。镜像内置包
(requests/pandas/numpy)不受影响。

修复策略：
1. wheelhouse/ 持久层（workspace不受overlay重置影响）存离线wheels
2. 检查关键包import，缺则 wheelhouse --no-index 静默恢复（无网络依赖，秒级）
3. libgomp单独处理（wget复制到/usr/local/lib + ldconfig）
4. 恢复日志写 logs/ensure_deps.log，异常exit 1供cron告警

接入位置：start_supercronic.sh（每次启动调用）+ 独立cron兜底（每15min）
"""
import importlib
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 关键包：wheelhouse必须覆盖（wipe高危名单）
PIP_PKGS = {
    'mcp': 'mcp',
    'tornado': 'tornado',
    'websocket': 'websocket-client',
    'pytest': 'pytest',
    'lightgbm': 'lightgbm',
}

LIBGOMP_SRC = None  # 由脚本探测


def have(mod):
    try:
        importlib.import_module(mod)
        return True
    except Exception:
        return False


def find_libgomp():
    for d in ['/usr/lib/x86_64-linux-gnu', '/usr/lib64', '/usr/lib']:
        p = os.path.join(d, 'libgomp.so.1')
        if os.path.exists(p):
            return p
    # workspace持久副本
    w = os.path.join(os.getcwd(), 'wheelhouse', 'libgomp.so.1')
    if os.path.exists(w):
        return w
    return None


def restore_libgomp():
    src = find_libgomp()
    if not src:
        return False
    try:
        dst = '/usr/local/lib/libgomp.so.1'
        if src != dst:
            subprocess.run(['cp', src, dst], check=True, timeout=10)
        subprocess.run(['ldconfig'], check=False, timeout=15)
        return True
    except Exception:
        return False


def pip_restore():
    missing = [pip_name for mod, pip_name in PIP_PKGS.items() if not have(mod)]
    if not missing:
        return 0
    cmd = [
        sys.executable, '-m', 'pip', 'install', '--break-system-packages',
        '-q', '--no-index', '--find-links', 'wheelhouse',
    ] + missing
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    log(f'pip restore missing={missing} rc={r.returncode}')
    if r.returncode != 0:
        # wheelhouse缺货 → 在线兜底（有网时自动补上，顺手补wheelhouse）
        cmd2 = [sys.executable, '-m', 'pip', 'install', '--break-system-packages', '-q'] + missing
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=180)
        log(f'pip online fallback rc={r2.returncode}')
        if r2.returncode == 0:
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'download', '--dest', 'wheelhouse', '-q'] + missing,
                               capture_output=True, timeout=180)
                log('wheelhouse topped up')
            except Exception:
                pass
        return r2.returncode
    return 0


def log(msg):
    from datetime import datetime, timezone
    line = f"[{datetime.now(timezone.utc).strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open('logs/ensure_deps.log', 'a') as f:
        f.write(line + '\n')


def main():
    os.makedirs('logs', exist_ok=True)
    rc = pip_restore()
    # libgomp检查+恢复
    if not os.path.exists('/usr/local/lib/libgomp.so.1'):
        if restore_libgomp():
            log('libgomp restored')
        else:
            log('WARN: libgomp not found anywhere')
    # 终验
    still_missing = [m for m in PIP_PKGS if not have(m)]
    if still_missing:
        log(f'FAIL still missing: {still_missing}')
        sys.exit(1)
    if rc == 0:
        log('all deps OK')


if __name__ == '__main__':
    main()
