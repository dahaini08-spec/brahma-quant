#!/usr/bin/env python3
"""
cron_reference_check.py — Crontab引用完整性检查
[9.19 设计院封印 苏摩111] 防止prediction_verifier类配置漂移

扫描brahma_crontab.txt中所有引用的：
  1. python模块 (from brahma_brain.xxx import / import brahma_brain.xxx)
  2. 脚本文件 (python3 xxx.py / bash xxx.sh)
  3. 检查每个引用的文件是否存在

用法:
  python3 scripts/cron_reference_check.py          # 检查
  python3 scripts/cron_reference_check.py --fix      # 自动注释掉失效条目

接入位置: supercronic启动后自动执行 / 手动运维检查
"""
import re, os, sys, json
from pathlib import Path

BASE = Path(__file__).parent.parent
CRONTAB = BASE / 'brahma_crontab.txt'

def extract_references(crontab_text: str) -> list:
    """提取crontab中所有文件/模块引用"""
    refs = []
    for i, line in enumerate(crontab_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # python3 -c "from brahma_brain.xxx import ..."
        for m in re.finditer(r'from\s+(brahma_brain\.\w+)', stripped):
            mod = m.group(1).replace('.', '/') + '.py'
            refs.append((i, mod, 'python_module', stripped))

        # python3 xxx.py
        for m in re.finditer(r'python3?\s+(\S+\.py)', stripped):
            refs.append((i, m.group(1), 'script', stripped))

        # bash xxx.sh
        for m in re.finditer(r'bash\s+(\S+\.sh)', stripped):
            refs.append((i, m.group(1), 'script', stripped))

    return refs

def resolve_path(ref: str, crontab_dir: Path, line_text: str) -> Path:
    """解析引用路径（考虑cd命令改变工作目录）"""
    # 检查行内是否有cd命令
    cd_match = re.search(r'cd\s+(\S+)', line_text)
    if cd_match:
        cwd = cd_match.group(1)
        # 如果是brahma_brain子目录
        if cwd.endswith('brahma_brain'):
            return Path(cwd) / ref
        return Path(cwd) / ref
    # 默认从trading-system根目录
    return crontab_dir / ref

def check():
    if not CRONTAB.exists():
        print('❌ brahma_crontab.txt不存在')
        return 1

    text = CRONTAB.read_text()
    refs = extract_references(text)

    ok = 0
    missing = []

    for line_no, ref, ref_type, line_text in refs:
        # brahma_brain.xxx → brahma_brain/xxx.py
        if ref_type == 'python_module':
            path = BASE / ref
        else:
            path = resolve_path(ref, BASE, line_text)

        if path.exists():
            ok += 1
        else:
            missing.append((line_no, ref, str(path), line_text))

    print(f'扫描 {len(refs)} 个引用: ✅{ok} ❌{len(missing)}')

    if missing:
        print('\n❌ 失效引用:')
        for line_no, ref, path, line_text in missing:
            print(f'  L{line_no}: {ref}')
            print(f'    路径: {path}')
            print(f'    行: {line_text[:80]}')

        # 检查--fix
        if '--fix' in sys.argv:
            print('\n🔧 自动注释失效条目...')
            lines = text.splitlines()
            fixed = 0
            for line_no, ref, path, line_text in missing:
                idx = line_no - 1
                if not lines[idx].strip().startswith('#'):
                    lines[idx] = '# [auto-fix 9.19] ' + lines[idx]
                    fixed += 1
            CRONTAB.write_text('\n'.join(lines) + '\n')
            print(f'  已注释 {fixed} 条')
    else:
        print('\n✅ 所有crontab引用完整')

    return 0 if not missing else 1

if __name__ == '__main__':
    sys.exit(check())
