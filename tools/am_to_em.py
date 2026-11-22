#!/usr/bin/env python3
"""Alight Motion effect XML -> engine graph XML (schema 2, native GLSL ES 3.00 passes, 16-bit float buffers).

Conventions are the app's existing ones (see GlslNative.kt): shaders see STRAIGHT alpha through texture2DCv(), the pass writes
premultiplied, acScreenNorm = gl_FragCoord.xy / size (top-left origin). Nothing is guessed silently: every effect gets a status
(ok / needs:<feature>) and a list of approximations, written into the output as <note> lines and into the build report.
"""
import re, sys, os, json, collections
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr, escape

BUILTINS = {  # name -> GLSL expression. Approximations are listed in APPROX.
    "acScreenNorm": "(gl_FragCoord.xy / size)", "acScreenSize": "size", "acLayerSize": "size",
    "acLayerNorm": "(gl_FragCoord.xy / size)", "acLayerCenter": "(size * 0.5)", "acLayerCenterNorm": "vec2(0.5)",
    "acLayerPivot": "vec2(0.0)", "acPreviewSize": "size", "acProjectSize": "size", "acTime": "time",
    "acVelocity": "vec2(0.0)", "acAngularVelocity": "0.0", "acScaleVelocity": "vec2(0.0)", "acShowGuides": "false",
    "acLayerSizeNorm": "vec2(1.0)", "acLayerToScreen": "mat4(1.0)", "acScreenToLayer": "mat4(1.0)",
}
APPROX = {"acLayerSize": "layer size = effect area", "acLayerPivot": "pivot = centre", "acVelocity": "no motion data (0)",
          "acAngularVelocity": "no motion data (0)", "acScaleVelocity": "no motion data (0)", "acShowGuides": "guides off",
          "acLayerToScreen": "identity matrix", "acScreenToLayer": "identity matrix", "acLayerSizeNorm": "1"}
PRE = """#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
uniform vec2 size;
uniform float time;
out vec4 _frag;
vec4 _out;
vec4 _amTex(sampler2D s, vec2 uv) {
  vec4 c = texture(s, uv);
  return c.a > 0.0 ? vec4(c.rgb / c.a, c.a) : c;
}
"""

def humanize(key):
    k = re.sub(r"^effect_(param_)?", "", key); k = re.sub(r"_(name|desc)$", "", k)
    return " ".join(w.capitalize() for w in k.split("_") if w)

def strip_comments(s):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", " ", s, flags=re.S))

def convert(root, fname, reg=None, depth=0):
    notes, approx, needs = [], set(), set()
    eid = root.get("id", fname)
    params = root.find("params")
    ctl, tex, decl, cast = [], [], [], {}
    for p in (params if params is not None else []):
        t, i = p.tag, p.get("id")
        if t == "texture":
            tex.append(dict(id=i, kind=p.get("srcType", "content"), src=p.get("src"), down=int(p.get("downsample", "1") or 1)))
        elif t in ("spinner",):
            ctl.append((i, p.get("default", "0"), "float")); decl.append(f"uniform float {i};")
        elif t == "selector":
            vals = [c.get("value", "") for c in p.findall("choice")]
            if not vals or not all(re.fullmatch(r"-?\d+", v) for v in vals):
                needs.add("selector-values"); continue
            d0 = p.get("default", vals[0]); di = vals.index(d0) if d0 in vals else 0
            ctl.append((i, str(di), "int")); decl.append(f"uniform float {i};")
            if vals == [str(k) for k in range(len(vals))]: cast[i] = "int"
            else:
                n_ = len(vals); decl.append(f"const int _am_map_{i}[{n_}] = int[{n_}]({','.join(vals)});"); cast[i] = ("map", n_)
        elif t == "slider":
            ctl.append((i, p.get("default", "0"), "int")); decl.append(f"uniform float {i};"); cast[i] = "int"
        elif t == "switch":
            ctl.append((i, p.get("default", "false"), "bool")); decl.append(f"uniform float {i};"); cast[i] = "bool"
        elif t == "color":
            ctl.append((i, p.get("default", "#FFFFFFFF"), "color")); decl.append(f"uniform vec4 {i};")
        elif t == "point":
            ctl.append((i, p.get("default", "0.5,0.5"), "vec2")); decl.append(f"uniform vec2 {i};")
        elif t == "xyz":
            ctl.append((i, p.get("default", "0,0,0"), "vec3")); decl.append(f"uniform vec3 {i};"); notes.append(f"control {i}: xyz type assumed vec3")
        elif t == "orient":
            ctl.append((i, p.get("default", "0,0,0"), "vec3")); decl.append(f"uniform vec3 {i};"); notes.append(f"control {i}: orient type assumed vec3")
        elif t == "hue-disc":
            ctl.append((i, p.get("default", "0,0,0"), "vec3")); decl.append(f"uniform vec3 {i};"); notes.append(f"control {i}: hue-disc type assumed vec3")
        # section / tip / string / checkbox: UI only
    ui = []
    for p in (params if params is not None else []):
        t, i = p.tag, p.get("id")
        if t not in ("spinner", "slider", "selector", "switch", "color", "point", "xyz", "orient", "hue-disc"): continue
        lab = p.get("label") or i
        m_ = re.fullmatch(r"@(?:am:)?string/(\w+)", lab)
        if m_: lab = humanize(m_.group(1))
        d = dict(id=i, label=lab, default=p.get("default", ""), min=p.get("min"), max=p.get("max"), step=p.get("step"))
        ty = p.get("type", "")
        if t in ("spinner", "slider"): d["kind"] = "angle" if ty == "angle" else "float"; d["percent"] = ty in ("percent", "relative-percent")
        elif t == "selector":
            _v = [c.get("value", "") for c in p.findall("choice")]
            d["src"] = _v; d["default"] = str(_v.index(p.get("default", "")) if p.get("default", "") in _v else 0)
            d["kind"] = "choice"; d["options"] = [humanize(c.get("label", "").split("/")[-1]) if c.get("label", "").startswith("@") else c.get("label", "") for c in p.findall("choice")]
            d["values"] = [c.get("value") for c in p.findall("choice")]
        elif t == "switch": d["kind"] = "switch"
        elif t == "color": d["kind"] = "color"
        elif t == "point": d["kind"] = "point"
        else: d["kind"] = "vec3"; needs.add("control:" + t)
        ui.append(d)
    shaders = root.findall("shader")
    frag = [s for s in shaders if s.get("type", "fragment") == "fragment"]
    if any(s.get("type") == "vertex" for s in shaders): needs.add("vertex-shader")
    sc = root.find("script")
    if sc is not None:
        sets = set(re.findall(r"\bel\.(\w+)\s*=", sc.text or ""))
        fr_groups = {s_.get("group") for s_ in shaders if s_.get("type", "fragment") == "fragment"}
        if sets <= {"shaderGroups"} and len(fr_groups) <= 1: notes.append("script only selects the shader group; the single group always runs")
        else: needs.add("script")
    if root.find("iterations") is not None or root.find("params/iterations") is not None: needs.add("iterations")
    if not frag: needs.add("no-fragment-shader"); return None, notes, approx, needs
    src = strip_comments(frag[0].text or "")
    src = re.sub(r"(?m)^\s*#\s*version.*$", "", src)
    src = re.sub(r"(?m)^\s*precision\s+\w+\s+\w+\s*;", "", src)
    src = re.sub(r"\b(lowp|mediump|highp)\s+", "", src)
    src = re.sub(r"(?m)^\s*uniform\b[^;]*;", "", src)      # controls are declared by the converter
    sampler_names = {t["id"] for t in tex}
    src = re.sub(r"\b(texture2DCv|texture2D|texture)\s*\(", "_amTex(", src)
    for n in sampler_names:
        src = re.sub(rf"\b{re.escape(n)}\.texture\b", n, src)
        src = re.sub(rf"\b{re.escape(n)}\.size\b", f"{n}_px", src)
    for n, k in cast.items():
        if isinstance(k, tuple): rep = f"_am_map_{n}[clamp(int({n}),0,{k[1] - 1})]"
        else: rep = f"(int({n}))" if k == "int" else f"({n} > 0.5)"
        src = re.sub(rf"(?<![\w.]){re.escape(n)}(?!\w)", rep, src)
    # redeclared names (function parameters like `mat4 acLTS`) must not be macros
    declared = set(re.findall(r"\b(?:mat\d|vec\d|float|int|bool)\s+(ac[A-Z]\w*)", src))
    used = [b for b in BUILTINS if re.search(rf"\b{b}\b", src) and b not in declared]
    macros = "".join(f"#define {b} {BUILTINS[b]}\n" for b in used)
    for b in used:
        if b in APPROX: approx.add(f"{b}: {APPROX[b]}")
    unknown = sorted(set(re.findall(r"\bac[A-Z]\w*", src)) - set(BUILTINS) - {"acPass"} - declared)
    for u in unknown: needs.add(f"builtin:{u}")
    m = re.search(r"\bvoid\s+main\s*\(\s*(void)?\s*\)\s*\{", src)
    if not m: needs.add("no-main"); return None, notes, approx, needs
    src = re.sub(r"\bvoid\s+main\s*\(\s*(void)?\s*\)", "void _am_main()", src).replace("gl_FragColor", "_out")
    # textures
    buffers = [t for t in tex if t["kind"] == "buffer"]
    for t in tex:
        if t["kind"] == "comp": needs.add("backdrop-texture(comp)")
        if t["kind"] == "image": needs.add("asset-texture(image)")
    content = [t for t in tex if t["kind"] == "content"]
    if not content: needs.add("no-content-texture")
    pe = root.find("passes")
    plist = [(p.get("target"), p.get("effect")) for p in pe.findall("pass")] if pe is not None else [(None, None)]
    if not plist: plist = [(None, None)]
    # passes
    written, out_passes = set(), []
    extra_buffers = []
    ctl_names = {c[0] for c in ctl}
    for k, (target, sub_id) in enumerate(plist):
        if sub_id:
            sub = (reg or {}).get(sub_id)
            if sub is None or depth >= 3: needs.add(f"sub-effect-missing({sub_id.split('.')[-1]})"); continue
            sf, sn, sa, sneeds = convert(sub, sub_id, reg, depth + 1)
            if sf is None or sneeds:
                needs.add(f"sub-effect-needs({sub_id.split('.')[-1]}:{','.join(sorted(sneeds))})"); continue
            approx |= sa
            ren = {b["id"]: f"s{k}_{b['id']}" for b in sf["buffers"]}
            for b in sf["buffers"]: extra_buffers.append(dict(b, id=ren[b["id"]]))
            consts = {i: (d, kd) for i, d, kd in sf["ctl"] if i not in ctl_names}
            for j, sp in enumerate(sf["passes"]):
                last = j == len(sf["passes"]) - 1
                ins = [(n, ("source" if r == "source" else "buffer:" + ren[r[7:]])) for n, r in sp["inputs"]]
                out = (target or "") if last else ren.get(sp["output"], sp["output"])
                out_passes.append(dict(id=f"p{k}_{j}", output=out, inputs=ins, source=sp["source"], consts=consts))
                if out: written.add(out)
            continue
        ins = []
        for t in tex:
            if t["kind"] == "content": ins.append((t["id"], "source"))
            elif t["kind"] == "buffer":
                if t["id"] != target and t["id"] in written: ins.append((t["id"], f"buffer:{t['id']}"))
                else: ins.append((t["id"], "source"))
            else: ins.append((t["id"], "source"))
        smp = "".join(f"uniform sampler2D {n};\n" for n, _ in ins)
        body = PRE + smp + "\n".join(decl) + "\n" + macros + f"#define acPass {k}\n" + src.strip() + \
            "\nvoid main() {\n  _out = vec4(0.0);\n  _am_main();\n  _frag = vec4(_out.rgb * _out.a, _out.a);\n}\n"
        out_passes.append(dict(id=f"p{k}", output=target or "", inputs=ins, source=body, consts={}))
        if target: written.add(target)
    buffers = buffers + extra_buffers
    return dict(id=eid, ui=ui, ctl=ctl, buffers=buffers, passes=out_passes, attrs={k: v for k, v in root.attrib.items() if k in ("overdraw", "max-overdraw", "maxOverdraw")}), notes, approx, needs

def const_value(d, kind):
    d = d.strip()
    if kind == "color" and d.startswith("#"):
        h = d[1:]; h = h if len(h) == 8 else "FF" + h
        a, r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4, 6))
        return f"{r:.6f},{g:.6f},{b:.6f},{a:.6f}"
    if kind == "bool": return "1" if d.lower() in ("true", "1") else "0"
    return d

def color_hex(v):
    v = v.strip()
    if v.startswith("#") and len(v) == 9: return "#" + v[7:9] + v[1:7] if False else v.lower()
    return v.lower()

def write_xml(fx, status, notes, approx, needs):
    o = ['<?xml version="1.0" encoding="UTF-8"?>']
    a = "".join(f" xml_{k.replace('-','_')}={quoteattr(v)}" for k, v in fx["attrs"].items())
    o.append(f'<effect id={quoteattr(fx["id"])} graph="2" status={quoteattr(status)}{a}>')
    for n in notes + [f"approx {x}" for x in sorted(approx)] + [f"needs {x}" for x in sorted(needs)]: o.append(f"  <note>{escape(n)}</note>")
    for u in fx["ui"]:
        at = "".join(f" {k}={quoteattr(str(u[k]))}" for k in ("min", "max", "step") if u.get(k) is not None)
        if u.get("percent"): at += ' percent="true"'
        if u["kind"] == "choice":
            vals = u.get("values") or []
            o.append(f'  <param name={quoteattr(u["id"])} label={quoteattr(u["label"])} kind="choice" value={quoteattr(u["default"])}>')
            for k_, (v, lbl) in enumerate(zip(vals, u["options"])): o.append(f'    <choice value="{k_}" src={quoteattr(v or "0")} label={quoteattr(lbl)}/>')
            o.append('  </param>')
        else: o.append(f'  <param name={quoteattr(u["id"])} label={quoteattr(u["label"])} kind={quoteattr(u["kind"])} value={quoteattr(u["default"])}{at}/>')
    for b in fx["buffers"]:
        o.append(f'  <buffer id={quoteattr(b["id"])} scale={quoteattr(str(round(1.0 / max(1, b["down"]), 6)))} format="rgba16f" precision="required" filter="linear" wrap="clamp" history="0"/>')
    for p in fx["passes"]:
        o.append(f'  <pass id={quoteattr(p["id"])} name={quoteattr(p["id"])} lang="glsl-es" output={quoteattr(p["output"])} blend="replace" clear="true">')
        o.append(f'    <source>{escape(p["source"])}</source>')
        for n, r in p["inputs"]: o.append(f'    <input name={quoteattr(n)} ref={quoteattr(r)}/>')
        for n, (d, kd) in p.get("consts", {}).items(): o.append(f'    <const name={quoteattr(n)} value={quoteattr(const_value(d, kd))}/>')
        o.append('  </pass>')
    o.append('</effect>')
    return "\n".join(o) + "\n"

def validate(fx):
    """Same rules the engine applies: every buffer written before it is read, no pass reads what it writes."""
    errs, written = [], set()
    for p in fx["passes"]:
        for n, r in p["inputs"]:
            if r.startswith("buffer:") and r[7:] not in written: errs.append(f"{p['id']} reads {r} before it is written")
            if r.startswith("buffer:") and r[7:] == p["output"]: errs.append(f"{p['id']} reads and writes {r}")
        if p["output"]: written.add(p["output"])
    return errs

def run(src, out):
    os.makedirs(out, exist_ok=True)
    global REG
    REG = {}
    for f2 in os.listdir(src):
        if f2.endswith(".xml"):
            r2 = ET.parse(os.path.join(src, f2)).getroot()
            if r2.tag == "effect": REG[r2.get("id")] = r2
    rep = collections.OrderedDict(); stat = collections.Counter(); needc = collections.Counter(); ap = collections.Counter()
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".xml"): continue
        root = ET.parse(os.path.join(src, fn)).getroot()
        if root.tag != "effect": continue
        fx, notes, approx, needs = convert(root, fn, REG)
        if fx is None: status = "needs:" + ",".join(sorted(needs))
        else:
            errs = validate(fx)
            for e in errs: needs.add("graph:" + e)
            status = "ok" if not needs else "needs:" + ",".join(sorted(needs))
            open(os.path.join(out, fn), "w", encoding="utf-8").write(write_xml(fx, status, notes, approx, needs))
        stat["ok" if status == "ok" else "needs"] += 1
        for n in needs: needc[re.sub(r"\(.*\)", "(...)", n.split(":")[0]) if n.startswith(("graph","sub-effect")) else n] += 1
        for a in approx: ap[a] += 1
        rep[fn] = status
    json.dump(rep, open(os.path.join(out, "_status.json"), "w"), indent=0)
    print(dict(stat)); print("needs:", needc.most_common(20)); print("approximations used:", ap.most_common(12))

if __name__ == "__main__": run(sys.argv[1], sys.argv[2])
