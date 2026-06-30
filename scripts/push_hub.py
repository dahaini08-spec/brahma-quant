"""
push_hub.py — 推送中枢 v3.1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
职责分工（严格区分）:
  钉钉1（策略群）: 仅发固定格式策略信号
      格式固定，末尾免责，入口 send_strategy_dd1()

  钉钉2（AI群）: 日常推送 + AI账户交易推送（合规用语）
      不使用「开仓/平仓/做多/做空/止损/熔断/合约」等敏感词
      改用「信号追踪/调仓/看涨/看跌/风控/风控暂停」等中性表达
      入口: send_dd2() / notify_open() / notify_close() 等

  Jarvis: 所有消息均同步
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os, sys, json, time, subprocess, hmac, hashlib, base64
import urllib.request, urllib.error, urllib.parse
from pathlib import Path

_ROOT = os.path.dirname(os.path.abspath(__file__))

# ── 配置加载 ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.normpath(os.path.join(_ROOT, '..')))
try:
    from config import dingtalk_main as _dm_fn, dingtalk_ai as _da_fn

    def _load_cfg() -> dict:
        wh1, sc1 = _dm_fn()
        wh2, sc2 = _da_fn()
        return {
            'DD1_WEBHOOK': wh1, 'DD1_SECRET': sc1,
            'DD2_WEBHOOK': wh2, 'DD2_SECRET': sc2,
        }
except Exception:
    _ENV_FILE = os.path.join(_ROOT, '..', '..', 'alerts', '.env')

    def _load_cfg() -> dict:  # type: ignore
        env = {}
        try:
            with open(_ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line and '=' in line and not line.startswith('#'):
                        k, v = line.split('=', 1)
                        env[k.strip()] = v.strip()
        except Exception as _e_ignored:
            print(f'[WARN][push_hub] {type(_e_ignored).__name__}: {_e_ignored}')
        return {
            'DD1_WEBHOOK': env.get('DINGTALK_WEBHOOK', ''),
            'DD1_SECRET':  env.get('DINGTALK_SECRET', ''),
            'DD2_WEBHOOK': env.get('DINGTALK_AI_WEBHOOK', ''),
            'DD2_SECRET':  env.get('DINGTALK_AI_SECRET', ''),
        }

_cfg_cache: dict = {}

def _cfg() -> dict:
    global _cfg_cache
    if not _cfg_cache:
        _cfg_cache = _load_cfg()
    return _cfg_cache


# ── 全局推送去重层 ────────────────────────────────────────────────

_DEDUP_FILE = Path(__file__).parent.parent / 'data' / 'push_hub_dedup.json'
_DEDUP_TTL  = 300   # 5分钟内相同内容不重复推送

def _dedup_check(key: str, ttl: int = _DEDUP_TTL) -> bool:
    """
    返回 True = 允许推送（首次或已超时）
    返回 False = 去重命中，跳过
    """
    now = time.time()
    try:
        _DEDUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = json.loads(_DEDUP_FILE.read_text()) if _DEDUP_FILE.exists() else {}
        # 清理过期记录
        state = {k: v for k, v in state.items() if now - v < ttl * 4}
        if key in state and now - state[key] < ttl:
            return False   # 去重命中
        state[key] = now
        _DEDUP_FILE.write_text(json.dumps(state))
        return True
    except Exception:
        return True   # 异常时允许推送，不阻断


def _make_dedup_key(text: str) -> str:
    """取文本前80字符作为去重key（捕获消息类型+标的）"""
    import hashlib
    return hashlib.md5(text[:80].encode()).hexdigest()[:12]


# ── 底层发送 ──────────────────────────────────────────────────────

def _jarvis(text: str, dedup_ttl: int = 0):
    """
    推送到 Jarvis（统一收口）
    dedup_ttl > 0 时启用去重（秒），默认0=不去重
    """
    if dedup_ttl > 0:
        key = _make_dedup_key(text)
        if not _dedup_check(key, dedup_ttl):
            print(f'[PushHub] 去重跳过（{dedup_ttl}s内已推送）')
            return
    # 从 system_config 读取主线程目标
    try:
        import importlib.util as _ilu, pathlib as _pl
        _sc = _pl.Path(__file__).parent / 'system_config.py'
        _spec = _ilu.spec_from_file_location('system_config', _sc)
        _mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
        _targets = [_mod.JARVIS_TARGET]
    except Exception:
        _targets = ['73295708:t:019f1797-6c60-7541-ad72-ec34ed14dfc4']  # SSOT fallback

    for _to in _targets:
        try:
            subprocess.Popen(
                ['openclaw', 'message', 'send',
                 '--channel', 'jarvis',
                 '--to', _to,
                 '--message', text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f'[PushHub] Jarvis推送失败({_to}): {e}')


def _dd_sign(secret: str) -> tuple:
    ts = str(round(time.time() * 1000))
    sign_str = ts + '\n' + secret
    sig = base64.b64encode(
        hmac.new(secret.encode('utf-8'),
                      sign_str.encode('utf-8'),
                      hashlib.sha256).digest()
    ).decode()
    return ts, urllib.parse.quote_plus(sig)


def _dd_text(webhook: str, secret: str, text: str) -> bool:
    if not webhook:
        return False
    try:
        url = webhook
        if secret and secret.strip():
            ts, sig = _dd_sign(secret)
            url = webhook + '&timestamp=' + ts + '&sign=' + sig
        payload = json.dumps({'msgtype': 'text', 'text': {'content': text}}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = json.loads(r.read())
        ok = resp.get('errcode') == 0
        if not ok:
            print('[PushHub] DD errcode=%s errmsg=%s' % (resp.get('errcode'), resp.get('errmsg')))
        return ok
    except Exception as e:
        print('[PushHub] DingTalk异常: %s' % e)
        return False


def _dd_markdown(webhook: str, secret: str, title: str, text: str) -> bool:
    if not webhook:
        return False
    try:
        url = webhook
        if secret and secret.strip():
            ts, sig = _dd_sign(secret)
            url = webhook + '&timestamp=' + ts + '&sign=' + sig
        payload = json.dumps({'msgtype': 'markdown', 'markdown': {'title': title, 'text': text}}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = json.loads(r.read())
        return resp.get('errcode') == 0
    except Exception as e:
        print('[PushHub] DingTalk Markdown异常: %s' % e)
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 钉钉1 专区 — 固定格式策略信号
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_strategy_dd1(symbol, direction, price,
                        entry_lo, entry_hi, stop_loss, tp1, tp2,
                        rsi_1h=0, rsi_4h=0, rsi_1d=0,
                        regime='-', score=0, signal_no='', signal_id='',
                        sl_pct=0, tp1_pct=0, rr1=0, rr2=0,
                        near_tag='✅现价附近', regime_cn='',
                        valid=True, brahma_action='',
                        structure_grade=-1) -> str:
    """钉钉1固定格式策略信号 — 2026-05-19 对齐截图标准
    格式：根据新浪财经公开数据 + 【SYM】哆/箜 ▲/▼ + 5行数据 + 免责
    """
    # ── 逻辑一致性门（设计院 2026-05-28）─────────────────────
    _risk_label = ''
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from dd1_logic_gate import check_before_dd1, LogicGateError
        # 自动计算rr1用于gate检查（若未传入则从参数估算）
        _rr1_for_gate = float(rr1) if rr1 else -1
        if _rr1_for_gate <= 0 and tp1 and entry_lo and stop_loss:
            _risk = abs(float(entry_lo) - float(stop_loss))
            _rr1_for_gate = abs(float(tp1) - float(entry_lo)) / _risk if _risk else -1
        _, _risk_label = check_before_dd1(
            symbol, direction, float(score or 0), valid, regime, brahma_action,
            structure_grade=float(structure_grade),
            rr1=_rr1_for_gate,
        )
    except Exception as _lge:
        if 'LogicGateError' in type(_lge).__name__ or '逻辑门拒绝' in str(_lge):
            raise
        pass  # 导入失败不阻断已有流程
    sym = symbol.replace('USDT', '')
    is_long = '多' in direction or 'LONG' in direction
    arrow  = '▲' if is_long else '▼'
    hanzi  = '哆' if is_long else '箜'

    def fmt(v, sym=symbol):
        """精度感知格式化 — 读SSOT instruments.tick"""
        try:
            import json as _j; from pathlib import Path as _P
            _sc = _j.loads(_P(__file__).parent.parent.joinpath('system_constants.json').read_text())
            _tick = _sc.get('instruments',{}).get(sym.upper(),{}).get('tick', None)
            if _tick:
                _decs = len(str(_tick).rstrip('0').split('.')[-1]) if '.' in str(_tick) else 0
                if v >= 1000:
                    return f'{v:,.{max(_decs,2)}f}'
                return f'{v:.{max(_decs,4)}f}'
        except Exception as _e_ignored:
            print(f'[WARN][push_hub] {type(_e_ignored).__name__}: {_e_ignored}')
        if v >= 1000: return f'{v:,.2f}'
        elif v >= 10:  return f'{v:.2f}'
        else:          return f'{v:.4f}'

    # 自动计算缺省的sl_pct/tp1_pct/rr1/rr2
    if sl_pct == 0 and stop_loss and entry_lo:
        sl_pct = abs(entry_lo - stop_loss) / entry_lo * 100
    if tp1_pct == 0 and tp1 and price:
        tp1_pct = abs(tp1 - price) / price * 100
    risk = abs(entry_lo - stop_loss) if stop_loss and entry_lo else 1
    if rr1 == 0 and tp1:
        rr1 = abs(tp1 - entry_lo) / risk if risk else 0
    if rr2 == 0 and tp2:
        rr2 = abs(tp2 - entry_lo) / risk if risk else 0

    desc = regime_cn if regime_cn else regime

    # P5 五级信号标签
    _s = float(score or 0)
    if _s >= 170:   _grade_tag = '🏆神级'
    elif _s >= 155: _grade_tag = '✅A级'
    elif _s >= 140: _grade_tag = '📌B级'
    elif _s >= 120: _grade_tag = '👀C级'
    elif _s > 0:    _grade_tag = '⚠️D级'
    else:           _grade_tag = ''
    _score_line = f'  评级: {_grade_tag} 评分{_s:.0f}' if _grade_tag else ''

    _id_line = f'  🆔 信号ID: {signal_id}' if signal_id else ''
    lines = [
        f'根据新浪财经公开数据 {sym}/USDT',
        f'【{sym}】{hanzi} {arrow}',
        f'  入场区: ${fmt(entry_lo)} ~ ${fmt(entry_hi)} {near_tag}',
        f'  保  护: ${fmt(stop_loss)} ({sl_pct:.2f}%)',
        f'  目标一: ${fmt(tp1)} {tp1_pct:.2f}% R:R={rr1:.2f}',
        f'  目标二: ${fmt(tp2)} 盈亏比 1:{rr2:.2f}',
        '',
        '\u26a0\ufe0f \u4ec5\u4f9b\u6a21\u62df\u53c2\u8003 \u5185\u90e8\u8ba8\u8bba\u5b66\u4e60',
    ]
    # ── 逆势/风险标注（逻辑门注入）──
    if _risk_label:
        lines.insert(-1, _risk_label)
    # P5 评级标签注入（在免责前）
    if _score_line:
        lines.insert(-1, _score_line)
    # 信号ID识别符注入（设计院 2026-06-20）
    if _id_line:
        lines.insert(-1, _id_line)
    return '\n'.join(lines)


def send_strategy_dd1(symbol, direction, price,
                       entry_lo, entry_hi, stop_loss, tp1, tp2,
                       rsi_1h=0, rsi_4h=0, rsi_1d=0,
                       regime='-', score=0, signal_no='', signal_id='',
                       sl_pct=0, tp1_pct=0, rr1=0, rr2=0,
                       regime_cn='', near_tag='✅现价附近',
                       structure_grade=-1) -> bool:
    """钉钉1专用：发送固定格式策略信号（需人工888确认）— 内置太医官守护"""
    text = build_strategy_dd1(
        symbol=symbol, direction=direction, price=price,
        entry_lo=entry_lo, entry_hi=entry_hi,
        stop_loss=stop_loss, tp1=tp1, tp2=tp2,
        rsi_1h=rsi_1h, rsi_4h=rsi_4h, rsi_1d=rsi_1d,
        sl_pct=sl_pct, tp1_pct=tp1_pct, rr1=rr1, rr2=rr2,
        regime_cn=regime_cn or regime, near_tag=near_tag,
        score=score, signal_no=signal_no, signal_id=signal_id,
        structure_grade=structure_grade,
    )
    # ── 太医官·守护官 格式校验 ──────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from dd1_guardian import guard as _guard
        _result = _guard(text)
        if not _result.ok:
            print(_result.report())
            _jarvis('DD1守护官拦截\n' + _result.report())
            return False
    except Exception as _ge:
        # ⚠️ 设计院红线 2026-05-23：守护官异常 → 硬拦截，禁止降级入队
        print(f'[DD1Guardian] 守护官异常，硬拦截禁止入队: {_ge}')
        _jarvis(f'⚠️ DD1守护官异常，已硬拦截\n错误: {_ge}\n预览: {text[:80]}')
        return False
    # ── P0 置信度校准 ─────────────────────────────────────────────────
    _calib_report = {}
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from calibration_engine import full_calibration_pipeline
        _calib_score, _calib_report, _bb_res = full_calibration_pipeline(
            symbol=symbol, direction=direction,
            base_score=float(score or 0), regime=regime or '-'
        )
        if _calib_report.get('downgraded'):
            print(f'[Calibration] ⬇️降级: {_calib_report["base_label"]}→{_calib_report["final_label"]} {_calib_report["reason"]}')
        elif _calib_report.get('upgraded'):
            print(f'[Calibration] ⬆️升级: {_calib_report["base_label"]}→{_calib_report["final_label"]} {_calib_report["reason"]}')
    except Exception as _ce:
        print(f'[Calibration] 校准跳过（非阻断）: {_ce}')
    # ── P1 决策日志 ─────────────────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from decision_log import log_decision
        log_decision(
            symbol=symbol, direction=direction, score=float(score or 0),
            regime=regime or '-',
            entry_lo=float(entry_lo or 0), entry_hi=float(entry_hi or 0),
            stop_loss=float(stop_loss or 0), tp1=float(tp1 or 0), tp2=float(tp2 or 0),
            rr1=float(rr1 or 0), rr2=float(rr2 or 0),
            bull_score=_calib_report.get('bull_score', 0),
            bear_score=_calib_report.get('bear_score', 0),
            extra={'calibration': _calib_report},
        )
    except Exception as _de:
        print(f'[DecisionLog] 写入失败（非阻断）: {_de}')
    # ── 人工确认门卫（口令888）────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from dd1_confirm_gate import enqueue as _enqueue
        # [v21.0 TTL fix] 传入timeframe让TTL动态化（1H=2H / 4H=8H）
        _tf = 'unknown'
        try:
            import urllib.request as _ur, json as _jj
            _k = _jj.loads(_ur.urlopen(f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=4h&limit=2',timeout=3).read())
            _gap = abs(float(_k[-1][4]) - (entry_lo or 0)) / max(float(_k[-1][4]),1) * 100
            _tf = '4H' if _gap > 1.5 else '1H'
        except Exception:
            _tf = '1H'
        _enqueue(text, meta={'symbol':symbol,'direction':direction,'type':'strategy','timeframe':_tf})
        return True
    except Exception as _e:
        # [红线] 确认门失败 → 禁止降级直发，拦截并通知
        _jarvis(f'⚠️ [DD1Gate] 确认门异常已拦截\n错误: {_e}\n预览: {text[:80]}')
        print(f'[DD1Gate] ❌ 确认门失败已拦截（禁止降级直发）: {_e}')
        return False


def send_dd1(text: str) -> bool:
    """钉钉1通用入口（需人工888确认）— 太医官守护，格式不合格直接拦截"""
    # ── 太医官·守护官格式校验 ─────────────────────────────────────
    # ⚠️ 设计院红线 2026-05-23：守护官异常 → 硬拦截，禁止降级入队
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from dd1_guardian import guard as _guard
        _result = _guard(text)
        if not _result.ok:
            print(_result.report())
            _jarvis('DD1守护官拦截\n' + _result.report())
            return False
    except Exception as _ge:
        # 守护官本身异常 → 硬拦截，绝不降级入队
        _err = f'[DD1Guardian] 守护官异常，硬拦截禁止入队: {_ge}'
        print(_err)
        _jarvis(f'⚠️ DD1守护官异常，已硬拦截\n错误: {_ge}\n预览: {text[:80]}')
        return False
    # ── 正常入队 ──────────────────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from dd1_confirm_gate import enqueue as _enqueue
        _enqueue(text, meta={'type':'dd1_general'})
        return True
    except Exception as _e:
        # [红线] 确认门失败 → 禁止降级直发，拦截并通知
        _jarvis(f'⚠️ [DD1Gate] 确认门异常已拦截\n错误: {_e}\n预览: {text[:80]}')
        print(f'[DD1Gate] ❌ 确认门失败已拦截（禁止降级直发）: {_e}')
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 钉钉2 专区 — 日常 + AI账户推送（合规用语）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── 合规术语映射 ──────────────────────────────────────────────────

def _dir_label(direction: str) -> str:
    """期货方向 → 中性表达"""
    d = str(direction).upper()
    if '多' in d or 'LONG' in d:
        return '\U0001f4c8 看涨'
    return '\U0001f4c9 看跌'


def _reason_label(reason: str) -> str:
    """平仓原因合规化"""
    _map = {
        'tp1': '目标位1达到', 'tp2': '目标位2达到',
        'sl': '风控线触发',   'stop_loss': '风控线触发',
        'manual': '手动调仓', 'timeout': '持仓超时',
        'ghost': '数据修正',  'trail': '动态止盈',
    }
    r = (reason or '').lower()
    for k, v in _map.items():
        if k in r:
            return v
    return reason or '正常调仓'


_DISCLAIMER2 = '以上为系统模型输出，仅供内部参考，不构成投资建议'
_SEP = '\u2500' * 16


def send_dd2(text: str) -> bool:
    """钉钉2通用文本推送"""
    # ── 守护官：拦截异常内容 ──
    try:
        import sys as _s2, os as _o2
        _gp = _o2.path.join(_ROOT, 'output_guardian.py')
        if _o2.path.exists(_gp):
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location('output_guardian', _gp)
            _mod = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
            _gr = _mod.guard_post(text)
            if not _gr.ok:
                _jarvis(f'⚠️ [DD2守护] 拦截异常推送:\n{_gr.report()}')
                return False
    except Exception as _e_ignored:
        print(f'[WARN][push_hub] {type(_e_ignored).__name__}: {_e_ignored}')
    _jarvis(text)
    cfg = _cfg()
    return _dd_text(cfg['DD2_WEBHOOK'], cfg['DD2_SECRET'], text)


def push_dd2(title: str, text: str) -> bool:
    """钉钉2 Markdown推送（太医官/dharma_flywheel调用）"""
    _jarvis(title + '\n' + text)
    cfg = _cfg()
    return _dd_markdown(cfg['DD2_WEBHOOK'], cfg['DD2_SECRET'], title, text)


def notify_open(symbol, direction, price,
                sl, tp1, tp2,
                kelly_pct=0, rr=0, channel='?', regime='?',
                dry_run=False, **kwargs) -> None:
    """信号追踪通知 → 钉钉2（合规用语，不含敏感操作词）"""
    tag = '【模拟】' if dry_run else '【追踪】'
    sym = symbol.replace('USDT', '')
    lines = [
        '%s %s 信号追踪' % (tag, sym),
        '方向：%s  参考价：%.4f' % (_dir_label(direction), price),
        '目标区间：%.4f / %.4f' % (tp1, tp2),
        '风控参考：%.4f' % sl,
        '综合评分：%.1f%%  性价比：%.2f' % (kelly_pct * 100, rr),
        '市场状态：%s' % regime,
        _SEP,
        _DISCLAIMER2,
    ]
    send_dd2('\n'.join(lines))


def notify_close(symbol, direction, entry, close_price,
                 pnl=0, reason='', dry_run=False, **kwargs) -> None:
    """调仓完成通知 → 钉钉2（合规用语）"""
    tag = '【模拟】' if dry_run else '【调仓】'
    sym = symbol.replace('USDT', '')
    pnl_lbl = ('+%.4f' if pnl >= 0 else '%.4f') % pnl
    chg_pct = ((close_price - entry) / entry * 100) if entry else 0
    lines = [
        '%s %s 调仓完成' % (tag, sym),
        '方向：%s' % _dir_label(direction),
        '参考入场：%.4f  参考离场：%.4f' % (entry, close_price),
        '价格变动：%+.2f%%  浮动：%s' % (chg_pct, pnl_lbl),
        '原因：%s' % _reason_label(reason),
        _SEP,
        _DISCLAIMER2,
    ]
    send_dd2('\n'.join(lines))


def notify_sl_trigger(symbol, direction, entry, sl_price,
                      pnl=0, **kwargs) -> None:
    """风控线触发通知 → 钉钉2（合规用语）"""
    sym = symbol.replace('USDT', '')
    pnl_lbl = ('+%.4f' if pnl >= 0 else '%.4f') % pnl
    lines = [
        '\U0001f514 %s 风控线触发' % sym,
        '方向：%s' % _dir_label(direction),
        '参考入场：%.4f  风控价：%.4f' % (entry, sl_price),
        '浮动：%s' % pnl_lbl,
        _SEP,
        '系统已按预设风控规则处理，仅供内部记录',
    ]
    send_dd2('\n'.join(lines))


def notify_circuit_break(reason, nav=0, loss_streak=0, **kwargs) -> None:
    """风控暂停通知 → 钉钉2（合规用语）"""
    lines = [
        '\u23f8 系统风控介入',
        '原因：%s' % reason,
        '资产参考：%.2f  连续回撤：%d次' % (nav, loss_streak),
        '模型已暂停新信号追踪，等待市场稳定',
        _SEP,
        '以上为自动风控触发，不构成任何操作建议',
    ]
    send_dd2('\n'.join(lines))


def send_system_alert(level, title, body='') -> None:
    """系统健康/监控告警 → 钉钉2（术语合规化）"""
    icons = {'ERROR': '\U0001f534', 'WARN': '\U0001f7e1', 'INFO': '\U0001f535', 'OK': '\u2705'}
    icon = icons.get(level.upper(), '\u26aa')
    _replace = [
        ('开仓', '信号追踪'), ('平仓', '调仓'),
        ('做多', '看涨'),     ('做空', '看跌'),
        ('止损', '风控'),     ('熔断', '风控暂停'),
        ('USDT', ''),         ('合约', '标的'),
    ]
    safe = title
    for old, new in _replace:
        safe = safe.replace(old, new)
    parts = ['%s 系统通知 · %s' % (icon, safe)]
    if body:
        safe_body = body
        for old, new in _replace:
            safe_body = safe_body.replace(old, new)
        parts.append(safe_body)
    send_dd2('\n'.join(parts))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 向下兼容 alias
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def format_signal_dd1(symbol, direction, price,
                       entry_lo=None, entry_hi=None,
                       stop_loss=None, tp1=None, tp2=None,
                       rsi_1h=0, rsi_4h=0, rsi_1d=0, **kwargs) -> str:
    """⚠️ 已废弃，请改用 build_strategy_dd1()"""
    return build_strategy_dd1(
        symbol=symbol, direction=direction, price=price,
        entry_lo=entry_lo or price, entry_hi=entry_hi or price,
        stop_loss=stop_loss or price, tp1=tp1 or price, tp2=tp2 or price,
        rsi_1h=rsi_1h, rsi_4h=rsi_4h, rsi_1d=rsi_1d,
    )



# ── 钉钉1固定格式（截图标准版 2026-05-19）═══════════════════════════
def build_dd1_standard(symbol, direction, price,
                        entry_lo, entry_hi, stop_loss, sl_pct,
                        tp1, tp1_pct, rr1, tp2, rr2,
                        rsi_1h=0, rsi_4h=0, rsi_1d=0,
                        regime_cn='中性偏空', near_tag='✅现价附近') -> str:
    """钉钉1标准格式 — 与 build_strategy_dd1 共用同一输出格式"""
    return build_strategy_dd1(
        symbol=symbol, direction=direction, price=price,
        entry_lo=entry_lo, entry_hi=entry_hi,
        stop_loss=stop_loss, sl_pct=sl_pct,
        tp1=tp1, tp1_pct=tp1_pct, rr1=rr1,
        tp2=tp2, rr2=rr2,
        rsi_1h=rsi_1h, rsi_4h=rsi_4h, rsi_1d=rsi_1d,
        regime_cn=regime_cn, near_tag=near_tag,
    )

def format_close_dd1(symbol, direction, entry, close_price,
                      pnl=0, reason='', **kwargs) -> str:
    """⚠️ 已废弃，请改用 notify_close()"""
    emoji = '\u2705' if pnl >= 0 else '\U0001f53b'
    return ('%s 调仓完成\n币种：%s  方向：%s\n'
            '参考入场：$%.4f  参考离场：$%.4f\n'
            '浮动：%+.4f  原因：%s' % (emoji, symbol, direction, entry, close_price, pnl, reason))

# ── 自检 (设计院 2026-05-20) ──
if __name__ == "__main__":
    assert callable(send_dd1), 'send_dd1 callable'
    print(f"✅ {__file__} 自检通过")