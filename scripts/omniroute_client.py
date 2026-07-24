#!/usr/bin/env python3
"""
OmniRoute × 梵天 适配层
设计院 2026-07-24 苏摩111封印

职责：
  1. chat_completion()  → 通用LLM推断（Kronos辅助/摘要）
  2. get_embedding()    → 文本向量化（LightRAG前置）
  3. kronos_infer()     → Kronos信号辅助推断（替代本地torch）

配置：config/omniroute.json
  - 不存在 → 静默返回 None（不阻断主流）
  - 存在   → 接入OmniRoute网关

调用规范：
  - 所有接口均返回 None 时不报错，主流降级处理
  - TTL缓存（5分钟）减少API调用
  - fallback_chain：按序尝试3个免费模型
"""

import os
import sys
import json
import time
import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── 路径 ────────────────────────────────────────────────────
BASE = Path(__file__).parent.parent
CONFIG_PATH = BASE / "config" / "omniroute.json"
CACHE_FILE  = BASE / "data" / "omniroute_cache.json"

# ─── 默认配置（未配置时使用公共OpenRouter端点）────────────────
DEFAULT_CONFIG = {
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": "",  # 空时为只读模式，仅embedding可能免费
    "chat_model": "mistralai/mistral-7b-instruct:free",
    "embedding_model": "text-embedding-3-small",
    "kronos_model": "mistralai/mistral-7b-instruct:free",
    "fallback_chain": [
        "mistralai/mistral-7b-instruct:free",
        "google/gemma-3-12b-it:free",
        "meta-llama/llama-3.2-3b-instruct:free"
    ],
    "cache_ttl_seconds": 300,
    "timeout_seconds": 10,
    "max_retries": 2
}

# ─── 内存缓存 ─────────────────────────────────────────────────
_cache: dict = {}


def _load_config() -> Optional[dict]:
    """加载OmniRoute配置。不存在则返回None（静默降级）"""
    if not CONFIG_PATH.exists():
        return None
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return {**DEFAULT_CONFIG, **cfg}
    except Exception as e:
        logger.warning(f"[OmniRoute] 配置加载失败: {e}")
        return None


def _cache_key(text: str, model: str) -> str:
    return hashlib.md5(f"{model}:{text}".encode()).hexdigest()[:12]


def _cache_get(key: str, ttl: int) -> Optional[any]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["val"]
    return None


def _cache_set(key: str, val: any):
    _cache[key] = {"ts": time.time(), "val": val}


# ─── 核心接口 ─────────────────────────────────────────────────

def chat_completion(
    prompt: str,
    system: str = "你是梵天量化系统的分析助手，给出简洁准确的判断。",
    model: Optional[str] = None,
    max_tokens: int = 256,
    temperature: float = 0.1,
) -> Optional[str]:
    """
    通用LLM推断。失败时返回None，不抛异常。

    Args:
        prompt: 用户输入
        system: 系统提示
        model: 指定模型（None=使用配置默认）
        max_tokens: 最大输出tokens
        temperature: 温度（推断任务用低值）

    Returns:
        str | None
    """
    cfg = _load_config()
    if not cfg or not cfg.get("api_key"):
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    use_model = model or cfg["chat_model"]
    cache_k = _cache_key(f"{system}:{prompt}", use_model)
    cached = _cache_get(cache_k, cfg["cache_ttl_seconds"])
    if cached is not None:
        return cached

    client = OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        timeout=cfg["timeout_seconds"],
    )

    # fallback loop
    models_to_try = [use_model] + [m for m in cfg["fallback_chain"] if m != use_model]
    for m in models_to_try[:cfg["max_retries"] + 1]:
        try:
            resp = client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            result = resp.choices[0].message.content.strip()
            _cache_set(cache_k, result)
            return result
        except Exception as e:
            logger.debug(f"[OmniRoute] chat {m} 失败: {e}")
            continue

    return None


def get_embedding(text: str, model: Optional[str] = None) -> Optional[list]:
    """
    文本向量化。返回 list[float] 或 None。

    用于 LightRAG 知识库入库。
    """
    cfg = _load_config()
    if not cfg or not cfg.get("api_key"):
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    use_model = model or cfg.get("embedding_model", "text-embedding-3-small")
    cache_k = _cache_key(text[:200], f"emb:{use_model}")
    cached = _cache_get(cache_k, 3600)  # embedding缓存1小时
    if cached is not None:
        return cached

    client = OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        timeout=cfg["timeout_seconds"],
    )

    try:
        resp = client.embeddings.create(model=use_model, input=text)
        vec = resp.data[0].embedding
        _cache_set(cache_k, vec)
        return vec
    except Exception as e:
        logger.debug(f"[OmniRoute] embedding 失败: {e}")
        return None


def kronos_infer(
    symbol: str,
    features: dict,
    direction: str = "LONG",
) -> Optional[dict]:
    """
    Kronos信号辅助推断（替代本地torch/lgbm）。

    Args:
        symbol:    交易对（如 BTCUSDT）
        features:  特征字典（RSI/EMA/OI等）
        direction: LONG | SHORT

    Returns:
        {
            "p_up": float,      # 0~1, 上涨概率
            "confidence": float, # 置信度
            "src": str          # 来源标识
        }
        None = 推断失败，主流使用fallback
    """
    cfg = _load_config()
    if not cfg or not cfg.get("api_key"):
        return None

    # 构建特征摘要提示
    feat_str = "\n".join([f"  {k}: {v}" for k, v in features.items() if v is not None])
    prompt = f"""你是加密货币量化交易信号分析器。

交易对: {symbol}
方向: {direction}
当前特征:
{feat_str}

基于以上特征，评估未来4小时内价格上涨的概率（0到1之间）。
只输出JSON格式，例如：{{"p_up": 0.65, "confidence": 0.7}}
不要解释，只输出JSON。"""

    result_str = chat_completion(
        prompt=prompt,
        system="你是专业量化信号分析器，只输出JSON。",
        model=cfg.get("kronos_model"),
        max_tokens=64,
        temperature=0.05,
    )

    if not result_str:
        return None

    try:
        # 提取JSON
        import re
        m = re.search(r'\{[^}]+\}', result_str)
        if m:
            data = json.loads(m.group())
            p_up = float(data.get("p_up", 0.5))
            conf = float(data.get("confidence", 0.5))
            # 置信度过滤：<0.6时返回NEUTRAL(0.5)
            if conf < 0.6:
                p_up = 0.5
            return {"p_up": p_up, "confidence": conf, "src": "omniroute_cloud"}
    except Exception as e:
        logger.debug(f"[OmniRoute] kronos_infer 解析失败: {e} raw={result_str}")

    return None


def health_check() -> dict:
    """冒烟测试：验证OmniRoute连接状态"""
    cfg = _load_config()
    if not cfg:
        return {"status": "no_config", "msg": "config/omniroute.json 不存在"}

    if not cfg.get("api_key"):
        return {"status": "no_key", "msg": "api_key 未配置"}

    # 测试chat
    chat_ok = False
    chat_result = chat_completion("用一个词回复：OK", max_tokens=10)
    if chat_result:
        chat_ok = True

    # 测试embedding
    emb_ok = False
    emb_result = get_embedding("test")
    if emb_result and len(emb_result) > 0:
        emb_ok = True

    return {
        "status": "ok" if (chat_ok or emb_ok) else "failed",
        "chat": chat_ok,
        "embedding": emb_ok,
        "emb_dim": len(emb_result) if emb_result else 0,
        "chat_sample": chat_result[:50] if chat_result else None,
        "base_url": cfg["base_url"],
        "model": cfg["chat_model"],
    }


# ─── CLI冒烟测试 ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    print("🔧 OmniRoute × 梵天 冒烟测试")
    print("=" * 40)
    result = health_check()
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if result["status"] == "no_config":
        print()
        print("📋 使用方法：")
        print("  1. 到 https://openrouter.ai 注册免费账号")
        print("  2. 创建 API Key")
        print(f"  3. 写入配置: {CONFIG_PATH}")
        print()
        print("  配置示例：")
        example = {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or-v1-YOUR_KEY_HERE",
            "chat_model": "mistralai/mistral-7b-instruct:free",
            "embedding_model": "text-embedding-3-small"
        }
        print(json.dumps(example, indent=2))
    elif result["status"] == "ok":
        print()
        print("✅ OmniRoute连接正常")
        if result["embedding"]:
            print(f"✅ Embedding可用，维度: {result['emb_dim']}")
            print("   → LightRAG等待条件达成！")
        if result["chat"]:
            print(f"✅ Chat可用: {result['chat_sample']}")
            print("   → Kronos云端推断可接入")
    else:
        print("❌ 连接失败，请检查API Key和网络")
