#!/usr/bin/env python3
"""
📰 自动发帖 - AUTO POSTER
功能：将执行层生成的信号卡自动发布到 Binance Square
- 评分 >= 75 才发帖
- 每日帖子数限制
- 信号编号自动递增
- 发帖后记录到信号历史
"""

import requests
import json
import os
from datetime import datetime, timezone
import sys as _ap_sys
_ap_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tz_utils import now_cst_short, now_cst_date

# ─── 发布到 Square（统一轮询容错）────────────────────────────────────
import sys as _sys
# FIX: trading-system/scripts/square/__init__.py 存在会遮蔽 workspace/scripts/square
# 必须将 workspace/scripts 插入到最前面（index=0），且在任何 'scripts' 相对路径之前
_SQUARE_SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'scripts'))
# 移除所有可能遮蔽的 trading-system/scripts 条目，再插入正确路径
_sys.path = [p for p in _sys.path if not p.endswith('trading-system/scripts') and p != 'scripts']
_sys.path.insert(0, _SQUARE_SCRIPTS)
from square.poster import post_to_square as _sq_post

# ─── 钉钉1硬拦截（设计院红线 2026-05-28）────────────────────────────
# 钉钉1格式专属「根据新浪财经公开数据」开头，禁止出现在广场帖
_DD1_SIGNATURE = '根据新浪财经公开数据'

def post_to_square(content):
    # ── 红线1：钉钉1格式硬拦截 ──────────────────────────────────────
    if _DD1_SIGNATURE in (content or ''):
        import sys as _sys_dd1
        print(f'[auto_poster] 🔴 拦截：内容含钉钉1专属「{_DD1_SIGNATURE}」，禁止发广场，请走 push_hub.send_dd1() 路径')
        _sys_dd1.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            from push_hub import send_system_alert
            send_system_alert('ERROR', '❌ DD1格式误触广场路径已拦截', f'内容开头: {content[:60]}')
        except Exception:
            pass
        # ── 自动写入错误注册表（防御纵深 Layer 4）──────────────
        try:
            _sys_dd1.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from guardrails.error_registry import register_error as _reg
            _reg(
                category='routing',
                description='运行时检测：DD1内容再次尝试走广场路径',
                trigger_condition=f'content[:80]={content[:80]}',
                fix_applied='auto_poster DD1硬拦截已触发',
                test_input=content[:200],
                test_should_block=True,
                test_fn='scripts/auto_poster.py::post_to_square',
            )
        except Exception:
            pass
        return False, '', 'DD1格式禁止走广场路径'

    # ── 红线2：禁词过滤（news_formatter.clean 前置）───────────────────
    try:
        import sys as _sys_nf
        _NF_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
        if _NF_DIR not in _sys_nf.path:
            _sys_nf.path.insert(0, _NF_DIR)
        from news_formatter import clean as _nf_clean
        content = _nf_clean(content)
    except Exception as _nf_e:
        print(f'[auto_poster] ⚠️ news_formatter.clean 加载失败，跳过禁词过滤: {_nf_e}')

    result = _sq_post(content, body_text_only=True)
    if result.get("ok"):
        post_id = result.get("post_id", "")
        url = f"https://www.binance.com/square/post/{post_id}" if post_id else ""
        return True, post_id, url
    return False, "", result.get("error", "发帖失败")
SIGNALS_DIR    = os.path.join(os.path.dirname(__file__), "..", "signals")
COUNTER_FILE   = os.path.join(SIGNALS_DIR, "signal_counter.json")

MIN_SCORE_TO_POST  = 75   # 低于此分不发帖
MAX_POSTS_PER_DAY  = 8    # 每日最多发帖数

# ── 信号编号管理 ──────────────────────────────────────
def get_next_signal_no():
    """获取下一个信号编号（全局递增）"""
    os.makedirs(SIGNALS_DIR, exist_ok=True)
    try:
        with open(COUNTER_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = {"counter": 313, "today_count": 0, "last_date": ""}

    today = now_cst_date()
    if data.get("last_date") != today:
        data["today_count"] = 0
        data["last_date"]   = today

    if data["today_count"] >= MAX_POSTS_PER_DAY:
        return None, data["counter"], True  # 超出每日限制

    data["counter"]     += 1
    data["today_count"] += 1
    with open(COUNTER_FILE, "w") as f:
        json.dump(data, f)

    return f"No.{data['counter']:04d}", data["counter"], False

def get_today_count():
    try:
        with open(COUNTER_FILE, "r") as f:
            data = json.load(f)
        today = now_cst_date()
        if data.get("last_date") == today:
            return data.get("today_count", 0)
    except Exception:
        pass
    return 0

# ── 信号卡生成（带编号）────────────────────────────────
def build_kol_content(brahma_data: dict) -> list:
    """
    [新闻局 v3.0 2026-06-03] KOL 三段式帖子生成
    返回3个版本，用户选一个说「发」

    brahma_data: brahma_analyze --json 输出的标准字段
    """
    try:
        import sys as _kol_sys, os as _kol_os
        _kol_root = _kol_os.path.dirname(_kol_os.path.abspath(__file__))
        if _kol_root not in _kol_sys.path:
            _kol_sys.path.insert(0, _kol_root)
        from square.kol_templates import build_kol_post, validate_post
        versions = build_kol_post(brahma_data, n_versions=3)
        # Gate-2 验证过滤
        ok_versions = []
        for v in versions:
            passed, errs = validate_post(v)
            if passed:
                ok_versions.append(v)
            else:
                print(f'[KOL] Gate-2 过滤: {errs}')
        return ok_versions if ok_versions else versions  # 无一通过则全返回供人审阅
    except Exception as e:
        print(f'[KOL] 模板生成失败: {e}')
        return []


def build_post_content(strategy, signal_no, scan_result=None, oi_result=None):
    """
    完整信号卡，带编号和OI数据
    """
    import executor as ex
    base_card = ex.format_signal_card(strategy)

    # 替换编号占位符（北京时间）
    ts_str = now_cst_short()  # 北京时间 MM/DD HH:MM
    base_card = base_card.replace("No.XXXX", signal_no)
    # executor.py 里已用 now_cst_short() 生成时间，直接替换编号即可
    # 兜底：如果卡片里还有旧 UTC 格式，也一并修正
    base_card = base_card.replace(
        f"{signal_no} {ts_str} UTC",
        f"{signal_no} {ts_str} CST"
    )

    # 附加OI数据
    if oi_result and oi_result.get("oi_signal") in ("BULL_BUILD","SHORT_SQUEEZE"):
        oi_block = (
            f"\n📡 合约OI数据\n"
            f"OI变化(4h): {oi_result['oi_chg_4h']:+.2f}%  "
            f"| OI价值: ${oi_result['oi_cur_usd']/1e6:.1f}M\n"
            f"多空比: {oi_result['ls_ratio']:.2f}  "
            f"| 资金费率: {oi_result['funding_rate']:+.4f}%\n"
            f"信号: {oi_result['signal_detail']}"
        )
        # 插入到止损行之后
        base_card = base_card.replace(
            "\n🔑 入场触发条件",
            oi_block + "\n\n🔑 入场触发条件"
        )

    return base_card

# ── 主发帖函数 ────────────────────────────────────────
def auto_post(strategy, scan_result=None, oi_result=None, force=False):
    """
    自动发帖主函数
    force=True: 跳过评分门槛（手动触发时使用）
    返回 (success, url, signal_no)
    """
    if not strategy.get("executable"):
        return False, "策略不可执行", None

    score = strategy.get("score", 0)
    if not force and score < MIN_SCORE_TO_POST:
        return False, f"评分{score}<{MIN_SCORE_TO_POST}，不发帖", None

    # ── PostGate: 点位守门（有点位的信号必须通过）────────────────
    _entry  = float(strategy.get('entry_lo', strategy.get('entry', 0)) or 0)
    _stop   = float(strategy.get('stop_loss', strategy.get('stop', 0)) or 0)
    _tp1    = float(strategy.get('tp1', 0) or 0)
    _rr1    = float(strategy.get('rr1', 0) or 0)
    _dir    = strategy.get('direction', '')
    if _entry > 0 and _stop > 0 and _tp1 > 0:
        try:
            import os as _pg_os
            import sys as _pg_sys
            _pg_sys.path.insert(0, _pg_os.path.dirname(_pg_os.path.abspath(__file__)))
            from post_gate import gate_check as _gate
            _gate_ok, _gate_reason = _gate(strategy.get('symbol','?'), _dir,
                                           _entry, _stop, _tp1, _rr1)
            if not _gate_ok:
                print(f'[auto_poster] 🚫 PostGate拦截: {_gate_reason}')
                return False, f'PostGate拦截: {_gate_reason}', None
            print(f'[auto_poster] ✅ PostGate通过 R:R={_rr1:.1f}x')
        except Exception as _pg_e:
            print(f'[auto_poster] ⚠️ PostGate加载失败({_pg_e})，跳过守门')
    # ── PostGate结束 ─────────────────────────────────────────────

    # ── P0-3: 体制一致性校验 ─────────────────────────────────
    # 读取指挥官作战令，校验发帖内容方向与作战令一致
    try:
        import os as _os
        _combat_path = _os.path.join(_os.path.dirname(__file__), 'data', 'combat_order.json')
        import json as _json, time as _time
        _order = _json.load(open(_combat_path))
        # 作战令有效且未过期
        if _time.time() < _order.get('valid_until_ts', 0):
            _allowed = _order.get('direction_allow', ['多','空'])
            _streak  = _order.get('loss_streak', 0)
            _allow   = _order.get('allow_trade', True)
            _dir_map = {'LONG':'多','SHORT':'空','做多':'多','做空':'空'}
            _sig_dir = _dir_map.get(strategy.get('direction',''), strategy.get('direction',''))

            # 4连损时暂停信号类发帖
            if not force and _streak >= 4:
                print(f"[auto_poster] ⚠️ {_streak}连损，暂停信号发帖（休整中）")
                return False, f"{_streak}连损暂停发帖", None

            # 全局停止时暂停
            if not force and not _allow:
                print(f"[auto_poster] ⛔ 作战令全停: {_order.get('stop_reason','')}")
                return False, "作战令全停，暂停发帖", None

            # 方向不符时打印警告（不阻止，但记录）
            if _sig_dir and _allowed and _sig_dir not in _allowed:
                print(f"[auto_poster] ⚠️ 发帖方向({_sig_dir})与作战令({_allowed})不符，仍发帖但标注")
                strategy['_regime_warning'] = f'注意：当前体制偏向{"空" if "空" in _allowed else "多"}'
    except Exception:
        pass  # 作战令不可用时不阻止发帖
    # ── 体制校验结束 ─────────────────────────────────────────

    # 获取编号
    signal_no, counter, over_limit = get_next_signal_no()
    if over_limit:
        return False, f"今日发帖已达上限({MAX_POSTS_PER_DAY})", None

    # 生成内容
    content = build_post_content(strategy, signal_no, scan_result, oi_result)

    print(f"\n[{_now()}] 📰 准备发帖: {signal_no} | {strategy['symbol']}")
    print(f"{'─'*50}")
    print(content[:500] + ("..." if len(content)>500 else ""))
    print(f"{'─'*50}")

    # 发帖
    success, post_id, url_or_err = post_to_square(content)
    # ── 广场封禁时自动转发到钉钉策略频道 ──────────────────────
    if not success:
        try:
            import sys as _sys2
            _sys2.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from push_hub import send_dd2 as _send_dd2
            _dd_content = f"📡 @姓赵不宣 信号（广场暂时不可用，转发钉钉）\n\n{content[:1000]}"
            _send_dd2(_dd_content)
            print(f"  📲 广场失败，已转发钉钉2")
        except Exception as _de:
            print(f"  ⚠️ 钉钉fallback失败: {_de}")

    if success:
        print(f"[{_now()}] ✅ 发帖成功: {url_or_err}")
        # 记录到信号历史
        _record_post(strategy, signal_no, post_id, url_or_err)
        return True, url_or_err, signal_no
    else:
        print(f"[{_now()}] ❌ 发帖失败: {url_or_err}")
        return False, url_or_err, signal_no

def _record_post(strategy, signal_no, post_id, url):
    """记录发帖到信号历史"""
    try:
        import stats_tracker as st
        record = st.create_signal_record(strategy, signal_no)
        if record:
            record["post_id"]  = post_id
            record["post_url"] = url
            history = st.load_history()
            history.append(record)
            st.save_history(history)
    except Exception as e:
        print(f"  ⚠️ 记录信号历史失败: {e}")

def _now():
    from tz_utils import now_cst_str
    return now_cst_str('%Y-%m-%d %H:%M CST')

# ── 入口 ─────────────────────────────────────────────
if __name__ == "__main__":
    print("auto_poster.py — 从 main.py 或 scheduler.py 调用")
    print(f"今日已发帖: {get_today_count()}/{MAX_POSTS_PER_DAY}")
