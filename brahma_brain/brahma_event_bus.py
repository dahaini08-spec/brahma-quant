#!/usr/bin/env python3
"""
brahma_event_bus.py — 梵天事件总线 v1.0
[封印 2026-11-01 苏摩111] DeepSeek Super-agent整合

DeepSeek Super-agent核心思想：
  Worker发现事件 → 写入EventBus → Router感知 → 推送/决策

梵天实现：
  各Worker(cron任务) → brahma_event_bus → brahma_robot(Router) → push_hub

事件优先级：
  P0 = 立即推送（SL触发/清算警报/体制切换）
  P1 = 下次心跳推送（信号激活/数据异常）
  P2 = 批量推送（状态更新/普通感知）
  P3 = 静默记录（审计日志）

接入位置：
  brahma_brain/brahma_event_bus.py
  被调用方：各Worker脚本 → emit()
  消费方：brahma_robot.py → Router → push_hub
"""
import json
import time
import os
import fcntl
from pathlib import Path
from typing import Optional

BASE = Path(__file__).parent.parent
EVENT_LOG = BASE / 'data' / 'brahma_event_bus.jsonl'
EVENT_STATE = BASE / 'data' / 'brahma_event_state.json'

# 事件类型注册表
EVENT_TYPES = {
    # P0 — 立即响应
    'SL_HIT':           ('P0', '止损触达'),
    'LIQ_WARNING':      ('P0', '清算警报'),
    'REGIME_SWITCH':    ('P0', '体制切换'),
    'CIRCUIT_BREAK':    ('P0', '熔断触发'),
    # P1 — 心跳推送
    'SIGNAL_ACTIVATE':  ('P1', '信号激活'),
    'DATA_EXPIRED':     ('P1', '数据过期'),
    'DATA_HEALED':      ('P1', '数据自愈'),
    'SENTINEL_ALERT':   ('P1', '哨兵警报'),
    'VIP_ENTRY_HIT':    ('P1', 'VIP入场触达'),
    'COUNCIL_VOTE':     ('P1', 'AI议会投票'),
    # P2 — 批量感知
    'OI_SURGE':         ('P2', 'OI急增'),
    'TAKER_CONFIRM':    ('P2', 'Taker确认'),
    'FR_EXTREME':       ('P2', '资金费极端'),
    'HURST_CHANGE':     ('P2', 'Hurst体制变化'),
    'POSITION_UPDATE':  ('P2', '持仓更新'),
    # P1 — SMC结构层（Phase2新增）
    'BOS_CONFIRMED':      ('P1', 'BOS结构突破确认'),
    'FVG_TOUCHED':        ('P1', 'FVG磁铁触达'),
    'PREMIUM_ZONE_ENTER': ('P1', '进入溢价做空区'),
    'DISCOUNT_ZONE_ENTER':('P1', '进入折扣做多区'),
    # P0 — 闪崩
    'FLASH_CRASH':        ('P0', '闪崩检测'),
    'WHALE_ALERT':        ('P0', '大户5min急降>3pt'),
    'FUNDING_SPIKE':      ('P0', 'FR极端突变>0.1%'),
    # P2 — 跨资产
    'CROSS_ASSET_DIVERGE':('P2', '跨标的强弱分化'),
    'HURST_REGIME_CHANGE':('P2', 'Hurst体制变化'),
    'BB_SQUEEZE':         ('P2', '布林带极度收窄<0.35%'),
    'COUNCIL_CONSENSUS':  ('P1', 'AI三院制共识'),
    'GEX_FLIP':           ('P1', 'GEX零翻转穿越'),
    'CORRELATION_BREAK':  ('P1', 'BTC/ETH相关性裂口>1.5%'),
    # P3 — 审计
    'ANALYSIS_DONE':    ('P3', '分析完成'),
    'CRON_RUN':         ('P3', 'Cron执行'),
    'HEARTBEAT':        ('P3', '心跳'),
}


def emit(
    event_type: str,
    sym: str = '',
    data: dict = None,
    source: str = '',
    priority: Optional[str] = None,
    dedup_key: str = '',
    dedup_ttl: int = 0,
) -> bool:
    """
    发送事件到梵天事件总线。
    
    Args:
        event_type: 事件类型（见EVENT_TYPES）
        sym: 标的（BTCUSDT / ETHUSDT / ALL）
        data: 事件附加数据
        source: 发送方脚本名
        priority: 优先级覆盖（P0/P1/P2/P3），None=用注册表默认值
        dedup_key: 去重key，TTL内同key只记录一次
        dedup_ttl: 去重TTL秒数（0=不去重）
    """
    now = time.time()

    # 去重检查
    if dedup_key and dedup_ttl > 0:
        try:
            state = json.loads(EVENT_STATE.read_text()) if EVENT_STATE.exists() else {}
            last = float(state.get(f'dedup_{dedup_key}', 0) or 0)
            if now - last < dedup_ttl:
                return False  # 在TTL内，跳过
            state[f'dedup_{dedup_key}'] = now
            # 清理过期key（>24h）
            state = {k: v for k, v in state.items() if now - float(v) < 86400}
            _atomic_write(EVENT_STATE, json.dumps(state, ensure_ascii=False))
        except Exception:
            pass

    # 确定优先级
    registered = EVENT_TYPES.get(event_type, ('P2', event_type))
    p = priority or registered[0]
    label = registered[1]

    event = {
        'ts': now,
        'ts_str': time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'type': event_type,
        'label': label,
        'priority': p,
        'sym': sym or 'ALL',
        'source': source or _caller_name(),
        'data': data or {},
    }

    # 写入事件日志（追加，线程安全）
    try:
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False) + '\n'
        with open(EVENT_LOG, 'a', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
            fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception as e:
        print(f'[EventBus] emit失败: {e}', flush=True)
        return False


def poll(
    min_priority: str = 'P1',
    since_ts: float = 0,
    sym: str = '',
    event_types: list = None,
    limit: int = 50,
) -> list:
    """
    Router消费事件队列。
    
    Args:
        min_priority: 最低优先级（P0=只看P0, P1=P0+P1, P2=全部）
        since_ts: 只返回此时间戳之后的事件
        sym: 标的过滤（空=全部）
        event_types: 事件类型过滤（空=全部）
        limit: 最多返回条数
    """
    priority_order = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
    min_p = priority_order.get(min_priority, 2)

    if not EVENT_LOG.exists():
        return []

    events = []
    try:
        lines = EVENT_LOG.read_text(encoding='utf-8').splitlines()
        for line in lines[-500:]:  # 最近500条
            try:
                e = json.loads(line)
                if e.get('ts', 0) <= since_ts:
                    continue
                if priority_order.get(e.get('priority', 'P3'), 3) > min_p:
                    continue
                if sym and e.get('sym', 'ALL') not in (sym, 'ALL'):
                    continue
                if event_types and e.get('type') not in event_types:
                    continue
                events.append(e)
            except Exception:
                continue
    except Exception:
        pass

    return events[-limit:]


def get_last_event_ts(source: str = '') -> float:
    """获取某个source最后一次事件的时间戳"""
    if not EVENT_LOG.exists():
        return 0.0
    try:
        lines = EVENT_LOG.read_text().splitlines()
        for line in reversed(lines[-200:]):
            try:
                e = json.loads(line)
                if not source or e.get('source') == source:
                    return float(e.get('ts', 0))
            except Exception:
                continue
    except Exception:
        pass
    return 0.0


def trim_log(max_lines: int = 10000):
    """清理事件日志，保留最近max_lines条"""
    if not EVENT_LOG.exists():
        return
    try:
        lines = EVENT_LOG.read_text(encoding='utf-8').splitlines()
        if len(lines) > max_lines:
            kept = lines[-max_lines:]
            _atomic_write(EVENT_LOG, '\n'.join(kept) + '\n')
    except Exception:
        pass


def _atomic_write(path: Path, content: str):
    """原子写入文件"""
    tmp = path.with_suffix('.tmp')
    tmp.write_text(content, encoding='utf-8')
    tmp.replace(path)


def _caller_name() -> str:
    """自动获取调用方脚本名"""
    import inspect
    frame = inspect.stack()
    for f in frame[2:5]:
        fname = f.filename
        if fname and 'brahma_event_bus' not in fname:
            return Path(fname).stem
    return 'unknown'


# ── 便捷快捷方法 ──────────────────────────────────────────────

def emit_sl_hit(sym: str, price: float, sl: float, entry: float, pnl: float):
    """止损触达事件"""
    return emit('SL_HIT', sym=sym, priority='P0',
                data={'price': price, 'sl': sl, 'entry': entry, 'pnl': pnl},
                dedup_key=f'sl_hit_{sym}', dedup_ttl=300)

def emit_signal(sym: str, direction: str, score: float, rr: float, source: str = ''):
    """信号激活事件"""
    return emit('SIGNAL_ACTIVATE', sym=sym, priority='P1',
                data={'direction': direction, 'score': score, 'rr': rr},
                source=source,
                dedup_key=f'signal_{sym}_{direction}_{int(score)}', dedup_ttl=3600)

def emit_regime_switch(sym: str, old_regime: str, new_regime: str):
    """体制切换事件"""
    return emit('REGIME_SWITCH', sym=sym, priority='P0',
                data={'old': old_regime, 'new': new_regime},
                dedup_key=f'regime_{sym}_{new_regime}', dedup_ttl=7200)

def emit_data_healed(sources: list):
    """数据自愈完成事件"""
    return emit('DATA_HEALED', priority='P1',
                data={'healed': sources},
                dedup_key='data_healed', dedup_ttl=3600)

def emit_sentinel(sym: str, dim: str, value, threshold):
    """哨兵维度触发事件"""
    return emit('SENTINEL_ALERT', sym=sym, priority='P1',
                data={'dim': dim, 'value': value, 'threshold': threshold},
                dedup_key=f'sentinel_{sym}_{dim}', dedup_ttl=1800)


if __name__ == '__main__':
    # 测试
    print('=== 梵天EventBus测试 ===')
    emit('HEARTBEAT', source='test', data={'msg': 'EventBus v1.0 online'})
    emit_signal('BTCUSDT', 'LONG', 165.0, 2.34, source='brahma_94d_engine')
    emit_regime_switch('ETHUSDT', 'CHOP_MID', 'BEAR_TREND')
    events = poll(min_priority='P0', since_ts=time.time()-60)
    print(f'P0事件(最近1min): {len(events)}条')
    events_p1 = poll(min_priority='P1', since_ts=time.time()-60)
    print(f'P1+事件(最近1min): {len(events_p1)}条')
    for e in events_p1:
        print(f'  [{e["priority"]}] {e["type"]} {e["sym"]} {e["data"]}')
    print('✅ EventBus测试通过')
