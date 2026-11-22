#!/usr/bin/env python3
"""Builds the Alight-Motion-format effect library.

Input : aligthmotion/ (effect .xml, presets/*.xml scene files, thumb/, textures/, resource/)
Output: Effects/shared/assets/alightmotion/
          index.xml          one <effect> per FAMILY (numbered revisions folded together),
                             12 app categories, presets, asset lists
          fx/<file>.xml      every revision, byte-identical to the source
          thumb/ textures/ resource/   copied unchanged
Run from the repo root:  python3 tools/am_build.py [src_dir] [out_dir]
"""
import os, re, sys, shutil, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import am_to_em
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

SRC = sys.argv[1] if len(sys.argv) > 1 else "aligthmotion"
OUT = sys.argv[2] if len(sys.argv) > 2 else "Effects/shared/assets/alightmotion"

CATS = [("color", "Color & Light"), ("drawing", "Drawing & Edge"), ("blur", "Blur"),
        ("distort", "Distortion/Warp"), ("procedural", "Procedural"), ("3d", "3D"),
        ("transform", "Move/Transform"), ("repeat", "Repeat"), ("matte", "Matte/Mask/Key"),
        ("opacity", "Opacity/Visibility"), ("text", "Text"), ("other", "Other")]
CATMAP = {"color": "color", "lighting": "color", "light": "color", "glow": "color",
          "drawing": "drawing", "stylize": "drawing", "drawing/edge": "drawing",
          "blur": "blur", "distort": "distort", "distortion": "distort", "warp": "distort",
          "procedural": "procedural", "generate": "procedural", "generative": "procedural",
          "3d": "3d", "transform": "transform", "move": "transform", "move/transform": "transform",
          "repeat": "repeat", "matte": "matte", "mask": "matte", "opacity": "opacity", "text": "text"}
KEY = re.compile(r"@(?:am:)?string/([A-Za-z0-9_]+)")

def humanize(key):
    k = re.sub(r"^effect_(param_)?", "", key)
    k = re.sub(r"_(name|desc)$", "", k)
    return " ".join(w.capitalize() for w in k.split("_") if w)

def load_effects():
    eff = {}
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".xml"): continue
        r = ET.parse(os.path.join(SRC, fn)).getroot()
        if r.tag != "effect": continue
        eff[r.get("id")] = dict(file=fn, root=r)
    return eff

def inline_strings(root):
    s = {}
    for loc in root.iter("locale"):
        if loc.get("lang", "en").startswith("en"):
            for st in loc.iter("string"): s[st.get("name")] = (st.text or "").strip()
    return s

def label(root, attr, fallback):
    v = root.get(attr) or ""
    m = re.fullmatch(r"@(?:am:)?string/(\w+)", v)
    if not m: return v or fallback
    return inline_strings(root).get(m.group(1)) or humanize(m.group(1)) or fallback

def families(eff):
    """Group explicit numbered revisions without corrupting opaque numeric IDs.

    Alight Motion packs are inconsistent: some revisions are expressed by the
    effect ID suffix (``...boxblur2``), while others use filenames such as
    ``blur.xml``/``blur2.xml`` and may have opaque numeric IDs. Prefer a
    filename-derived family when it has siblings; otherwise use the ID.
    Unnumbered IDs (including all-numeric IDs) remain independent entries.
    """
    file_groups = collections.defaultdict(list)
    id_groups = collections.defaultdict(list)

    def split_revision(value):
        # Only treat a trailing number as a revision when there is a textual
        # stem. Pure numeric identifiers are opaque stable IDs, not revisions.
        m = re.fullmatch(r"(.+?[A-Za-z_-])(\d+)", value)
        if m:
            return m.group(1).rstrip("-_").lower(), int(m.group(2))
        return value, 0

    for effect_id, entry in eff.items():
        stem = os.path.splitext(os.path.basename(entry["file"]))[0]
        fbase, fnum = split_revision(stem)
        ibase, inum = split_revision(effect_id)
        file_groups[fbase].append((fnum, effect_id))
        id_groups[ibase].append((inum, effect_id))

    # File-stem grouping is authoritative only when it actually identifies
    # multiple files. This supports blur.xml + blur2.xml even when IDs are
    # opaque numbers. Otherwise use IDs (the common upstream convention).
    selected = {}
    consumed = set()
    for base, members in file_groups.items():
        if len(members) > 1 and any(n > 0 for n, _ in members):
            members.sort(key=lambda x: (x[0], x[1]))
            selected[base] = [effect_id for _, effect_id in members]
            consumed.update(effect_id for _, effect_id in members)

    for base, members in id_groups.items():
        remaining = [(n, effect_id) for n, effect_id in members if effect_id not in consumed]
        if not remaining:
            continue
        if len(remaining) > 1 and any(n > 0 for n, _ in remaining):
            remaining.sort(key=lambda x: (x[0], x[1]))
            selected[base] = [effect_id for _, effect_id in remaining]
            consumed.update(effect_id for _, effect_id in remaining)

    for effect_id in eff:
        if effect_id not in consumed:
            selected[effect_id] = [effect_id]
    return sorted(selected.items(), key=lambda item: item[0])

def live_revision(eff, ids, status=None):
    """Choose the newest usable revision, not merely the newest numbered file.

    Keep every revision in the family manifest so the app can expose manual
    switching. Prefer a non-deprecated revision that the converter can express;
    if none is usable, retain the newest non-deprecated revision so diagnostics
    can still explain why the family is unavailable.
    """
    candidates = [i for i in ids if eff[i]["root"].get("deprecated") != "true"] or list(ids)
    if status is not None:
        usable = [i for i in candidates if status.get(i) == "ok"]
        if usable:
            return usable[-1]
    return candidates[-1]

def preset_scenes(eff):
    """presets/*.xml: one layer per preset, <effect id=...><property .../></effect>."""
    out = collections.defaultdict(list)
    pdir = os.path.join(SRC, "presets")
    if not os.path.isdir(pdir): return out
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".xml"): continue
        sc = ET.parse(os.path.join(pdir, fn)).getroot()
        for layer in sc:
            lab = layer.get("label") or ""
            dur = (int(layer.get("endTime", 0)) - int(layer.get("startTime", 0))) or 0
            for e in layer.findall("effect"):
                eid = e.get("id")
                if eid not in eff: continue
                name = re.split(r";\s*", lab)[0].strip() or "Preset"
                tm = re.search(r"t:\s*([^,;]+)", lab); cm = re.search(r"c:\s*([^,;]+)", lab)
                out[eid].append(dict(name=name, t=(tm.group(1).strip().lower() if tm else ""),
                                     c=(cm.group(1).strip() if cm else ""), dur=dur, el=e, scene=fn))
    return out

def main():
    eff = load_effects()
    pres = preset_scenes(eff)
    fams = families(eff)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT + "/em")
    am_to_em.REG = {i: e["root"] for i, e in eff.items()}
    status = {}
    live_choice_maps = {}
    for i, e in eff.items():
        fx, notes, approx, needs = am_to_em.convert(e["root"], e["file"], am_to_em.REG)
        if fx is not None:
            needs = set(needs) | {"graph:" + x for x in am_to_em.validate(fx)}
        st = "ok" if fx is not None and not needs else "needs:" + ",".join(sorted(needs))
        status[i] = st
        if fx is not None: live_choice_maps[i] = {u["id"]: {v: k for k, v in enumerate(u["src"])} for u in fx["ui"] if u["kind"] == "choice"}
        if fx is not None:
            open(os.path.join(OUT, "em", e["file"]), "w", encoding="utf-8").write(am_to_em.write_xml(fx, st, notes, approx, needs))
    for sub in ("thumb", "textures", "resource"):
        if os.path.isdir(os.path.join(SRC, sub)): shutil.copytree(os.path.join(SRC, sub), os.path.join(OUT, sub))
    # presets that point at a revision belong to the family
    rev_to_fam = {}
    rows = collections.defaultdict(list)
    n_pre = n_fam = missing_thumb = missing_tex = 0
    warn = []
    hidden = []
    for base, ids in sorted(fams, key=lambda f: f[0]):
        live = live_revision(eff, ids, status); r = eff[live]["root"]
        if status[live] != "ok": hidden.append((base, status[live])); continue
        cat = CATMAP.get((r.get("category") or "").lower(), "other")
        thumb = r.get("thumb") or ""
        if thumb and not os.path.exists(os.path.join(SRC, thumb)): missing_thumb += 1
        files = [(eff[i]["file"], eff[i]["root"].get("deprecated") == "true", i == live, i) for i in ids]
        pr = [p for i in ids for p in pres.get(i, [])]
        n_pre += len(pr); n_fam += 1
        lines = ['  <effect id=%s family=%s name=%s desc=%s tags=%s thumb=%s>' % (
            quoteattr(live), quoteattr(base), quoteattr(label(r, "name", live.split(".")[-1])),
            quoteattr(label(r, "desc", "")), quoteattr(r.get("tags") or ""), quoteattr(thumb))]
        for f, dep, isl, i in files:
            lines.append('    <revision id=%s file=%s live=%s deprecated=%s/>' % (quoteattr(i), quoteattr("em/" + f), quoteattr(str(isl).lower()), quoteattr(str(dep).lower())))
        for t in eff[live]["root"].iter("texture"):
            s = t.get("src")
            if s:
                if not os.path.exists(os.path.join(SRC, s)): missing_tex += 1; warn.append(f"{eff[live]['file']}: missing texture {s}")
                lines.append('    <asset src=%s/>' % quoteattr(s))
        lines.append('    <preset id="standard" name="Standard Settings"/>')
        for k, p in enumerate(pr):
            props = []
            cmaps = live_choice_maps.get(live, {})
            for pe in p["el"].findall("property"):
                if pe.get("name") in cmaps:
                    m_ = cmaps[pe.get("name")]
                    if pe.find("kf") is not None or pe.get("value") not in m_: continue
                    pe = ET.Element("property", dict(pe.attrib, value=str(m_[pe.get("value")])))
                kfs = "".join('<kf t=%s v=%s%s/>' % (quoteattr(kf.get("t")), quoteattr(kf.get("v")), (' e=%s' % quoteattr(kf.get("e"))) if kf.get("e") else "") for kf in pe.findall("kf"))
                props.append('<property name=%s type=%s%s>%s</property>' % (quoteattr(pe.get("name")), quoteattr(pe.get("type", "float")), (' value=%s' % quoteattr(pe.get("value"))) if pe.get("value") is not None else "", kfs) if kfs else '<property name=%s type=%s value=%s/>' % (quoteattr(pe.get("name")), quoteattr(pe.get("type", "float")), quoteattr(pe.get("value", ""))))
            lines.append('    <preset id=%s name=%s type=%s code=%s duration=%s revision=%s>%s</preset>' % (
                quoteattr(f"p{k}"), quoteattr(p["name"]), quoteattr(p["t"]), quoteattr(p["c"]), quoteattr(str(p["dur"])), quoteattr(p["el"].get("id")), "".join(props)))
        lines.append('  </effect>')
        rows[cat].append("\n".join(lines))
    with open(OUT + "/index.xml", "w", encoding="utf-8") as o:
        o.write('<?xml version="1.0" encoding="UTF-8"?>\n<library version="1">\n')
        for cid, cname in CATS:
            o.write(f'  <category id="{cid}" name={quoteattr(cname)} count="{len(rows[cid])}"/>\n')
        for cid, _ in CATS:
            for blk in rows[cid]: o.write(blk.replace("  <effect ", f'  <effect category="{cid}" ', 1) + "\n")
        o.write("</library>\n")
    print(f"{len(eff)} xml files -> {n_fam} effects, {n_pre} presets, thumbs missing {missing_thumb}, textures missing {missing_tex}")
    print({c: len(rows[c]) for c, _ in CATS})
    print(f"listed {n_fam}, hidden {len(hidden)} (the converter could not express them yet)")
    with open(OUT + "/_hidden.txt", "w") as h:
        for b, s_ in hidden: h.write(f"{b}\t{s_}\n")
    for w in warn: print("WARN", w)

if __name__ == "__main__": main()
