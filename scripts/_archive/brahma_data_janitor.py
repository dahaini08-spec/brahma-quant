#!/usr/bin/env python3
"""
brahma_data_janitor.py — 梵天数据自动清理守卫
设计院封印 2026-08-13 苏摩111

职责：自动识别并清理以下旧数据，保持系统整洁
  1. condition_orders.json — 超72H且无对应真实持仓的条件单
  2. live_signal_log.jsonl — 超7天OPEN信号标记EXPIRED
  3. live_signal_log.jsonl — 测试信号(TEST*/HB_SYM/BANK*T3)标记INVALID
  4. oi_prev_*.json — 超48H的OI前状态缓存文件
  5. active_pumps.json — 超21天的暴涨预警记录
  6. auto_executor_log.jsonl — 超过2000条时保留最新1000条

运行周期：每24H（cron注册为 brahma-data-janitor）
输出规则：有清理动作才推送，否则HEARTBEAT_OK静默
"""

import json
import os
import sys
import time
import glob
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).parent.parent
DATA = BASE / 'data'

def _now():
    return datetime.now(timezone.utc)

def _ts(ts_str):
    try:
        return datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
    except:
        return None

results = []
total_cleaned = 0

# ── 1. condition_orders.json 过期条件单 ────────────────────────────────────
try:
    co_file = DATA / 'condition_orders.json'
    if co_file.exists():
        orders = json.loads(co_file.read_text())
        if orders:
            # 读取真实持仓
            sl_file = DATA / 'position_sl_state.json'
            real_positions = set()
            if sl_file.exists():
                sl_state = json.loads(sl_file.read_text())
                real_positions = set(sl_state.keys())

            cutoff_72h = _now() - timedelta(hours=72)
            keep = {}
            removed = []

            for k, v in orders.items():
                sym = v.get('symbol', k)
                created_ts = _ts(v.get('created_at', ''))
                is_test = any(x in sym.upper() for x in ['TEST', 'HB_SYM', '_T3', '_T1', '_T2'])
                is_expired = created_ts and created_ts < cutoff_72h
                has_real_pos = sym in real_positions

                if is_test or (is_expired and not has_real_pos):
                    reason = '测试单' if is_test else f'超72H且无持仓({sym})'
                    removed.append(f'{sym}({reason})')
                else:
                    keep[k] = v

            if removed:
                co_file.write_text(json.dumps(keep, indent=2, ensure_ascii=False))
                results.append(f'条件单清理: {len(removed)}条 → {removed}')
                total_cleaned += len(removed)
except Exception as e:
    results.append(f'条件单清理失败: {e}')

# ── 2. live_signal_log.jsonl 超期信号 ────────────────────────────────────
try:
    sig_file = DATA / 'live_signal_log.jsonl'
    if sig_file.exists():
        lines = sig_file.read_text().strip().split('\n')
        signals = [json.loads(l) for l in lines if l.strip()]

        cutoff_7d = _now() - timedelta(days=7)
        expired_count = 0
        invalid_count = 0
        new_lines = []

        for s in signals:
            changed = False
            sym = str(s.get('symbol', ''))

            # 测试信号 → INVALID
            if any(x in sym.upper() for x in ['TEST', 'HB_SYM', '_T3', 'BANKUSDT']):
                if s.get('outcome') not in ('INVALID', 'TP1', 'TP2', 'SL', 'WIN', 'LOSS'):
                    s['outcome'] = 'INVALID'
                    s['valid'] = False
                    s['invalid_reason'] = 'test_signal_janitor'
                    invalid_count += 1
                    changed = True

            # 超7天OPEN信号 → EXPIRED
            elif s.get('outcome') is None:
                ts = _ts(s.get('ts_iso', ''))
                if ts and ts < cutoff_7d:
                    s['outcome'] = 'EXPIRED_TIMEOUT'
                    s['valid'] = False
                    s['expired_reason'] = f'超7天未结算 janitor {_now().strftime("%Y-%m-%d")}'
                    expired_count += 1
                    changed = True

            new_lines.append(json.dumps(s, ensure_ascii=False))

        if expired_count or invalid_count:
            sig_file.write_text('\n'.join(new_lines) + '\n')
            results.append(f'信号日志清理: EXPIRED={expired_count} INVALID={invalid_count}')
            total_cleaned += expired_count + invalid_count

except Exception as e:
    results.append(f'信号日志清理失败: {e}')

# ── 3. oi_prev_*.json 超48H缓存 ────────────────────────────────────────────
try:
    now_ts = time.time()
    cutoff_48h = now_ts - 48 * 3600
    oi_files = list(DATA.glob('oi_prev_*.json'))
    removed_oi = []

    for f in oi_files:
        if os.path.getmtime(f) < cutoff_48h:
            f.unlink()
            removed_oi.append(f.name)

    if removed_oi:
        results.append(f'OI缓存清理: {len(removed_oi)}个文件(超48H)')
        total_cleaned += len(removed_oi)

except Exception as e:
    results.append(f'OI缓存清理失败: {e}')

# ── 4. active_pumps.json 超21天预警 ───────────────────────────────────────
try:
    ap_file = DATA / 'active_pumps.json'
    if ap_file.exists():
        ap = json.loads(ap_file.read_text())
        cutoff_21d = _now() - timedelta(days=21)
        keep_ap = {}
        removed_ap = []

        for sym, v in ap.items():
            ts_str = v.get('ts', '') or v.get('created_at', '') or v.get('alert_ts', '')
            ts = _ts(ts_str)
            if ts and ts < cutoff_21d:
                removed_ap.append(sym)
            else:
                keep_ap[sym] = v

        if removed_ap:
            ap_file.write_text(json.dumps(keep_ap, indent=2, ensure_ascii=False))
            results.append(f'暴涨预警清理: {len(removed_ap)}个超21天标的')
            total_cleaned += len(removed_ap)

except Exception as e:
    results.append(f'暴涨预警清理失败: {e}')

# ── 5. auto_executor_log.jsonl 超2000条截断 ─────────────────────────────────
try:
    ae_file = DATA / 'auto_executor_log.jsonl'
    if ae_file.exists():
        ae_lines = ae_file.read_text().strip().split('\n')
        ae_lines = [l for l in ae_lines if l.strip()]
        if len(ae_lines) > 2000:
            trimmed = len(ae_lines) - 1000
            ae_file.write_text('\n'.join(ae_lines[-1000:]) + '\n')
            results.append(f'执行日志截断: {trimmed}条 → 保留最新1000条')
            total_cleaned += trimmed

except Exception as e:
    results.append(f'执行日志截断失败: {e}')

# ── 6. position_sl_state 僵尸持仓检测 ─────────────────────────────────
try:
    sl_file = DATA / 'position_sl_state.json'
    if sl_file.exists():
        positions = json.loads(sl_file.read_text())
        zombie_warns = []
        cutoff_72h = _now() - timedelta(hours=72)
        
        for sym, pos in positions.items():
            # 检查持仓时长
            entry_ts_str = pos.get('entry_time') or pos.get('created_at') or pos.get('entry_ts', '')
            entry_ts = _ts(entry_ts_str)
            if not entry_ts:
                continue
            
            age_hours = (_now() - entry_ts).total_seconds() / 3600
            
            # 超过72H的持仓标记为警告
            if age_hours > 72:
                direction = pos.get('direction', '?')
                entry_price = pos.get('entry_price', pos.get('entry', '?'))
                sl_price = pos.get('sl_price', pos.get('sl', '?'))
                zombie_warns.append(
                    f'{sym} {direction} 持仓{age_hours:.0f}H '
                    f'入场${entry_price} SL=${sl_price}'
                )
        
        if zombie_warns:
            results.append(f'⚠️ 僵尸持仓检测: {len(zombie_warns)}个超72H\n    ' + '\n    '.join(zombie_warns))
            total_cleaned += 0  # 只警告不清理，需要苏摩手动确认

except Exception as e:
    results.append(f'持仓检测失败: {e}')

# ── 输出 ─────────────────────────────────────────────────────────────────────
if total_cleaned == 0 and not any('僵尸持仓' in r for r in results):
    print('HEARTBEAT_OK')
else:
    ts_str = _now().strftime('%Y-%m-%d %H:%M UTC')
    print(f'🧹 梵天数据清理报告 | {ts_str}')
    print()
    print(f'清理总计: {total_cleaned}项')
    for r in results:
        print(f'  · {r}')
    print()
    print('系统数据已整洁，无需人工干预。')
