#!/usr/bin/env python3
"""
pipeline_bridge.py — 流水线自动补全桥 v1.0
设计院 2026-06-06

解决B级触发率27%根本原因：
  signal_queue里entry_lo=None → trigger_15m无法工作 → 超时

功能：
  1. 扫描signal_queue里entry_lo=None的PENDING信号
  2. 对每个信号补调brahma_analyze获取完整入场区
  3. 更新price_zones.json + signal_queue
  4. 同时清理超48H的EXPIRED信号
"""
import json, subprocess, time, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE      = Path(__file__).parent.parent
DATA      = BASE / 'data'
QUEUE_F   = DATA / 'signal_queue.jsonl'
ZONES_F   = DATA / 'price_zones.json'
sys.path.insert(0, str(BASE / 'scripts'))

CST = timezone(timedelta(hours=8))

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def load_queue():
    if not QUEUE_F.exists(): return []
    result = []
    for line in open(QUEUE_F):
        if line.strip():
            try: result.append(json.loads(line))
            except: pass
    return result

def save_queue(entries):
    tmp = str(QUEUE_F) + '.tmp'
    with open(tmp, 'w') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    os.replace(tmp, str(QUEUE_F))

def load_zones():
    if not ZONES_F.exists(): return {}
    try: return json.load(open(ZONES_F))
    except: return {}

def save_zones(zones):
    tmp = str(ZONES_F) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(ZONES_F))

def call_brahma_analyze(sym_base, direction='SHORT', timeout=40):
    """调用brahma_analyze获取完整信号，返回JSON"""
    try:
        r = subprocess.run(
            ['python3', 'brahma_analyze.py', sym_base, direction, '--json'],
            capture_output=True, text=True, timeout=timeout, cwd=str(BASE)
        )
        # 优先尝试整体解析（brahma_analyze输出pretty-print多行JSON）
        try:
            data = json.loads(r.stdout)
            if isinstance(data, dict) and 'score' in data:
                return data
        except Exception:
            pass
        # 回退：逐行查找单行JSON
        for line in r.stdout.split('\n'):
            line = line.strip()
            if line.startswith('{') and 'score' in line:
                try: return json.loads(line)
                except: continue
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        try:
            from error_collector import log_error
            log_error('pipeline_bridge', e, context=f'{sym_base} {direction}')
        except: pass
        return None

def extract_entry(result):
    """从brahma_analyze结果提取入场区"""
    if not result: return None, None
    params = result.get('params', {}) or {}
    elo = params.get('entry_lo') or result.get('entry_lo')
    ehi = params.get('entry_hi') or result.get('entry_hi') or elo
    sl  = params.get('stop_loss') or result.get('stop_loss')
    tp1 = params.get('tp1') or result.get('tp1')
    tp2 = params.get('tp2') or result.get('tp2')
    grade = result.get('structure_grade') or result.get('grade')
    score = result.get('score') or result.get('score_final')
    price = result.get('price')
    return {
        'entry_lo': elo, 'entry_hi': ehi,
        'stop_loss': sl, 'tp1': tp1, 'tp2': tp2,
        'structure_grade': grade, 'score': score, 'price': price,
    }, None

def run():
    queue   = load_queue()
    zones   = load_zones()
    now_ts  = time.time()
    cutoff  = now_ts - 48 * 3600  # 48H过期

    filled  = 0
    expired = 0
    kept    = []

    for sig in queue:
        ts_str = sig.get('ts', '')
        # 解析时间
        try:
            from datetime import datetime as dt
            ts_epoch = dt.fromisoformat(ts_str.replace('Z','+00:00')).timestamp()
        except:
            ts_epoch = now_ts

        # 过期清理
        if ts_epoch < cutoff:
            expired += 1
            continue

        symbol    = sig.get('symbol', '')
        direction = sig.get('direction') or sig.get('signal_dir', 'SHORT')
        entry_lo  = sig.get('entry_lo')

        # 补全入场区
        if not entry_lo or float(entry_lo or 0) == 0:
            sym_base = symbol.replace('USDT', '')
            print(f'  补全 {sym_base} {direction}...', end='', flush=True)
            result = call_brahma_analyze(sym_base, direction)
            if result:
                extracted, _ = extract_entry(result)
                if extracted and extracted.get('entry_lo'):
                    sig['entry_lo']  = extracted['entry_lo']
                    sig['entry_hi']  = extracted['entry_hi']
                    sig['stop_loss'] = extracted.get('stop_loss') or sig.get('stop_loss')
                    sig['tp1']       = extracted.get('tp1') or sig.get('tp1')
                    sig['tp2']       = extracted.get('tp2') or sig.get('tp2')
                    sig['structure_grade'] = extracted.get('structure_grade') or sig.get('structure_grade')
                    sig['bridge_filled_at'] = now_iso()
                    # 更新price_zones
                    zones[symbol] = {
                        **zones.get(symbol, {}),
                        'last_entry_lo': extracted['entry_lo'],
                        'last_entry_hi': extracted['entry_hi'],
                        'last_analyze_ts': now_iso(),
                        'last_score': extracted.get('score'),
                        'cur_price': extracted.get('price'),
                    }
                    filled += 1
                    print(f' ✅ entry={extracted["entry_lo"]:.4f}')
                    # 增量保存，防止timeout时丢失进度
                    kept.append(sig)
                    remaining = [s for s in queue if s not in kept]
                    save_queue(kept + remaining)
                    save_zones(zones)
                    continue
                else:
                    print(f' — 无结构(grade不足)')
            else:
                print(f' — analyze失败')

        kept.append(sig)

    save_queue(kept)
    save_zones(zones)

    summary = f'流水线补全: 处理{len(queue)}条 → 补全{filled}条 过期清理{expired}条 剩余{len(kept)}条'
    print(summary)
    return summary

if __name__ == '__main__':
    run()
