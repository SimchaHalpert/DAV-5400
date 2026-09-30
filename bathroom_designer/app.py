"""Bathroom designer - web form.

    python app.py

Opens a page in your browser (runs only on your computer) with dropdowns for
every choice, a live floor-plan preview, and a Build button that makes the PDF.
Projects are saved as text files in the projects/ folder.
"""

import base64
import contextlib
import io
import json
import re
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from build import build_project, load_items, load_tiles
from drawings import draw_plan, fonts, parse_color
from products import to_inches
from project import (FIXED_SIZE, FIXTURES, SECTION_HELP, SIZE_FIELDS, TEMPLATES, TILES, Room, check_layout,
                     place_items, read_project, section_fields, template_values, write_values)
from plans import read_plan
from tiles import FLOOR_DIRECTIONS, PATTERNS, WALL_DIRECTIONS
import os

HERE = Path(__file__).resolve().parent
PROJECTS = HERE / "projects"
OUTPUT = HERE / "output"
PORT = 8750
SECTIONS = ["PROJECT", "ROOM", *FIXTURES, *TILES, "SETTINGS"]

WALL_CHOICES = [["N", "N - North (top of plan)"], ["E", "E - East (right)"], ["S", "S - South (bottom)"],
                ["W", "W - West (left)"]]
CHOICES = {
    "INCLUDE": [["yes", "Yes"], ["no", "No"]],
    "WALL": WALL_CHOICES,
    "SINKS": [["1", "1"], ["2", "2"]],
    "COUNT": [["", "Auto (one per sink)"], ["1", "1"], ["2", "2"], ["3", "3"]],
    "TYPE": [["alcove", "Alcove (built-in)"], ["freestanding", "Freestanding"]],
    "TYPE@TOILET": [["one-piece", "One-piece"], ["two-piece", "Two-piece"], ["wall-hung", "Wall-hung"]],
    "WALL@TP_HOLDER": [["", "Auto (next to toilet)"]] + WALL_CHOICES,
    "SWING": [["left", "Hinge on left"], ["right", "Hinge on right"]],
    "PATTERN": [[p, p[0].upper() + p[1:]] for p in PATTERNS],
    "RENDER_WALL": [["", "Auto (vanity wall)"]] + WALL_CHOICES,
    "TEMPLATE": [[k, k.replace("_", " ")] for k in TEMPLATES],
}
COLOR_FIELDS = {"WALL_COLOR", "TRIM_COLOR", "COLOR", "GROUT"}
SUGGEST = {
    "TILE_SIZE": ["1x1", "2x2", "2x8", "2x10", "3x6", "3x12", "4x4", "4x12", "6x24", "8x48", "12x12",
                  "12x24", "24x24", "24x48", "32x32", "48x48"],
    "HEIGHT@tile": ["0", "36", "42", "48", "54", "full"],
    "GEMINI_MODEL": ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"],
}
LABELS = {"OFF_FLOOR": "Off floor (in)", "POSITION": "Position (in from left corner)", "WIDTH": "Width (in)",
          "HEIGHT": "Height (in)", "DEPTH": "Depth (in)", "OFF_WALL": "Off wall (in)", "X": "X (in from west)",
          "Y": "Y (in from north)", "TILE_SIZE": "Tile size (in)", "GEMINI_API_KEY": "Gemini API key",
          "ROOM_NAME": "Room name", "LENGTH": "Length N-S (in)", "CEILING": "Ceiling (in)",
          "MIRROR_GAP": "Mirror gap above faucet (in)", "RENDER_WALL": "Rendering looks at",
          "URL": "Product link", "DIRECTION": "Long side runs", "TYPE": "Type"}


def schema():
    sections = []
    for s in SECTIONS:
        fields = []
        for f in section_fields(s):
            spec = {"name": f, "label": LABELS.get(f, f.replace("_", " ").capitalize())}
            if s == "ROOM" and f == "WIDTH":
                spec["label"] = "Width E-W (in)"
            if f == "DIRECTION":
                opts = FLOOR_DIRECTIONS if s == "FLOOR_TILE" else WALL_DIRECTIONS
                spec["choices"] = [[o, o[0].upper() + o[1:]] for o in opts]
            elif f"{f}@{s}" in CHOICES:
                spec["choices"] = CHOICES[f"{f}@{s}"]
            elif f in CHOICES:
                spec["choices"] = CHOICES[f]
            elif f in COLOR_FIELDS:
                spec["type"] = "color"
            elif f == "GEMINI_API_KEY":
                spec["type"] = "password"
            elif f == "URL":
                spec["type"] = "url"
            key = f"{f}@tile" if (s in TILES and f == "HEIGHT") else f
            if key in SUGGEST:
                spec["suggest"] = SUGGEST[key]
            if f in SIZE_FIELDS and s in FIXTURES and s not in FIXED_SIZE:
                spec["placeholder"] = "from website"
            fields.append(spec)
        sections.append({"name": s, "title": s.replace("_", " ").title().replace("Tp ", "TP "), "help": SECTION_HELP.get(s, ""),
                         "fields": fields})
    templates = {}
    for name, t in TEMPLATES.items():
        values = template_values(name)
        values["PROJECT"]["TEMPLATE"] = name
        hints = {}
        for s in FIXTURES:
            if s in FIXED_SIZE:
                continue
            for f in SIZE_FIELDS:
                if values[s].get(f):
                    hints[f"{s}.{f}"] = f"from website, else {values[s].pop(f)}"
        templates[name] = {"about": t["about"], "values": values, "hints": hints}
    return {"sections": sections, "templates": templates}


def slug(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name or "").strip("_")
    return s or "project"


def save(name, values):
    PROJECTS.mkdir(exist_ok=True)
    path = PROJECTS / f"{slug(name)}.txt"
    write_values(path, values, values.get("PROJECT", {}).get("TEMPLATE", ""))
    return path


def preview(values):
    """Quick dimensioned floor plan (no web lookups) + layout checks."""
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        r = values.get("ROOM", {})
        room = Room(to_inches(r.get("WIDTH")) or 60, to_inches(r.get("LENGTH")) or 96,
                    to_inches(r.get("CEILING")) or 96)
        colors = {"wall": parse_color(r.get("WALL_COLOR"), (242, 239, 234)),
                  "trim": parse_color(r.get("TRIM_COLOR"), (255, 255, 255))}
        items = load_items(values, fetch=False)
        tiles = load_tiles(values, room, fetch=False)
        placed = place_items(values, items, room, values.get("SETTINGS", {}))
        notes = check_layout(room, placed)
        ppi = max(3.0, min(8.0, 560 / (max(room.width, room.length) + 60)))
        img = draw_plan(room, placed, tiles, colors, "color", ppi, fonts(15))
        line = draw_plan(room, placed, tiles, colors, "line", ppi, fonts(15))
    out = []
    for im in (img, line):
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        out.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
    return {"plans": out, "notes": notes}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if ctype == "application/json" else body.encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if url.path == "/api/schema":
            return self.send(200, schema())
        if url.path == "/api/projects":
            PROJECTS.mkdir(exist_ok=True)
            return self.send(200, sorted(p.stem for p in PROJECTS.glob("*.txt")))
        if url.path == "/api/project":
            name = parse_qs(url.query).get("name", [""])[0]
            path = PROJECTS / f"{slug(name)}.txt"
            if not path.exists():
                return self.send(404, {"error": "not found"})
            return self.send(200, read_project(path))
        if url.path.startswith("/output/"):
            f = (OUTPUT / unquote(url.path[len("/output/"):])).resolve()
            if f.parent != OUTPUT.resolve() or not f.is_file():
                return self.send(404, {"error": "not found"})
            ctype = "application/pdf" if f.suffix == ".pdf" else "image/png"
            return self.send(200, f.read_bytes(), ctype)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            data = self.body()
            if url.path == "/api/preview":
                return self.send(200, preview(data["values"]))
            if url.path == "/api/plan":
                key = data.get("key") or os.environ.get("GEMINI_API_KEY", "")
                result = read_plan(base64.b64decode(data["file"]), data.get("mime") or "application/pdf", key,
                                   room_hint=data.get("room", ""))
                return self.send(200, result)
            if url.path == "/api/save":
                path = save(data["name"], data["values"])
                return self.send(200, {"saved": path.name})
            if url.path == "/api/build":
                path = save(data["name"], data["values"])
                log = io.StringIO()
                with contextlib.redirect_stdout(log):
                    pdf, images = build_project(path, OUTPUT, no_ai=data.get("no_ai", False))
                return self.send(200, {"log": log.getvalue(), "pdf": f"/output/{pdf.name}", "name": path.stem,
                                       "images": [f"/output/{p.name}" for p in images if p.exists()]})
        except Exception as exc:
            return self.send(500, {"error": str(exc), "trace": traceback.format_exc()})
        self.send(404, {"error": "not found"})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bathroom Designer</title>
<style>
:root{--bg:#f6f4f0;--card:#fff;--ink:#23211e;--soft:#77726b;--line:#e3dfd8;--accent:#2f4a3e;--warn:#a8321f}
*{box-sizing:border-box}body{margin:0;font:14px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:14px 20px;background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
header h1{font-size:17px;margin:0 12px 0 0}
select,input{font:inherit;padding:7px 8px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);width:100%}
button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid var(--accent);background:#fff;color:var(--accent);cursor:pointer;white-space:nowrap}
button.primary{background:var(--accent);color:#fff}button:disabled{opacity:.5;cursor:wait}
.bar{display:flex;gap:8px;align-items:center}.bar select,.bar input{width:auto}
main{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:18px;padding:18px 20px}
@media(max-width:1000px){main{grid-template-columns:1fr}}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:12px}
summary{cursor:pointer;padding:12px 14px;font-weight:600;display:flex;justify-content:space-between}
summary .off{color:var(--soft);font-weight:400}
.help{color:var(--soft);font-size:12.5px;white-space:pre-line;padding:0 14px 6px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px 12px;padding:4px 14px 14px}
label{display:block;font-size:12px;color:var(--soft);margin-bottom:3px}
.wide{grid-column:1/-1}.colorrow{display:flex;gap:6px}.colorrow input[type=color]{width:42px;padding:2px}
aside{position:sticky;top:76px;align-self:start}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:12px}
.plans img{width:100%;border:1px solid var(--line);border-radius:6px;margin-bottom:8px;background:#fff}
.notes div{color:var(--warn);font-size:13px;margin:4px 0}.ok{color:#2f6b3a;font-size:13px}
pre{white-space:pre-wrap;font-size:12px;max-height:220px;overflow:auto;background:#faf9f7;border:1px solid var(--line);border-radius:6px;padding:8px}
a.pdf{display:inline-block;margin-top:8px;font-weight:600;color:var(--accent)}
.renders img{width:100%;border-radius:6px;margin-top:8px}
.changed{background:#fff6cf !important;border-color:#e0c44d !important}
.plan-view img,.plan-view object{width:100%;height:320px;object-fit:contain;border:1px solid var(--line);border-radius:6px;background:#fff}
.check div{font-size:13px;margin:3px 0;color:#7a5a00}
.row{display:flex;gap:8px;align-items:center;margin-top:8px}
</style></head><body>
<header><h1>Bathroom Designer</h1>
<div class="bar"><select id="projectList"></select><button id="openBtn">Open</button></div>
<div class="bar"><select id="templateList"></select><button id="newBtn">New from layout</button></div>
<div class="bar" style="margin-left:auto"><input id="fileName" placeholder="file name" style="width:200px">
<button id="saveBtn">Save</button></div>
</header>
<main><div id="form"></div>
<aside>
 <div class="panel"><b>Upload a plan</b>
  <div class="help" style="padding:4px 0 0">PDF, photo, scan, or hand sketch. Gemini reads the room size and fixture
  locations and fills in the form for you to check.</div>
  <div class="row"><input type="file" id="planFile" accept=".pdf,image/*"></div>
  <div class="row"><input id="planRoom" placeholder="Which room? (optional, e.g. Primary Bath)">
   <button id="planBtn">Read plan</button></div>
  <div id="planOut"></div></div>
 <div class="panel"><div class="bar" style="justify-content:space-between"><b>Floor plan preview</b><button id="prevBtn">Refresh</button></div>
  <div class="plans" id="plans" style="margin-top:10px"></div><div class="notes" id="notes"></div></div>
 <div class="panel"><label style="display:flex;gap:6px;align-items:center;color:var(--ink);font-size:13px;margin-bottom:10px">
   <input type="checkbox" id="noAi" style="width:auto"> Skip AI renderings</label>
  <button class="primary" id="buildBtn" style="width:100%">Build PDF</button>
  <div id="result"></div></div>
</aside></main>
<script>
let S=null, hints={};
const $=id=>document.getElementById(id);
function field(sec,f){
  const id=`${sec}.${f.name}`, wrap=document.createElement('div');
  if(f.name==='URL'||f.name==='GEMINI_API_KEY'||f.name==='PROJECT') wrap.className='wide';
  wrap.innerHTML=`<label for="${id}">${f.label}</label>`;
  let el;
  if(f.choices){el=document.createElement('select');for(const [v,t] of f.choices){const o=document.createElement('option');o.value=v;o.textContent=t;el.append(o)}}
  else{el=document.createElement('input');el.type=f.type==='password'?'password':(f.type==='url'?'url':'text');
    if(f.placeholder)el.placeholder=f.placeholder;
    if(f.suggest){const dl=document.createElement('datalist');dl.id=id+'-list';for(const s of f.suggest){const o=document.createElement('option');o.value=s;dl.append(o)}wrap.append(dl);el.setAttribute('list',dl.id)}}
  el.id=id;el.dataset.sec=sec;el.dataset.f=f.name;
  if(f.type==='color'){const row=document.createElement('div');row.className='colorrow';const pick=document.createElement('input');pick.type='color';
    pick.oninput=()=>{el.value=pick.value.toUpperCase();};el.oninput=()=>{if(/^#[0-9a-f]{6}$/i.test(el.value))pick.value=el.value};el._pick=pick;row.append(pick,el);wrap.append(row)}
  else wrap.append(el);
  return wrap;
}
function build(){
  const form=$('form');form.innerHTML='';
  for(const s of S.sections){
    const d=document.createElement('details');d.open=['PROJECT','ROOM'].includes(s.name);d.id='sec-'+s.name;
    d.innerHTML=`<summary><span>${s.title}</span><span class="off"></span></summary>`+(s.help?`<div class="help">${s.help}</div>`:'');
    const g=document.createElement('div');g.className='grid';for(const f of s.fields)g.append(field(s.name,f));d.append(g);form.append(d);
  }
  form.addEventListener('change',e=>{if(e.target.dataset.f==='INCLUDE')marks();schedulePreview()});
}
function setValues(v,h){
  hints=h||{};
  for(const el of document.querySelectorAll('[data-sec]')){
    const val=(v[el.dataset.sec]||{})[el.dataset.f];el.value=val??'';
    if(el.tagName==='SELECT'&&val===undefined)el.selectedIndex=0;
    if(el._pick&&/^#[0-9a-f]{6}$/i.test(el.value))el._pick.value=el.value;
    const k=`${el.dataset.sec}.${el.dataset.f}`;if(hints[k])el.placeholder=hints[k];
  }
  marks();
}
function values(){const v={};for(const el of document.querySelectorAll('[data-sec]')){(v[el.dataset.sec]??={})[el.dataset.f]=el.value.trim()}return v}
function marks(){for(const s of S.sections){const inc=document.getElementById(s.name+'.INCLUDE');const m=document.querySelector(`#sec-${s.name} .off`);if(m)m.textContent=inc&&inc.value==='no'?'not included':''}}
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
let pt=null;function schedulePreview(){clearTimeout(pt);pt=setTimeout(doPreview,500)}
async function doPreview(){try{const r=await api('/api/preview',{values:values()});
  $('plans').innerHTML=r.plans.map(s=>`<img src="${s}">`).join('');
  $('notes').innerHTML=r.notes.length?r.notes.map(n=>`<div>⚠ ${n}</div>`).join(''):'<span class="ok">✓ Layout passes clearance checks</span>';
 }catch(e){$('notes').innerHTML=`<div>${e.message}</div>`}}
function defaultName(){const p=values().PROJECT||{};return [p.CLIENT,p.ROOM_NAME].filter(Boolean).join(' ')||'project'}
async function refreshProjects(sel){const list=await api('/api/projects');$('projectList').innerHTML=list.length?list.map(n=>`<option ${n===sel?'selected':''}>${n}</option>`).join(''):'<option value="">(no saved projects)</option>'}
$('newBtn').onclick=()=>{const t=S.templates[$('templateList').value];setValues(t.values,t.hints);$('fileName').value='';doPreview()};
$('openBtn').onclick=async()=>{const n=$('projectList').value;if(!n)return;const v=await api('/api/project?name='+encodeURIComponent(n));const t=S.templates[(v.PROJECT||{}).TEMPLATE];
  const h=t?t.hints:{};setValues(v,h);$('fileName').value=n;doPreview()};
$('saveBtn').onclick=async()=>{const name=$('fileName').value||defaultName();const r=await api('/api/save',{name,values:values()});$('fileName').value=r.saved.replace(/\.txt$/,'');refreshProjects($('fileName').value)};
$('prevBtn').onclick=doPreview;
$('planFile').onchange=()=>{const f=$('planFile').files[0];if(!f)return;const url=URL.createObjectURL(f);
  $('planOut').innerHTML=`<div class="plan-view" style="margin-top:10px">${f.type==='application/pdf'?`<object data="${url}" type="application/pdf"></object>`:`<img src="${url}">`}</div>`};
$('planBtn').onclick=async()=>{const f=$('planFile').files[0];if(!f){alert('Choose a plan file first.');return}
  const b=$('planBtn');b.disabled=true;b.textContent='Reading…';
  const view=$('planOut').querySelector('.plan-view');const keep=view?view.outerHTML:'';
  try{const data=await new Promise((ok,bad)=>{const rd=new FileReader();rd.onload=()=>ok(rd.result.split(',')[1]);rd.onerror=bad;rd.readAsDataURL(f)});
    const key=(document.getElementById('SETTINGS.GEMINI_API_KEY')||{}).value||'';
    const r=await api('/api/plan',{file:data,mime:f.type||'application/pdf',room:$('planRoom').value,key});
    document.querySelectorAll('.changed').forEach(e=>e.classList.remove('changed'));
    let n=0;for(const [sec,fields] of Object.entries(r.values))for(const [k,v] of Object.entries(fields)){
      const el=document.getElementById(`${sec}.${k}`);if(!el)continue;if(el.value!==String(v)){el.value=v;el.classList.add('changed');n++;
        const d=document.getElementById('sec-'+sec);if(d)d.open=true}}
    marks();doPreview();
    $('planOut').innerHTML=keep+`<div class="check" style="margin-top:8px"><b style="color:var(--ink)">Filled in ${n} fields (highlighted in yellow). Check them against the plan.</b>
      ${r.notes.map(x=>`<div>• ${x}</div>`).join('')}</div>`;
  }catch(e){$('planOut').innerHTML=keep+`<pre>${e.message}</pre>`}
  b.disabled=false;b.textContent='Read plan'};
document.addEventListener('input',e=>e.target.classList&&e.target.classList.remove('changed'));
$('buildBtn').onclick=async()=>{const b=$('buildBtn');b.disabled=true;b.textContent='Building… (reading product sites)';$('result').innerHTML='';
  try{const name=$('fileName').value||defaultName();const r=await api('/api/build',{name,values:values(),no_ai:$('noAi').checked});
    $('fileName').value=r.name;refreshProjects(r.name);
    $('result').innerHTML=`<a class="pdf" href="${r.pdf}" target="_blank">Open PDF ↗</a><div class="renders">${r.images.map(i=>`<img src="${i}?t=${Date.now()}">`).join('')}</div><pre>${r.log.replace(/[<&]/g,c=>c==='<'?'&lt;':'&amp;')}</pre>`;
  }catch(e){$('result').innerHTML=`<pre>${e.message}</pre>`}
  b.disabled=false;b.textContent='Build PDF'};
(async()=>{S=await api('/api/schema');build();
  $('templateList').innerHTML=Object.entries(S.templates).map(([k,t])=>`<option value="${k}" title="${t.about}">${k.replace(/_/g,' ')}</option>`).join('');
  $('templateList').value='primary_bath_10x12';const t=S.templates['primary_bath_10x12'];setValues(t.values,t.hints);
  await refreshProjects();doPreview();})();
</script></body></html>"""


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Bathroom Designer running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
