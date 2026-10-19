"""
master_chat.py — 主AI兜底接口
[封印 2026-10-19 苏摩111]
通过OpenClaw原生exec调用openclaw内置模型
"""
import subprocess, json, sys, os
from pathlib import Path


def chat(messages: list, max_tokens: int = 100) -> str:
    """调用openclaw内置模型作为最终兜底"""
    try:
        # 构造单行prompt
        prompt = messages[-1].get('content', '') if messages else ''
        if not prompt:
            return ''

        # 使用openclaw CLI调用内置模型
        result = subprocess.run(
            ['openclaw', 'chat', '--message', prompt[:500],
             '--max-tokens', str(min(max_tokens, 200)),
             '--no-stream', '--quiet'],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path(__file__).parent.parent)
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:max_tokens*4]

        # 备用：简单规则兜底（保证永不返回空）
        content_lower = prompt.lower()
        if any(w in content_lower for w in ['wait','等待','chop','震荡']):
            return 'WAIT'
        if any(w in content_lower for w in ['bull','多','long','上涨']):
            return 'WATCH'
        if any(w in content_lower for w in ['bear','空','short','下跌']):
            return 'WATCH'
        return 'WAIT'

    except Exception:
        return 'WAIT'
