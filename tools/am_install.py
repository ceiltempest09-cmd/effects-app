#!/usr/bin/env python3
"""Install the Alight Motion effect library (aligthmotion/ at the repo root) into the app's assets.

What it does (all of it was missing before, so aligthmotion.zip showed nothing):
  1. Reads every effect .json in aligthmotion/ (the zip's own index.json is ignored: it listed files that are not in the zip).
  2. Repairs what the app's importer would reject (pass/buffer ids longer than 48 characters or with illegal characters).
  3. Fills what is missing: unbounded 3D rotate/position sliders get a range, empty descriptions get one.
  4. Checks each effect with the same rules as the app (GraphJson.parse + GraphValidator.validate) and skips, with a reason, any it would refuse.
  5. Copies the effects to Effects/shared/assets/alightmotion/ and writes a correct index.json there (also lists .xml files that
     tools/fxconvert.py already put in that folder, so nothing it made is lost).
  6. Puts every effect's picture at Effects/shared/assets/effects/thumb/am_<id>.webp, the exact place the app looks.
     Pictures are found by id, file name or any case; png/jpg are converted; an effect with no picture gets a generated card, never a blank.
Run from the repo root:  python3 tools/am_install.py   (needs Pillow; the workflow installs it)
"""
import hashlib, json, os, re, shutil, sys, collections

import json, os, re, sys, collections

ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,47}$')
LANGS = {"sksl", "glsl", "glsl-es", "glsl-vk", "spirv"}
NATIVE_LANGS = {"sksl", "glsl-es", "glsl-vk", "spirv"}
BLENDS = {"replace", "over", "add", "multiply", "screen"}
FORMATS = {"inherit", "rgba8", "rgba16f", "rgba32f"}
FILTERS = {"nearest", "linear"}
WRAPS = {"clamp", "repeat", "mirror", "decal"}
PRECISIONS = {"preferred", "required"}
TEXSRC = {"builtin:noise", "builtin:ramp-x", "builtin:ramp-y"}
KINDS = {"float", "angle", "point", "color", "switch", "choice", "text"}
CAPS = {"current_frame", "previous_frame", "next_frame", "frame_window", "time_access", "motion_data",
        "mask_input", "depth_input", "auxiliary_inputs", "multi_pass", "geometry_input", "audio_data"}
SERVED = {"current_frame", "multi_pass", "auxiliary_inputs"}
FEATURES = {"float16", "float32", "history"}
REF = re.compile(r'^(source|buffer:|texture:|pass:|frame:)')


def parse_ref(ref):
    m = re.match(r'^(source|buffer|texture|pass|frame)(?::(.*))?$', ref or "")
    if not m:
        return None
    kind, rest = m.group(1), m.group(2) or ""
    back = 0
    if "@" in rest:
        rest, n = rest.rsplit("@", 1)
        if not n.isdigit():
            return None
        back = int(n)
    if kind != "source" and not rest:
        return None
    return kind, rest, back


def check_param(p, errs):
    pid = p.get("id")
    if not pid or "type" not in p:
        errs.append("param missing id/type"); return
    k = p["type"]
    if k not in KINDS:
        errs.append(f"{pid}: unknown type {k}"); return
    d = p.get("default")
    try:
        if k == "point":
            assert isinstance(d, list) and len(d) >= 2 and all(isinstance(x, (int, float)) for x in d[:2])
        elif k == "color":
            assert isinstance(d, str) and re.fullmatch(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})', d)
        elif k == "switch":
            assert d in (True, False, 0, 1)
        elif k == "text":
            assert isinstance(d, str)
        elif k == "choice":
            opts = p.get("options")
            assert isinstance(opts, list) and opts and all(isinstance(o, str) for o in opts)
            assert isinstance(d, (int, float)) and not isinstance(d, bool) and 0 <= int(d) < len(opts)
        else:
            assert isinstance(d, (int, float)) and not isinstance(d, bool)
    except AssertionError:
        errs.append(f"{pid}: bad default {d!r} for type {k}")


def validate(j):
    errs, warns = [], []
    for key in ("id", "category"):
        if not isinstance(j.get(key), str) or not j.get(key):
            errs.append(f"missing {key}")
    if "passes" not in j:
        errs.append("no passes"); return errs, warns
    if j.get("schema", 2) != 2:
        errs.append(f"schema {j.get('schema')} (must be 2)")
    if "sksl" in j:
        errs.append("has both sksl and passes")
    if j.get("native"):
        errs.append("graph effect cannot be native")
    passes = j["passes"]
    if not isinstance(passes, list) or not passes:
        errs.append("no passes"); return errs, warns
    req = j.get("requires") or {}
    for b in req.get("backends", []):
        if b != "skia":
            errs.append(f"requires backend {b}")
    for f in req.get("features", []):
        if f not in FEATURES:
            errs.append(f"unknown feature {f}")
    caps = req.get("capabilities", [])
    for c in caps:
        c = c.strip().lower()
        if c not in CAPS:
            errs.append(f"unknown capability {c}")
        elif c not in SERVED:
            errs.append(f"capability {c} not served by this build")
    if req.get("minSchema", 2) > 2:
        errs.append("minSchema > 2")
    hist = j.get("history") or {}
    if hist.get("seek", "reset") not in ("reset", "replay"):
        errs.append("bad history.seek")
    if not (1 <= hist.get("maxReplay", 120) <= 1000):
        errs.append("bad maxReplay")
    fw = j.get("frames") or {}
    wide = "frame_window" in caps or "time_access" in caps
    before = fw.get("before", 0) if wide else (1 if "previous_frame" in caps else 0)
    after = fw.get("after", 0) if wide else (1 if "next_frame" in caps else 0)
    if fw and not wide:
        errs.append("frames set without frame_window")

    pass_at = {}
    for i, p in enumerate(passes):
        pid = p.get("id", f"pass{i}")
        if not ID.match(pid):
            errs.append(f"pass id {pid!r} invalid")
        elif pid in pass_at:
            errs.append(f"pass id {pid} twice")
        else:
            pass_at[pid] = i
        src = p.get("source", "")
        text = "\n".join(src) if isinstance(src, list) else src
        if not (text or "").strip():
            errs.append(f"{pid}: no shader source")
        lang = p.get("lang", "sksl")
        if lang not in LANGS:
            errs.append(f"{pid}: lang {lang} unknown")
        elif lang not in NATIVE_LANGS:
            errs.append(f"{pid}: lang {lang} not runnable ({', '.join(sorted(NATIVE_LANGS))} only)")
        if p.get("blend", "replace") not in BLENDS:
            errs.append(f"{pid}: blend {p.get('blend')}")
        inputs = p.get("inputs")
        if isinstance(inputs, dict):
            inputs = [{"name": k, "ref": v} for k, v in inputs.items()]
        inputs = inputs or []
        if len(inputs) > 8:
            errs.append(f"{pid}: {len(inputs)} inputs")
        p["_inputs"] = inputs
        for k, v in (p.get("constants") or {}).items():
            vals = v if isinstance(v, list) else [v]
            if not vals or not all(isinstance(x, (int, float)) for x in vals):
                errs.append(f"{pid}: constant {k} bad")
    tex_at = {}
    for i, t in enumerate(j.get("textures") or []):
        tid = t.get("id", "")
        if not ID.match(tid) or tid in tex_at:
            errs.append(f"texture id {tid!r} bad")
        tex_at[tid] = i
        s = t.get("source", "")
        if s not in TEXSRC and not s.startswith("lutf16:"):
            errs.append(f"texture {tid}: source {s!r} unsupported (image textures are not loaded)")
        if s.startswith("lutf16:") and "skia" not in req.get("backends", []):
            errs.append(f"texture {tid}: lut needs skia backend")
        if not (1 <= t.get("width", 256) <= 2048 and 1 <= t.get("height", 256) <= 2048):
            errs.append(f"texture {tid}: size")
        if t.get("filter", "nearest") not in FILTERS or t.get("wrap", "repeat") not in WRAPS:
            errs.append(f"texture {tid}: filter/wrap")
    bufs = {}
    blist = []
    for i, b in enumerate(j.get("buffers") or []):
        bid = b.get("id", "")
        if not ID.match(bid) or bid == "source":
            errs.append(f"buffer id {bid!r} invalid")
        elif bid in bufs:
            errs.append(f"buffer {bid} twice")
        elif bid in tex_at:
            errs.append(f"{bid} is both buffer and texture")
        bufs[bid] = len(blist); blist.append(b)
        sc = b.get("scale", 1.0)
        if not (0 < sc <= 8):
            errs.append(f"buffer {bid}: scale {sc}")
        if not (0 <= b.get("history", 0) <= 8):
            errs.append(f"buffer {bid}: history")
        if b.get("format", "inherit") not in FORMATS:
            errs.append(f"buffer {bid}: format {b.get('format')}")
        if b.get("precision", "preferred") not in PRECISIONS:
            errs.append(f"buffer {bid}: precision")
        if b.get("filter", "linear") not in FILTERS or b.get("wrap", "clamp") not in WRAPS:
            errs.append(f"buffer {bid}: filter/wrap")
    if errs:
        return errs, warns
    for i, p in enumerate(passes):
        o = p.get("output") or p.get("id", f"pass{i}")
        if not ID.match(o):
            errs.append(f"output {o!r} invalid"); continue
        if o in tex_at:
            errs.append(f"{p.get('id')}: writes texture {o}"); continue
        if o not in bufs:
            bufs[o] = len(blist); blist.append({"id": o})
    out = j.get("output") or (passes[-1].get("output") or passes[-1].get("id", f"pass{len(passes)-1}"))
    if out not in bufs:
        errs.append(f"output buffer {out!r} unknown")
    if len(blist) > 32:
        errs.append(f"{len(blist)} buffers")
    if errs:
        return errs, warns
    writers = [[] for _ in blist]
    for i, p in enumerate(passes):
        writers[bufs[p.get("output") or p.get("id", f"pass{i}")]].append(i)
    preds = [set() for _ in passes]
    for i, p in enumerate(passes):
        outb = bufs[p.get("output") or p.get("id", f"pass{i}")]
        names = set()
        for inp in p["_inputs"]:
            n = inp.get("name", "")
            if not ID.match(n) or n in names:
                errs.append(f"{p.get('id')}: input name {n!r} bad/dup"); continue
            names.add(n)
            r = parse_ref(inp.get("ref"))
            if r is None:
                errs.append(f"{p.get('id')}: ref {inp.get('ref')!r} invalid"); continue
            kind, rid, back = r
            if kind == "frame":
                try:
                    k = int(rid)
                    if k < -before or k > after:
                        errs.append(f"{p.get('id')}: frame:{k} outside window")
                except ValueError:
                    errs.append(f"{p.get('id')}: frame ref")
                continue
            if kind == "source":
                continue
            if kind == "texture":
                if rid not in tex_at:
                    errs.append(f"{p.get('id')}: unknown texture {rid}")
                continue
            if kind == "pass":
                q = pass_at.get(rid)
                if q is None:
                    errs.append(f"{p.get('id')}: unknown pass {rid}"); continue
                rid = passes[q].get("output") or passes[q].get("id")
            b = bufs.get(rid)
            if b is None:
                errs.append(f"{p.get('id')}: unknown buffer {rid}"); continue
            depth = blist[b].get("history", 0)
            if back > depth:
                errs.append(f"{p.get('id')}: {rid}@{back} but history {depth}"); continue
            if back > 0:
                continue
            if b == outb:
                errs.append(f"{p.get('id')}: reads and writes {rid}"); continue
            if not writers[b]:
                errs.append(f"{p.get('id')}: buffer {rid} never written"); continue
            for w in writers[b]:
                if w != i:
                    preds[i].add(w)
        for a in p.get("after", []):
            q = pass_at.get(a)
            if q is None:
                errs.append(f"{p.get('id')}: after {a} unknown")
            elif q != i:
                preds[i].add(q)
    for b in range(len(blist)):
        for before_i, later in zip(writers[b], writers[b][1:]):
            preds[later].add(before_i)
            lp = passes[later]
            if lp.get("clear", True) and lp.get("blend", "replace") == "replace":
                errs.append(f"{lp.get('id')}: overwrites buffer {blist[b]['id']}")
    if errs:
        return errs, warns
    indeg = [len(s) for s in preds]
    succ = [[] for _ in passes]
    for to, s in enumerate(preds):
        for f in s:
            succ[f].append(to)
    q = [i for i, d in enumerate(indeg) if d == 0]
    seen = 0
    while q:
        n = q.pop(); seen += 1
        for s in succ[n]:
            indeg[s] -= 1
            if indeg[s] == 0:
                q.append(s)
    if seen != len(passes):
        errs.append("dependency cycle")
    if not writers[bufs[out]]:
        errs.append("no pass writes output")
    # params
    ps = j.get("params") or []
    for p in ps:
        check_param(p, errs)
    ids = [p.get("id") for p in ps]
    for i, p in enumerate(ps):
        a = p.get("after")
        if a and a not in ids[:i]:
            errs.append(f"{p.get('id')}: after {a} is not an earlier parameter")
    for pr in j.get("presets") or []:
        for pid in (pr.get("params") or {}):
            if pid not in ids:
                errs.append(f"preset {pr.get('name')}: no parameter {pid}")
    return errs, warns



# --------------------------------------------------------------------------------------------------------------------
# Repair and fill
# --------------------------------------------------------------------------------------------------------------------
CATEGORY_LABELS = {
    "colorlight": "Color & Light", "drawedge": "Drawing & Edge", "blur": "Blur", "warp": "Distortion/Warp",
    "procedural": "Procedural", "3d": "3D", "transform": "Move/Transform", "repeat": "Repeat",
    "matte": "Matte/Mask/Key", "opacity": "Opacity/Visibility", "text": "Text",
}
CATEGORY_RGB = {  # used only for generated picture cards
    "colorlight": (230, 140, 40), "drawedge": (150, 70, 200), "blur": (50, 130, 210), "warp": (30, 160, 150),
    "procedural": (200, 70, 120), "3d": (60, 90, 190), "transform": (90, 170, 80), "repeat": (170, 150, 40),
    "matte": (120, 120, 130), "opacity": (80, 150, 190), "text": (190, 100, 70),
}
# Alight Motion's own category names -> the 11 cards of the library (same table as amCardFor in EffectSources.kt)
CATEGORY_ALIASES = {
    "color": "colorlight", "light": "colorlight", "lighting": "colorlight", "glow": "colorlight",
    "drawing": "drawedge", "drawing/edge": "drawedge", "stylize": "drawedge",
    "distort": "warp", "distortion": "warp", "glitch": "warp", "generate": "procedural", "generative": "procedural",
    "move": "transform", "move/transform": "transform", "mask": "matte", "key": "matte", "transition": "opacity",
}


def _safe_id(old, taken):
    new = re.sub(r"[^A-Za-z0-9_]", "_", old)
    if not re.match(r"[A-Za-z_]", new):
        new = "_" + new
    if len(new) > 48 or new != old:
        digest = hashlib.sha1(old.encode()).hexdigest()[:8]
        new = (new[:38].rstrip("_") + "_" + digest) if len(new) > 38 else new
    n = 2
    base = new
    while new in taken and taken[new] != old:
        new = f"{base[:44]}_{n}"
        n += 1
    return new


def repair_ids(j):
    """Rename pass and buffer ids the importer refuses (too long / illegal characters) and every reference to them."""
    names = set()
    for p in j.get("passes", []):
        names.add(p.get("id", ""))
        if p.get("output"):
            names.add(p["output"])
    for b in j.get("buffers") or []:
        names.add(b.get("id", ""))
    bad = [n for n in names if n and not ID.match(n)]
    if not bad:
        return 0
    taken, mapping = {}, {}
    for n in sorted(names):
        if n in bad:
            mapping[n] = _safe_id(n, taken)
            taken[mapping[n]] = n
        else:
            taken[n] = n
    m = lambda x: mapping.get(x, x)
    for p in j["passes"]:
        if "id" in p:
            p["id"] = m(p["id"])
        if p.get("output"):
            p["output"] = m(p["output"])
        if "after" in p:
            p["after"] = [m(a) for a in p["after"]]
        inputs = p.get("inputs")
        items = inputs if isinstance(inputs, list) else None
        if isinstance(inputs, dict):
            for k, v in list(inputs.items()):
                inputs[k] = _remap_ref(v, m)
        for it in items or []:
            if isinstance(it, dict) and "ref" in it:
                it["ref"] = _remap_ref(it["ref"], m)
    for b in j.get("buffers") or []:
        b["id"] = m(b.get("id", ""))
    if j.get("output"):
        j["output"] = m(j["output"])
    return len(mapping)


def _remap_ref(ref, m):
    mt = re.match(r"^(buffer|pass):([^@]*)(@\d+)?$", ref or "")
    return f"{mt.group(1)}:{m(mt.group(2))}{mt.group(3) or ''}" if mt else ref


def fill_missing(j):
    """Fill what an effect file leaves out. Returns a list of what was filled."""
    done = []
    cat = (j.get("category") or "").strip().lower()
    card = cat if cat in CATEGORY_LABELS else CATEGORY_ALIASES.get(cat, "procedural")
    if card != j.get("category"):
        j["category"] = card
        done.append("category")
    for p in j.get("params") or []:
        if p.get("type") in ("float", "angle") and ("min" not in p or "max" not in p):
            d = float(p.get("default", 0))
            if p["type"] == "angle":
                lo, hi = -3600.0, 3600.0
            else:
                span = max(10000.0, 10 * abs(d))
                lo, hi = -span, span
            p.setdefault("min", lo)
            p.setdefault("max", hi)
            if "step" not in p or p["step"] < 0.1:
                p["step"] = 0.5 if p["type"] == "angle" else 1.0
            done.append("range:" + p.get("id", "?"))
        if not p.get("label"):
            p["label"] = p.get("id", "")
            done.append("label:" + p.get("id", ""))
    if not (j.get("description") or "").strip():
        tags = [t.strip() for t in (j.get("tags") or "").split(",") if t.strip()] if isinstance(j.get("tags"), str) else []
        extra = (" (" + ", ".join(tags[:4]) + ")") if tags else ""
        j["description"] = f"{j.get('name') or j.get('id')}: {CATEGORY_LABELS[card]} effect from the Alight Motion library{extra}."
        done.append("description")
    if not j.get("name"):
        j["name"] = j["id"]
        done.append("name")
    j["schema"] = 2
    return done


# --------------------------------------------------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------------------------------------------------
PICTURE_TYPES = (".webp", ".png", ".jpg", ".jpeg")


def index_pictures(folders):
    """lowercase stem -> path, for every picture in the given folders."""
    found = {}
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for f in sorted(files):
                stem, ext = os.path.splitext(f)
                if ext.lower() in PICTURE_TYPES and ".original" not in f.lower():
                    found.setdefault(stem.lower(), os.path.join(root, f))
    return found


def write_picture(src, dest):
    from PIL import Image
    im = Image.open(src)
    im.load()
    if im.format == "WEBP" and max(im.size) <= 512:
        shutil.copyfile(src, dest)
        return
    im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    im.thumbnail((384, 384))
    im.save(dest, "WEBP", quality=82)


def make_card(name, category, dest):
    from PIL import Image, ImageDraw, ImageFont
    r, g, b = CATEGORY_RGB.get(category, (100, 100, 110))
    size = 384
    im = Image.new("RGB", (size, size))
    px = im.load()
    for y in range(size):
        t = y / (size - 1)
        for x in range(size):
            s = 0.55 + 0.45 * (1 - (x / (size - 1) * 0.35 + t * 0.65))
            px[x, y] = (int(r * s), int(g * s), int(b * s))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.load_default(size=34)
    except TypeError:
        font = ImageFont.load_default()
    words, lines, cur = name.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=font) > size - 40 and cur:
            lines.append(cur); cur = w
        else:
            cur = trial
    lines.append(cur)
    y = size / 2 - 20 * len(lines)
    for ln in lines[:4]:
        d.text(((size - d.textlength(ln, font=font)) / 2, y), ln, fill=(255, 255, 255), font=font)
        y += 40
    im.save(dest, "WEBP", quality=80)


# --------------------------------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------------------------------
def main(argv):
    import argparse, copy
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="aligthmotion")
    ap.add_argument("--assets", default="Effects/shared/assets")
    ap.add_argument("--extra-thumbs", default="thumbs", help="repo folder of extra pictures (named am_<id>.webp), searched too")
    ap.add_argument("--rewrite-src", action="store_true", help="also write the repaired effects back into --src (to rebuild the zip)")
    a = ap.parse_args(argv)

    dest = os.path.join(a.assets, "alightmotion")
    thumb_dest = os.path.join(a.assets, "effects", "thumb")
    if not os.path.isdir(a.src):
        print(f"::notice::{a.src}/ not found, nothing to install for Alight Motion")
        return 0
    os.makedirs(dest, exist_ok=True)
    os.makedirs(thumb_dest, exist_ok=True)

    files = []
    for root, dirs, names in os.walk(a.src):
        dirs[:] = [d for d in dirs if d.lower() != "thumb"]
        for f in sorted(names):
            if f.lower().endswith(".json") and f.lower() != "index.json":
                files.append(os.path.join(root, f))
    pictures = index_pictures([os.path.join(a.src, "thumb"), a.src, a.extra_thumbs])

    stale = []
    zip_index = os.path.join(a.src, "index.json")
    if os.path.isfile(zip_index):
        try:
            stale = json.load(open(zip_index, encoding="utf-8"))
        except Exception:
            pass

    installed, skipped, repaired, filled, carded, seen = [], [], [], [], [], set()
    for path in files:
        base = os.path.basename(path)
        try:
            j = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            skipped.append((base, f"not valid JSON: {e}")); continue
        if not isinstance(j, dict) or not isinstance(j.get("id"), str) or not j["id"]:
            skipped.append((base, "no \"id\"")); continue
        if "passes" not in j:
            skipped.append((base, "no \"passes\" (this loader takes the converted .json form)")); continue
        n = repair_ids(j)
        if n:
            repaired.append((base, n))
        f = fill_missing(j)
        if f:
            filled.append((base, f))
        errs, _ = validate(copy.deepcopy(j))
        if errs:
            skipped.append((base, "; ".join(errs[:3]))); continue
        key = "am_" + j["id"]
        if key in seen:
            skipped.append((base, f"duplicate id {j['id']}")); continue
        seen.add(key)
        out_name = key + ".json"
        with open(os.path.join(dest, out_name), "w", encoding="utf-8") as fh:
            json.dump(j, fh, ensure_ascii=False, indent=1)
        if a.rewrite_src:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(j, fh, ensure_ascii=False, indent=1)
        # picture: by key, by file stem, by bare id (any case)
        pic = None
        for cand in (key, os.path.splitext(base)[0], j["id"]):
            pic = pictures.get(cand.lower())
            if pic:
                break
        target = os.path.join(thumb_dest, key + ".webp")
        try:
            if pic:
                write_picture(pic, target)
            else:
                raise FileNotFoundError("no picture")
        except Exception:
            try:
                make_card(j.get("name") or j["id"], j.get("category", ""), target)
                carded.append(j["id"])
            except Exception as e:
                print(f"::warning::no picture for {key}: {e}")
        installed.append(out_name)

    # index: every effect file now in the folder (ours + whatever fxconvert.py put there), so the app never meets a missing file
    present = sorted(f for f in os.listdir(dest) if f.lower().endswith((".json", ".xml")) and f != "index.json")
    with open(os.path.join(dest, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(present, fh)

    if a.rewrite_src:
        with open(zip_index, "w", encoding="utf-8") as fh:
            json.dump(sorted(os.path.basename(p) for p in files if os.path.basename(p) not in {s[0] for s in skipped}), fh)

    missing_listed = sorted(set(stale) - {os.path.basename(p) for p in files}) if stale else []
    lines = [
        "### Alight Motion library",
        f"- installed: **{len(installed)}** effects into `{dest}` (index lists {len(present)} files)",
        f"- repaired ids: {len(repaired)} file(s); filled missing fields: {len(filled)} file(s)",
        f"- pictures generated because none was found: {len(carded)}",
        f"- skipped (the app would refuse them): {len(skipped)}",
    ]
    for base, why in skipped:
        lines.append(f"  - `{base}`: {why}")
    if missing_listed:
        lines.append(f"- the old index.json listed {len(missing_listed)} effect files that are not in the zip (dropped from the index); first ones: "
                     + ", ".join(missing_listed[:8]))
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if files and not installed:
        print("::error::Alight Motion: no effect could be installed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
