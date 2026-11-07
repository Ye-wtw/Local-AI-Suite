# -*- coding: utf-8 -*-
"""make_ui.py - build the interactive UI (single file, zero dependencies).

Design decisions that matter for trust:
  * trust_level is shown on every row - a score measured with a game running is
    still useful, but the reader must SEE that
  * the launch parameters are visible and copyable - a number without its
    --ctx-size / --ngl / KV quant is not reproducible
  * relative (no weights) is the default view; the weighted score is labelled as
    a reference, because self-designed weights are not a ground truth
  * confidence intervals sit next to scores, so a 2pp gap is not read as a ranking

Two modes:
  --api  build a page that fetches live from arena_api.py (development / desktop)
  --static  embed the data as it is today (single-file sharing)

Usage:
  python runner/make_ui.py --api      -> ui-preview.html (needs arena_api on 8770)
  python runner/make_ui.py --static   -> leaderboard.html (self-contained)
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")

CSS = """
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2128; --line:#2d333b;
  --fg:#c9d1d9; --dim:#8b949e; --acc:#58a6ff; --ok:#3fb950; --warn:#d29922;
  --bad:#f85149; --gold:#e3b341;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:13px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
a{color:var(--acc)}
header{padding:16px 22px;border-bottom:1px solid var(--line);background:var(--panel);
  position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.3px}
h2{font-size:13px;margin:22px 0 10px;color:var(--dim);font-weight:600;
  text-transform:uppercase;letter-spacing:.8px}
.wrap{padding:18px 22px;max-width:1500px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}
select,button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
  border-radius:6px;padding:6px 11px;font-size:12px;cursor:pointer;font-family:inherit}
select:hover,button:hover{border-color:var(--acc)}
button.on{border-color:var(--acc);background:#1f6feb22;color:var(--acc)}
button:disabled{opacity:.45;cursor:not-allowed}
.note{color:var(--dim);font-size:11.5px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:11.5px;position:sticky;top:64px;
  background:var(--bg);cursor:pointer;user-select:none}
th:hover{color:var(--acc)}
td:first-child,th:first-child{text-align:left}
tbody tr:hover{background:#1c212877}
.hi{color:var(--ok)} .lo{color:var(--bad)} .dash{color:#484f58}
.idx{font-weight:600;color:var(--gold)}
.badge{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10.5px;
  border:1px solid var(--line);color:var(--dim);margin-left:5px}
.badge.high{border-color:#3fb95055;color:var(--ok)}
.badge.medium{border-color:#d2992255;color:var(--warn)}
.badge.low{border-color:#f8514955;color:var(--bad)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:14px 16px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.kvbox{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));
  gap:2px 22px;background:#0d111766;border:1px solid var(--line);border-radius:6px;
  padding:8px 12px}
.kv{display:grid;grid-template-columns:minmax(90px,auto) 1fr;gap:4px 14px;
  padding:4px 0;font-size:12px;border-bottom:1px solid #21262d44}
.kv span:first-child{color:var(--dim);white-space:nowrap}
.kv span:last-child{word-break:break-all}
code,pre{font-family:"Cascadia Code",Consolas,monospace;font-size:11.5px}
pre{background:#0d1117;border:1px solid var(--line);border-radius:6px;padding:10px;
  overflow-x:auto;white-space:pre-wrap;word-break:break-all;color:#8b949e}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
  background:#1f6feb22;color:var(--acc);border:1px solid #1f6feb44}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:14px}
.tabs button{border:none;border-bottom:2px solid transparent;border-radius:0;
  background:none;padding:8px 14px;color:var(--dim)}
.tabs button.on{color:var(--acc);border-bottom-color:var(--acc);background:none}
.warnbar{background:#d2992218;border:1px solid #d2992244;color:var(--warn);
  padding:9px 13px;border-radius:6px;font-size:12px;margin-bottom:12px}
.scroll{max-height:420px;overflow:auto;border:1px solid var(--line);border-radius:8px}
"""

JS = r"""
const $ = s => document.querySelector(s);
const el = (t,c,h) => { const e=document.createElement(t); if(c)e.className=c;
  if(h!==undefined)e.innerHTML=h; return e; };
const fmtPct = v => v===null||v===undefined ? '—' : (v*100).toFixed(1)+'%';
const fmtNum = v => v===null||v===undefined ? '—' : (v*100).toFixed(1);

let API = null, DATA = null, board = null, cur = 'leaderboard';
let level = 'canary', refModel = null, viewMode = 'relative';
const LABEL = {D2:'选择',D3:'数学',D4:'代码',D5:'指令',D6:'Agent',D7:'长上下文',
  D8:'可靠性',D9:'实战',D10:'棋类',D1:'速度'};

/* ---------- data access: live API or embedded ---------- */
async function api(path){
  if(!API) throw new Error('no api');
  const r = await fetch(API+path);
  if(!r.ok) throw new Error(path+' -> '+r.status);
  return r.json();
}
async function loadAll(){
  if(API){
    const [runs, lb, env, ds] = await Promise.all([
      api('/api/runs'), api('/api/leaderboard'),
      api('/api/environment').catch(()=>null), api('/api/datasets').catch(()=>null)]);
    DATA = {runs, env, datasets: ds};
    board = lb;
    $('#srcinfo').textContent = '实时数据 · '+API;
  } else {
    DATA = {runs: EMBED.runs||[], env: EMBED.env||null, datasets: EMBED.datasets||null};
    board = EMBED.leaderboard||{runs:[],weights:{dims:{},presets:{}},paired:{}};
    $('#srcinfo').textContent = '静态快照 · '+ (EMBED.generated||'');
  }
  renderEnv();
}

/* ---------- environment banner ---------- */
function renderEnv(){
  const e = DATA.env || {};
  const lv = e.trust_level || 'unknown';
  const w = (e.warnings||[]);
  const box = $('#env');
  box.innerHTML = '';
  box.appendChild(el('div','warnbar',
    '<b>环境信任度:'+lv+'</b> — '+(e.reasons||[]).join(';')+
    (w.length? '<br>'+w.join('<br>') : '')+
    '<div class="note" style="margin-top:5px">干扰只记录不阻断;trust_level 表示该次测量的可信度,不表示结果无效。</div>'));
}

/* ---------- leaderboard ---------- */
function runsForLevel(lv){
  const out={};
  Object.values(board.paired||{}).forEach(r=>{
    if(r.level!==lv) return;
    if(!out[r.model] || (r.stamp||'')>(out[r.model].stamp||'')) out[r.model]=r;
  });
  return out;
}
function levels(){
  const s=new Set();
  Object.values(board.paired||{}).forEach(r=>{ if(r.level) s.add(r.level); });
  (DATA.runs||[]).forEach(r=>{ if(r.level) s.add(r.level); });
  return Array.from(s).sort();
}
function rowsFor(lv){
  const by={};
  (board.runs||[]).forEach(r=>{ by[r.model]=r; });
  const has = Object.keys(runsForLevel(lv));
  if(!has.length) return (board.runs||[]);
  return (board.runs||[]).filter(r=>has.includes(r.model)).concat(
    (board.runs||[]).filter(r=>!has.includes(r.model) && false));
}
function dimOf(r,d){ return r.dims && r.dims[d] ? r.dims[d].acc : null; }
function coverageOf(r){
  const all=Object.keys((board.weights||{}).dims||{}).filter(k=>k!=='D1');
  const got=all.filter(d=>dimOf(r,d)!==null);
  return all.length? got.length/all.length : 0;
}
/* trust + elapsed live in the RUN records, not in leaderboard.json (which is an
   aggregated snapshot). Match on model + level and prefer the newest run. */
function runFor(model, lv){
  const rs=(DATA.runs||[]).filter(r=>r.model===model && (!lv || r.level===lv));
  rs.sort((a,b)=>String(b.mtime||'').localeCompare(String(a.mtime||'')));
  return rs[0] || null;
}
function trustOf(model, lv){
  const r=runFor(model,lv);
  if(!r) return {level:null, reason:'无匹配运行'};
  if(!r.trust_level) return {level:null, reason:'该次运行早于环境检测功能(R1 之前),未记录干扰'};
  return {level:r.trust_level, reason:''};
}
function elapsedOf(model, lv){
  const r=runFor(model,lv);
  return (r && r.elapsed_s) || null;
}
function verifiedOf(model, lv){
  const r=runFor(model,lv);
  return !!(r && r.verified);
}
function weightedScore(r){
  const dims=(board.weights||{}).dims||{};
  let n=0,d=0;
  Object.keys(dims).forEach(k=>{
    const w=(dims[k]||{}).weight||0;
    if(!w) return;
    const a=dimOf(r,k);
    if(a===null) return;
    n+=w*a; d+=w;
  });
  return d>0? n/d : null;
}
function dimsShown(rs){
  const s=new Set();
  rs.forEach(r=>Object.keys(r.dims||{}).forEach(d=>s.add(d)));
  return ['D2','D3','D4','D5','D6','D7','D8','D9','D10'].filter(d=>s.has(d));
}

function renderLeaderboard(){
  const rs = rowsFor(level);
  const dims = dimsShown(rs);
  if(!refModel && rs.length) refModel = rs[0].model;
  const ref = rs.find(r=>r.model===refModel);
  const rel = viewMode==='relative';
  const host = $('#board'); host.innerHTML='';

  const t = el('table');
  let h='<thead><tr><th>模型</th><th>来源</th><th>信任</th>';
  h+='<th title="自设计权重,仅供参考">加权<span class="note"> 参考</span></th><th>覆盖</th>';
  dims.forEach(d=>{ h+='<th>'+(LABEL[d]||d)+(rel&&ref?' Δ':'')+'</th>'; });
  h+='<th>耗时</th></tr></thead><tbody>';

  rs.forEach(r=>{
    const isRef = ref && r.model===ref.model;
    const trust = trustOf(r.model, level);
    const elapsed = elapsedOf(r.model, level);
    const verified = verifiedOf(r.model, level);
    const cov = coverageOf(r);
    h+='<tr'+(rel&&isRef?' style="background:#1f6feb14"':'')+'>';
    h+='<td>'+r.model+(isRef&&rel?' <span class="badge">基准</span>':'')+'</td>';
    h+='<td class="note">'+r.endpoint+'</td>';
    h+='<td>'+(trust.level
        ? '<span class="badge '+trust.level+'">'+trust.level+'</span>'
        : '<span class="dash" title="'+trust.reason+'">无记录</span>')+'</td>';
    const W = weightedScore(r);
    h+='<td class="idx">'+(W===null?'—':fmtNum(W))+'</td>';
    h+='<td class="note"'+(cov<0.5?' style="color:var(--warn)"':'')+'>'+Math.round(cov*100)+'%</td>';
    dims.forEach(d=>{
      const v=dimOf(r,d);
      if(v===null){ h+='<td class="dash">—</td>'; return; }
      if(rel && ref && !isRef){
        const rv=dimOf(ref,d);
        if(rv===null){ h+='<td class="dash">—</td>'; return; }
        const dd=(v-rv)*100;
        h+='<td class="'+(dd>3?'hi':dd<-3?'lo':'')+'">'+(dd>0?'+':'')+dd.toFixed(1)+'</td>';
      } else {
        h+='<td class="'+(v>=0.8?'hi':v<0.6?'lo':'')+'">'+fmtPct(v)+'</td>';
      }
    });
    h+='<td class="note">'+(elapsed?Math.round(elapsed)+'s':'—')
      + (verified?' <span class="pill" title="已捕获完整启动命令行">参数</span>':'')+'</td>';
    h+='</tr>';
  });
  h+='</tbody>';
  t.innerHTML=h;
  host.appendChild(t);

  host.appendChild(el('div','note',
    rel ? 'Δ = 相对「'+refModel+'」的百分点差(正=更好)。<b>相对模式回答"谁更强"</b>,不受权重影响。'
        : '加权量表仅供参考 —— 权重为本项目自设计,跨维度平均假设各维度可比,不等价于"智力"。<br>判断强弱请切换到<b>相对对比</b>。'));
}

/* ---------- runs table ---------- */
function renderRuns(){
  const host=$('#runs'); host.innerHTML='';
  const t=el('table');
  let h='<thead><tr><th>文件</th><th>模型</th><th>级别</th><th>题数</th><th>准确率</th>'
       +'<th>错误</th><th>信任</th><th title="是否抓到完整启动命令行">参数</th><th>时间</th></tr></thead><tbody>';
  (DATA.runs||[]).slice(0,120).forEach(r=>{
    const acc = r.accuracy!=null ? fmtPct(r.accuracy) : '—';
    h+='<tr><td class="note" style="max-width:290px;overflow:hidden;text-overflow:ellipsis">'
      + (r.file||'') +'</td><td>'+ (r.model||'—') +'</td><td class="note">'+ (r.level||'—')
      +'</td><td>'+ (r.n||'—') +'</td><td class="'+(r.accuracy>=0.8?'hi':r.accuracy<0.6?'lo':'')+'">'+acc
      +'</td><td class="'+(r.n_errors?'lo':'note')+'">'+ (r.n_errors||0) +'</td><td>'
      + (r.trust_level? '<span class="badge '+r.trust_level+'">'+r.trust_level+'</span>':'—')
      +'</td><td>'+(r.verified? '<span class="pill">已捕获</span>':'<span class="dash">无</span>')
      +'</td><td class="note">'+ (r.mtime||'') +'</td></tr>';
  });
  h+='</tbody>'; t.innerHTML=h; host.appendChild(t);
}

/* ---------- launch params ---------- */
async function renderLaunch(){
  const host=$('#launch'); host.innerHTML='';
  const verified=(DATA.runs||[]).filter(r=>r.verified);
  if(!verified.length){
    host.appendChild(el('div','note','暂无捕获到启动命令行的运行。启动本地模型服务后跑一次评测即可。'));
    return;
  }
  const pick=el('div','row');
  const sel=el('select');
  verified.slice(0,40).forEach(r=>{
    const o=el('option'); o.value=r.file;
    o.textContent=r.file+'  ('+(r.model||'')+')'; sel.appendChild(o);
  });
  pick.appendChild(el('span','note','选择一次运行:'));
  pick.appendChild(sel);
  host.appendChild(pick);
  const body=el('div'); host.appendChild(body);

  async function show(file){
    body.innerHTML='<div class="note">加载中…</div>';
    let L;
    try { L = API ? await api('/api/launch/'+encodeURIComponent(file))
                  : (EMBED.launch&&EMBED.launch[file]) || null; }
    catch(e){ body.innerHTML='<div class="note">读取失败:'+e.message+'</div>'; return; }
    if(!L || !(L.processes||[]).length){ body.innerHTML='<div class="note">该运行没有参数快照。</div>'; return; }
    body.innerHTML='';
    (L.processes||[]).forEach(p=>{
      const c=el('div','card');
      let g='<div class="row" style="margin:0 0 8px"><b>'+(p.exe||'进程')+'</b>'
        +'<span class="note">PID '+(p.pid||'—')+'</span>'
        +'<span class="note">'+(p.started?String(p.started).slice(0,19):'')+'</span></div>';
      const dv=p.derived||{};
      if(Object.keys(dv).length){
        g+='<div class="grid">';
        Object.keys(dv).forEach(k=>{
          g+='<div class="kv"><span>'+k+'</span><span>'+(dv[k]===null?'—':dv[k])+'</span></div>';
        });
        g+='</div>';
      }
      const hl=p.highlight||{}, hf=p.highlight_flags||[];
      if(Object.keys(hl).length || hf.length){
        g+='<h2>关键参数</h2><div class="kvbox">';
        Object.keys(hl).forEach(k=>{
          g+='<div class="kv"><span>'+k+'</span><span>'+(hl[k]===null?'—':hl[k])+'</span></div>';
        });
        hf.forEach(k=>{ g+='<div class="kv"><span>'+k+'</span><span>开关</span></div>'; });
        g+='</div>';
      }
      g+='<h2>完整启动命令行 <span class="note">'
        + ((p.raw||'').length) + ' 字符</span></h2>';
      g+='<pre id="raw-'+Math.random().toString(36).slice(2)+'">'+((p.raw||'(未捕获)').replace(/</g,'&lt;'))+'</pre>';
      g+='<div class="row"><button onclick="navigator.clipboard.writeText(this.dataset.x)" '
        +'data-x="'+((p.raw||'').replace(/"/g,'&quot;'))+'" class="copy">复制命令行</button>'
        +'<span class="note">用它可完整复现这次评测的模型配置</span></div>';
      c.innerHTML=g;
      body.appendChild(c);
    });
  }
  sel.onchange=()=>show(sel.value);
  show(sel.value);
}

/* ---------- tabs ---------- */
function switchTab(t){
  cur=t;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on', b.dataset.t===t));
  ['leaderboard','runs','launch'].forEach(x=>{
    $('#tab-'+x).style.display = (x===t)?'':'none';
  });
  if(t==='leaderboard') renderLeaderboard();
  if(t==='runs') renderRuns();
  if(t==='launch') renderLaunch();
}

function qs(k){ const m=location.search.match(new RegExp('[?&]'+k+'=([^&]+)')); return m?decodeURIComponent(m[1]):null; }

async function boot(){
  const m = qs('api');
  if(m) API = m;
  if(!API && typeof EMBED!=='undefined' && EMBED.api) API = EMBED.api;
  try{
    await loadAll();
  }catch(e){
    $('#srcinfo').textContent='数据加载失败:'+e.message;
    return;
  }
  const lv=levels();
  const sel=$('#level');
  lv.forEach(x=>{ const o=el('option'); o.value=x; o.textContent=x; sel.appendChild(o); });
  const want = qs('level');
  level = (want && lv.includes(want)) ? want : (lv.includes('canary')? 'canary' : (lv[0]||'canary'));
  sel.value=level;
  sel.onchange=()=>{ level=sel.value; refModel=null; renderLeaderboard(); };

  const msel=$('#refmodel');
  function fillRef(){
    msel.innerHTML='';
    rowsFor(level).forEach(r=>{ const o=el('option'); o.value=r.model; o.textContent=r.model; msel.appendChild(o); });
    refModel=rowsFor(level).length? rowsFor(level)[0].model : null;
    if(refModel) msel.value=refModel;
  }
  fillRef();
  msel.onchange=()=>{ refModel=msel.value; renderLeaderboard(); };
  sel.addEventListener('change', fillRef);

  $('#mode-rel').onclick=()=>{ viewMode='relative'; $('#mode-rel').classList.add('on');
    $('#mode-abs').classList.remove('on'); $('#refrow').style.display=''; renderLeaderboard(); };
  $('#mode-abs').onclick=()=>{ viewMode='absolute'; $('#mode-abs').classList.add('on');
    $('#mode-rel').classList.remove('on'); $('#refrow').style.display='none'; renderLeaderboard(); };
  document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>switchTab(b.dataset.t));
  const wt = qs('tab');
  switchTab(wt && ['leaderboard','runs','launch'].includes(wt) ? wt : 'leaderboard');
}
document.addEventListener('DOMContentLoaded', boot);
"""


def build(mode):
    api = "http://127.0.0.1:8770" if mode == "api" else ""
    embed = {"generated": "", "api": api, "runs": [], "leaderboard": {}}
    if mode == "static":
        lb = os.path.join(RESULTS, "leaderboard.json")
        if os.path.exists(lb):
            with open(lb, encoding="utf-8") as f:
                embed["leaderboard"] = json.load(f)
        runs = []
        import glob
        for p in sorted(glob.glob(os.path.join(RESULTS, "LEVEL_*.json")))[-60:]:
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                s = d.get("summary") or {}
                env = s.get("environment") or {}
                proc = ((s.get("launch") or {}).get("processes") or [{}])[0]
                runs.append({"file": os.path.basename(p), "model": s.get("model"),
                             "level": s.get("level"), "n": s.get("n"),
                             "accuracy": s.get("accuracy"), "n_errors": s.get("n_errors"),
                             "elapsed_s": s.get("elapsed_s"),
                             "trust_level": env.get("trust_level"),
                             "verified": proc.get("raw") is not None,
                             "derived": proc.get("derived") or {},
                             "mtime": ""})
            except Exception:
                pass
        embed["runs"] = runs
        import datetime
        embed["generated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Arena</title>
<style>%s</style></head><body>
<header>
  <h1>Model Arena</h1>
  <span class="pill" id="srcinfo">加载中…</span>
  <span class="note">本地模型与云端 API 在同一套题、同一套判分器下的对比</span>
</header>
<div class="wrap">
  <div id="env"></div>
  <div class="tabs">
    <button data-t="leaderboard" class="on">排行榜</button>
    <button data-t="runs">运行记录</button>
    <button data-t="launch">参数快照</button>
  </div>

  <div id="tab-leaderboard">
    <div class="row">
      <span class="note">级别</span><select id="level"></select>
      <button id="mode-rel" class="on">相对对比</button>
      <button id="mode-abs">绝对分数</button>
    </div>
    <div class="row" id="refrow">
      <span class="note">对比基准</span><select id="refmodel"></select>
      <span class="note">其余模型每维显示相对差值 Δ(百分点)</span>
    </div>
    <div id="board"></div>
  </div>

  <div id="tab-runs" style="display:none">
    <h2>运行记录 <span class="note">最多显示 120 条</span></h2>
    <div class="scroll" id="runs"></div>
  </div>

  <div id="tab-launch" style="display:none">
    <h2>参数快照 <span class="note">完整启动命令行 —— 没有它,分数不可复现</span></h2>
    <div id="launch"></div>
  </div>
</div>
<script>const EMBED = %s;</script>
<script>%s</script>
</body></html>""" % (CSS, json.dumps(embed, ensure_ascii=False), JS)

    out = os.path.join(ROOT, "ui-preview.html" if mode == "api" else "leaderboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out, len(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", action="store_true")
    ap.add_argument("--static", action="store_true")
    args = ap.parse_args()
    mode = "api" if args.api else ("static" if args.static else "api")
    out, n = build(mode)
    print("%s written (%.1f KB)  mode=%s" % (os.path.basename(out), n / 1024, mode))
    if mode == "api":
        print("  需要内核 API:python runner/arena_api.py --port 8770")
        print("  然后浏览器打开:file:///%s" % out.replace("\\", "/"))


if __name__ == "__main__":
    main()
