#!/usr/bin/env python3
"""venv 健康检查 - 检查核心依赖是否可导入"""
import sys, os, subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)

# 补建 libgomp（每次重启后可能丢失，从 xgboost.libs 复制）
gomp_dst = '/usr/local/lib/libgomp.so.1'
gomp_src = 'venv/lib/python3.11/site-packages/xgboost.libs/libgomp-e985bcbb.so.1.0.0'
if not os.path.exists(gomp_dst) and os.path.exists(gomp_src):
    try:
        import shutil
        shutil.copyfile(gomp_src, gomp_dst)
        os.chmod(gomp_dst, 0o755)
        os.system('ldconfig 2>/dev/null')
        print('FIXED: libgomp 已补建')
    except Exception as e:
        print(f'WARN: libgomp 补建失败：{e}')

# 检查核心依赖
env = os.environ.copy()
env['LD_LIBRARY_PATH'] = '/usr/local/lib:' + env.get('LD_LIBRARY_PATH', '')
r = subprocess.run(['venv/bin/python3', '-c', 'import numpy,pandas,lightgbm,requests,chromadb;print("OK")'],
    capture_output=True, text=True, timeout=10, env=env)
if 'OK' in r.stdout:
    print('HEARTBEAT_OK')
else:
    print('FAIL: ' + r.stderr[:100])
