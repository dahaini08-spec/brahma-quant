#!/usr/bin/env python3
"""
update_jarvis_thread.py — 智能适配主线程 SSOT
设计院 · 2026-06-30

用法：
  python3 scripts/update_jarvis_thread.py <thread_id>
  python3 scripts/update_jarvis_thread.py YOUR_THREAD_ID

苏摩每次切换对话线程后，AI 自动调用此脚本更新 system_config.py
全系统所有推送路由立即生效，无需重启任何进程。
"""

import sys, re
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / 'system_config.py'

def update_thread(new_thread_id: str) -> bool:
    """更新 system_config.py 中的 JARVIS_THREAD_ID"""
    new_thread_id = new_thread_id.strip()
    
    # 验证格式
    if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', new_thread_id):
        print(f'❌ 无效的 thread_id 格式: {new_thread_id}')
        return False
    
    content = CONFIG_PATH.read_text()
    
    # 找到当前值
    match = re.search(r'JARVIS_THREAD_ID\s*=\s*"([^"]+)"', content)
    if not match:
        print(f'❌ 未找到 JARVIS_THREAD_ID 字段')
        return False
    
    old_thread_id = match.group(1)
    
    if old_thread_id == new_thread_id:
        print(f'✅ 无需更新，当前已是: {new_thread_id}')
        return True
    
    # 替换
    new_content = content.replace(
        f'JARVIS_THREAD_ID = "{old_thread_id}"',
        f'JARVIS_THREAD_ID = "{new_thread_id}"'
    )
    
    CONFIG_PATH.write_text(new_content)
    print(f'✅ SSOT 已更新')
    print(f'   旧: {old_thread_id}')
    print(f'   新: {new_thread_id}')
    print(f'   全系统推送路由立即生效')
    return True

if __name__ == '__main__':
    if len(sys.argv) < 2:
        # 从 system_config 读取当前值并显示
        import importlib.util
        spec = importlib.util.spec_from_file_location('sc', CONFIG_PATH)
        sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
        print(f'当前 JARVIS_THREAD_ID: {sc.JARVIS_THREAD_ID}')
        print(f'当前 JARVIS_TARGET:    {sc.JARVIS_TARGET}')
        print()
        print(f'用法: python3 {sys.argv[0]} <new_thread_id>')
        sys.exit(0)
    
    new_id = sys.argv[1]
    success = update_thread(new_id)
    sys.exit(0 if success else 1)
