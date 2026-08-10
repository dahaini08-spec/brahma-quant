#!/bin/bash
# ============================================================
# 梵天依赖安装脚本 v1.0
# 经过模拟验证(dry-run全通过) | 2026-08-10 设计院封印
# 安全顺序：小包 → statsmodels → sklearn → torch(CPU)
# ============================================================

set -e  # 任意一步失败即停止

PIP="pip3 --break-system-packages"
LOG_FILE="/tmp/brahma_install_$(date +%Y%m%d_%H%M%S).log"

echo "=============================================="
echo " 梵天依赖安装 — 开始"
echo " 日志: $LOG_FILE"
echo "=============================================="

# -------- 函数：安装单个包并验证 --------
install_and_verify() {
    local pkg=$1
    local import_name=$2
    local extra_args=${3:-""}
    echo ""
    echo ">>> 安装 $pkg ..."
    pip3 install --break-system-packages $extra_args "$pkg" 2>&1 | tee -a "$LOG_FILE"
    echo ">>> 验证 $import_name ..."
    python3 -c "import $import_name; print('  ✅ $import_name OK:', getattr($import_name,'__version__','?'))"
    echo ">>> $pkg 安装验证通过"
}

# ============================================================
# 第一步：小包（无风险，立即安装）磁盘<50MB 内存+<50MB
# ============================================================
echo ""
echo "====== 第一步：小包安装（无风险）======"

# lightgbm — Kronos BLEND模式激活
install_and_verify "lightgbm" "lightgbm"

# qdrant-client — 方仓向量库持久化
install_and_verify "qdrant-client" "qdrant_client"

# statsmodels — CausalVerifier精度 conf:0.27→0.32
install_and_verify "statsmodels" "statsmodels"

# scikit-learn — 在线学习完整版
install_and_verify "scikit-learn" "sklearn"

echo ""
echo "====== 第一步完成：小包全部安装成功 ======"

# ============================================================
# 第二步：安装前内存检查
# ============================================================
echo ""
echo "====== 第二步前：内存检查 ======"
python3 -c "
import subprocess
r = subprocess.run(['free','-m'], capture_output=True, text=True)
print(r.stdout)
lines = r.stdout.strip().split('\n')
for l in lines:
    if l.startswith('Mem:'):
        parts = l.split()
        available = int(parts[6])
        print(f'可用内存: {available}MB')
        if available < 600:
            print('⚠️  可用内存<600MB，torch安装后推理可能OOM')
            print('⚠️  建议：确保内存保护代码已就位再安装torch')
        else:
            print(f'✅ 内存充裕，可以安装torch')
"

# ============================================================
# 第三步：torch CPU版（核心，需内存保护）
# ============================================================
echo ""
echo "====== 第三步：torch CPU版安装 ======"
echo ">>> 预计下载 ~220MB，耗时约2-5分钟..."
echo ""

pip3 install --break-system-packages \
    torch \
    --index-url https://download.pytorch.org/whl/cpu \
    2>&1 | tee -a "$LOG_FILE"

echo ""
echo ">>> 验证 torch ..."
python3 -c "
import torch
print(f'  ✅ torch OK: {torch.__version__}')
print(f'  CPU推理支持: {torch.backends.cpu.is_built()}')
print(f'  CUDA可用: {torch.cuda.is_available()} (CPU版=False正常)')

# 验证基本推理能力
import time
t0 = time.time()
x = torch.randn(4, 512)
w = torch.randn(512, 256)
y = torch.mm(x, w)
elapsed = (time.time()-t0)*1000
print(f'  矩阵乘法测试: {elapsed:.1f}ms ✅')

# 内存使用
import subprocess
r = subprocess.run(['free','-m'], capture_output=True, text=True)
for l in r.stdout.split('\n'):
    if l.startswith('Mem:'):
        parts = l.split()
        print(f'  安装后可用内存: {parts[6]}MB')
"

echo ""
echo "====== 第三步完成：torch安装成功 ======"

# ============================================================
# 第四步：全量功能验证
# ============================================================
echo ""
echo "====== 第四步：梵天功能验证 ======"

python3 -c "
import sys
sys.path.insert(0, '.')

print('--- 1. Kronos全模型验证 ---')
try:
    from brahma_brain.kronos_engine import KronosEngine
    ke = KronosEngine()
    print(f'  ✅ KronosEngine加载成功')
except Exception as e:
    print(f'  ⚠️  KronosEngine: {e}')

print()
print('--- 2. Kronos Bridge验证 ---')
try:
    from brahma_brain.kronos_bridge import get_s23_kronos
    print('  ✅ kronos_bridge import成功')
except Exception as e:
    print(f'  ⚠️  kronos_bridge: {e}')

print()
print('--- 3. Qdrant方仓库验证 ---')
try:
    from brahma_brain.fangcang_tradfi_db import query_tradfi
    result = query_tradfi('NVDAUSDT', 2.5, 20, 1.0, 1.5, 62.0, 'UP', top_k=5)
    print(f'  ✅ TradFi方仓查询成功: WR={result[\"wr\"]:.1%} n={result[\"n\"]}')
except Exception as e:
    print(f'  ⚠️  fangcang_tradfi_db: {e}')

print()
print('--- 4. 在线学习验证 ---')
try:
    from brahma_brain.online_learner_v2 import OnlineLearnerV2
    print('  ✅ OnlineLearnerV2 import成功')
except Exception as e:
    print(f'  ⚠️  online_learner_v2: {e}')

print()
print('--- 5. CausalVerifier验证 ---')
try:
    from brahma_brain.causal_regime_verifier import CausalRegimeVerifier
    print('  ✅ CausalRegimeVerifier import成功')
except Exception as e:
    print(f'  ⚠️  causal_regime_verifier: {e}')

print()
print('--- 6. 内存最终状态 ---')
import subprocess
r = subprocess.run(['free','-h'], capture_output=True, text=True)
print(r.stdout)
"

# ============================================================
# 完成汇报
# ============================================================
echo ""
echo "=============================================="
echo " 梵天依赖安装完成"
echo " 日志文件: $LOG_FILE"
echo "=============================================="
echo ""
echo "安装包汇总:"
python3 -c "
pkgs = ['lightgbm','qdrant_client','statsmodels','sklearn','torch']
for pkg in pkgs:
    try:
        m = __import__(pkg)
        ver = getattr(m,'__version__','?')
        print(f'  ✅ {pkg:<15s} {ver}')
    except:
        print(f'  ❌ {pkg:<15s} 未安装')
"
echo ""
echo "下一步："
echo "  1. 验证梵天健康检查: python3 scripts/brahma_health.py"
echo "  2. 运行冒烟测试:     python3 brahma_brain/brahma_smoke_test.py"
echo "  3. 观察首次Kronos全模型推理日志"
echo "=============================================="
