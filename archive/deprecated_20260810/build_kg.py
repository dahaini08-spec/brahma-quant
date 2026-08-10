#!/usr/bin/env python3
"""梵天知识图谱生成器 · 巨册版 · 2026-08-02"""
import json, re
from pathlib import Path
from collections import Counter

BASE = Path(__file__).parent.parent

triples_raw = json.loads((BASE / 'data/brahma_triples_raw.json').read_text())

# ── 实体标准化 ──────────────────────────────────────────────────
ALIASES = {
    'brahma_engine.py': 'brahma_engine',
    'brahma_analyze.py': 'brahma_analyze',
    'brahma_analysis_runner.py': 'brahma_analysis_runner',
    'dharma_data_bridge.py': 'dharma_data_bridge',
    'auto_executor.py': 'auto_executor',
    'signal_settler.py': 'signal_settler',
    'live_signal_log.jsonl': 'live_signal_log',
    'timing_filter.py': 'timing_filter',
    'brahma_health.py': 'brahma_health',
    'ssi_engine.py': 'ssi_engine',
    'brahma_engine.analyze(deep=True)': 'brahma_engine.analyze()',
    '1号工程': '梵天1号工程',
    '梵天系统': '梵天',
    'scripts/brahma_1hao_analysis.py': 'brahma_1hao_analysis.py',
    'scripts/rsi_structure_watcher.py': 'rsi_structure_watcher.py',
}

def norm(s):
    s = s.strip()
    return ALIASES.get(s, s)

def classify(n):
    if any(x in n for x in ['BEAR_TREND体制','BULL_TREND体制','CHOP_MID体制','BEAR_RECOVERY体制']):
        return 'regime'
    if any(x in n for x in ['死穴','BEAR_TREND_LONG','CHOP_LONG','BULL_TREND_SHORT','三禁','封禁做多','封禁空单']):
        return 'rule'
    if any(x in n for x in ['P0-2根因','P1-2根因','P1-3根因','_forced_dir被体制','UnboundLocal','324条信号','23个整点']):
        return 'bug'
    if any(x in n for x in ['P0-2修复','P1-2修复','P1-3修复','全局修复封印','封口验证封印']):
        return 'fix'
    nl = n.lower()
    if any(x in nl for x in ['engine','runner','bridge','settler','watcher','executor','bus','gate','filter','health','ssi','kronos','router','hub']):
        return 'module'
    if any(x in n for x in ['WR=','EV=','score','grade','NAV','ATR','SL=','评分','胜率','RR=']):
        return 'metric'
    if any(x in nl for x in ['torch','lgbm','lightgbm','venv','ml层','ai-ml','omniroute']):
        return 'ml'
    if any(x in n for x in ['35维','矩阵','SMC','FVG','OB新鲜度','PD Zone','流动性','Order Block','confluence']):
        return 'analysis'
    return 'other'

COLOR = {
    'regime':   '#e74c3c',
    'rule':     '#c0392b',
    'bug':      '#e67e22',
    'fix':      '#27ae60',
    'module':   '#2980b9',
    'metric':   '#f39c12',
    'ml':       '#16a085',
    'analysis': '#8e44ad',
    'other':    '#7f8c8d',
}

# ── 去重 + 标准化 ───────────────────────────────────────────────
seen = set()
unique = []
for t in triples_raw:
    s = norm(t.get('subject','') or t.get('s',''))
    p = t.get('predicate','') or t.get('p','')
    o = norm(t.get('object','') or t.get('o',''))
    if not s or not p or not o:
        continue
    key = (s, p, o)
    if key not in seen:
        seen.add(key)
        unique.append({'s': s, 'p': p, 'o': o})

# ── 构建节点 ────────────────────────────────────────────────────
nodes = {}
for t in unique:
    for name in (t['s'], t['o']):
        if name not in nodes:
            nodes[name] = {'id': len(nodes), 'type': classify(name), 'deg': 0}
        nodes[name]['deg'] += 1

def short(s, n=24):
    return s if len(s) <= n else s[:n-1]+'…'

def esc(s):
    return s.replace('"', "'").replace('\\', '/')

nodes_js = ','.join(
    '{"id":%d,"label":"%s","title":"%s","color":"%s","size":%d}' % (
        nd['id'], short(esc(name)), esc(name), COLOR.get(nd['type'], '#7f8c8d'),
        max(12, min(50, nd['deg'] * 7))
    )
    for name, nd in nodes.items()
)

edges_js = ','.join(
    '{"id":%d,"from":%d,"to":%d,"label":"%s","arrows":"to"}' % (
        i, nodes[t['s']]['id'], nodes[t['o']]['id'], esc(t['p'])
    )
    for i, t in enumerate(unique)
)

type_dist = Counter(nd['type'] for nd in nodes.values())
print(f'节点: {len(nodes)}  边: {len(unique)}')
print('类型:', dict(type_dist))

legend_items = ''.join(
    '<div class="li"><div class="ld" style="background:%s"></div>%s</div>' % (COLOR[k], v)
    for k, v in [
        ('regime','体制'), ('rule','宪法规则'), ('bug','BUG'), ('fix','修复'),
        ('module','模块'), ('metric','指标/WR'), ('ml','ML模型'), ('analysis','分析层'), ('other','其他')
    ]
)

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>梵天系统知识图谱 · 巨册版</title>
<script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,'PingFang SC',monospace;overflow:hidden}
#hd{padding:11px 18px;background:#161b22;border-bottom:1px solid #30363d;display:flex;justify-content:space-between;align-items:center}
#hd h1{font-size:15px;color:#f0f6fc;font-weight:600}
#hd .st{font-size:11px;color:#8b949e}
#lg{padding:7px 18px;background:#161b22;border-bottom:1px solid #30363d;display:flex;gap:12px;flex-wrap:wrap}
.li{display:flex;align-items:center;gap:4px;font-size:11px;color:#8b949e}
.ld{width:9px;height:9px;border-radius:50%}
#net{width:100vw;height:calc(100vh - 82px)}
#tip{position:fixed;bottom:14px;right:14px;background:#161b22;border:1px solid #30363d;border-radius:8px;padding:11px 14px;max-width:360px;font-size:12px;display:none;z-index:99;max-height:50vh;overflow-y:auto}
#tip h3{color:#58a6ff;margin-bottom:5px;font-size:13px}
.rel{margin:3px 0;color:#8b949e;line-height:1.5}
.rel .arrow{color:#3fb950}
#ctrl{position:fixed;top:56px;right:14px;display:flex;flex-direction:column;gap:5px;z-index:99}
#ctrl button{background:#21262d;border:1px solid #30363d;color:#c9d1d9;padding:5px 10px;border-radius:5px;cursor:pointer;font-size:11px}
#ctrl button:hover{background:#30363d}
</style>
</head>
<body>
<div id="hd">
  <h1>🏛️ 梵天系统知识图谱 · 巨册版</h1>
  <div class="st">STATS_PLACEHOLDER · 2026-08-02 设计院自主生成</div>
</div>
<div id="lg">LEGEND_PLACEHOLDER</div>
<div id="net"></div>
<div id="tip"><h3 id="tip-t"></h3><div id="tip-b"></div></div>
<div id="ctrl">
  <button onclick="network.fit()">🔍 全局视图</button>
  <button onclick="network.setOptions({physics:{enabled:true}});setTimeout(()=>network.setOptions({physics:{enabled:false}}),3000)">⚡ 重排</button>
</div>
<script>
const nodes=new vis.DataSet([NODES_JS]);
const edges=new vis.DataSet([EDGES_JS]);
const network=new vis.Network(document.getElementById('net'),{nodes,edges},{
  nodes:{font:{color:'#c9d1d9',size:11},borderWidth:1,borderWidthSelected:2,shape:'dot'},
  edges:{font:{color:'#8b949e',size:9,align:'middle'},
         color:{color:'#30363d',highlight:'#58a6ff',hover:'#58a6ff'},
         smooth:{type:'continuous',roundness:0.25},
         arrows:{to:{scaleFactor:0.5}}},
  physics:{stabilization:{iterations:350,fit:true},
           barnesHut:{gravitationalConstant:-5000,springLength:140,springConstant:0.025,damping:0.12}},
  interaction:{hover:true,tooltipDelay:150,zoomView:true},
});
const nm={};nodes.forEach(n=>nm[n.id]=n);
const ea=edges.get();
network.on('click',p=>{
  const tip=document.getElementById('tip');
  if(!p.nodes.length){tip.style.display='none';return;}
  const nid=p.nodes[0],n=nm[nid];
  const rs=ea.filter(e=>e.from===nid||e.to===nid);
  const html=rs.slice(0,12).map(e=>{
    const other=nm[e.from===nid?e.to:e.from];
    const dir=e.from===nid;
    return '<div class="rel">'+(dir?n.title:other.title)+' <span class="arrow">—'+e.label+'→</span> '+(dir?other.title:n.title)+'</div>';
  }).join('')+(rs.length>12?'<div style="color:#555;margin-top:3px">...还有'+(rs.length-12)+'条</div>':'');
  document.getElementById('tip-t').textContent=n.title;
  document.getElementById('tip-b').innerHTML=html;
  tip.style.display='block';
});
network.on('stabilizationIterationsDone',()=>network.setOptions({physics:{enabled:false}}));
</script>
</body>
</html>"""

HTML = HTML.replace('STATS_PLACEHOLDER', f'{len(nodes)} 节点 · {len(unique)} 关系')
HTML = HTML.replace('LEGEND_PLACEHOLDER', legend_items)
HTML = HTML.replace('NODES_JS', nodes_js)
HTML = HTML.replace('EDGES_JS', edges_js)

out = BASE / 'data/brahma_knowledge_graph.html'
out.write_text(HTML, encoding='utf-8')
print(f'✅ 已生成: {out}  ({len(HTML):,} 字节)')
