#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║         梵天子系统 · 达摩院 Dharma Report v2.0                   ║
║         实验报告生成器 · HTML + 文字摘要                          ║
╚══════════════════════════════════════════════════════════════════╝

用法（CLI）：
    python3 dharma_report.py             # 生成 HTML + 打印文字摘要
    python3 dharma_report.py --text      # 仅打印文字摘要
    python3 dharma_report.py --html      # 仅生成 HTML 文件

作为模块：
    from dharma.dharma_report import DharmaReport
    reporter = DharmaReport(registry=reg, session_results={})
    reporter.generate_html_report()
    reporter.generate_text_report()
"""

import os
import sys
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────
#  路径配置
# ─────────────────────────────────────────────────────────────────
DHARMA_DIR  = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(DHARMA_DIR, 'reports')
sys.path.insert(0, os.path.dirname(DHARMA_DIR))

# ─────────────────────────────────────────────────────────────────
#  HTML 模板助手
# ─────────────────────────────────────────────────────────────────
_HTML_CSS = """
<style>
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117;
         color: #c9d1d9; margin: 0; padding: 20px; }
  h1   { color: #f0a500; border-bottom: 2px solid #30363d; padding-bottom: 8px; }
  h2   { color: #58a6ff; margin-top: 30px; }
  h3   { color: #79c0ff; }
  .stat-grid { display: flex; gap: 20px; flex-wrap: wrap; margin: 20px 0; }
  .stat-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
               padding: 16px 24px; min-width: 120px; text-align: center; }
  .stat-card .num  { font-size: 2.2em; font-weight: bold; color: #f0a500; }
  .stat-card .label{ font-size: 0.85em; color: #8b949e; margin-top: 4px; }
  table { border-collapse: collapse; width: 100%; margin: 16px 0; }
  th    { background: #21262d; color: #8b949e; padding: 8px 12px;
          text-align: left; border: 1px solid #30363d; }
  td    { padding: 8px 12px; border: 1px solid #30363d; vertical-align: top; }
  tr:nth-child(even) td { background: #161b22; }
  .badge-done    { background: #1a7f37; color: #fff; border-radius: 4px;
                   padding: 2px 8px; font-size: 0.8em; }
  .badge-planned { background: #1d4ed8; color: #fff; border-radius: 4px;
                   padding: 2px 8px; font-size: 0.8em; }
  .badge-running { background: #b45309; color: #fff; border-radius: 4px;
                   padding: 2px 8px; font-size: 0.8em; }
  .badge-prod    { background: #7c3aed; color: #fff; border-radius: 4px;
                   padding: 2px 8px; font-size: 0.8em; }
  .conclusion    { color: #a5d6ff; font-style: italic; }
  .meta-footer   { color: #484f58; font-size: 0.8em; margin-top: 40px;
                   border-top: 1px solid #21262d; padding-top: 12px; }
  .suggestion-box{ background: #161b22; border: 1px solid #30363d;
                   border-left: 4px solid #f0a500; border-radius: 4px;
                   padding: 12px 16px; margin: 8px 0; }
</style>
"""


def _html_escape(s: str) -> str:
    return (s
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))


def _status_badge(status: str) -> str:
    cls_map = {
        'DONE':    'badge-done',
        'PLANNED': 'badge-planned',
        'RUNNING': 'badge-running',
    }
    return f'<span class="{cls_map.get(status, "")}">{status}</span>'


# ─────────────────────────────────────────────────────────────────
#  DharmaReport
# ─────────────────────────────────────────────────────────────────
class DharmaReport:
    """
    达摩院实验报告生成器。

    参数：
        registry       : ExperimentRegistry 实例（可选，缺省自动加载）
        session_results: DharmaRunner.session_results 字典（可选）
    """

    def __init__(
        self,
        registry: Any = None,
        session_results: Optional[Dict[str, Any]] = None,
    ):
        self.session_results = session_results or {}
        self._ts = datetime.now(timezone.utc)
        self._date_str = self._ts.strftime('%Y-%m-%d')

        # 加载/接受注册表
        if registry is not None:
            self.registry = registry
        else:
            try:
                from dharma.experiments_v2 import ExperimentRegistry
                self.registry = ExperimentRegistry()
                self.registry.load_from_soul_db()
            except ImportError:
                self.registry = None

        # 确保 reports 目录存在
        os.makedirs(REPORTS_DIR, exist_ok=True)

    # ── 公开方法 ───────────────────────────────────────────────────

    def generate_html_report(self) -> str:
        """
        生成 HTML 报告到 reports/dharma_report_{date}.html。
        返回文件路径。
        """
        path = os.path.join(REPORTS_DIR, f'dharma_report_{self._date_str}.html')
        html = self._build_html()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"  📄 HTML 报告已保存: {path}")
        return path

    def generate_text_report(self) -> str:
        """
        生成文字摘要并打印到终端。
        返回摘要字符串。
        """
        text = self._build_text()
        print(text)
        return text

    # ── HTML 构建 ──────────────────────────────────────────────────

    def _build_html(self) -> str:
        summary = self._get_summary()
        exps    = self._get_all_exps()
        prods   = [e for e in exps if e.get('promoted_to_prod')]
        done    = [e for e in exps if e.get('status') == 'DONE']
        suggestions = self._build_suggestions(summary, exps)

        ts_str = self._ts.strftime('%Y-%m-%d %H:%M UTC')

        parts = [
            '<!DOCTYPE html>',
            '<html lang="zh-CN">',
            '<head>',
            '  <meta charset="utf-8">',
            f'  <title>达摩院实验报告 · {self._date_str}</title>',
            _HTML_CSS,
            '</head>',
            '<body>',
            f'<h1>🏛️ 梵天·达摩院 实验报告</h1>',
            f'<p style="color:#8b949e">生成时间：{ts_str}</p>',
        ]

        # ── 统计卡片 ──
        parts.append('<h2>📊 实验统计</h2>')
        parts.append('<div class="stat-grid">')
        stat_items = [
            (summary.get('total',    0), '实验总数'),
            (summary.get('done',     0), '✅ 已完成'),
            (summary.get('running',  0), '🔄 进行中'),
            (summary.get('planned',  0), '📋 计划中'),
            (summary.get('promoted', 0), '🚀 已晋升'),
        ]
        for num, label in stat_items:
            parts.append(
                f'<div class="stat-card"><div class="num">{num}</div>'
                f'<div class="label">{label}</div></div>'
            )
        parts.append('</div>')

        # ── 实验总览表 ──
        parts.append('<h2>📋 实验总览</h2>')
        parts.append('<table>')
        parts.append(
            '<tr><th>ID</th><th>名称</th><th>状态</th><th>晋升生产</th>'
            '<th>相关模块</th><th>结论摘要</th></tr>'
        )
        for e in exps:
            status_html  = _status_badge(e.get('status', ''))
            promo_html   = '<span class="badge-prod">🚀 已晋升</span>' if e.get('promoted_to_prod') else '—'
            module_html  = _html_escape(e.get('related_module') or '—')
            conclusion   = e.get('conclusion') or '（尚无）'
            conclusion_h = f'<span class="conclusion">{_html_escape(conclusion)}</span>'
            parts.append(
                f'<tr><td>{_html_escape(e["id"])}</td>'
                f'<td>{_html_escape(e["name"])}</td>'
                f'<td>{status_html}</td>'
                f'<td>{promo_html}</td>'
                f'<td>{module_html}</td>'
                f'<td>{conclusion_h}</td></tr>'
            )
        parts.append('</table>')

        # ── 已验证结论 ──
        if done:
            parts.append('<h2>✅ 已验证实验结论</h2>')
            for e in done:
                parts.append(f'<h3>{_html_escape(e["id"])} · {_html_escape(e["name"])}</h3>')
                parts.append(f'<p><strong>假设：</strong>{_html_escape(e.get("hypothesis","N/A"))}</p>')
                parts.append(f'<p><strong>结论：</strong><span class="conclusion">{_html_escape(e.get("conclusion",""))}</span></p>')
                if e.get('related_module'):
                    parts.append(f'<p><strong>生产模块：</strong><code>{_html_escape(e["related_module"])}</code></p>')

        # ── 生产环境晋升记录 ──
        if prods:
            parts.append('<h2>🚀 生产环境晋升记录</h2>')
            parts.append('<table>')
            parts.append('<tr><th>ID</th><th>名称</th><th>模块</th><th>结论</th></tr>')
            for e in prods:
                parts.append(
                    f'<tr><td>{_html_escape(e["id"])}</td>'
                    f'<td>{_html_escape(e["name"])}</td>'
                    f'<td><code>{_html_escape(e.get("related_module") or "—")}</code></td>'
                    f'<td><span class="conclusion">{_html_escape(e.get("conclusion",""))}</span></td></tr>'
                )
            parts.append('</table>')

        # ── 本次 session 运行结果 ──
        if self.session_results:
            parts.append('<h2>▶ 本次运行结果</h2>')
            parts.append('<table>')
            parts.append('<tr><th>ID</th><th>状态</th><th>耗时(s)</th><th>详情</th></tr>')
            for exp_id, r in self.session_results.items():
                status = r.get('status', 'UNKNOWN')
                elapsed = r.get('elapsed_s', 0)
                msg = r.get('message') or r.get('conclusion') or '—'
                parts.append(
                    f'<tr><td>{_html_escape(exp_id)}</td>'
                    f'<td>{_status_badge(status)}</td>'
                    f'<td>{elapsed:.2f}</td>'
                    f'<td>{_html_escape(str(msg))}</td></tr>'
                )
            parts.append('</table>')

        # ── 下一步建议 ──
        parts.append('<h2>💡 下一步建议</h2>')
        for sug in suggestions:
            parts.append(f'<div class="suggestion-box">{_html_escape(sug)}</div>')

        # ── 页脚 ──
        parts.append(f'<div class="meta-footer">梵天·达摩院 · 报告生成于 {ts_str} · Python 标准库驱动</div>')
        parts.append('</body></html>')

        return '\n'.join(parts)

    # ── 文字报告构建 ───────────────────────────────────────────────

    def _build_text(self) -> str:
        summary = self._get_summary()
        exps    = self._get_all_exps()
        prods   = [e for e in exps if e.get('promoted_to_prod')]
        done    = [e for e in exps if e.get('status') == 'DONE']
        suggestions = self._build_suggestions(summary, exps)

        lines = []
        sep  = '═' * 65
        sep2 = '─' * 65

        lines.append('')
        lines.append(sep)
        lines.append('  梵天·达摩院 实验报告')
        lines.append(f'  生成时间：{self._ts.strftime("%Y-%m-%d %H:%M UTC")}')
        lines.append(sep)

        # 统计
        lines.append('')
        lines.append('  📊 实验统计')
        lines.append(sep2)
        lines.append(f"  总实验数   : {summary.get('total',0)}")
        lines.append(f"  ✅ 已完成  : {summary.get('done',0)}")
        lines.append(f"  🔄 进行中  : {summary.get('running',0)}")
        lines.append(f"  📋 计划中  : {summary.get('planned',0)}")
        lines.append(f"  🚀 已晋升  : {summary.get('promoted',0)}")

        # 已验证结论
        lines.append('')
        lines.append('  ✅ 已验证实验结论')
        lines.append(sep2)
        for e in done:
            lines.append(f"  {e['id']}  {e['name']}")
            lines.append(f"       假设：{e.get('hypothesis','N/A')}")
            lines.append(f"       结论：{e.get('conclusion','—')}")
            if e.get('related_module'):
                lines.append(f"       模块：{e['related_module']}")
            lines.append('')

        # 生产晋升
        if prods:
            lines.append('  🚀 生产环境晋升记录')
            lines.append(sep2)
            for e in prods:
                mod = e.get('related_module') or '—'
                lines.append(f"  {e['id']}  {e['name']}  [{mod}]")
            lines.append('')

        # 本次运行
        if self.session_results:
            lines.append('  ▶ 本次运行结果')
            lines.append(sep2)
            for exp_id, r in self.session_results.items():
                status  = r.get('status', '?')
                elapsed = r.get('elapsed_s', 0)
                msg     = r.get('message') or r.get('conclusion') or ''
                lines.append(f"  {exp_id}  [{status}]  {elapsed:.1f}s  {msg}")
            lines.append('')

        # 计划中
        planned_list = [e for e in exps if e.get('status') == 'PLANNED']
        if planned_list:
            lines.append('  📋 计划中实验')
            lines.append(sep2)
            for e in planned_list:
                lines.append(f"  {e['id']}  {e['name']}")
            lines.append('')

        # 建议
        lines.append('  💡 下一步建议')
        lines.append(sep2)
        for i, sug in enumerate(suggestions, 1):
            lines.append(f"  {i}. {sug}")

        lines.append('')
        lines.append(sep)
        lines.append('')

        return '\n'.join(lines)

    # ── 内部辅助 ───────────────────────────────────────────────────

    def _get_summary(self) -> Dict[str, Any]:
        if self.registry and hasattr(self.registry, 'summary'):
            return self.registry.summary()
        return {'total': 0, 'done': 0, 'running': 0, 'planned': 0, 'promoted': 0, 'conclusions': []}

    def _get_all_exps(self) -> List[Dict[str, Any]]:
        if self.registry and hasattr(self.registry, 'all'):
            return self.registry.all()
        return []

    def _build_suggestions(
        self,
        summary: Dict[str, Any],
        exps: List[Dict[str, Any]],
    ) -> List[str]:
        """根据实验状态生成下一步建议。"""
        sugs = []
        planned = [e for e in exps if e.get('status') == 'PLANNED']
        done_count = summary.get('done', 0)
        total = summary.get('total', 0)

        if planned:
            next_exp = planned[0]
            sugs.append(
                f"优先推进 {next_exp['id']}（{next_exp['name']}）："
                f"{next_exp.get('hypothesis','')}"
            )

        if done_count >= 3:
            sugs.append(
                "已有3+个实验验证完成，建议整合生产模块（signal_filter_v3.py + "
                "state_engine.py + exit_engine.py），运行端到端回测验证协同效果。"
            )

        if total - done_count > 10:
            sugs.append(
                f"仍有 {total - done_count} 个实验待完成，建议按信息增益优先级排序：EXP-04（特征重要性）→ EXP-09（跨币种泛化）→ EXP-14（多时间框架确认）。"
            )

        sugs.append("定期运行 Walk-Forward 验证（EXP-07）以检测 Alpha 衰减。")
        sugs.append("所有 PLANNED 实验完成后，运行 dharma_report.py 生成最终综合报告，并更新 DHARMA.md 实验知识库。")

        return sugs


# ─────────────────────────────────────────────────────────────────
#  CLI 入口
# ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog='dharma_report.py',
        description='梵天·达摩院实验报告生成器 v2.0',
    )
    parser.add_argument('--html', action='store_true', help='仅生成 HTML 报告')
    parser.add_argument('--text', action='store_true', help='仅打印文字摘要')
    args = parser.parse_args()

    reporter = DharmaReport()

    if args.html:
        path = reporter.generate_html_report()
        print(f"  ✅ HTML 报告：{path}")
    elif args.text:
        reporter.generate_text_report()
    else:
        # 默认：两者都生成
        path = reporter.generate_html_report()
        reporter.generate_text_report()
        print(f"  ✅ 报告完成：{path}")


if __name__ == '__main__':
    main()
