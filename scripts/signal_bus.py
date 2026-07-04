#!/usr/bin/env python3
"""
signal_bus.py — 梵天统一信号总线 v1.0
设计院 2026-07-04

所有信号源(主系统/OI猎手/暴涨猎手)统一写入此文件
所有执行器从此文件读取
格式标准: 每行一个JSON信号对象
"""
import json, time, hashlib, os
from pathlib import Path
from datetime import datetime, timezone

BASE     = Path(__file__).parent.parent
BUS_FILE = BASE / 'data' / 'signal_bus.jsonl'
LOCK_FILE= BASE / 'data' / '.signal_bus.lock'

# 必须字段
REQUIRED = ['signal_id','source','symbol','direction','score',
            'valid','entry_lo','entry_hi','sl','tp1','rr1','expires_at','regime']

def _lock():
    """简单文件锁，防止并发写入"""
    for _ in range(20):
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            # 锁文件超5s视为死锁，强制清除
            if LOCK_FILE.exists() and time.time()-LOCK_FILE.stat().st_mtime > 5:
                LOCK_FILE.unlink()
            time.sleep(0.1)
    return False

def _unlock():
    try: LOCK_FILE.unlink()
    except: pass

def write(signal: dict) -> bool:
    """
    写入一条信号到总线
    自动补充缺失字段，验证必须字段
    """
    now = time.time()
    sym = signal.get('symbol','')
    score = signal.get('score', 0)
    direction = signal.get('direction','')
    source = signal.get('source','unknown')
    regime = signal.get('regime','')
    ts_str = datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y%m%d%H%M')

    # 自动生成signal_id
    if not signal.get('signal_id'):
        sha8 = hashlib.sha256(f'{sym}{score}{direction}{now}'.encode()).hexdigest()[:8]
        signal['signal_id'] = f'[BRAHMA:SIG:{source.upper()}:{sym}:{score:.0f}:{direction}:{ts_str}:{sha8}]'

    # 默认值补全
    signal.setdefault('ts', now)
    signal.setdefault('status', 'pending')
    signal.setdefault('priority', 'P1')
    signal.setdefault('entry_type', 'limit')

    # 有效期默认12H
    if not signal.get('expires_at'):
        exp = datetime.fromtimestamp(now + 43200, tz=timezone.utc)
        signal['expires_at'] = exp.isoformat()

    # 验证必须字段
    missing = [f for f in REQUIRED if f not in signal or signal[f] is None]
    if missing:
        print(f'[SignalBus] ❌ 缺少必须字段: {missing}')
        return False

    if not _lock():
        print('[SignalBus] ❌ 获取锁超时')
        return False
    try:
        with open(BUS_FILE, 'a') as f:
            f.write(json.dumps(signal, ensure_ascii=False) + '\n')
        print(f'[SignalBus] ✅ 写入: {sym} {direction} score={score:.0f} source={source}')
        return True
    finally:
        _unlock()

def read_pending(max_age_h=12, min_score=100) -> list:
    """
    读取待执行信号
    过滤：status=pending / valid=True / 未过期 / score≥门槛
    """
    if not BUS_FILE.exists():
        return []
    now = time.time()
    results = []
    seen_symbols = set()

    lines = BUS_FILE.read_text().strip().split('\n')
    for line in reversed(lines):  # 最新的先处理
        if not line.strip(): continue
        try:
            s = json.loads(line)
        except: continue

        # 基本过滤
        if s.get('status') != 'pending': continue
        if not s.get('valid'): continue
        if float(s.get('score', 0)) < min_score: continue

        # 过期检查
        exp = s.get('expires_at','')
        if exp:
            try:
                exp_ts = datetime.fromisoformat(exp.replace('Z','+00:00')).timestamp()
                if now > exp_ts: continue
            except: pass

        # 年龄检查
        if now - s.get('ts', 0) > max_age_h * 3600: continue

        # 同标的去重（只取最新的）
        sym = s.get('symbol','')
        if sym in seen_symbols: continue
        seen_symbols.add(sym)

        results.append(s)

    return results

def mark_status(signal_id: str, status: str, order_id: str = None):
    """更新信号状态"""
    if not BUS_FILE.exists(): return
    lines = BUS_FILE.read_text().strip().split('\n')
    new_lines = []
    for line in lines:
        if not line.strip(): continue
        try:
            s = json.loads(line)
            if s.get('signal_id') == signal_id:
                s['status'] = status
                if order_id: s['order_id'] = order_id
                s['updated_at'] = time.time()
            new_lines.append(json.dumps(s, ensure_ascii=False))
        except:
            new_lines.append(line)
    if _lock():
        try:
            BUS_FILE.write_text('\n'.join(new_lines) + '\n')
        finally:
            _unlock()

def validate():
    """验收标准脚本"""
    if not BUS_FILE.exists():
        print('signal_bus.jsonl 不存在')
        return False
    lines = [l for l in BUS_FILE.read_text().strip().split('\n') if l.strip()]
    errors = 0
    for l in lines[-10:]:
        try:
            s = json.loads(l)
            miss = [f for f in REQUIRED if f not in s]
            if miss:
                print(f'  缺字段: {miss} in {s.get("signal_id","?")}')
                errors += 1
        except Exception as e:
            print(f'  JSON解析错误: {e}')
            errors += 1
    sources = set()
    for l in lines:
        try: sources.add(json.loads(l).get('source',''))
        except: pass
    print(f'✅ signal_bus: {len(lines)}条 / {len(sources)}个source({sources}) / 错误{errors}个')
    return errors == 0

if __name__ == '__main__':
    validate()
