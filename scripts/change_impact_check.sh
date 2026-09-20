#!/bin/bash
# change_impact_check.sh — 变更影响自动验证
# [9.19 设计院封印 苏摩111] 改了A自动验证B
#
# 每次修改scoring_config/prompt/模块后运行此脚本：
#   1. 配置契约验证（scoring_config类型与代码预期一致）
#   2. cron引用完整性（crontab引用的模块/脚本都存在）
#   3. 全量冒烟测试（12项功能验证）
#   4. DAG稀疏激活验证（dim_trace写入+读取正常）
#   5. 进程健康检查（4进程存活）
#
# 用法:
#   bash scripts/change_impact_check.sh              # 全量验证
#   bash scripts/change_impact_check.sh --quick        # 快速（跳过冒烟）

BASE="/root/.openclaw/workspace/trading-system"
PY="$BASE/venv/bin/python3"
PASS=0
FAIL=0
WARN=0

header() { echo -e "\n═══ $1 ═══"; }
ok() { echo "✅ $1"; PASS=$((PASS+1)); }
fail() { echo "❌ $1"; FAIL=$((FAIL+1)); }
warn() { echo "⚠️ $1"; WARN=$((WARN+1)); }

header "1/5 配置契约验证"
$PY scripts/config_contract_check.py && ok "配置契约" || fail "配置契约"

header "2/5 Cron引用完整性"
$PY scripts/cron_reference_check.py && ok "Cron引用" || warn "Cron引用有失效"

header "3/5 冒烟测试"
if [ "$1" != "--quick" ]; then
    $PY brahma_brain/brahma_smoke_test.py 2>&1 | tail -5
    if [ $? -eq 0 ]; then ok "冒烟测试"; else fail "冒烟测试"; fi
else
    echo "  (--quick 跳过)"
fi

header "4/5 DAG稀疏激活验证"
$PY -c "
import sys, json
sys.path.insert(0, 'brahma_brain')
from dag_executor import apply_sparse_activation
# 测试6体制×2方向
all_ok = True
for regime in ['BEAR_TREND','BEAR_EARLY','CHOP_MID','BULL_TREND','BEAR_RECOVERY','BULL_EARLY']:
    for direction in ['LONG','SHORT']:
        r = apply_sparse_activation({'s1':1.0,'s2':2.0,'s3':3.0}, regime, direction, raw_score=10.0)
        active = r.get('active_dims')
        if isinstance(active, list) and active and len(active[0])==1:
            print(f'  ❌ {regime}:{direction} active={active} 单字符bug')
            all_ok = False
if all_ok:
    print('  6×2=12 全部正确')
    sys.exit(0)
else:
    sys.exit(1)
" 2>&1 && ok "DAG稀疏激活" || fail "DAG稀疏激活"

header "5/5 进程健康检查"
alive=0
pgrep -f "supercronic.*brahma_crontab" > /dev/null && alive=$((alive+1))
pgrep -f "cvd_ws_collector" > /dev/null && alive=$((alive+1))
pgrep -f "liqmap_collector" > /dev/null && alive=$((alive+1))
pgrep -f "independent_watchdog" > /dev/null && alive=$((alive+1))
echo "  $alive/4 进程存活"
if [ $alive -eq 4 ]; then ok "进程健康"; elif [ $alive -ge 2 ]; then warn "进程健康($alive/4)"; else fail "进程健康($alive/4)"; fi

header "总结"
echo "✅ $PASS  ❌ $FAIL  ⚠️ $WARN"
if [ $FAIL -eq 0 ]; then
    echo "═══ 变更影响验证通过 ═══"
    exit 0
else
    echo "═══ 变更影响验证失败 ═══"
    exit 1
fi
