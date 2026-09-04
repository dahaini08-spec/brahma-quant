#!/usr/bin/env python3
"""
free_llm_client.py — 梵天免费LLM接入层
设计院 2026-09-03 苏摩111封印

优先级（自动降级）：
  1. OpenRouter 免费模型（21个，无限额，仅需免费注册）
  2. DeepInfra（部分免费）
  3. Qwen3.5（OpenClaw内置Standard模型，fallback）

接入方式：
  from scripts.free_llm_client import ask

用于替代监控类任务的AI判断（月省$119）

注册OpenRouter（免费）：
  https://openrouter.ai/settings/keys
  → Create Key → 复制Key
  → 写入: echo 'OPENROUTER_API_KEY=sk-or-xxx' >> trading-system/.env
"""
import os, json, urllib.request, urllib.error, logging
from pathlib import Path

log = logging.getLogger(__name__)

# 免费模型优先级列表
FREE_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",      # 快速，1M ctx
    "thinkingmachines/inkling-small:free",     # 轻量，1M ctx
    "liquid/lfm-2.5-2.6b:free",               # 极轻
    "minimax/minimax-m3:free",                 # 中文友好
]

BASE_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
BASE_DEEPINFRA  = "https://api.deepinfra.com/v1/openai/chat/completions"

def _load_key(env_var: str) -> str:
    """从环境变量或.env文件读取Key"""
    val = os.environ.get(env_var, "")
    if val:
        return val
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{env_var}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

def ask(prompt: str, max_tokens: int = 150, timeout: int = 12) -> str:
    """
    向免费LLM提问，返回回答文本。
    自动按优先级降级：OpenRouter → DeepInfra → 空字符串（调用方自行处理）
    """
    # 优先：OpenRouter
    or_key = _load_key("OPENROUTER_API_KEY")
    if or_key:
        for model in FREE_MODELS:
            try:
                result = _call_openai_compat(
                    BASE_OPENROUTER, or_key, model, prompt, max_tokens, timeout,
                    extra_headers={"HTTP-Referer": "https://brahma.ai", "X-Title": "BrahmaQuant"}
                )
                if result:
                    return result
            except Exception as e:
                log.debug(f"OpenRouter {model} failed: {e}")
                continue

    # 降级：DeepInfra
    di_key = _load_key("DEEPINFRA_API_KEY")
    if di_key:
        try:
            result = _call_openai_compat(
                BASE_DEEPINFRA, di_key,
                "meta-llama/Meta-Llama-3-8B-Instruct",
                prompt, max_tokens, timeout
            )
            if result:
                return result
        except Exception as e:
            log.debug(f"DeepInfra failed: {e}")

    # 无可用免费Key
    return ""


def _call_openai_compat(
    url: str, api_key: str, model: str,
    prompt: str, max_tokens: int, timeout: int,
    extra_headers: dict = None
) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=payload, headers=headers)
    r   = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return r["choices"][0]["message"]["content"].strip()


def is_available() -> bool:
    """检查是否有可用的免费LLM Key"""
    return bool(_load_key("OPENROUTER_API_KEY") or _load_key("DEEPINFRA_API_KEY"))


if __name__ == "__main__":
    if is_available():
        ans = ask("回复一个字：好")
        print(f"测试成功: {ans}")
    else:
        print("未配置API Key，接入方式：")
        print("  OpenRouter（推荐）: https://openrouter.ai/settings/keys")
        print("  写入: echo 'OPENROUTER_API_KEY=sk-or-xxx' >> .env")
        print("  DeepInfra备用:      https://deepinfra.com/dashboard")
        print("  写入: echo 'DEEPINFRA_API_KEY=xxx' >> .env")
