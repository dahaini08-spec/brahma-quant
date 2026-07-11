# Brahma 2.0 P0-Plus — Minimal Institutional Patch

**封印日期：2026-07-11**
**设计原则：最小代价、最大可复现性、不动实盘链路**

---

## 本次升级范围（4 PR）

| PR | 内容 | 验收命令 |
|----|------|---------|
| PR-1 | pyproject.toml + requirements 分层 | `pip install -e .` pass |
| PR-2 | brahma-ci.yml + test_import_smoke.py | `pytest tests/test_import_smoke.py` pass |
| PR-3 | dharma_simfactory/ 5文件最小闭环 | `data-audit` + `baseline` pass |
| PR-4 | reports/simfactory/ + 本文档 | 路径固定，可落盘 |

---

## 快速验证

```bash
# 1. 安装
python -m venv .venv && source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -r requirements-core.txt
pip install -e .

# 2. 编译检查
python -m compileall brahma_brain brahma_v6 dharma dharma_simfactory guardrails nerve_system tests -q

# 3. 测试
pytest tests/test_import_smoke.py -v

# 4. 数据审计（需提前准备 parquet 数据）
python -m dharma_simfactory.run_simfactory data-audit \
    --data-root data/historical \
    --symbols BTCUSDT ETHUSDT \
    --timeframes 15m 1h 4h 1d

# 5. Baseline Replay
python -m dharma_simfactory.run_simfactory baseline \
    --data-root data/historical \
    --symbols BTCUSDT ETHUSDT \
    --timeframes 1h 4h \
    --cost-multiplier 1.0
```

---

## 依赖分层说明

| 文件 | 用途 | 禁止内容 |
|------|------|---------|
| requirements-core.txt | 所有环境基础 | — |
| requirements-research.txt | 回测/研究环境 | 不进 live |
| requirements-dev.txt | 开发/测试环境 | 不进 live |
| requirements-live-lite.txt | 生产实盘环境 | 禁 vectorbt/scipy/sklearn 等重型包 |

---

## 下一步（本次不做）

- Walk-Forward 验证框架接入 SimFactory
- Monte Carlo Stress Test 接入 SimFactory
- Brahma 真实信号 replay（替换 simple_baseline）
- Survival Engine 接入
- Constitution Engine 宪法候选生成
