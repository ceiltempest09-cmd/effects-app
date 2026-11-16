#!/usr/bin/env python3
"""Adds the repo's effect files to the app.
  effects/**/*.json, pack/**/*.json -> Performance   (8-bit json library)
  glsl/**/*.glsl                    -> Balance       (GLSL converted to SkSL)
  emx/**/*.emx                      -> Heavy effects (ray-march scene wrapped, then converted)
  aligthmotion/**/*.xml (+ thumb/)  -> Alight Motion library (xml files are bundled; the app converts them on first start)
Run from the repo root. A bad file is skipped with a warning; the build goes on."""
import glob, json, os, re, shlex, shutil, sys
from xml.sax.saxutils import unescape

DST = 'Effects/shared/assets/effects'
AM_DST = 'Effects/shared/assets/alightmotion'
AM_DIRS = ('aligthmotion', 'alightmotion')
JSON_DIRS = ('effects', 'pack')
LIST_FILES = ('index.json', 'releases.json', 'foreign.json')
CATS = {
    'default': 'color blur distort stylize noise repeat key move beat 3d text other'.split(),
    'vibrant': 'glow neon liquid glitch leaks sparkle depth beat text other'.split(),
    'raytraced': 'colorlight drawedge blur warp procedural 3d transform repeat matte opacity text pnkj'.split(),
}
AUTO = {'size', 'time', 'duration', 'layer', 'beat', 'beatTime', 'beatIndex'}
SAMPLERS = {'image', 'blurred', 'bright', 'backdrop', 'below'}
TYPES = {'vec2': 'float2', 'vec3': 'float3', 'vec4': 'float4', 'ivec2': 'int2', 'ivec3': 'int3', 'ivec4': 'int4',
         'bvec2': 'bool2', 'bvec3': 'bool3', 'bvec4': 'bool4', 'mat2': 'float2x2', 'mat3': 'float3x3', 'mat4': 'float4x4'}

class Bad(Exception):
    pass

def warn(msg):
    print('::warning::' + msg)

# ---------- header ----------
def parse_header(src):
    meta, params = {}, []
    for line in src.splitlines():
        m = re.match(r'\s*//\s*@(\w+)\s*(.*)$', line)
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        if key == 'param':
            params.append(parse_param(rest))
        else:
            meta[key] = rest
    for k in ('id', 'name', 'category'):
        if k not in meta:
            raise Bad('missing // @%s' % k)
    if not re.fullmatch(r'[a-z0-9]+', meta['id']):
        raise Bad('id must be lowercase letters and digits only')
    return meta, params

def parse_param(rest):
    t = shlex.split(rest)
    if len(t) < 3:
        raise Bad('bad @param: ' + rest)
    pid, kind, default = t[0], t[1], t[2]
    o = dict(x.split('=', 1) for x in t[3:] if '=' in x)
    p = {'id': pid, 'type': kind, 'label': o.get('label', pid)}
    if kind in ('float', 'angle'):
        p['default'] = float(default)
    elif kind == 'switch':
        p['default'] = 1 if default in ('1', 'true') else 0
    elif kind == 'point':
        x, y = default.split(',')
        p['default'] = [float(x), float(y)]
    elif kind == 'color':
        p['default'] = default
    elif kind == 'choice':
        p['default'] = int(default)
        p['options'] = o['options'].split('|')
    else:
        raise Bad('unknown param type ' + kind)
    for k in ('min', 'max', 'step'):
        if k in o:
            p[k] = float(o[k])
    for k in ('percent', 'pixels'):
        if o.get(k) in ('1', 'true'):
            p[k] = True
    if 'unit' in o:
        p['unit'] = o['unit']
    if 'when' in o:
        k, v = o['when'].split(':')
        p['when'] = {k: [int(x) for x in v.split(',')]}
    return p

# ---------- GLSL -> SkSL ----------
def split_args(s):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch in '([': depth += 1
        if ch in ')]': depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return out

def find_close(s, i):  # s[i] is an opening bracket
    o = s[i]; c = {'(': ')', '{': '}'}[o]
    d = 0
    for j in range(i, len(s)):
        if s[j] == o: d += 1
        elif s[j] == c:
            d -= 1
            if d == 0: return j
    raise Bad('unbalanced ' + o)

def convert_textures(s):
    pat = re.compile(r'\btexture(?:2D)?\s*\(')
    while True:
        m = pat.search(s)
        if not m:
            return s
        op = m.end() - 1
        cl = find_close(s, op)
        args = split_args(s[op + 1:cl])
        if len(args) != 2:
            raise Bad('texture2D needs exactly 2 arguments')
        s = s[:m.start()] + '%s.eval((%s) * size)' % (args[0].strip(), args[1].strip()) + s[cl + 1:]

def glsl_to_sksl(src, params):
    color_ids = {p['id'] for p in params if p['type'] == 'color'}
    pids = {p['id'] for p in params}
    s = re.sub(r'(?m)^\s*//\s*@.*$', '', src)
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r'(?m)^\s*#version.*$', '', s)
    s = re.sub(r'(?m)^\s*precision\s+\w+\s+\w+\s*;', '', s)
    s = re.sub(r'(?m)^\s*#ifdef\s+GL_ES\s*$', '', s)
    s = re.sub(r'(?m)^\s*#endif\s*$', '', s)
    defines = {}
    def grab(m):
        name, val = m.group(1), m.group(2).strip()
        if m.group(0).lstrip().startswith('#define') and re.match(r'#define\s+\w+\(', m.group(0).strip()):
            raise Bad('function-like #define is not supported: ' + name)
        defines[name] = val
        return ''
    if re.search(r'(?m)^\s*#define\s+\w+\(', s):
        raise Bad('function-like #define is not supported; write a function instead')
    s = re.sub(r'(?m)^\s*#define\s+(\w+)[ \t]+(.*)$', grab, s)
    if re.search(r'(?m)^\s*#', s):
        raise Bad('only #define NAME value is supported (no #if / #include)')
    for name, val in defines.items():
        s = re.sub(r'\b%s\b' % re.escape(name), '(%s)' % val if not re.fullmatch(r'[\w.]+', val) else val, s)
    s = re.sub(r'\b(%s)\b' % '|'.join(TYPES), lambda m: TYPES[m.group(1)], s)
    s = convert_textures(s)
    if re.search(r'\btexelFetch|\bdFd[xy]|\bgl_FragData|\bvarying\b|\battribute\b', s):
        raise Bad('unsupported GLSL feature (texelFetch/dFdx/varying/attribute)')

    def uni(m):
        typ, name = m.group(1), m.group(2)
        if typ == 'sampler2D':
            return 'uniform shader %s;' % name
        if name in color_ids:
            return 'layout(color) uniform half4 %s;' % name
        return 'uniform %s %s;' % (typ, name)
    s = re.sub(r'\buniform\s+(?:(?:lowp|mediump|highp)\s+)?(\w+)\s+(\w+)\s*;', uni, s)
    for m in re.finditer(r'uniform\s+(?:shader|half4|float\d?|int\d?)\s+(?:layout)?\s*(\w+)\s*;', s):
        pass
    for m in re.finditer(r'\buniform\s+\w+\s+(\w+)\s*;', s):
        n = m.group(1)
        if n not in AUTO and n not in SAMPLERS and n not in pids:
            warn('uniform %s has no @param (its value will be 0)' % n)

    mm = re.search(r'\bvoid\s+main\s*\(\s*(?:void)?\s*\)\s*\{', s)
    if not mm:
        raise Bad('no void main() found')
    ob = mm.end() - 1
    cb = find_close(s, ob)
    head, body, tail = s[:mm.start()], s[ob + 1:cb], s[cb + 1:]
    if re.search(r'\bgl_Frag', head + tail):
        raise Bad('gl_FragCoord / gl_FragColor can only be used inside main()')
    body = re.sub(r'\bgl_FragColor\b', 'fx_outColor', body)
    body = re.sub(r'\bgl_FragCoord\b', 'fx_fragCoord', body)
    body = re.sub(r'\breturn\s*;', 'return half4(fx_outColor);', body)
    body = re.sub(r'\bdiscard\s*;', 'return half4(0.0);', body)
    return (head + 'half4 main(float2 fx_xy) {\n    float4 fx_outColor = float4(0.0);\n'
            '    float4 fx_fragCoord = float4(fx_xy, 0.0, 1.0);\n' + body +
            '\n    return half4(fx_outColor);\n}\n' + tail).strip() + '\n'

# ---------- EMX harness ----------
EMX_PARAMS = [
    dict(id='camDist', label='Camera Distance', type='float', default=4.0, min=1.5, max=14.0, step=0.01),
    dict(id='camYaw', label='Camera Yaw', type='angle', default=0.0),
    dict(id='camPitch', label='Camera Pitch', type='angle', default=15.0),
    dict(id='lightYaw', label='Light Direction', type='angle', default=40.0),
    dict(id='shadowAmt', label='Shadows', type='float', default=0.7, min=0.0, max=1.0, step=0.01, percent=True),
    dict(id='aoAmt', label='Occlusion', type='float', default=0.6, min=0.0, max=1.0, step=0.01, percent=True),
    dict(id='reflectAmt', label='Reflections', type='float', default=0.6, min=0.0, max=1.0, step=0.01, percent=True),
    dict(id='exposure', label='Exposure', type='float', default=1.0, min=0.2, max=3.0, step=0.01),
    dict(id='opacity', label='Opacity', type='float', default=1.0, min=0.0, max=1.0, step=0.01, percent=True),
]
EMX_PRE = """uniform sampler2D image;
uniform vec2 size;
uniform float time;
uniform float camDist;
uniform float camYaw;
uniform float camPitch;
uniform float lightYaw;
uniform float shadowAmt;
uniform float aoAmt;
uniform float reflectAmt;
uniform float exposure;
uniform float opacity;
// The layer drawn flat: xy in scene units, height 1 = y from -0.5 to 0.5, centred on 0,0.
vec3 layerOnPlane(vec2 xy) {
    vec2 u = vec2(xy.x * size.y / size.x + 0.5, 0.5 - xy.y);
    return texture2D(image, clamp(u, 0.0, 1.0)).rgb;
}
"""
EMX_POST = """
vec3 fx_normal(vec3 p) {
    vec2 e = vec2(0.0015, -0.0015);
    return normalize(e.xyy * map(p + e.xyy) + e.yyx * map(p + e.yyx) + e.yxy * map(p + e.yxy) + e.xxx * map(p + e.xxx));
}
float fx_march(vec3 ro, vec3 rd) {
    float t = 0.02;
    for (int i = 0; i < 80; i++) {
        float d = map(ro + rd * t);
        if (d < 0.0008 * t) return t;
        t += d;
        if (t > 30.0) break;
    }
    return -1.0;
}
float fx_shadow(vec3 ro, vec3 rd, float k) {
    float res = 1.0;
    float t = 0.03;
    for (int i = 0; i < 28; i++) {
        float h = map(ro + rd * t);
        res = min(res, k * h / t);
        t += clamp(h, 0.02, 0.4);
        if (res < 0.002 || t > 12.0) break;
    }
    return clamp(res, 0.0, 1.0);
}
float fx_ao(vec3 p, vec3 n) {
    float o = 0.0;
    float s = 1.0;
    for (int i = 1; i <= 5; i++) {
        float h = 0.04 * float(i);
        o += (h - map(p + n * h)) * s;
        s *= 0.7;
    }
    return clamp(1.0 - 1.6 * o, 0.0, 1.0);
}
void main() {
    vec2 uv = gl_FragCoord.xy / size;
    vec4 base = texture2D(image, uv);
    vec2 q = (gl_FragCoord.xy - 0.5 * size) / size.y;
    q.y = -q.y;
    float yaw = radians(camYaw);
    float pitch = radians(camPitch);
    vec3 ro = vec3(sin(yaw) * cos(pitch), sin(pitch), -cos(yaw) * cos(pitch)) * camDist;
    vec3 ww = normalize(-ro);
    vec3 uu = normalize(cross(vec3(0.0, 1.0, 0.0), ww));
    vec3 vv = cross(ww, uu);
    vec3 rd = normalize(q.x * uu + q.y * vv + 1.6 * ww);
    float t = fx_march(ro, rd);
    if (t < 0.0) {
        gl_FragColor = base;
        return;
    }
    vec3 p = ro + rd * t;
    vec3 n = fx_normal(p);
    float refl = 0.0;
    vec3 alb = material(p, n, uv, refl);
    float ly = radians(lightYaw);
    vec3 L = normalize(vec3(sin(ly), 0.8, -cos(ly)));
    float dif = clamp(dot(n, L), 0.0, 1.0);
    float sh = mix(1.0, fx_shadow(p + n * 0.01, L, 12.0), shadowAmt);
    float occ = mix(1.0, fx_ao(p, n), aoAmt);
    vec3 H = normalize(L - rd);
    float spe = pow(clamp(dot(n, H), 0.0, 1.0), 48.0) * dif * sh;
    vec3 col = alb * (0.18 * occ + 0.95 * dif * sh) + spe * 0.5;
    if (reflectAmt * refl > 0.001) {
        vec3 r = reflect(rd, n);
        vec3 ro2 = p + n * 0.02;
        float t2 = fx_march(ro2, r);
        vec3 rc;
        if (t2 < 0.0) {
            rc = texture2D(image, clamp(vec2(0.5 + r.x * 0.5, 0.5 - r.y * 0.5), 0.0, 1.0)).rgb * 0.8 + 0.2;
        } else {
            vec3 p2 = ro2 + r * t2;
            vec3 n2 = fx_normal(p2);
            float r2 = 0.0;
            rc = material(p2, n2, uv, r2) * (0.25 + 0.75 * clamp(dot(n2, L), 0.0, 1.0));
        }
        float fres = pow(clamp(1.0 + dot(rd, n), 0.0, 1.0), 3.0);
        col = mix(col, rc, clamp(refl * reflectAmt * (0.25 + 0.75 * fres), 0.0, 1.0));
    }
    col = pow(clamp(col * exposure, 0.0, 1.0), vec3(0.4545));
    float a = clamp(opacity, 0.0, 1.0);
    gl_FragColor = vec4(col * a + base.rgb * (1.0 - a), a + base.a * (1.0 - a));
}
"""

def build(src, pack):
    meta, params = parse_header(src)
    if meta['category'] not in CATS[pack]:
        raise Bad('category "%s" is not in this pack. Use: %s' % (meta['category'], ', '.join(CATS[pack])))
    body = src
    if pack == 'raytraced':
        for need in ('float map(', 'material('):
            if need not in src:
                raise Bad('emx needs a function ' + need.rstrip('('))
        have = {p['id'] for p in params}
        params = params + [dict(p) for p in EMX_PARAMS if p['id'] not in have]
        body = EMX_PRE + re.sub(r'(?m)^\s*uniform\s+(sampler2D\s+image|vec2\s+size|float\s+time)\s*;', '', src) + EMX_POST
    sksl = glsl_to_sksl(body, params)
    d = {'id': meta['id'], 'name': meta['name'], 'category': meta['category'],
         'description': meta.get('description', ''), 'params': params, 'sksl': sksl}
    if 'since' in meta:
        d['since'] = meta['since']
    return d

def add_index(pack, ids):
    name = 'index.json' if pack == 'default' else 'index.%s.json' % pack
    path = os.path.join(DST, name)
    idx = json.load(open(path)) if os.path.exists(path) else []
    for i in ids:
        if i not in idx:
            idx.append(i)
    json.dump(idx, open(path, 'w'), indent=1)

def main():
    taken = set()
    for f in glob.glob(DST + '/*.json'):
        taken.add(os.path.basename(f)[:-5])
    # Default: json files, as before
    ids = []
    # The app's own list files, when a folder carries them (the pack/ library does): they replace the app's copy.
    for d in JSON_DIRS:
        for name in LIST_FILES:
            src = os.path.join(d, name)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(DST, name))
                print('replaced', name, 'from', d)
    jsons = sorted({f for d in JSON_DIRS for f in glob.glob(d + '/**/*.json', recursive=True) if os.path.basename(f) not in LIST_FILES})
    for f in jsons:
        try:
            d = json.load(open(f))
            if not isinstance(d, dict) or 'id' not in d:
                raise Bad('not an effect file (no "id")')
            shutil.copy(f, DST)
            ids.append(d['id']); taken.add(d['id'])
            print('added (default)', d['id'])
        except Exception as e:
            warn('%s skipped: %s' % (f, e))
    add_index('default', ids)
    for pack, pattern in (('vibrant', 'glsl/**/*.glsl'), ('raytraced', 'emx/**/*.emx')):
        ids = []
        for f in sorted(glob.glob(pattern, recursive=True)):
            try:
                d = build(open(f, encoding='utf-8').read(), pack)
                if d['id'] in taken:
                    raise Bad('id "%s" is already used by another effect' % d['id'])
                json.dump(d, open(os.path.join(DST, d['id'] + '.json'), 'w'), indent=1)
                taken.add(d['id']); ids.append(d['id'])
                print('added (%s) %s' % (pack, d['id']))
            except Exception as e:
                warn('%s skipped: %s' % (f, e))
        add_index(pack, ids)
    for d in AM_DIRS:
        if os.path.isdir(d):
            alight_motion(d)

# ---------- Alight Motion library ----------
CARD_BY_CATEGORY = {
    'color': 'colorlight', 'light': 'colorlight', 'lighting': 'colorlight', 'glow': 'colorlight',
    'drawing': 'drawedge', 'drawing/edge': 'drawedge', 'stylize': 'drawedge',
    'blur': 'blur', 'distort': 'warp', 'distortion': 'warp', 'warp': 'warp', 'glitch': 'warp',
    'procedural': 'procedural', 'generate': 'procedural', 'generative': 'procedural',
    '3d': '3d', 'transform': 'transform', 'move': 'transform', 'move/transform': 'transform',
    'repeat': 'repeat', 'matte': 'matte', 'mask': 'matte', 'key': 'matte',
    'opacity': 'opacity', 'transition': 'opacity', 'text': 'text',
}
CARD_WORDS = [  # for files with no usable category: first match in name + tags wins
    ('text', ('text', 'font', 'typewriter', 'subtitle', 'caption')),
    ('blur', ('blur', 'defocus', 'bokeh', 'lensblur')),
    ('matte', ('matte', 'chroma', 'keying', 'alpha', 'mask')),
    ('opacity', ('opacity', 'fade', 'dissolve', 'wipe', 'transition', 'reveal', 'blink')),
    ('repeat', ('repeat', 'tile', 'mirror', 'kaleido', 'clone', 'echo', 'mosaic')),
    ('3d', ('3d', 'cube', 'sphere', 'perspective', 'extrude', 'skybox', 'raymarch')),
    ('warp', ('warp', 'distort', 'wave', 'ripple', 'twist', 'bulge', 'lens', 'glitch', 'liquid', 'displace', 'swirl', 'pinch')),
    ('transform', ('shake', 'scale', 'rotate', 'zoom', 'slide', 'move', 'transform', 'wiggle', 'shift', 'flip')),
    ('drawedge', ('edge', 'outline', 'sketch', 'stroke', 'draw', 'cartoon', 'halftone', 'pixel', 'comic')),
    ('colorlight', ('color', 'colour', 'glow', 'light', 'tone', 'grade', 'lut', 'bloom', 'shadow', 'exposure', 'gradient', 'bright', 'contrast', 'hue', 'vhs', 'film')),
]

def am_card(category, name, tags):
    c = (category or '').strip().lower()
    if c in CARD_BY_CATEGORY:
        return CARD_BY_CATEGORY[c]
    hay = (name + ' ' + tags).lower()
    for card, words in CARD_WORDS:
        if any(w in hay for w in words):
            return card
    return 'procedural'

def am_slug(effect_id, name):
    last = effect_id.rsplit('.', 1)[-1] if effect_id else ''
    base = last if last.strip() else name  # the app uses the part after the last dot of the id
    slug = re.sub(r'[^a-z0-9]+', '_', base.lower()).strip('_')
    return slug or 'effect'

def attr(tag, name):
    m = re.search(r'\b%s="([^"]*)"' % name, tag)
    return unescape(m.group(1)) if m else ''

def set_attr(tag, name, value):
    value = value.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;')
    if re.search(r'\b%s="[^"]*"' % name, tag):
        return re.sub(r'\b%s="[^"]*"' % name, lambda m: '%s="%s"' % (name, value), tag, count=1)
    return re.sub(r'\s*(/?>)$', lambda m: ' %s="%s"%s' % (name, value, m.group(1)), tag, count=1)

def norm(text):
    return re.sub(r'[^a-z0-9]+', '', text.lower())

def save_thumb(src, key):
    out = os.path.join(DST, 'thumb', key + '.webp')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        from PIL import Image
        im = Image.open(src)
        im.seek(0)
        im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
        im.thumbnail((256, 256))
        im.save(out, 'WEBP', quality=80)
    except Exception as e:  # no Pillow or an odd file: copy it as it is (the app reads the picture by content)
        shutil.copy(src, out)

def alight_motion(root):
    pictures = {}
    for ext in ('webp', 'png', 'jpg', 'jpeg', 'gif'):
        for p in glob.glob(os.path.join(root, '**', '*.' + ext), recursive=True):
            if os.sep + 'textures' + os.sep in p or p.startswith(os.path.join(root, 'textures')):
                continue
            stem = os.path.splitext(os.path.basename(p))[0]
            pictures.setdefault(stem.lower(), p)
            pictures.setdefault(norm(stem), p)
    os.makedirs(AM_DST, exist_ok=True)
    slugs, index, counts, no_thumb, skipped = set(), [], {}, 0, 0
    for f in sorted(set(glob.glob(os.path.join(root, '**', '*.xml'), recursive=True))):
        try:
            text = open(f, encoding='utf-8', errors='replace').read()
            m = re.search(r'<effect\b[^>]*>', text)
            if not m:
                raise Bad('no <effect> tag')
            tag = m.group(0)
            if re.search(r'<texture\b[^>]*srcType="image"', text):
                skipped += 1
                warn('%s skipped: it needs an image texture, which the app does not load' % f)
                continue
            name, eid = attr(tag, 'name'), attr(tag, 'id')
            slug = am_slug(eid, name)
            n = 2
            base = slug
            while slug in slugs:
                slug = '%s_%d' % (base, n); n += 1
            slugs.add(slug)
            if slug != base:  # two files with the same id: the app would store them as one effect
                eid = (eid.rsplit('.', 1)[0] + '.' if '.' in eid else '') + slug
                tag2 = set_attr(tag, 'id', eid)
            else:
                tag2 = tag
            card = am_card(attr(tag, 'category'), name, attr(tag, 'tags'))
            tag2 = set_attr(tag2, 'category', card)
            key = 'am_' + slug
            counts[card] = counts.get(card, 0) + 1
            open(os.path.join(AM_DST, key + '.xml'), 'w', encoding='utf-8').write(text[:m.start()] + tag2 + text[m.end():])
            index.append(key + '.xml')
            thumb = attr(tag, 'thumb')
            pic = None
            if thumb:
                p = os.path.join(root, thumb)
                stem = os.path.splitext(os.path.basename(thumb))[0]
                pic = p if os.path.isfile(p) else pictures.get(stem.lower()) or pictures.get(norm(stem))
            pic = pic or pictures.get(base) or pictures.get(norm(name)) or pictures.get(norm(os.path.splitext(os.path.basename(f))[0]))
            if pic:
                save_thumb(pic, key)
            else:
                no_thumb += 1
        except Exception as e:
            warn('%s skipped: %s' % (f, e))
    json.dump(index, open(os.path.join(AM_DST, 'index.json'), 'w'), indent=1)
    print('added (alight motion) %d files; %d skipped (image textures); %d without a picture' % (len(index), skipped, no_thumb))
    for card, c in sorted(counts.items()):
        print('  %-11s %d' % (card, c))

if __name__ == '__main__':
    main()
      
