#!/usr/bin/env python3
"""
OmniRoute × 梵天 适配层
设计院 2026-07-24 苏摩111封印

Embedding策略（优先级）：
  1. fastembed本地模型（BAAI/bge-small-en-v1.5，384维，纯CPU，无需Key）✅ 已验证
  2. OmniRoute云端（需config/omniroute.json中有api_key）
  3. 降级返回None

配置：config/omniroute.json（可选）
  - 不存在 → embedding用本地fastembed，chat返回None
  - 存在   → chat + embedding均走OmniRoute网关
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

BASE        = Path(__file__).parent.parent
CONFIG_PATH = BASE / "config" / "omniroute.json"

DEFAULT_CONFIG = {
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": "",
    "chat_model": "mistralai/mistral-7b-instruct:free",
    "embedding_model": "text-embedding-3-small",
    "kronos_model": "mistralai/mistral-7b-instruct:free",
    "fallback_chain": [
        "mistralai/mistral-7b-instruct:free",
        "google/gemma-3-12b-it:free",
        "meta-llama/llama-3.2-3b-instruct:free"
    ],
    "cache_ttl_seconds": 300,
    "timeout_seconds": 15,
    "max_retries": 2
}

_cache: dict = {}


def _load_config() -> Optional[dict]:
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


def _cache_get(key: str, ttl: int) -> Optional[object]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < ttl:
        return entry["val"]
    return None


def _cache_set(key: str, val: object):
    _cache[key] = {"ts": time.time(), "val": val}


def chat_completion(
    prompt: str,
    system: str = "你是梵天量化系统的分析助手，给出简洁准确的判断。",
    model: Optional[str] = None,
    max_tokens: int = 256,
    temperature: float = 0.1,
) -> Optional[str]:
    """通用LLM推断。需要OmniRoute配置，失败返回None。"""
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

    优先级：
      1. fastembed 本地模型（无需Key，纯CPU，384维）
      2. OmniRoute 云端（需api_key）
      3. 降级返回 None
    """
    # 优先走本地fastembed
    try:
        from fastembed import TextEmbedding
        _fe_model_name = "BAAI/bge-small-en-v1.5"
        cache_k = _cache_key(text[:200], f"fastembed:{_fe_model_name}")
        cached = _cache_get(cache_k, 3600)
        if cached is not None:
            return cached
        # 单例模式，避免重复加载
        if not hasattr(get_embedding, '_fe_model'):
            get_embedding._fe_model = TextEmbedding(_fe_model_name)
        vecs = list(get_embedding._fe_model.embed([text]))
        vec = [float(v) for v in vecs[0]]
        _cache_set(cache_k, vec)
        return vec
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[OmniRoute] fastembed失败: {e}")

    # fallback: OmniRoute云端
    cfg = _load_config()
    if not cfg or not cfg.get("api_key"):
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    use_model = model or cfg.get("embedding_model", "text-embedding-3-small")
    cache_k = _cache_key(text[:200], f"emb:{use_model}")
    cached = _cache_get(cache_k, 3600)
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
    """Kronos信号辅助推断（替代本地torch/lgbm）。需要OmniRoute配置。"""
    cfg = _load_config()
    if not cfg or not cfg.get("api_key"):
        return None

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
        import re
        m = re.search(r'\{[^}]+\}', result_str)
        if m:
            data = json.loads(m.group())
            p_up = float(data.get("p_up", 0.5))
            conf = float(data.get("confidence", 0.5))
            if conf < 0.6:
                p_up = 0.5
            return {"p_up": p_up, "confidence": conf, "src": "omniroute_cloud"}
    except Exception as e:
        logger.debug(f"[OmniRoute] kronos_infer 解析失败: {e}")

    return None


def health_check() -> dict:
    """冒烟测试：验证embedding和chat状态"""
    # 测试embedding（本地fastembed）
    emb_result = get_embedding("test signal BTCUSDT BEAR_TREND")
    emb_ok = emb_result is not None and len(emb_result) > 0
    emb_src = "fastembed_local" if emb_ok else "none"

    # 测试chat（需要OmniRoute key）
    cfg = _load_config()
    chat_ok = False
    chat_result = None
    if cfg and cfg.get("api_key"):
        chat_result = chat_completion("用一个词回复：OK", max_tokens=10)
        chat_ok = chat_result is not None

    if not cfg:
        status = "embedding_only" if emb_ok else "no_config"
    elif not cfg.get("api_key"):
        status = "embedding_only" if emb_ok else "no_key"
    else:
        status = "ok" if (chat_ok or emb_ok) else "failed"

    return {
        "status": status,
        "chat": chat_ok,
        "chat_sample": chat_result[:50] if chat_result else None,
        "embedding": emb_ok,
        "emb_dim": len(emb_result) if emb_result else 0,
        "emb_src": emb_src,
        "has_config": cfg is not None,
        "has_key": bool(cfg and cfg.get("api_key")),
    }


if __name__ == "__main__":
    print("🔧 OmniRoute × 梵天 冒烟测试")
    print("=" * 45)
    result = health_check()
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print()
    if result["embedding"]:
        print(f"✅ Embedding已解锁 | 来源: {result['emb_src']} | 维度: {result['emb_dim']}")
        print("   → LightRAG等待条件达成！可立即建立知识图谱")
    if result["chat"]:
        print(f"✅ Chat已解锁 | {result['chat_sample']}")
        print("   → Kronos云端推断可接入")
    if not result["chat"]:
        print("⚠️  Chat未配置 | 提供OmniRoute API Key后解锁Kronos云端推断")
        print(f"   写入: {CONFIG_PATH}")
