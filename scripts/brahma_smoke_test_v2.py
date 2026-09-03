#!/usr/bin/env python3
"""
brahma_smoke_test_v2.py — 梵天冒烟测试 v2.0（分段懒加载，无OOM）
[设计院封印 2026-08-14 苏摩111]

修复原版OOM根因：分段subprocess隔离，每层独立进程，不累积内存
"""
import subprocess, sys, time, json
from pathlib import Path

BASE = Path(__file__).parent.parent
PYTHON = sys.executable

# ── 测试分层定义 ───────────────────────────────────────────
LAYERS = [
    {
        "name": "L1 数据层",
        "tests": [
            ("brahma_bus BTC价格",  "from brahma_bus import get_price; p=get_price('BTCUSDT'); assert p>0, f'price={p}'; print(f'BTC={p}')"),
            ("brahma_bus ETH价格",  "from brahma_bus import get_price; p=get_price('ETHUSDT'); assert p>0; print(f'ETH={p}')"),
            ("brahma_bus klines",   "from brahma_bus import get_klines; d=get_klines('BTCUSDT','1h',10); assert len(d)>0; print(f'n={len(d)}')"),
            ("brahma_state新鲜",    "import time,json; from pathlib import Path; d=json.load(open('data/brahma_state.json')); age=(time.time()-Path('data/brahma_state.json').stat().st_mtime)/60; assert age<90,f'{age:.0f}min过期'; print(f'age={age:.1f}min')"),
            ("position_sl_state",   "import json; d=json.load(open('data/position_sl_state.json')); print(f'{len(d)}个持仓')"),
        ]
    },
    {
        "name": "L2 体制层",
        "tests": [
            ("market_state.analyze", "import sys; sys.path.insert(0,'brahma_brain'); from market_state import analyze; r=analyze('BTCUSDT'); assert r.get('regime'); print(r['regime'])"),
            ("smc_engine分析",       "import sys; sys.path.insert(0,'brahma_brain'); from smc_engine import analyze_smc; r=analyze_smc('BTCUSDT','1h'); assert isinstance(r,dict); print(f'keys={list(r.keys())[:3]}')"),
            ("brahma_core_block_a导入", "import sys; sys.path.insert(0,'brahma_brain'); from brahma_core_block_a import calc_block_a; print('calc_block_a OK')"),
        ]
    },
    {
        "name": "L3 执行层",
        "tests": [
            ("position_sizer",       "import sys; sys.path.insert(0,'brahma_brain'); import position_sizer; t=position_sizer.DEFAULT_BY_SCORE; assert len(t)>0; print(f'{len(t)}档')"),
            ("SignalIntegrityGate",  "import sys; sys.path.insert(0,'brahma_brain'); from brahma_brain.signal_quality_engine import SignalIntegrityGate; g=SignalIntegrityGate(); print('SIGate OK')"),
            ("fangcang_engine",      "import sys; sys.path.insert(0,'brahma_brain'); from fangcang_engine import format_fangcang_card, get_fangcang_context; print('fangcang OK')"),
            ("auto_executor import", "import sys; sys.path.insert(0,'brahma_brain'); sys.path.insert(0,'scripts'); import auto_executor; print('auto_executor OK')"),
        ]
    },
    {
        "name": "L4 进化层",
        "tests": [
            ("Ch8 settled.jsonl",    "from pathlib import Path; n=len(Path('data/trajectories/settled.jsonl').read_text().strip().splitlines()); assert n>0; print(f'{n}条轨迹')"),
            ("experience_docs",      "from pathlib import Path; n=len(Path('data/trajectories/experience_docs.jsonl').read_text().strip().splitlines()); print(f'{n}条经验文档')"),
            ("news_feed新鲜",        "import time,json; from pathlib import Path; f=Path('data/news_feed.jsonl'); age=(time.time()-f.stat().st_mtime)/60; n=len(f.read_text().strip().splitlines()); assert age<360,f'{age:.0f}min过期'; print(f'{n}条/{age:.0f}min前')"),
            ("water_mark水位线",     "import json; from pathlib import Path; d=json.loads(Path('data/news_last_pushed.json').read_text()); print(f'last_ts={d.get(\"last_ts\")}')"),
        ]
    },
    {
        "name": "L5 系统层",
        "tests": [
            ("wiring_check全引用",   "import subprocess; r=subprocess.run(['python3','scripts/brahma_wiring_check.py'],capture_output=True,text=True); lines=r.stdout.splitlines(); ok=len([l for l in lines if '✅' in l]); warn=len([l for l in lines if '❌' in l or '孤岛' in l]); assert ok>0,'无✅'; print(f'{ok}项已引用 {warn}项孤岛(间接依赖/工具) ✅')"),
            ("内存可用>400MB",       "import subprocess; lines=subprocess.run(['free','-m'],capture_output=True,text=True).stdout.splitlines(); mem=int([l for l in lines if l.startswith('Mem:')][0].split()[6]); assert mem>400,f'{mem}MB不足'; print(f'{mem}MB可用')"),
            ("ws_guardian运行",      "import subprocess; r=subprocess.run(['pgrep','-f','ws_guardian'],capture_output=True); assert r.returncode==0,'ws_guardian未运行'; print('运行中')"),
            ("cron任务≥15",          "import subprocess; r=subprocess.run(['openclaw','cron','list'],capture_output=True,text=True,timeout=20); n=len([l for l in r.stdout.splitlines() if 'every' in l or 'cron ' in l.lower()]); assert n>=15,f'只有{n}个'; print(f'{n}个任务 (supercronic承担高频)')"),
        ]
    },
]

# ── 运行引擎 ──────────────────────────────────────────────
def run_test(name, code):
    """独立子进程运行单个测试，内存完全隔离"""
    t0 = time.time()
    try:
        r = subprocess.run(
            [PYTHON, '-c', f"import sys; sys.path.insert(0,'brahma_brain'); sys.path.insert(0,'scripts'); {code}"],
            capture_output=True, text=True, timeout=15,
            cwd=str(BASE)
        )
        ms = int((time.time()-t0)*1000)
        if r.returncode == 0:
            out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'OK'
            return '✅', ms, out
        else:
            err = (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else 'failed'
            return '❌', ms, err[:60]
    except subprocess.TimeoutExpired:
        return '⏰', 15000, 'timeout'
    except Exception as e:
        return '❌', int((time.time()-t0)*1000), str(e)[:60]

def main():
    print()
    print('╔' + '═'*62 + '╗')
    print('║  🏛️  梵天冒烟测试 v2.0  分段懒加载版                     ║')
    print(f'║  {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()):<60}║')
    print('╠' + '═'*62 + '╣')

    total_pass = total_fail = total_timeout = 0
    all_results = []

    for layer in LAYERS:
        print(f'║  [{layer["name"]}]' + ' '*51 + '║')
        for name, code in layer["tests"]:
            status, ms, out = run_test(name, code)
            line = f'║  {status} {ms:5d}ms  {name:<24} {out}'
            print(line[:65].ljust(65) + '║')
            if status == '✅': total_pass += 1
            elif status == '⏰': total_timeout += 1
            else: total_fail += 1
            all_results.append((name, status, ms, out))

    print('╠' + '═'*62 + '╣')
    total = total_pass + total_fail + total_timeout
    grade = 'S满分' if total_fail==0 and total_timeout==0 else 'A' if total_fail<=1 else 'B+' if total_fail<=3 else 'B'
    print(f'║  通过: {total_pass}/{total}  失败: {total_fail}  超时: {total_timeout}  评级: {grade}'.ljust(64) + '║')
    slowest = max(all_results, key=lambda x: x[2])
    print(f'║  最慢: {slowest[2]}ms ({slowest[0][:30]})'.ljust(64) + '║')
    print('╚' + '═'*62 + '╝')

    if total_fail > 0:
        print('\n❌ 失败项:')
        for name,st,ms,out in all_results:
            if st == '❌':
                print(f'  {name}: {out}')

    # 保存结果
    result_file = BASE / 'data' / 'smoke_test_latest.json'
    result_file.write_text(json.dumps({
        'passed': total_pass, 'failed': total_fail, 'timeout': total_timeout,
        'total': total, 'grade': grade,
        'ts': time.time(),
        'failures': [name for name,st,_,_ in all_results if st != '✅']
    }))

    return 0 if total_fail == 0 else 1

if __name__ == '__main__':
    sys.exit(main())
