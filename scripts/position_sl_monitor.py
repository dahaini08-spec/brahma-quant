#!/usr/bin/env python3
"""
position_sl_monitor.py — 软止损监控器 v2.2（分批止盈 + 超时止损）
设计院 × 量化工程师360 · 2026-06-21

变更：
  v2.1  从 position_sl_state.json 动态读取SL配置（不再hardcode）
        修复语法错误（SL_CONFIG重复赋值）
        纯脚本静默运行，触发时通过 push_hub._jarvis 推送
        正常无持仓/无触发 → 输出 HEARTBEAT_OK，AI不消耗token
  v2.2  分批止盈: TP1触达时推送建议平仓50%，标记partial_tp1_closed
        72H超时止损：持仓超72H推送P3时间止损警报（含当前PnL）
        [2026-08-13 苏摩111封印]
"""
import json, urllib.request, hmac, hashlib, time, sys
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).parent.parent

# ── API Key ────────────────────────────────────────────────────────
import re
_tools = (BASE.parent / 'TOOLS.md').read_text()
api_key = re.search(r'API Key:\s*(\S+)', _tools).group(1)
secret  = re.search(r'Secret:\s*(\S+)',  _tools).group(1)

# ── 动态读取SL配置（由开仓逻辑写入 position_sl_state.json）─────────
SL_STATE_FILE = BASE / 'data' / 'position_sl_state.json'

def load_sl_config():
    if not SL_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SL_STATE_FILE.read_text())
    except Exception:
        return {}

# ── Binance 请求工具 ───────────────────────────────────────────────
def _sign(params: dict) -> str:
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + '&signature=' + sig

def _get(endpoint: str, params: dict):
    qs = _sign(params)
    url = f'https://fapi.binance.com{endpoint}?{qs}'
    req = urllib.request.Request(url, headers={'X-MBX-APIKEY': api_key})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())

def _post(endpoint: str, params: dict):
    qs = _sign(params)
    url = f'https://fapi.binance.com{endpoint}'
    req = urllib.request.Request(
        url, data=qs.encode(),
        headers={'X-MBX-APIKEY': api_key}, method='POST'
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read()), None
    except urllib.error.HTTPError as e:
        err = e.read()
        try:    return None, json.loads(err)
        except: return None, err.decode()

# ── 推送（复用 push_hub）──────────────────────────────────────────
sys.path.insert(0, str(BASE / 'scripts'))
try:
    from push_hub import _jarvis as _pj
except Exception:
    def _pj(msg, **_): print(msg)

# ── 主逻辑 ────────────────────────────────────────────────────────
def main():
    SL_CONFIG = load_sl_config()
    if not SL_CONFIG:
        pass  # [静默]
        return

    triggered = []

    for sym, cfg in SL_CONFIG.items():
        # 跳过已关闭/非活跃记录
        if not cfg.get('active', True) or cfg.get('status') == 'CLOSED' or cfg.get('close_reason'):
            continue
        # 兼容不同字段名
        side  = cfg.get('side', cfg.get('direction', 'LONG'))
        sl    = float(cfg.get('sl_price', cfg.get('sl', 0)))
        tp    = float(cfg.get('tp_price', cfg.get('tp', 0)))
        entry = float(cfg.get('entry', cfg.get('entry_price', 0)))

        if not sl:
            continue

        try:
            ts = int(time.time() * 1000)
            pos_list = _get('/fapi/v2/positionRisk', {'symbol': sym, 'timestamp': ts})
            time.sleep(0.3)  # 防止请求过密触发418限频
        except Exception as e:
            print(f'⚠️ {sym} API异常: {e}', file=sys.stderr)
            continue

        for p in pos_list:
            amt = float(p.get('positionAmt', 0))
            if amt == 0:
                continue

            mark    = float(p.get('markPrice', 0))
            pnl     = float(p.get('unRealizedProfit', 0))
            pnl_pct = (mark - entry) / entry * 100 if entry else 0
            sl_hit  = (mark <= sl) if side == 'LONG' else (mark >= sl)
            tp_hit  = (mark >= tp) if side == 'LONG' else (mark <= tp)
            dist_sl = abs(mark - sl) / mark * 100 if mark else 0
            dist_tp = abs(tp - mark) / mark * 100 if mark else 0

            # ══ [分批止盈 2026-08-13 苏摩111封印] ══
            # 分批止盈: TP1触达时推送建议平仓50%，标记partial_tp1_closed
            tp1_price = cfg.get('tp1') or cfg.get('tp1_price')
            partial_closed = cfg.get('partial_tp1_closed', False)

            if tp1_price and not partial_closed and mark:
                tp1 = float(tp1_price)
                tp1_hit = (mark >= tp1) if side == 'LONG' else (mark <= tp1)
                if tp1_hit:
                    tp1_msg = (
                        f'🎯 TP1触达 {sym} {side}\n'
                        f'入场: ${entry:.4f} → 当前: ${mark:.4f}\n'
                        f'TP1: ${tp1:.4f} ✅ 建议平仓50%锁定利润\n'
                        f'剩余50%继续持有，追踪止损跟进\n'
                        f'PnL: {pnl:+.4f} USDT ({pnl_pct:+.2f}%)'
                    )
                    print(f'[TP1_HIT] {tp1_msg}')
                    _pj(tp1_msg, dedup_key=f'tp1_{sym}', dedup_ttl=86400)
                    # 标记已触达TP1，写回state文件
                    try:
                        cfg_now = load_sl_config()
                        if sym in cfg_now:
                            cfg_now[sym]['partial_tp1_closed'] = True
                            cfg_now[sym]['partial_tp1_ts'] = datetime.now(timezone.utc).isoformat()
                            SL_STATE_FILE.write_text(json.dumps(cfg_now, indent=2))
                    except Exception as e:
                        print(f'⚠️ TP1写回state失败: {e}', file=sys.stderr)

            # ══ [72H超时止损 2026-08-13 苏摩111封印] ══
            # 持仓超过72H → 推送P3时间止损警报
            entry_time_raw = cfg.get('entry_time') or cfg.get('created_at') or cfg.get('updated_at')
            if entry_time_raw:
                try:
                    if isinstance(entry_time_raw, (int, float)):
                        entry_dt = datetime.fromtimestamp(entry_time_raw, tz=timezone.utc)
                    else:
                        entry_dt = datetime.fromisoformat(str(entry_time_raw).replace('Z', '+00:00'))
                    hours_held = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
                    if hours_held >= 72:
                        timeout_msg = (
                            f'⏰ [P3时间止损] {sym} {side} 持仓超72H\n'
                            f'持仓时长: {hours_held:.1f}H\n'
                            f'入场: ${entry:.4f} → 当前: ${mark:.4f}\n'
                            f'PnL: {pnl:+.4f} USDT ({pnl_pct:+.2f}%)\n'
                            f'建议：评估是否需要时间止损平仓'
                        )
                        print(f'[72H_TIMEOUT] {timeout_msg}')
                        _pj(timeout_msg, dedup_key=f'timeout72h_{sym}', dedup_ttl=21600)
                except Exception as e:
                    print(f'⚠️ {sym} 时间止损计算异常: {e}', file=sys.stderr)

            if sl_hit:
                close_side = 'SELL' if side == 'LONG' else 'BUY'
                ts2 = int(time.time() * 1000)
                r, err = _post('/fapi/v1/order', {
                    'symbol':     sym,
                    'side':       close_side,
                    'type':       'MARKET',
                    'quantity':   str(abs(round(float(amt), 6))),
                    'reduceOnly': 'true',
                    'timestamp':  ts2,
                })
                if r:
                    msg = (
                        f'🚨 SL触发自动平仓 | {sym} {side}\n'
                        f'mark={mark:.4g}  SL={sl:.4g}\n'
                        f'orderId={r.get("orderId")}  status={r.get("status")}\n'
                        f'PnL: {pnl:+.4f} USDT ({pnl_pct:+.2f}%)'
                    )
                else:
                    msg = (
                        f'🚨 SL触发但平仓失败! {sym} {side}\n'
                        f'mark={mark:.4g}  SL={sl:.4g}\n'
                        f'err={err}'
                    )
                _pj(msg, dedup_ttl=3600)
                print(msg)
                triggered.append(sym)

                # 触发后从state文件移除该标的
                try:
                    cfg_now = load_sl_config()
                    cfg_now.pop(sym, None)
                    SL_STATE_FILE.write_text(json.dumps(cfg_now, indent=2))
                except Exception:
                    pass

            elif dist_sl < 1.0:
                # 距SL不足1% → 发出警告（去重1h）
                warn = (
                    f'⚠️ SL临近预警 | {sym} {side}\n'
                    f'mark={mark:.4g}  SL={sl:.4g}  距离={dist_sl:.2f}%\n'
                    f'PnL: {pnl:+.4f} USDT ({pnl_pct:+.2f}%)'
                )
                _pj(warn, dedup_ttl=3600)

    if not triggered:
        pass  # [静默]

if __name__ == '__main__':
    main()
