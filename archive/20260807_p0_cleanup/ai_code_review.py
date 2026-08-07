#!/usr/bin/env python3
"""
梵天AI代码审查 (open-code-review风格)
pre-commit hook调用，审查暂存的Python文件diff
设计院 2026-07-25
"""
import sys, subprocess, json
from pathlib import Path

def get_diff(files):
    r = subprocess.run(['git','diff','--cached','--'] + files,
                       capture_output=True, text=True, timeout=10)
    return r.stdout[:3000] if r.stdout else ''

def review_with_omniroute(diff_text):
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from omniroute_client import chat_completion
        if not diff_text.strip():
            return None
        prompt = f"""请审查以下Git diff，指出：
1. 潜在bug或逻辑错误
2. 对梵天交易系统的风险点
3. 命名或结构问题

diff:
{diff_text}

请简洁回复（3行内），严重问题用❌标注，建议用⚠️标注，无问题回复✅ LGTM"""
        return chat_completion(prompt, max_tokens=200)
    except Exception:
        return None

def main():
    files = [f for f in sys.argv[1:] if f.endswith('.py')]
    if not files:
        return
    diff = get_diff(files)
    if not diff:
        return
    result = review_with_omniroute(diff)
    if result:
        print(f"\n🤖 AI Code Review:")
        print(result)
        print()

if __name__ == '__main__':
    main()
