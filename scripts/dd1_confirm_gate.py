#!/usr/bin/env python3
"""
DD1人工确认门卫 v2.1
策略群钉钉1推送前需要人工确认（口令：888）

⚠️ 设计院红线 2026-05-23：
  一次 888 只确认并发出【最早一条】PENDING策略（FIFO严格逐条）
  禁止批量发出，禁止 BATCH_WINDOW 批次模式
  发完一条自动提示：还有N条待确认，请再次回复888
"""
import os, sys, json, time, uuid, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

# push_hub统一收口
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from push_hub import _jarvis
except Exception:
    def _jarvis(text: str, **kw):
        try:
            subprocess.Popen(
                ['openclaw','message','send','--channel','jarvis',
                 '--to','73295708:t:019f1797-6c60-7541-ad72-ec34ed14dfc4',
                 '--message', text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f'[DD1Gate] jarvis fallback失败: {e}')

BASE    = Path(__file__).parent.parent
PENDING = BASE / 'data' / 'dd1_pending.json'
CST     = timezone(timedelta(hours=8))
PASS    = '888'           # 确认口令
TTL_SEC = 300             # 默认5分钟（由meta.timeframe动态覆盖）

# [P1-B v21.0] 动态TTL：根据信号周期设置有效时间
_TTL_BY_TF = {
    '15m': 60 * 30,    # 15m信号  → 30分钟
    '1H':  60 * 120,   # 1H信号   → 2小时
    '1h':  60 * 120,
    '4H':  60 * 480,   # 4H信号   → 8小时
    '4h':  60 * 480,
    '1D':  60 * 1440,  # 日线信号  → 24小时
    '1d':  60 * 1440,
}

def _get_ttl(meta: dict) -> int:
    """根据meta.timeframe返回TTL秒数，默认5分钟"""
    tf = (meta or {}).get('timeframe', '')
    return _TTL_BY_TF.get(tf, TTL_SEC)

def _load():
    if PENDING.exists():
        try: return json.loads(PENDING.read_text())
        except: pass
    return []

def _save(q): PENDING.write_text(json.dumps(q, indent=2, ensure_ascii=False))


def enqueue(text: str, meta: dict = None, **kwargs) -> str:
    """将DD1推送加入等待队列，发Jarvis通知，返回task_id"""
    q = _load()
    task_id = uuid.uuid4().hex[:8]
    now = time.time()

    # 从 text 中解析数値字段，方便后续查找
    import re as _re
    _prices = _re.findall(r'\$([\d,]+(?:\.\d+)?)', text)
    _nums   = [float(p.replace(',','')) for p in _prices]
    _entry_lo = _nums[0] if len(_nums)>=1 else 0
    _entry_hi = _nums[1] if len(_nums)>=2 else 0
    _sl       = _nums[2] if len(_nums)>=3 else 0
    _tp1      = _nums[3] if len(_nums)>=4 else 0
    _tp2      = _nums[4] if len(_nums)>=5 else 0

    item = {
        'task_id':  task_id,
        'text':     text,
        'meta':     meta or {},
        'entry_lo': kwargs.get('entry_lo', _entry_lo),
        'entry_hi': kwargs.get('entry_hi', _entry_hi),
        'stop_loss':kwargs.get('stop_loss', _sl),
        'tp1':      kwargs.get('tp1', _tp1),
        'tp2':      kwargs.get('tp2', _tp2),
        'score':    kwargs.get('score', 0),
        'regime':   kwargs.get('regime', ''),
        'enqueue_ts': now,
        'expire_ts':  now + _get_ttl(meta),
        'status': 'PENDING',
    }

    # [v3.1 高频信号即时清理 · 「识别不是封禁」哲学 · 2026-06-10]
    # 旧逻辑：同标的同方向新信号才触发SUPERSEDED
    # 新逻辑：同标的任何高分新信号 → 清理低分旧信号（保持信号池新鲜）
    # 哲学：高频精神 → 旧信号不适合就即时清理，让新的更好的信号进来
    _new_sym   = (meta or {}).get('symbol', '')
    _new_dir   = (meta or {}).get('direction', '')
    _new_score = float((meta or {}).get('score', 0) or 0)
    _new_grade = float((meta or {}).get('grade', 0) or 0)
    _superseded_ids = []
    if _new_sym:
        for _old in q:
            if _old.get('status') != 'PENDING': continue
            if _old.get('task_id') == task_id: continue
            _old_sym  = _old.get('meta', {}).get('symbol', '')
            _old_dir  = _old.get('meta', {}).get('direction', '')
            _old_score= float(_old.get('meta', {}).get('score', 0) or 0)
            _old_grade= float(_old.get('meta', {}).get('grade', 0) or 0)
            if _old_sym != _new_sym: continue
            # 规则1：同标的同方向 → 直接替代（原逻辑）
            if _old_dir == _new_dir:
                _old['status'] = 'SUPERSEDED'
                _old['superseded_by'] = task_id
                _old['superseded_reason'] = 'same_dir_new_signal'
                _superseded_ids.append(_old['task_id'])
            # 规则2：同标的旧信号分低 → 新高分信号替代（高频清理）
            # 条件：新信号score比旧信号高15+，且grade更高
            elif (_new_score > _old_score + 15 and _new_grade >= _old_grade
                  and _new_grade >= 70):
                _old['status'] = 'SUPERSEDED'
                _old['superseded_by'] = task_id
                _old['superseded_reason'] = f'higher_score({_new_score:.0f}>{_old_score:.0f})'
                _superseded_ids.append(_old['task_id'])
    if _superseded_ids:
        print(f'[DD1Gate] SUPERSEDED {len(_superseded_ids)}条旧信号: {_superseded_ids} → 被{task_id}替代')

    q.append(item)
    _save(q)

    # 统计当前待确认总数
    pending_count = len([i for i in q if i['status'] == 'PENDING'])

    # Jarvis通知
    dt = datetime.fromtimestamp(now, tz=CST).strftime('%H:%M:%S')
    msg = (
        f'📬 **DD1策略群 待确认推送（队列第{pending_count}条）**\n'
        f'时间: {dt}  ID: `{task_id}`\n\n'
        f'```\n{text[:300]}\n```\n\n'
        f'⚡ **逐条确认 — 回复 `888` 发出最早一条**\n'
        f'⚠️ 每次888只发最早一条，当前队列共{pending_count}条\n'
        f'⏱ {_get_ttl(meta or {})//60}分钟内未确认自动取消'
    )
    _jarvis(msg)
    print(f'[DD1Gate] 等待确认 task_id={task_id}  队列共{pending_count}条')
    return task_id

# ─── 红线：禁止 AI 代替人工确认 ─────────────────────────────
# confirm() 只能由人工（Jarvis消息回复"888"）触发
# 任何 AI 内部代码调用 confirm() 均视为越权，直接拒绝
import inspect as _inspect

def confirm(passphrase: str, _caller: str = 'human') -> int:
    """用口令确认，每次只发送最早一条PENDING的DD1策略（FIFO严格逐条）
    ⚠️ 只允许人工通过 Jarvis 消息触发，AI 内部调用被硬锁拒绝
    ⚠️ 设计院红线 2026-05-23：禁止批量发出，一次888=一条策略
    """
    # 检测调用栈：若来自 AI 内部模块（非 jarvis_hook/inbound），拒绝
    stack = _inspect.stack()
    callers = [f.filename for f in stack[1:6]]
    # 红线：brahma_core / executor / push_hub 等内部模块禁止调用
    blocked = ['brahma_core','executor','push_hub','brahma_brain','alpha_hunter','lana']
    is_blocked = any(any(b in c for b in blocked) for c in callers)
    if is_blocked:
        print(f'[DD1Gate] ❌ 红线拦截：内部模块禁止调用 confirm()')
        return -1

    if passphrase.strip() != PASS:
        print(f'[DD1Gate] 口令错误')
        return 0

    q = _load()
    now = time.time()

    # 先清理所有过期条目
    for item in q:
        if item['status'] == 'PENDING' and now > item['expire_ts']:
            item['status'] = 'EXPIRED'

    # 取 PENDING 中最早入队的一条（FIFO）
    pending_valid = sorted(
        [i for i in q if i['status'] == 'PENDING' and now <= i.get('expire_ts', now+1)],
        key=lambda x: x.get('enqueue_ts', 0)
    )

    if not pending_valid:
        _save(q)
        _jarvis('ℹ️ DD1队列当前无待确认策略')
        print('[DD1Gate] 无待确认任务')
        return 0

    # ⚠️ 只取最早一条（FIFO）
    target = pending_valid[0]
    remaining = len(pending_valid) - 1  # 发出后剩余数量

    # 延迟import避免循环
    sys.path.insert(0, str(BASE / 'scripts'))
    from push_hub import _cfg, _dd_text

    cfg = _cfg()
    ok = _dd_text(cfg['DD1_WEBHOOK'], cfg['DD1_SECRET'], target['text'])
    target['status'] = 'SENT' if ok else 'FAILED'
    target['sent_ts'] = now

    _save(q)
    print(f'[DD1Gate] 已发送 task_id={target["task_id"]} ok={ok}  剩余{remaining}条')

    # 发完后通知剩余情况
    if ok:
        if remaining > 0:
            next_item = pending_valid[1]
            next_preview = next_item['text'][:200].replace('\n', '  ')
            _jarvis(
                f'✅ DD1已发出 `{target["task_id"]}`\n\n'
                f'📋 还有 **{remaining}** 条待确认\n'
                f'下一条预览：\n```\n{next_preview}\n```\n\n'
                f'⚡ 继续请回复 `888`｜跳过请回复 `跳过`'
            )
        else:
            _jarvis(f'✅ DD1已发出 `{target["task_id"]}`\n\n📭 队列已清空，无更多待确认策略')
    else:
        _jarvis(f'❌ DD1发送失败 task_id=`{target["task_id"]}`，请检查钉钉配置')

    return 1 if ok else 0


def skip_next() -> bool:
    """跳过下一条待确认任务（人工手动跳过）"""
    q = _load()
    now = time.time()
    pending_valid = sorted(
        [i for i in q if i['status'] == 'PENDING' and now <= i.get('expire_ts', now+1)],
        key=lambda x: x.get('enqueue_ts', 0)
    )
    if not pending_valid:
        _jarvis('ℹ️ 无待确认策略可跳过')
        return False
    target = pending_valid[0]
    target['status'] = 'SKIPPED'
    remaining = len(pending_valid) - 1
    _save(q)
    _jarvis(
        f'⏭ 已跳过 `{target["task_id"]}`\n'
        f'剩余 {remaining} 条待确认'
    )
    print(f'[DD1Gate] 跳过 task_id={target["task_id"]}  剩余{remaining}条')
    return True


def cancel_expired() -> int:
    """清理过期未确认任务，返回清理数量"""
    q = _load()
    now = time.time()
    expired = 0
    for item in q:
        if item['status'] == 'PENDING' and now > item['expire_ts']:
            item['status'] = 'EXPIRED'
            expired += 1
    _save(q)
    return expired

def status() -> dict:
    """返回队列状态摘要"""
    q = _load()
    now = time.time()
    pending = sorted(
        [i for i in q if i['status']=='PENDING' and now <= i['expire_ts']],
        key=lambda x: x.get('enqueue_ts', 0)
    )
    return {
        'pending': len(pending),
        'total': len(q),
        'next': {'id': pending[0]['task_id'], 'preview': pending[0]['text'][:80]} if pending else None,
        'items': [{'id':i['task_id'],'preview':i['text'][:60],'enqueue_ts':i.get('enqueue_ts',0)} for i in pending]
    }

if __name__ == '__main__':
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'confirm':
        pw = sys.argv[2] if len(sys.argv) > 2 else ''
        n = confirm(pw)
        print(f'已确认发送 {n} 条')
    elif cmd == 'status':
        s = status()
        print(f'待确认: {s["pending"]}条 / 总计: {s["total"]}条')
        if s.get('next'):
            print(f'  下一条: [{s["next"]["id"]}] {s["next"]["preview"]}')
        for i in s["items"]: print(f'  [{i["id"]}] {i["preview"]}')
    elif cmd == 'cancel':
        n = cancel_expired()
        print(f'清理过期 {n} 条')
    elif cmd == 'skip':
        skip_next()
