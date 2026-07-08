"""
import_audit.py — 模块导入副作用审计
设计院 2026-07-08 | 第三方审计v4.0 Step6

测试：仅import是否会触发下单/发请求/启动线程
"""
import sys, os, time, json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from audit_tools.no_trade_guard import install
install()

MODULES = [
    ('brahma_brain.brahma_event_bus',    'EventBus'),
    ('brahma_brain.brahma_bus',          'DataBus(TTL缓存)'),
    ('brahma_brain.circuit_breaker',     '技术熔断器'),
    ('brahma_brain.regime_state_machine','Regime状态机'),
    ('brahma_brain.brahma_logger',       '结构化日志'),
    ('brahma_brain.safety',              '安全闸'),
    ('guardrails.layer_9_12',            'Layer9-12断路器'),
    ('brahma_brain.rl_position_ab',      'RL A/B仓位'),
    ('brahma_brain.online_learner_v2',   '在线学习'),
    ('brahma_brain.regime_hmm_v2',       'HMM概率模型'),
]

results = []
print(f"\n{'模块':<40} {'状态':^8} {'耗时':>8}  说明")
print("-" * 80)

for module, desc in MODULES:
    t0 = time.time()
    try:
        import importlib
        importlib.import_module(module)
        elapsed = (time.time()-t0)*1000
        status = '✅ OK'
        error = None
    except ImportError as e:
        elapsed = (time.time()-t0)*1000
        status = '⚠️  SKIP'
        error = f'ImportError: {str(e)[:60]}'
    except Exception as e:
        elapsed = (time.time()-t0)*1000
        status = '❌ FAIL'
        error = f'{type(e).__name__}: {str(e)[:60]}'

    row = {'module': module, 'desc': desc, 'status': status,
           'elapsed_ms': round(elapsed,1), 'error': error}
    results.append(row)
    err_str = f"  [{error}]" if error else ""
    print(f"  {module:<38} {status:^8} {elapsed:>6.0f}ms  {desc}{err_str}")

# 写入报告
out = BASE / 'audit_outputs' / 'import_audit.jsonl'
out.parent.mkdir(exist_ok=True)
with open(out, 'w') as f:
    for r in results:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

ok = sum(1 for r in results if 'OK' in r['status'])
skip = sum(1 for r in results if 'SKIP' in r['status'])
fail = sum(1 for r in results if 'FAIL' in r['status'])
print(f"\n结果: {ok}✅ OK  {skip}⚠️ SKIP  {fail}❌ FAIL")
print(f"报告: {out}")
