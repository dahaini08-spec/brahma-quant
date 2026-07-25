# OmniRoute × 梵天系统 集成落地方案

> 文档版本：v1.0 | 日期：2026-07-24 | 作者：梵天设计院

---

## 目录

1. [架构图](#架构图)
2. [三大价值点](#三大价值点)
3. [安装步骤](#安装步骤)
4. [配置文件示例](#配置文件示例)
5. [梵天适配层代码](#梵天适配层代码)
6. [Kronos补丁方案](#kronos补丁方案)
7. [LightRAG集成MVP](#lightrag集成mvp)
8. [积分优化方案](#积分优化方案)
9. [风险点](#风险点)
10. [实施优先级排序](#实施优先级排序)
11. [苏摩111确认清单](#苏摩111确认清单)

---

## 架构图

### 当前梵天系统（集成OmniRoute前）

```
┌─────────────────────────────────────────────────────────────────┐
│                        梵天系统 (Current)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │  Cron    │───▶│  梵天核心    │───▶│  Binance CLI / API    │  │
│  │  Jobs    │    │  Orchestrator│    │  (交易执行层)          │  │
│  └──────────┘    └──────┬───────┘    └───────────────────────┘  │
│                         │                                         │
│              ┌──────────▼──────────┐                             │
│              │    Kronos 决策引擎   │                             │
│              │  ❌ torch (缺失)     │  ← 本地推断 BROKEN          │
│              │  ❌ lightgbm (缺失)  │                             │
│              └──────────┬──────────┘                             │
│                         │                                         │
│              ┌──────────▼──────────┐                             │
│              │  LightRAG 知识库    │                             │
│              │  ❌ sentence_transf  │  ← embedding BROKEN         │
│              │  ❌ openai SDK       │                             │
│              └─────────────────────┘                             │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 集成OmniRoute后（目标架构）

```
┌─────────────────────────────────────────────────────────────────┐
│                     梵天系统 (OmniRoute集成后)                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │  Cron    │───▶│  梵天核心    │───▶│  Binance CLI / API    │  │
│  │  Jobs    │    │  Orchestrator│    │  (交易执行层)          │  │
│  └──────────┘    └──────┬───────┘    └───────────────────────┘  │
│                         │                                         │
│              ┌──────────▼──────────────────────────────────────┐ │
│              │           OmniRoute 适配层                        │ │
│              │   omniroute_client.py                            │ │
│              │   - chat_completion()   ✅                       │ │
│              │   - get_embedding()     ✅                       │ │
│              │   - 自动 fallback 路由  ✅                       │ │
│              └──────────┬──────────────────────────────────────┘ │
│                         │                                         │
│              ┌──────────▼──────────┐                             │
│              │    OmniRoute网关     │  MIT开源，OpenAI兼容         │
│              │  290+ 提供商         │                             │
│              │  90+ 免费模型        │                             │
│              └──────┬──────┬───────┘                             │
│                     │      │                                      │
│          ┌──────────▼──┐ ┌─▼──────────┐                         │
│          │ Cloud推断    │ │ Embedding  │                         │
│          │ (Kronos补丁) │ │ (LightRAG) │                         │
│          │ ✅ 替代torch │ │ ✅ 替代ST  │                         │
│          └─────────────┘ └────────────┘                         │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘

OmniRoute端点: https://openrouter.ai/api/v1  (或自建OmniRoute实例)
协议: OpenAI兼容 (openai Python SDK / requests均可)
```

---

## 三大价值点

### 价值点一：Kronos Cloud推断（解锁决策引擎）

**问题：** Kronos决策引擎依赖本地`torch`和`lightgbm`进行模式推断，但容器环境中两者均缺失且无法安装（EROFS只读限制）。

**OmniRoute解法：**
- 用Cloud LLM替代本地ML推断
- 将K线特征（OHLCV + 技术指标）序列化为结构化prompt
- 调用OmniRoute上的免费推断模型（如`mistralai/mistral-7b-instruct:free`）
- 获取市场方向信号：LONG / SHORT / NEUTRAL + 置信度
- 延迟：~1-3秒（可接受，cron任务不需要毫秒级响应）
- 成本：免费额度覆盖日常使用

**实际效果：**
```
输入: {"symbol":"BTCUSDT","close":[...30条],"rsi":62.4,"macd_hist":0.003,...}
输出: {"signal":"LONG","confidence":0.71,"reasoning":"RSI适中，MACD多头..."}
```

---

### 价值点二：Embedding支持（解锁LightRAG知识库）

**问题：** LightRAG需要`sentence_transformers`生成文本向量，用于知识图谱检索；当前完全断链。

**OmniRoute解法：**
- 调用OmniRoute embedding端点（`text-embedding-ada-002`兼容接口）
- 免费providers：`openai/text-embedding-3-small`（部分路由免费）或使用`nomic/nomic-embed-text`（完全免费）
- 梵天LightRAG层换用`omniroute_client.get_embedding()`替代`SentenceTransformer`
- 向量维度：768或1536（取决于选用模型，LightRAG可配置）

**实际效果：**
- 新闻/研报入库：文本 → OmniRoute embedding → ChromaDB/本地向量存储
- 知识检索：query → embedding → 余弦相似度 → Top-K召回

---

### 价值点三：积分/Token消耗优化

**问题：** 梵天当前所有LLM调用走主力模型（claude-4-sonnet等），积分消耗集中。

**OmniRoute解法：**
- **分流策略：** 低价值任务（摘要、格式化、简单判断）路由到OmniRoute免费模型
- **RTK压缩接入：** 通过OmniRoute中转，可在路由层做prompt压缩（减少token数）
- **可降频cron任务：** 见[积分优化方案](#积分优化方案)章节
- **预期节省：** 主力模型调用量减少30-50%

---

## 安装步骤

### 前提条件
- Python 3.11（已可用 ✅）
- `pip` externally-managed（需venv）
- 磁盘：23G可用（足够）

### Step 1：创建虚拟环境

```bash
# 在梵天工作目录下创建venv
cd /root/.openclaw/workspace/trading-system
python3.11 -m venv .venv

# 激活
source .venv/bin/activate

# 验证
python --version  # 应输出 Python 3.11.x
```

### Step 2：安装依赖

```bash
# 核心依赖（最小化，不装torch等重型包）
pip install openai requests

# 可选：向量存储（LightRAG MVP阶段需要）
pip install chromadb

# 验证安装
python -c "import openai; print('openai OK:', openai.__version__)"
python -c "import requests; print('requests OK')"
```

### Step 3：获取OmniRoute API Key

1. 访问 https://openrouter.ai
2. 注册账号（免费）
3. 进入 API Keys → Create Key
4. 复制key，格式：`sk-or-v1-xxxxxxxxxxxx`

### Step 4：写入配置

```bash
# 创建配置目录
mkdir -p /root/.openclaw/workspace/trading-system/config

# 写入omniroute.json（见下节）
```

---

## 配置文件示例

**文件路径：** `/root/.openclaw/workspace/trading-system/config/omniroute.json`

```json
{
  "omniroute": {
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": "sk-or-v1-YOUR_KEY_HERE",
    "timeout_seconds": 30,
    "max_retries": 3
  },
  "model_map": {
    "kronos_inference": "mistralai/mistral-7b-instruct:free",
    "summarize": "google/gemma-3-12b-it:free",
    "embedding": "nomic/nomic-embed-text",
    "heavy_reasoning": "deepseek/deepseek-r1-0528:free",
    "fallback_chat": "qwen/qwen3-8b:free"
  },
  "routing": {
    "prefer_free": true,
    "fallback_on_error": true,
    "fallback_chain": [
      "mistralai/mistral-7b-instruct:free",
      "qwen/qwen3-8b:free",
      "google/gemma-3-12b-it:free"
    ]
  },
  "rate_limits": {
    "requests_per_minute": 20,
    "tokens_per_minute": 40000
  }
}
```

**字段说明：**

| 字段 | 说明 |
|------|------|
| `base_url` | OmniRoute兼容端点，直接对接openrouter.ai或自建实例 |
| `api_key` | OpenRouter免费注册获取 |
| `model_map.kronos_inference` | Kronos推断专用模型（免费tier） |
| `model_map.embedding` | LightRAG向量化模型 |
| `routing.prefer_free` | 优先路由到免费模型 |
| `routing.fallback_chain` | 主模型失败时依次尝试 |

---

## 梵天适配层代码

**文件路径：** `/root/.openclaw/workspace/trading-system/omniroute_client.py`

```python
"""
OmniRoute 梵天适配层
替代: torch / lightgbm / sentence_transformers / openai SDK (直连)
依赖: openai>=1.0.0, requests
"""
import json
import time
import logging
from pathlib import Path
from typing import Optional, Union
from openai import OpenAI

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "omniroute.json"


def _load_config() -> dict:
    """加载OmniRoute配置"""
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _get_client(cfg: dict) -> OpenAI:
    """构建OpenAI兼容客户端（指向OmniRoute）"""
    return OpenAI(
        base_url=cfg["omniroute"]["base_url"],
        api_key=cfg["omniroute"]["api_key"],
        timeout=cfg["omniroute"].get("timeout_seconds", 30),
        max_retries=cfg["omniroute"].get("max_retries", 3),
    )


def chat_completion(
    prompt: str,
    system: str = "You are a financial analysis assistant. Reply in JSON.",
    model_key: str = "fallback_chat",
    temperature: float = 0.1,
) -> str:
    """
    通用chat推断接口。
    替代场景: Kronos信号推断、摘要、分类等
    
    Returns: 模型输出的原始字符串
    """
    cfg = _load_config()
    client = _get_client(cfg)
    model = cfg["model_map"].get(model_key, cfg["model_map"]["fallback_chat"])
    
    fallback_chain = cfg["routing"].get("fallback_chain", [model])
    models_to_try = [model] + [m for m in fallback_chain if m != model]
    
    last_error = None
    for m in models_to_try:
        try:
            resp = client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=512,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Model {m} failed: {e}. Trying next fallback...")
            last_error = e
            time.sleep(1)
    
    raise RuntimeError(f"All models failed. Last error: {last_error}")


def get_embedding(text: str, model_key: str = "embedding") -> list[float]:
    """
    文本向量化接口。
    替代场景: LightRAG知识库入库/检索、语义相似度
    
    Returns: 浮点数向量列表
    """
    cfg = _load_config()
    client = _get_client(cfg)
    model = cfg["model_map"].get(model_key, "nomic/nomic-embed-text")
    
    resp = client.embeddings.create(
        model=model,
        input=text,
    )
    return resp.data[0].embedding


def kronos_infer(features: dict) -> dict:
    """
    Kronos推断入口：将市场特征转化为交易信号。
    替代: torch本地模型推断
    
    Args:
        features: {"symbol":"BTCUSDT","rsi":62.4,"macd_hist":0.003,
                   "close_30":[...], "volume_24h":1234567, ...}
    Returns:
        {"signal":"LONG"|"SHORT"|"NEUTRAL",
         "confidence": 0.0~1.0,
         "reasoning": "..."}
    """
    prompt = f"""分析以下加密货币市场特征，输出交易信号。

特征数据:
{json.dumps(features, ensure_ascii=False, indent=2)}

必须以JSON格式回复，包含以下字段:
- signal: "LONG" 或 "SHORT" 或 "NEUTRAL"
- confidence: 0.0到1.0之间的浮点数
- reasoning: 简短的中文推理（50字以内）

仅输出JSON，不要任何额外文字。"""

    raw = chat_completion(
        prompt=prompt,
        system="你是一个量化交易信号分析师。严格输出JSON格式。",
        model_key="kronos_inference",
        temperature=0.05,
    )
    
    # 清理可能的markdown代码块
    raw = raw.strip().strip("```json").strip("```").strip()
    
    result = json.loads(raw)
    # 验证字段完整性
    assert "signal" in result and result["signal"] in ("LONG", "SHORT", "NEUTRAL")
    assert 0.0 <= float(result.get("confidence", 0)) <= 1.0
    return result


if __name__ == "__main__":
    # 快速测试
    logging.basicConfig(level=logging.INFO)
    
    # 测试Kronos推断
    test_features = {
        "symbol": "BTCUSDT",
        "rsi": 58.3,
        "macd_hist": 0.0021,
        "bb_position": 0.65,
        "volume_ratio_24h": 1.3,
        "close_5": [67200, 67350, 67500, 67420, 67600],
    }
    signal = kronos_infer(test_features)
    print("Kronos信号:", json.dumps(signal, ensure_ascii=False, indent=2))
    
    # 测试Embedding
    vec = get_embedding("比特币今日放量上涨，突破关键阻力位")
    print(f"Embedding维度: {len(vec)}, 前5值: {vec[:5]}")
```

---

## Kronos补丁方案

### 当前痛点

Kronos决策引擎原始设计：
```
市场数据 → 特征工程(pandas) → torch/lightgbm推断 → 信号输出
```
由于`torch`和`lightgbm`在容器中不可用，推断层完全断路。

### 补丁策略：Cloud推断替代本地ML

#### 方案A：直接替换推断调用（最小改动）

```python
# 原始 kronos/inference.py（假设路径）
# 改动前:
# import torch
# model = torch.load("kronos_model.pt")
# signal = model(features_tensor)

# 改动后（Patch）:
from omniroute_client import kronos_infer

def get_signal(features: dict) -> dict:
    """
    原本地torch推断 → OmniRoute cloud推断
    接口不变，内部实现替换
    """
    return kronos_infer(features)
```

#### 方案B：特征提取保留，仅替换推断层

```python
# kronos/cloud_inference_patch.py

import pandas as pd
from omniroute_client import kronos_infer

def extract_features(df: pd.DataFrame, symbol: str) -> dict:
    """保留原有特征工程逻辑（pandas可用）"""
    close = df["close"].values.tolist()
    volume = df["volume"].values.tolist()
    
    # 计算基础指标（不依赖torch）
    rsi = _calc_rsi(close, period=14)
    macd_hist = _calc_macd_hist(close)
    
    return {
        "symbol": symbol,
        "rsi": round(rsi, 2),
        "macd_hist": round(macd_hist, 6),
        "close_30": close[-30:],
        "volume_ratio": volume[-1] / (sum(volume[-24:]) / 24),
    }

def predict(df: pd.DataFrame, symbol: str) -> dict:
    """完整推断流程：特征提取 + Cloud推断"""
    features = extract_features(df, symbol)
    return kronos_infer(features)  # 走OmniRoute
```

#### 推断延迟评估

| 方式 | 延迟 | 成本 | 稳定性 |
|------|------|------|--------|
| 本地torch（理想） | <10ms | 无 | 需GPU内存 |
| OmniRoute云推断 | 1-5秒 | 免费 | 依赖网络 |
| 缓存+OmniRoute | <100ms（缓存命中） | 免费 | 可接受 |

**结论：** 梵天cron任务通常为分钟级/小时级，1-5秒延迟完全可接受。建议加本地缓存（TTL=5分钟）减少重复调用。

---

## LightRAG集成MVP

### 解锁前提

LightRAG完整集成需要：
- ✅ OmniRoute embedding（本方案已解锁）
- ⬜ ChromaDB 或 FAISS（需`pip install chromadb`）
- ⬜ LightRAG源码适配层（约100行）

### MVP路线图

#### Phase 0：验证（当前可做，1天）

```bash
# 验证embedding工作
source .venv/bin/activate
python omniroute_client.py
# 预期: 打印Kronos信号 + Embedding维度
```

#### Phase 1：向量存储接入（2天）

```python
# lightrag_adapter.py
import chromadb
from omniroute_client import get_embedding

class FanTianKnowledgeBase:
    """梵天知识库 - LightRAG简化版"""
    
    def __init__(self, persist_dir: str = "./data/chroma"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="fantian_kb",
            metadata={"hnsw:space": "cosine"}
        )
    
    def add_document(self, doc_id: str, text: str, metadata: dict = None):
        """入库：文本 → embedding → ChromaDB"""
        embedding = get_embedding(text)
        self.collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[text],
            metadatas=[metadata or {}]
        )
    
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """检索：query → embedding → 余弦相似度 → Top-K"""
        q_embedding = get_embedding(query)
        results = self.collection.query(
            query_embeddings=[q_embedding],
            n_results=top_k,
        )
        return [
            {"text": doc, "distance": dist, "metadata": meta}
            for doc, dist, meta in zip(
                results["documents"][0],
                results["distances"][0],
                results["metadatas"][0],
            )
        ]
```

#### Phase 2：知识入库（3天）

- 将历史Binance Square帖子入库
- 将每日交易复盘报告入库
- 将新闻摘要入库（接入binance-pro-cli news）

#### Phase 3：RAG增强推断（持续迭代）

```python
# rag_enhanced_kronos.py
def rag_infer(symbol: str, features: dict) -> dict:
    """RAG增强版Kronos推断：结合历史知识"""
    kb = FanTianKnowledgeBase()
    
    # 检索相关历史上下文
    query = f"{symbol}市场信号 RSI:{features.get('rsi')} MACD:{features.get('macd_hist')}"
    context = kb.search(query, top_k=3)
    context_text = "\n".join([r["text"] for r in context])
    
    # 构建增强prompt
    prompt = f"""历史参考:\n{context_text}\n\n当前特征:\n{features}\n\n基于历史经验，分析当前信号。"""
    
    from omniroute_client import chat_completion, kronos_infer
    # 优先用RAG增强，失败fallback到普通推断
    try:
        return kronos_infer({**features, "_context": context_text[:500]})
    except Exception:
        return kronos_infer(features)
```

---

## 积分优化方案

### 当前问题

所有LLM调用（包括简单任务）都走主力模型，积分消耗集中在：
1. 行情摘要生成
2. 新闻情绪分类
3. 格式化/模板填充
4. Kronos推断（本应离线）

### RTK压缩接入

RTK（Retrieval Token Kompression）通过在路由层压缩冗余上下文，减少实际发送token数。

```python
# rtk_compressor.py（简化版）

def compress_prompt(prompt: str, max_tokens: int = 1000) -> str:
    """
    简化RTK压缩：删除冗余空白/重复词/低信息密度段落
    完整RTK需要LLM二次压缩，这里做规则预处理
    """
    import re
    # 1. 删除多余空白行
    prompt = re.sub(r'\n{3,}', '\n\n', prompt)
    # 2. 删除重复句子（简单hash去重）
    lines = prompt.split('\n')
    seen = set()
    deduped = []
    for line in lines:
        key = line.strip().lower()
        if key not in seen:
            seen.add(key)
            deduped.append(line)
    prompt = '\n'.join(deduped)
    # 3. 截断（粗略估计：4字符≈1 token）
    max_chars = max_tokens * 4
    if len(prompt) > max_chars:
        prompt = prompt[:max_chars] + "\n[...截断...]"
    return prompt
```

### 可降频的Cron任务

以下任务当前可能过于频繁，建议降频并路由到OmniRoute免费模型：

| 任务名称 | 当前频率（估算） | 建议频率 | 替换模型 | 预计节省 |
|----------|----------------|----------|----------|---------|
| 行情摘要生成 | 每30分钟 | 每2小时 | `qwen3-8b:free` | 75% |
| 新闻情绪分类 | 每1小时 | 每3小时 | `gemma-3-12b:free` | 66% |
| 持仓健康检查 | 每15分钟 | 每1小时 | `mistral-7b:free` | 75% |
| Kronos特征计算 | 每5分钟 | 每30分钟 | OmniRoute（仅变化时触发） | 83% |
| 日报生成 | 每天 | 每天 | 保持主力模型 | 0% |

### 实施代码

```python
# cron_optimizer.py

TASK_MODEL_MAP = {
    "market_summary": "qwen/qwen3-8b:free",
    "news_sentiment": "google/gemma-3-12b-it:free",
    "position_health": "mistralai/mistral-7b-instruct:free",
    "kronos_signal": "mistralai/mistral-7b-instruct:free",
    "daily_report": None,  # None = 使用主力模型
}

def get_model_for_task(task_name: str) -> Optional[str]:
    """根据任务名返回推荐的OmniRoute模型key"""
    return TASK_MODEL_MAP.get(task_name)
```

---

## 风险点

### R1：外部免费服务可靠性（高优先级风险）

**风险：** OpenRouter免费tier有以下限制：
- 免费模型可能随时下线或被替换
- 每分钟请求频率限制（通常20-200 RPM）
- 免费额度用尽后需付费或降级

**缓解措施：**
- 配置`fallback_chain`（至少3个免费模型）
- 本地缓存推断结果（TTL=5分钟，减少API调用）
- 关键路径（实际下单）不依赖OmniRoute，仅用于分析层
- 监控API错误率，超过10%时告警

---

### R2：推断质量不稳定（中优先级风险）

**风险：** 免费小模型（7B-12B参数）的推断质量不如本地精调模型，可能产生错误信号。

**缓解措施：**
- Kronos信号加置信度过滤：`confidence < 0.6`时输出NEUTRAL
- 多模型投票：同一特征发给2个模型，取一致结果
- 人工复核：所有信号进入待确认队列，苏摩确认后执行

---

### R3：网络延迟影响时效性（低优先级风险）

**风险：** 梵天部分决策对时效性敏感，1-5秒延迟在极端行情下可能错过入场点。

**缓解措施：**
- 区分"决策任务"和"分析任务"
- 实际下单使用本地规则引擎（无LLM依赖）
- OmniRoute仅用于分析层，不在交易关键路径上

---

### R4：API Key泄露风险（中优先级风险）

**风险：** `config/omniroute.json`中存储明文API Key，可能泄露。

**缓解措施：**
- 文件权限设为600：`chmod 600 config/omniroute.json`
- 考虑使用环境变量：`export OMNIROUTE_API_KEY=sk-or-v1-xxx`
- 不将config目录提交到git（加入.gitignore）

```bash
echo "config/omniroute.json" >> .gitignore
chmod 600 /root/.openclaw/workspace/trading-system/config/omniroute.json
```

---

## 实施优先级排序

### P0：立即可做（本周，不需要苏摩批准的技术验证）

| # | 任务 | 预计耗时 | 风险 |
|---|------|----------|------|
| P0-1 | 创建venv + 安装openai/requests | 10分钟 | 极低 |
| P0-2 | 写入omniroute.json配置（需API Key） | 5分钟 | 低 |
| P0-3 | 部署omniroute_client.py到trading-system目录 | 5分钟 | 低 |
| P0-4 | 运行`python omniroute_client.py`冒烟测试 | 10分钟 | 低 |

**P0完成标准：** `python omniroute_client.py`输出Kronos信号JSON + Embedding维度数字，无报错。

---

### P1：第一周（需要苏摩确认的改动）

| # | 任务 | 前置条件 | 影响范围 |
|---|------|----------|---------|
| P1-1 | Kronos推断层patch：替换torch调用 | P0完成 | Kronos信号质量 |
| P1-2 | ChromaDB安装 + FanTianKnowledgeBase部署 | P0完成 | LightRAG知识库 |
| P1-3 | 降频cron任务（行情摘要2h一次） | 苏摩确认 | 积分消耗 |
| P1-4 | 新闻/研报批量入库LightRAG | P1-2完成 | 知识库内容 |

---

### P2：持续迭代（第二周及以后）

| # | 任务 | 说明 |
|---|------|------|
| P2-1 | RAG增强推断上线（rag_enhanced_kronos） | 需P1-2 + P1-4完成后评估效果 |
| P2-2 | 多模型投票机制（提升信号质量） | 需P1-1验证基础版可行 |
| P2-3 | RTK压缩完整实现（LLM二次压缩） | 积分节省进一步优化 |
| P2-4 | OmniRoute自建实例评估（稳定性提升） | 免费tier稳定则可跳过 |

---

## 苏摩111确认清单

以下改动涉及系统行为变更，**必须经苏摩明确批准后才能执行**：

```
┌─────────────────────────────────────────────────────────────────┐
│              苏摩111 确认清单（请逐项回复 ✅/❌）               │
├────┬────────────────────────────────────────┬──────────────────┤
│ #  │ 改动内容                               │ 影响             │
├────┼────────────────────────────────────────┼──────────────────┤
│ 1  │ 创建venv并安装openai/requests/chromadb │ 新增本地依赖     │
│ 2  │ 写入omniroute.json（含外部API Key）    │ 引入外部服务依赖 │
│ 3  │ Kronos推断层替换torch → OmniRoute      │ 改变决策逻辑     │
│ 4  │ 降低行情摘要cron频率（30min→2h）       │ 减少实时性       │
│ 5  │ 降低持仓健康检查频率（15min→1h）       │ 监控粒度降低     │
│ 6  │ 历史数据批量入库LightRAG              │ 磁盘用量增加     │
│ 7  │ 启用RAG增强推断（云端知识检索）        │ 推断延迟增加     │
└────┴────────────────────────────────────────┴──────────────────┘

【最低启动包】P0阶段只需批准第 1、2 项，其余待冒烟测试通过后再决策。
```

---

*文档结束 | 梵天设计院 | 2026-07-24*
