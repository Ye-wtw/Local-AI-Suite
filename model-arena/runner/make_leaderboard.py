# -*- coding: utf-8 -*-
"""make_leaderboard.py - render results/leaderboard.json into ONE self-contained HTML file.
No CDN, no server: open the file directly. Features: sortable table, weight-preset
switching (recomputes the index client-side), intelligence-vs-speed scatter.
Usage: python runner/build_report.py && python runner/make_leaderboard.py
"""
import json, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NL = chr(10)

CSS = """
:root{--bg:#0f1115;--fg:#e6e8ee;--muted:#8b93a7;--card:#171a21;--line:#262b36;--acc:#4c8bf5;--ok:#39c07a;--warn:#e0a33a;--bad:#e0574a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:32px 0 12px;color:var(--fg)}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--muted);font-weight:600;cursor:pointer;user-select:none;font-size:12.5px}
th:hover{color:var(--fg)}
tr:hover td{background:#1c202a}
.idx{font-weight:700;color:var(--acc)}
.dim{color:var(--fg)}
.dim.lo{color:var(--warn)}
.dim.hi{color:var(--ok)}
.dash{color:#4a5164}
select,button{background:#1e222b;color:var(--fg);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font-size:13px}
button{cursor:pointer}
button:hover{border-color:var(--acc)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.note{color:var(--muted);font-size:12.5px}
svg{max-width:100%}
.bar{height:6px;background:#222834;border-radius:3px;overflow:hidden;min-width:52px;display:inline-block;vertical-align:middle}
.bar>i{display:block;height:100%;background:var(--acc)}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}
code{background:#1e222b;padding:1px 5px;border-radius:4px;font-size:12.5px}
"""

JS = """
const D = DATA;
let preset = 'balanced';
const DIMS = Object.keys(D.weights.dims);
const SCORED = DIMS.filter(d => D.weights.dims[d].scored);
const LABEL = D.dim_label;

function activeWeights(){
  const p = D.weights.presets[preset];
  if (preset === 'balanced' || !p) {
    const o = {}; SCORED.forEach(d => o[d] = D.weights.dims[d].weight); return o;
  }
  const o = {}; SCORED.forEach(d => o[d] = p[d] || 0); return o;
}
function computeIndex(run){
  const w = activeWeights(); let num = 0, den = 0;
  SCORED.forEach(d => { if (run.dims[d]) { num += w[d] * run.dims[d].acc; den += w[d]; } });
  return den > 0 ? num/den : null;
}
function coverage(run){
  const w = activeWeights(); let den = 0;
  SCORED.forEach(d => { if (run.dims[d]) den += w[d]; });
  const tot = Object.values(w).reduce((a,b)=>a+b,0);
  return tot > 0 ? den/tot : 0;
}
function fmt(x, n){ return x===null||x===undefined ? '—' : (x*100).toFixed(1)+'%'; }

let sortKey = 'index', sortDir = -1;
function rows(){
  const rs = D.runs.map(r => ({...r, _idx: computeIndex(r), _cov: coverage(r)}));
  rs.sort((a,b)=>{
    let x = a[sortKey], y = b[sortKey];
    if (sortKey === 'index') { x = a._idx; y = b._idx; }
    if (sortKey === 'speed') { x = (a.speed.medium||a.speed.short||{}).tps_p50 ?? -1;
                              y = (b.speed.medium||b.speed.short||{}).tps_p50 ?? -1; }
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'string') return sortDir * x.localeCompare(y);
    return sortDir * (x - y);
  });
  return rs;
}
let refModel = null;
let viewMode = 'relative';      // 'relative' | 'absolute'
function renderTable(){
  const rs = rows();
  const dims = DIMS.filter(d => rs.some(r => r.dims[d]));
  if (!refModel || !rs.some(r => r.model === refModel)) refModel = rs.length ? rs[0].model : null;
  const refRow = rs.find(r => r.model === refModel);
  const rel = (viewMode === 'relative');
  let h = '<table><thead><tr>';
  h += '<th data-k="model">模型</th><th data-k="endpoint">端点</th>';
  h += '<th data-k="index">加权量表'+(rel?' <span class="note">参考</span>':'')+'</th><th data-k="cov">覆盖</th>';
  dims.forEach(d => h += '<th data-k="'+d+'">'+ (LABEL[d]||d) + (rel && refRow ? ' Δ' : '') +'</th>');
  h += '<th data-k="speed">输出 tok/s</th></tr></thead><tbody>';
  rs.forEach(r => {
    const sp = (r.speed.medium || r.speed.short || {}).tps_p50;
    const isRef = refRow && r.model === refRow.model;
    h += '<tr' + (rel && isRef ? ' style="background:#1f2530"' : '') + '><td>'+r.model
       + (rel && isRef ? ' <span class="note">(基准)</span>' : '') + '</td>'
       + '<td class="note">'+r.endpoint+'</td>';
    h += '<td class="idx">'+(r._idx===null?'—':(r._idx*100).toFixed(1))+'</td>';
    h += '<td class="note">'+Math.round(r._cov*100)+'%</td>';
    dims.forEach(d => {
      const v = r.dims[d];
      if (!v) { h += '<td class="dash">—</td>'; return; }
      const rv = refRow ? refRow.dims[d] : null;
      if (rel && refRow && !isRef && rv) {
        const dd = (v.acc - rv.acc) * 100;
        const cls = dd > 3 ? 'hi' : (dd < -3 ? 'lo' : '');
        h += '<td class="dim '+cls+'">'+(dd>0?'+':'')+dd.toFixed(1)+'</td>';
      } else {
        const a = v.acc, cls = a>=0.8?'hi':(a<0.6?'lo':'');
        h += '<td class="dim '+cls+'">'+fmt(a)+'</td>';
      }
    });
    h += '<td>'+(sp?sp.toFixed(1):'—')+'</td></tr>';
  });
  h += '</tbody></table>';
  if (rel && refRow) {
    h += '<div class="note" style="margin-top:10px">Δ = 相对「'+refModel+'」的百分点差(正=更好);基准行显示绝对值。<br>'
       + '<b>相对模式回答"谁更强"</b>:不受权重影响。要更严格,用 '
       + '<code>python runner/compare_models.py --level canary --a &lt;模型A&gt; --b &lt;模型B&gt;</code> 做逐题配对比较。</div>';
  } else {
    h += '<div class="note" style="margin-top:10px"><b>加权量表仅供参考</b>:权重为本项目自设计,'
       + '且跨维度平均假设各维度可比 —— 它不等价于"智力",换一组权重排名就可能翻转。<br>'
       + '判断两个模型谁更强,请切到<b>相对模式</b>并选择基准。</div>';
  }
  document.getElementById('tbl').innerHTML = h;
  document.querySelectorAll('th').forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
    renderTable();
  });
  renderScatter(rs);
}
/* Cost-quality Pareto frontier.
 *
 * Arena's HarnessTax study found harness choice barely moves task success (+-2-5%)
 * while changing cost by up to 5x. That makes "best" ill-defined without the cost
 * axis: a configuration only wins if nothing else is both better AND cheaper.
 *
 * For a local model the cost axis is throughput (tok/s), and the quality axis is the
 * weighted index. A point is on the frontier when no other point beats it on BOTH -
 * that is what "not dominated" means, and it is the honest way to present trade-offs
 * instead of collapsing them into one number.
 */
function paretoFrontier(pts, xOf, yOf, xWorse, yWorse){
  // xWorse/yWorse are +1 when a LARGER value is better, -1 when smaller is better
  return pts.filter((p,i) => !pts.some((q,j) => {
    if (i === j) return false;
    const xb = (xOf(q)-xOf(p)) * xWorse >= 0 && (xOf(q)-xOf(p)) !== 0;
    const yb = (yOf(q)-yOf(p)) * yWorse >= 0 && (yOf(q)-yOf(p)) !== 0;
    const xe = xOf(q) === xOf(p), ye = yOf(q) === yOf(p);
    // q dominates p when q is no worse on both and strictly better on at least one
    const noWorseX = xe || (xOf(q)-xOf(p)) * xWorse > 0;
    const noWorseY = ye || (yOf(q)-yOf(p)) * yWorse > 0;
    const strict = (!xe && (xOf(q)-xOf(p)) * xWorse > 0) || (!ye && (yOf(q)-yOf(p)) * yWorse > 0);
    return noWorseX && noWorseY && strict;
  }));
}

function renderScatter(rs){
  const pts = rs.filter(r => r._idx !== null && (r.speed.medium||r.speed.short));
  const W=640,H=380,P=48;
  let s = '<svg viewBox="0 0 '+W+' '+H+'" width="'+W+'" height="'+H+'">';
  s += '<rect x="0" y="0" width="'+W+'" height="'+H+'" fill="none"/>';
  s += '<line x1="'+P+'" y1="'+(H-P)+'" x2="'+(W-P)+'" y2="'+(H-P)+'" stroke="#262b36"/>';
  s += '<line x1="'+P+'" y1="'+P+'" x2="'+P+'" y2="'+(H-P)+'" stroke="#262b36"/>';
  if (!pts.length){ s += '<text x="'+(W/2)+'" y="'+(H/2)+'" fill="#8b93a7" text-anchor="middle">暂无同时含智力与速度的数据</text>'; }
  else {
    const xOf = p => (p.speed.medium||p.speed.short).tps_p50;
    const yOf = p => p._idx*100;
    const xs = pts.map(xOf), ys = pts.map(yOf);
    const x0=Math.min(...xs)*0.8, x1=Math.max(...xs)*1.1, y0=Math.min(...ys)-5, y1=Math.max(...ys)+5;
    const X = v => P + (W-2*P) * (v-x0)/((x1-x0)||1);
    const Y = v => (H-P) - (H-2*P) * (v-y0)/((y1-y0)||1);

    const front = paretoFrontier(pts, xOf, yOf, +1, +1);
    const onFront = new Set(front.map(p => p.model));

    // draw the frontier as a staircase, highest quality first
    const sorted = front.slice().sort((a,b) => xOf(a) - xOf(b));
    if (sorted.length > 1) {
      let d = '';
      sorted.forEach((p,i) => { d += (i?'L':'M') + X(xOf(p)) + ' ' + Y(yOf(p)) + ' '; });
      s += '<path d="'+d+'" fill="none" stroke="#e0a33a" stroke-width="2" stroke-dasharray="5 3" opacity="0.8"/>';
    }
    pts.forEach(p => {
      const isFront = onFront.has(p.model);
      s += '<circle cx="'+X(xOf(p))+'" cy="'+Y(yOf(p))+'" r="'+(isFront?7:5)+'" fill="'
         + (isFront?'#e0a33a':'#4c8bf5')+'" opacity="'+(isFront?0.95:0.7)+'"/>';
      s += '<text x="'+(X(xOf(p))+10)+'" y="'+(Y(yOf(p))+4)+'" fill="'
         + (isFront?'#e0a33a':'#8b93a7')+'" font-size="11">'+p.model+(isFront?' ★':'')+'</text>';
    });
    s += '<text x="'+(W/2)+'" y="'+(H-10)+'" fill="#8b93a7" font-size="12" text-anchor="middle">输出速度 tok/s (越高越好)</text>';
    s += '<text x="14" y="'+(H/2)+'" fill="#8b93a7" font-size="12" transform="rotate(-90 14 '+(H/2)+')" text-anchor="middle">智力量表</text>';
    s += '<text x="'+(P+6)+'" y="'+(P+14)+'" fill="#e0a33a" font-size="11">★ 帕累托前沿(不存在同时更快且更强的模型)</text>';
    s += '<text x="'+(P+6)+'" y="'+(P+30)+'" fill="#8b93a7" font-size="11">蓝点被前沿上的某点支配:既能更准,又能更快</text>';
  }
  s += '</svg>';
  document.getElementById('scatter').innerHTML = s;
}
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('preset');
  Object.keys(D.weights.presets).forEach(p => {
    const o = document.createElement('option'); o.value = p; o.textContent = p; sel.appendChild(o);
  });
  sel.value = preset;
  sel.onchange = () => { preset = sel.value; renderTable(); };
  document.getElementById('btn-balanced').onclick = () => { preset='balanced'; sel.value='balanced'; renderTable(); };

  // reference model selector (used by relative mode)
  const msel = document.getElementById('refmodel');
  const models = rows().map(r => r.model);
  models.forEach(m => { const o = document.createElement('option'); o.value = m; o.textContent = m; msel.appendChild(o); });
  if (models.length) refModel = msel.value = models[0];
  msel.onchange = () => { refModel = msel.value; renderTable(); };

  // mode switch: relative (default) <-> absolute
  function applyMode(){
    const rel = (viewMode === 'relative');
    document.getElementById('row-ref').style.display = rel ? '' : 'none';
    document.getElementById('mode-rel').style.borderColor = rel ? '#4c8bf5' : '';
    document.getElementById('mode-abs').style.borderColor = rel ? '' : '#4c8bf5';
    document.getElementById('mode-note').innerHTML = rel
      ? '看「谁比谁强多少」,不受权重影响'
      : '看「各自什么水平」,含自设计权重的加权量表';
    renderTable();
  }
  document.getElementById('mode-rel').onclick = () => { viewMode = 'relative'; applyMode(); };
  document.getElementById('mode-abs').onclick = () => { viewMode = 'absolute'; applyMode(); };
  applyMode();
});
"""

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>model-arena 评测榜单</title><style>__CSS__</style></head>
<body><div class="wrap">
<h1>model-arena 评测榜单</h1>
<div class="sub">生成时间 __GEN__ · 共 __N__ 个模型</div>

<div class="card">
  <div class="row">
    <span class="note">显示模式</span>
    <button id="mode-rel">相对对比(推荐)</button>
    <button id="mode-abs">绝对分数</button>
    <span class="note" id="mode-note"></span>
  </div>
  <div class="row" id="row-ref">
    <span class="note">对比基准</span>
    <select id="refmodel"></select>
    <span class="note">其余模型每维显示<b>相对差值 Δ</b>(百分点)。</span>
  </div>
  <div class="row">
    <span class="note">权重预设(影响「加权量表」)</span>
    <select id="preset"></select>
    <button id="btn-balanced">恢复 balanced</button>
  </div>
  <div id="tbl"></div>
</div>

<h2>智力 vs 速度</h2>
<div class="card"><div id="scatter"></div>
  <div class="note">帕累托理想区在右上:同等智力下速度越快越好。</div>
</div>

<h2>维度权重</h2>
<div class="card"><div id="wtab">__WTAB__</div>
  <div class="note">weight=0 表示仅诊断、不计入智力量表。</div>
</div>

<footer>
  <b>权重来源</b>:仅<b>权重比例</b>参考 Artificial Analysis 官方四大类
  (Agents 30% / Coding 20% / General 30% / Scientific 20%);
  <b>维度→基准的映射为本项目自设计</b>,不是 AA 的映射,也不是官方榜单。<br>
  覆盖率 = 该模型实际测过的计分维度权重占比;覆盖率低时智力量表参考价值有限。<br>
  数据:<code>results/leaderboard.json</code> · 重新生成:<code>python runner/make_leaderboard.py</code>
</footer>
</div>
<script>const DATA = __DATA__;
__JS__
</script></body></html>
"""


def weight_table(data):
    rows = []
    for d, v in data["weights"]["dims"].items():
        rows.append("<tr><td>%s</td><td>%s</td><td>%.2f</td><td>%s</td></tr>"
                    % (d, v["name"], v["weight"], "是" if v.get("scored") else "否(仅诊断)"))
    return ("<table><thead><tr><th>维度</th><th>名称</th><th>权重</th><th>计分</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>")


def main():
    src = os.path.join(ROOT, "results", "leaderboard.json")
    if not os.path.exists(src):
        print("leaderboard.json not found - run: python runner/build_report.py")
        return
    data = json.load(open(src, encoding="utf-8"))
    html = (HTML.replace("__CSS__", CSS)
                .replace("__GEN__", data.get("generated", ""))
                .replace("__N__", str(len(data.get("runs", []))))
                .replace("__DATA__", json.dumps(data, ensure_ascii=False))
                .replace("__JS__", JS)
                .replace("__WTAB__", weight_table(data)))
    out = os.path.join(ROOT, "leaderboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("leaderboard.html written (%.1f KB), %d models"
          % (os.path.getsize(out) / 1024, len(data.get("runs", []))))


if __name__ == "__main__":
    main()
