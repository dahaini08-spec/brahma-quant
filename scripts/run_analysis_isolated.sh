#!/bin/bash
# 第二层：独立子进程运行梵天分析，用完即释放内存
# 用法: bash scripts/run_analysis_isolated.sh BTCUSDT
SYMBOL=${1:-BTCUSDT}
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export BRAHMA_SKIP_COUNCIL=1
cd /root/.openclaw/workspace/trading-system
python3 -c "
import sys, os, json
sys.path.insert(0,'.')
from brahma_brain.brahma_analysis_runner import run_analysis
r = run_analysis('$SYMBOL', deep=False)
print(json.dumps({'price':r.get('price'),'regime':r.get('regime'),'score':r.get('score'),'direction':r.get('direction',''),'grade':r.get('grade',0)}))
" 2>/dev/null
