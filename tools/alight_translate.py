#!/usr/bin/env python3
"""
alight_translate.py - reads Alight Motion effect XML and writes Effects schema-2 JSON.

Input : a folder of Alight Motion effect .xml files (the "aligthmotion" library).
Output: one <slug>.json per effect (native GLSL ES 3.00 passes, RGBA16F buffers), index.json, report.json.

Rules this translator follows
  * files whose name starts with "gm" are skipped (user rule)
  * shaders stay GLSL: lang = "glsl-es", #version 300 es. Nothing is converted to SkSL.
  * every intermediate buffer is rgba16f
  * Alight convention is kept: textures and the output are PREMULTIPLIED (verified on chromakey.xml)
  * Alight's y-up screen space is emulated (acScreenNorm, gl_FragCoord, texture2DCv) on top of the engine's y-down contract
  * `acPass` is a per-pass constant; `if (acPass == N)` branches are folded, so a pass only declares the buffers it really reads
  * <pass effect="..."> (ids may be hex encoded) is inlined as passes of the same graph
  * the <script> animate() that picks `el.shaderGroups` is compiled to a GLSL function and gates each shader group
Anything it cannot translate is written to report.json with the reason. Nothing is faked.
"""
import json, os, re, sys, binascii, argparse
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alight_js
from alight_js import JsError as JsErr

# ----------------------------------------------------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------------------------------------------------
CARD = {
    'color': 'colorlight', 'light': 'colorlight', 'lighting': 'colorlight', 'glow': 'colorlight', 'colorlight': 'colorlight',
    'drawing': 'drawedge', 'stylize': 'drawedge', 'drawing/edge': 'drawedge', 'drawedge': 'drawedge',
    'blur': 'blur',
    'distort': 'warp', 'distortion': 'warp', 'warp': 'warp', 'glitch': 'warp',
    'procedural': 'procedural', 'generate': 'procedural', 'generative': 'procedural', 'other': 'procedural',
    '3d': '3d',
    'transform': 'transform', 'move': 'transform', 'move/transform': 'transform',
    'repeat': 'repeat',
    'matte': 'matte', 'mask': 'matte', 'key': 'matte',
    'opacity': 'opacity', 'transition': 'opacity',
    'text': 'text',
}
TAG_HINT = ['blur', 'color', 'light', 'glow', 'distort', 'warp', 'glitch', 'transform', 'repeat', 'tile', 'mask', 'matte', 'key', 'text', '3d', 'draw', 'edge']


def card_for(category, tags):
    c = (category or '').strip().lower()
    if c in CARD and c != 'other':
        return CARD[c]
    for t in (tags or '').lower().replace(' ', '').split(','):
        for h in TAG_HINT:
            if h in t and h in CARD:
                return CARD[h]
        if t in CARD and t != 'other':
            return CARD[t]
    return 'procedural'


def pretty(s):
    s = re.sub(r'^@(am:)?string/', '', s or '')
    s = re.sub(r'^effect_', '', s)
    s = re.sub(r'_(name|desc|label)$', '', s)
    s = re.sub(r'^param_', '', s)
    s = s.replace('_', ' ').replace('-', ' ')
    s = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', s)
    return s.strip().title() or 'Effect'


def num(s, d=0.0):
    try:
        return float(str(s).strip())
    except Exception:
        return d


def floats(s, n, d=0.0):
    v = [num(x, d) for x in str(s or '').replace(';', ',').split(',') if x.strip() != '']
    v += [d] * n
    return v[:n]


def slug(s):
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_') or 'effect'


def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', ' ', s, flags=re.S)
    return re.sub(r'//[^\n]*', '', s)


# ----------------------------------------------------------------------------------------------------------------------
# XML model
# ----------------------------------------------------------------------------------------------------------------------
class Fx:
    pass


def load_fx(path):
    root = ET.parse(path).getroot()
    if root.tag != 'effect':
        raise ValueError('first tag is <%s>, expected <effect>' % root.tag)
    fx = Fx()
    fx.path = path
    fx.file = os.path.basename(path)
    fx.id = root.get('id', '')
    fx.attrs = dict(root.attrib)
    strings = {}
    for st in root.iter('string'):
        if st.get('name') and st.text is not None:
            strings.setdefault(st.get('name'), st.text)
    fx.strings = strings

    def label(v, fallback):
        if v is None:
            return pretty(fallback)
        m = re.match(r'^@(?:am:)?string/(.+)$', v)
        if m:
            return strings.get(m.group(1)) or pretty(m.group(1))
        return v
    fx.label = label
    fx.name = label(root.get('name'), fx.id.rsplit('.', 1)[-1] or fx.file[:-4]) if root.get('name') else pretty(fx.id.rsplit('.', 1)[-1] or fx.file[:-4])
    if root.get('name') and root.get('name').startswith('@'):
        # built-in Alight string: not shipped in the xml. Use the xml file name, it is what the user sees in the folder.
        fx.name = pretty(fx.file[:-4]) if not strings.get(re.sub(r'^@(am:)?string/', '', root.get('name'))) else strings[re.sub(r'^@(am:)?string/', '', root.get('name'))]
    fx.desc = '' if (root.get('desc') or '').startswith('@') else (root.get('desc') or '')
    fx.params = list(root.find('params')) if root.find('params') is not None else []
    shaders = []
    for sh in root.findall('shader'):
        shaders.append(dict(type=sh.get('type', 'fragment'), group=int(sh.get('group')) if (sh.get('group') or '').isdigit() else None,
                            text=sh.text or '', attrs=dict(sh.attrib)))
    fx.shaders = shaders
    pe = root.find('passes')
    fx.passes = [dict(p.attrib) for p in pe] if pe is not None else []
    fx.script = None
    fx.script_external = None
    for sc in root.findall('script'):
        if sc.get('external'):
            fx.script_external = sc.get('external')
        elif (sc.get('lang') or 'js') == 'js':
            fx.script = (fx.script + '\n' if fx.script else '') + (sc.text or '')
    return fx


# ----------------------------------------------------------------------------------------------------------------------
# JS (the tiny subset used by shaderGroups scripts) -> GLSL
# ----------------------------------------------------------------------------------------------------------------------
class JsUnsupported(Exception):
    pass


TOK = re.compile(r'\s*(?:(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)|(?P<id>[A-Za-z_$][\w$]*)|(?P<str>"[^"]*"|\'[^\']*\')|(?P<op>===|!==|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|[-+*/%<>!=?:;,.(){}\[\]]))')


def js_tokens(src):
    pos = 0
    out = []
    src = strip_comments(src)
    while pos < len(src):
        if src[pos:].strip() == '':
            break
        m = TOK.match(src, pos)
        if not m:
            raise JsUnsupported('cannot read %r' % src[pos:pos + 20])
        pos = m.end()
        for k in ('num', 'id', 'str', 'op'):
            if m.group(k) is not None:
                out.append((k, m.group(k)))
                break
    return out


class JsToGlsl:
    """Parses `function animate(env,el,p){ ... }` limited to: let/var groups=[], if/else, groups.push(n), el.shaderGroups=..., return el."""

    def __init__(self, src, param_names, defaults):
        self.t = js_tokens(src)
        self.i = 0
        self.params = param_names
        self.defaults = defaults
        self.uses_env = set()
        self.locals = {}

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, val=None):
        tok = self.peek()
        if val is not None and tok[1] != val:
            raise JsUnsupported('expected %s, got %s' % (val, tok[1]))
        self.i += 1
        return tok

    # ---- expressions (precedence climbing)
    PREC = {'||': 1, '&&': 2, '==': 3, '!=': 3, '===': 3, '!==': 3, '<': 4, '>': 4, '<=': 4, '>=': 4, '+': 5, '-': 5, '*': 6, '/': 6, '%': 6}

    def expr(self, minp=0):
        lhs = self.unary()
        while True:
            kind, op = self.peek()
            if kind != 'op' or op not in self.PREC or self.PREC[op] < minp:
                break
            self.take()
            rhs = self.expr(self.PREC[op] + 1)
            op2 = {'===': '==', '!==': '!='}.get(op, op)
            if op2 == '%':
                lhs = 'mod(%s, %s)' % (lhs, rhs)
            else:
                lhs = '(%s %s %s)' % (lhs, op2, rhs)
        if self.peek() == ('op', '?') and minp == 0:
            self.take()
            a = self.expr()
            self.take(':')
            b = self.expr()
            lhs = '(%s ? %s : %s)' % (lhs, a, b)
        return lhs

    def unary(self):
        kind, v = self.peek()
        if v == '!':
            self.take()
            return '(!%s)' % self.unary()
        if v == '-':
            self.take()
            return '(-%s)' % self.unary()
        return self.primary()

    def primary(self):
        kind, v = self.take()
        if kind == 'num':
            return v if ('.' in v or 'e' in v.lower()) else v + '.0'
        if kind == 'str':
            return '"%s"' % v[1:-1]
        if v == '(':
            e = self.expr()
            self.take(')')
            return '(%s)' % e
        if kind == 'id':
            if v in ('true', 'false'):
                return v
            if v == 'p':
                self.take('.')
                _, name = self.take()
                if name not in self.params:
                    raise JsUnsupported('p.%s is not a control' % name)
                return name
            if v == 'env':
                self.take('.')
                _, name = self.take()
                path = name
                while self.peek() == ('op', '.'):
                    self.take()
                    path += '.' + self.take()[1]
                self.uses_env.add(path)
                return {'duration': '_AM_DURATION', 'time': 'time', 'absTime': 'time', 'inTime': 'time', 'frame': 'frame', 'fps': '_AM_FPS',
                        'velocity.x': '0.0', 'velocity.y': '0.0', 'scaleVelocity': '0.0', 'angularVelocity': '0.0',
                        'editMode': '""', 'selected': 'false'}.get(path) or (_ for _ in ()).throw(JsUnsupported('env.%s' % path))
            if v == 'Math':
                self.take('.')
                _, fn = self.take()
                if fn == 'PI':
                    return '3.14159265359'
                self.take('(')
                args = []
                while self.peek()[1] != ')':
                    args.append(self.expr())
                    if self.peek()[1] == ',':
                        self.take()
                self.take(')')
                m = {'abs': 'abs', 'sin': 'sin', 'cos': 'cos', 'min': 'min', 'max': 'max', 'floor': 'floor', 'trunc': 'trunc', 'round': 'round', 'sqrt': 'sqrt', 'pow': 'pow'}
                if fn not in m:
                    raise JsUnsupported('Math.%s' % fn)
                return '%s(%s)' % (m[fn], ', '.join(args))
            if v in self.locals:
                return v
            raise JsUnsupported('identifier %s' % v)
        raise JsUnsupported('token %s' % v)

    # ---- statements
    def block(self):
        self.take('{')
        out = []
        while self.peek()[1] != '}':
            out += self.stmt()
        self.take('}')
        return out

    def body(self):
        return self.block() if self.peek()[1] == '{' else self.stmt()

    def stmt(self):
        kind, v = self.peek()
        if v in ('let', 'var', 'const'):
            self.take()
            _, name = self.take()
            self.take('=')
            if self.peek()[1] == '[':
                self.take('[')
                self.take(']')
                self.take(';') if self.peek()[1] == ';' else None
                self.locals[name] = 'groups'
                self.gname = name
                return ['int _gm = 0;']
            e = self.expr()
            self.take(';') if self.peek()[1] == ';' else None
            self.locals[name] = 'num'
            return ['float %s = %s;' % (name, e)]
        if v == 'if':
            self.take()
            self.take('(')
            c = self.expr()
            self.take(')')
            a = self.body()
            out = ['if (%s) {' % c] + a + ['}']
            if self.peek()[1] == 'else':
                self.take()
                if self.peek()[1] == 'if':
                    out += ['else {'] + self.stmt() + ['}']
                else:
                    out += ['else {'] + self.body() + ['}']
            return out
        if v == self.gname_or_none() and self.peek(1)[1] == '.':
            self.take(); self.take('.')
            _, m = self.take()
            if m != 'push':
                raise JsUnsupported('groups.%s' % m)
            self.take('(')
            n = self.take()[1]
            self.take(')')
            self.take(';') if self.peek()[1] == ';' else None
            return ['_gm |= (1 << %d);' % int(float(n))]
        if v == 'el':
            self.take('.')
            _, f = self.take()
            if f != 'shaderGroups':
                raise JsUnsupported('el.%s' % f)
            self.take('=')
            if self.peek()[1] == '[':
                self.take('[')
                gs = []
                while self.peek()[1] != ']':
                    gs.append(int(float(self.take()[1])))
                    if self.peek()[1] == ',':
                        self.take()
                self.take(']')
                self.take(';') if self.peek()[1] == ';' else None
                return ['_gm = %d;' % sum(1 << g for g in gs)]
            self.take()  # the groups variable
            self.take(';') if self.peek()[1] == ';' else None
            return []
        if v == 'return':
            while self.peek()[1] not in (';', '}', None):
                self.take()
            self.take(';') if self.peek()[1] == ';' else None
            return []
        raise JsUnsupported('statement starting with %s' % v)

    def gname_or_none(self):
        return getattr(self, 'gname', None)

    def convert(self):
        self.take('function')
        self.take()  # animate
        self.take('(')
        while self.peek()[1] != ')':
            self.take()
        self.take(')')
        body = self.block()
        return 'int _am_groups() {\n  ' + '\n  '.join(body) + '\n  return _gm;\n}\n'


def static_groups(js, defaults):
    """Fallback when the script is outside the subset: evaluate it once with the control defaults (Node)."""
    import subprocess
    code = js + '\nconst p=%s;const env={frame:0,fps:30,time:0,absTime:0,inTime:0,duration:5,editMode:"",selected:false,velocity:{x:0,y:0},scaleVelocity:0,angularVelocity:0};' \
                'const el={shaderGroups:[],alpha:1,text:"",transform:{location:{x:0,y:0,z:0},scale:{x:1,y:1},angle:0}};' \
                'const AM={simplexNoise:()=>0,triangle:(x)=>0};console.log(JSON.stringify((animate(env,el,p)||el).shaderGroups||[]));' % json.dumps(defaults)
    r = subprocess.run(['node', '-e', code], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise JsUnsupported(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'node failed')
    return json.loads(r.stdout.strip())


# ----------------------------------------------------------------------------------------------------------------------
# controls
# ----------------------------------------------------------------------------------------------------------------------
RESERVED = {'size', 'time', 'frame', 'out_px', 'input', 'output', 'filter', 'sample', 'common', 'active', 'partition', 'resource', 'layout',
            'uniform', 'main', 'texture', 'image', 'precision', 'half', 'fixed', 'long', 'short', 'double', 'unsigned', 'class', 'union',
            'enum', 'typedef', 'template', 'this', 'packed', 'goto', 'inline', 'noinline', 'volatile', 'public', 'static', 'extern',
            'external', 'interface', 'superp', 'sizeof', 'cast', 'namespace', 'using', 'asm', 'attribute', 'varying', 'flat', 'smooth',
            'centroid', 'patch', 'subroutine', 'switch', 'default', 'case', 'in', 'out', 'inout', 'const', 'float', 'int', 'bool', 'true', 'false'}
BUILTIN_FN = set('mix step smoothstep clamp min max abs sign floor ceil fract mod pow exp exp2 log log2 sqrt inversesqrt sin cos tan asin acos atan sinh cosh tanh length distance dot cross normalize reflect refract round trunc radians degrees any all not equal transpose inverse determinant noise lessThan greaterThan modf isnan isinf saturate'.split())
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class Controls:
    def __init__(self, fx):
        self.params = []          # json
        self.decls = []           # uniform declarations
        self.consts = []          # const declarations
        self.swaps = {}           # source identifier -> replacement expression
        self.swaps_nocall = set()  # ids that are also GLSL function names: only replaced when not called
        self.defaults = {}        # for the JS evaluation fallback: id -> default value
        self.names = {}           # id -> uniform name(s)
        self.textures = {}        # id -> dict(srcType, downsample, src)
        self.notes = []
        self.problems = []
        self.info = {}
        self.fx = fx
        seen = set()
        for e in fx.params:
            t = e.tag
            if t in ('section', 'tip'):
                continue
            id_ = e.get('id', '')
            if t == 'texture':
                self.textures[id_] = dict(srcType=e.get('srcType', ''), downsample=int(num(e.get('downsample'), 1)) or 1, src=e.get('src'), optional=e.get('optional'))
                continue
            if not IDENT.match(id_):
                self.problems.append("control id '%s' is not a valid name" % id_)
                continue
            if id_ in seen:
                self.problems.append("control id '%s' appears twice" % id_)
                continue
            seen.add(id_)
            self.add(e, id_)

    def uname(self, id_):
        if id_ in BUILTIN_FN:
            n = id_ + '_p'
            self.swaps_nocall.add(id_)
            self.swaps[id_] = n
            return n
        if id_ in RESERVED or id_.startswith('gl_') or '__' in id_ or id_.startswith('ac') and id_[2:3].isupper():
            n = id_ + '_p'
            self.swaps[id_] = n
            return n
        return id_

    def add(self, e, id_):
        t = e.tag
        lab = self.fx.label(e.get('label') or e.get('text'), id_)
        n = self.uname(id_)
        self.info[id_] = dict(tag=t, typ=e.get('type', ''), n=n)
        if t in ('spinner', 'slider'):
            typ = e.get('type', '')
            d = num(e.get('default'), 0.0)
            j = dict(id=n, label=lab, type='angle' if typ == 'angle' else 'float', default=d, step=num(e.get('step'), 0.01))
            if e.get('min') is not None: j['min'] = num(e.get('min'))
            if e.get('max') is not None: j['max'] = num(e.get('max'))
            if typ == 'percent': j['percent'] = True
            self.params.append(j)
            self.decls.append('uniform float %s;' % n)
            self.defaults[id_] = d
        elif t in ('switch', 'checkbox'):
            d = str(e.get('default', 'false')).strip().lower() in ('true', '1')
            self.params.append(dict(id=n, label=lab, type='switch', default=1 if d else 0))
            self.decls.append('uniform bool %s;' % n)
            self.defaults[id_] = d
        elif t == 'selector':
            ch = [(int(num(c.get('value'), i)), c.get('label') or str(i)) for i, c in enumerate(e.findall('choice'))]
            if not ch:
                self.problems.append("selector '%s' has no choices" % id_)
                return
            vals = [v for v, _ in ch]
            dv = int(num(e.get('default'), vals[0]))
            at = vals.index(dv) if dv in vals else 0
            if vals == list(range(len(vals))):
                self.params.append(dict(id=n, label=lab, type='choice', default=at, options=[l for _, l in ch]))
                self.decls.append('uniform int %s;' % n)
            else:
                ni = n + '_i'
                self.params.append(dict(id=ni, label=lab, type='choice', default=at, options=[l for _, l in ch]))
                self.decls.append('uniform int %s;' % ni)
                body = ''.join('  if (i == %d) return %d;\n' % (k, v) for k, v in enumerate(vals[:-1])) + '  return %d;\n' % vals[-1]
                self.consts.append('int %s_map(int i) {\n%s}' % (n, body))
                self.info[id_]['mapped'] = True
                self.swaps[id_] = '%s_map(%s)' % (n, ni)
            self.defaults[id_] = dv
        elif t == 'point':
            d = floats(e.get('default'), 2)
            j = dict(id=n, label=lab, type='point', default=d)
            self.params.append(j)
            self.decls.append('uniform vec2 %s;' % n)
            self.defaults[id_] = d
        elif t == 'color':
            c = (e.get('default') or '#ff000000').strip().lower()
            if not re.fullmatch(r'#[0-9a-f]{6}|#[0-9a-f]{8}', c):
                c = '#ff000000'
            self.params.append(dict(id=n, label=lab, type='color', default=c))
            self.decls.append('uniform vec4 %s;' % n)
            self.defaults[id_] = c
        elif t == 'xyz':
            d = floats(e.get('default'), 3)
            lo, hi = e.get('min'), e.get('max')
            for k, ax in enumerate('xyz'):
                j = dict(id='%s_%s' % (n, ax), label='%s %s' % (lab, ax.upper()), type='angle' if e.get('type') == 'rotate' else 'float', default=d[k], step=num(e.get('step'), 0.01))
                if lo is not None: j['min'] = num(lo)
                if hi is not None: j['max'] = num(hi)
                self.params.append(j)
                self.decls.append('uniform float %s_%s;' % (n, ax))
            self.swaps[id_] = 'vec3(%s_x, %s_y, %s_z)' % (n, n, n)
            self.defaults[id_] = d
        elif t == 'hue-disc':
            d = floats(e.get('default'), 3)
            bias = e.get('bias') == 'true'
            specs = [('h', 'Hue', 0.0, 360.0, 'angle'), ('s', 'Saturation', 0.0, 1.0, 'float'), ('v', 'Brightness', -1.0 if bias else 0.0, 1.0, 'float')]
            for k, (ax, nm, lo, hi, ty) in enumerate(specs):
                self.params.append(dict(id='%s_%s' % (n, ax), label='%s %s' % (lab, nm), type=ty, default=d[k], min=lo, max=hi, step=0.01 if k else 1.0))
                self.decls.append('uniform float %s_%s;' % (n, ax))
            self.swaps[id_] = 'vec3(%s_h, %s_s, %s_v)' % (n, n, n)
            self.defaults[id_] = d
        elif t == 'orient':
            # the layer's 3D orientation matrix; the engine binds no matrices, so it is the identity (layer not rotated in 3D)
            self.consts.append('const mat4 %s = mat4(1.0);' % n)
            self.notes.append("<orient id='%s'> is the identity matrix (the engine passes no layer orientation)" % id_)
        elif t == 'float':
            self.consts.append('const float %s = %s;' % (n, repr(num(e.get('value'), 0.0))))
        else:
            self.notes.append('skipped unknown control <%s>' % t)

    def js_control(self, id_):
        i = self.info.get(id_)
        if not i:
            raise JsErr('p.%s is not a control' % id_)
        t, n = i['tag'], i['n']
        if t in ('spinner', 'slider'):
            return ('(%s * 6.0 * time)' % n if i['typ'] == 'rpm' else n), 'f'
        if t in ('switch', 'checkbox'):
            return n, 'b'
        if t == 'selector':
            return ('float(%s_map(%s_i))' % (n, n) if i.get('mapped') else 'float(%s)' % n), 'f'
        raise JsErr('p.%s is a <%s> control' % (id_, t))

    def apply_swaps(self, src):
        if not self.swaps:
            return src
        pat = re.compile(r'(?<![\w.])(' + '|'.join(re.escape(k) for k in sorted(self.swaps, key=len, reverse=True)) + r')(?!\w)')
        def rep(m):
            k = m.group(1)
            if k in self.swaps_nocall and re.match(r'\s*\(', src[m.end():]):
                return k
            return self.swaps[k]
        return pat.sub(rep, src)


# ----------------------------------------------------------------------------------------------------------------------
# shader text work
# ----------------------------------------------------------------------------------------------------------------------
def match_brace(s, i):
    depth = 0
    for k in range(i, len(s)):
        if s[k] == '{':
            depth += 1
        elif s[k] == '}':
            depth -= 1
            if depth == 0:
                return k
    return -1


def match_paren(s, i):
    depth = 0
    for k in range(i, len(s)):
        if s[k] == '(':
            depth += 1
        elif s[k] == ')':
            depth -= 1
            if depth == 0:
                return k
    return -1


def eval_pass_cond(cond, n):
    """True/False when the condition only involves acPass and integer literals, else None."""
    c = cond.strip()
    if 'acPass' not in c:
        return None
    c2 = c.replace('acPass', str(n))
    if not re.fullmatch(r'[\s\d()<>=!&|]+', c2):
        return None
    try:
        py = c2.replace('&&', ' and ').replace('||', ' or ')
        py = re.sub(r'!(?!=)', ' not ', py)
        return bool(eval(py, {'__builtins__': {}}, {}))
    except Exception:
        return None


def _skip_ws(s, k):
    while k < len(s) and s[k] in ' \t\r\n':
        k += 1
    return k


def _body(s, k):
    """(text, end) of a braced block (braces removed) or of exactly one statement (nested if/for/while handled)."""
    k = _skip_ws(s, k)
    if k < len(s) and s[k] == '{':
        e = match_brace(s, k)
        return s[k + 1:e], e + 1
    m = re.compile(r'(if|for|while)\s*\(').match(s, k)
    if m:
        if m.group(1) == 'if':
            _, _, e = _parse_chain(s, k)
            return s[k:e], e
        pc = match_paren(s, m.end() - 1)
        _, e = _body(s, pc + 1)
        return s[k:e], e
    depth = 0
    q = k
    while q < len(s):
        ch = s[q]
        if ch in '({[':
            depth += 1
        elif ch in ')}]':
            depth -= 1
            if depth < 0:
                return s[k:q], q
        elif ch == ';' and depth == 0:
            return s[k:q + 1], q + 1
        q += 1
    return s[k:], len(s)


def _parse_chain(s, i):
    """s[i:] starts at `if`. Returns (items[(cond, body)], else_body|None, end)."""
    items = []
    while True:
        m = re.compile(r'if\s*\(').match(s, i)
        po = m.end() - 1
        pc = match_paren(s, po)
        cond = s[po + 1:pc]
        body, e = _body(s, pc + 1)
        items.append((cond, body))
        k = _skip_ws(s, e)
        if re.compile(r'else\b').match(s, k):
            k2 = _skip_ws(s, k + 4)
            if re.compile(r'if\s*\(').match(s, k2):
                i = k2
                continue
            eb, e2 = _body(s, k2)
            return items, eb, e2
        return items, None, e


def fold_pass_branches(src, n):
    """Removes the branches of `if (acPass ...)` chains that cannot run in pass n. Conditions that mix in anything else are left alone."""
    out = []
    i = 0
    pat = re.compile(r'\bif\s*\(')
    while True:
        m = pat.search(src, i)
        if not m:
            out.append(src[i:])
            break
        out.append(src[i:m.start()])
        items, else_body, end = _parse_chain(src, m.start())
        kept = []
        taken = None
        for cond, body in items:
            v = eval_pass_cond(cond, n)
            if v is None:
                kept.append((cond, body))
            elif v and not kept:
                taken = body
                break
            elif v:
                kept.append(('1', body))       # a later always-true branch behaves like `else`
                break
        if taken is not None:
            out.append('{' + fold_pass_branches(taken, n) + '}')
        elif kept:
            text = ''
            for k, (cond, body) in enumerate(kept):
                if cond == '1' and k > 0:
                    text += ' else {' + fold_pass_branches(body, n) + '}'
                    break
                text += ('if (%s) {' if k == 0 else ' else if (%s) {') % cond + fold_pass_branches(body, n) + '}'
            else:
                if else_body is not None:
                    text += ' else {' + fold_pass_branches(else_body, n) + '}'
            out.append(text)
        elif else_body is not None:
            out.append('{' + fold_pass_branches(else_body, n) + '}')
        i = end
    return ''.join(out)


PRELUDE_BUILTINS = """\
vec2 _SIZE() { return size; }
float _TIME() { return time; }
#define acScreenSize _SIZE()
#define acPreviewSize _SIZE()
#define acProjectSize _SIZE()
#define acLayerSize _SIZE()
#define acLayerSizeNorm vec2(1.0)
#define acLayerCenter (_SIZE() * 0.5)
#define acLayerCenterNorm vec2(0.5)
#define acLayerPivot (_SIZE() * 0.5)
#define acTime _TIME()
#define acVelocity vec2(0.0)
#define acAngularVelocity 0.0
#define acScaleVelocity 0.0
#define acShowGuides false
const mat4 acLayerToScreen = mat4(1.0);
const mat4 acScreenToLayer = mat4(1.0);"""


def rewrite_dialect(src, ctl, tex_names):
    src = ctl.apply_swaps(src)
    for tid, sname in tex_names.items():
        src = re.sub(r'\b%s\s*\.\s*texture\b' % re.escape(tid), sname, src)
        src = re.sub(r'\b%s\s*\.\s*size\b' % re.escape(tid), sname + '_px', src)
    src = re.sub(r'\btexture2DCv\s*\(', '_amTex(', src)
    src = re.sub(r'\btexture2D\s*\(', '_amTex(', src)
    src = re.sub(r'\btexture\s*\(', '_amTex(', src)
    src = re.sub(r'\(\s*void\s*\)\s*[\w.]+\s*;', '', src)
    src = re.sub(r'\bgl_FragColor\b', '_frag', src)
    src = re.sub(r'\bgl_FragCoord\b', '_FC', src)
    src = re.sub(r'\bacScreenNorm\b', '_SN', src)
    src = re.sub(r'\bacLayerNorm\b', '_SN', src)
    src = re.sub(r'\bvoid\s+main\s*\(\s*(?:void)?\s*\)', 'void _am_main()', src)
    for w in ('smooth', 'flat', 'centroid', 'sample', 'input', 'output', 'filter', 'resource', 'partition', 'active', 'common', 'buffer', 'shared', 'packed', 'cast'):
        src = re.sub(r'(?<![\w.])%s\b' % w, '_u_' + w, src)
    if re.search(r'\b(?:mat\d|vec\d)\s+inverse\s*\(', src):
        src = re.sub(r'(?<![\w.])inverse\b', '_u_inverse', src)
    if re.search(r'\bAC_ImageInfo\b', src):
        names = set(re.findall(r'\bAC_ImageInfo\s+(\w+)', src))
        src = re.sub(r'\bAC_ImageInfo\s+(\w+)', r'sampler2D \1_texture, vec2 \1_size', src)
        for n in names:
            src = re.sub(r'\b%s\s*\.\s*texture\b' % n, n + '_texture', src)
            src = re.sub(r'\b%s\s*\.\s*size\b' % n, n + '_size', src)
            src = re.sub(r'(?<![\w.])%s\b(?!\s*[\w.])(?=\s*[,)])(?<!_texture)' % n, '%s_texture, %s_size' % (n, n), src)
        for tid, sname in tex_names.items():
            src = re.sub(r'(?<![\w.])%s\b(?!\s*\.)(?=\s*[,)])' % re.escape(tid), '%s, %s_px' % (sname, sname), src)
    # saturate is not a GLSL ES function; Alight's shaders use it for float and vectors
    for t in ('float', 'vec2', 'vec3', 'vec4'):
        if not re.search(r'\b%s\s+saturate\s*\(' % t, src):
            src = '%s saturate(%s x) { return clamp(x, 0.0, 1.0); }\n' % (t, t) + src
    return src


def assemble(ctl, body, samplers, flip_y, groups_fn='', gate_code=''):
    head = ['#version 300 es', 'precision highp float;', 'precision highp int;', 'precision highp sampler2D;',
            'uniform vec2 size;', 'uniform vec2 out_px;', 'uniform float time;', 'uniform float frame;']
    for s in samplers:
        head += ['uniform sampler2D %s;' % s, 'uniform vec2 %s_px;' % s]
    head += ctl.decls
    head += ['out vec4 _frag;', 'vec4 _FC;', 'vec2 _SN;', PRELUDE_BUILTINS, 'const float _AM_FPS = 30.0;', 'const float _AM_DURATION = 10.0;']
    if flip_y:
        head += ['vec4 _amTex(sampler2D s, vec2 uv) { return texture(s, vec2(uv.x, 1.0 - uv.y)); }',
                 'vec4 _amTex(sampler2D s, vec2 uv, float bias) { return texture(s, vec2(uv.x, 1.0 - uv.y), bias); }']
    else:
        head += ['vec4 _amTex(sampler2D s, vec2 uv) { return texture(s, uv); }',
                 'vec4 _amTex(sampler2D s, vec2 uv, float bias) { return texture(s, uv, bias); }']
    head += ctl.consts
    if groups_fn:
        head.append(groups_fn)
    flip = 'out_px.y - gl_FragCoord.y' if flip_y else 'gl_FragCoord.y'
    tail = ['void main() {',
            '  vec2 _p = vec2(gl_FragCoord.x, %s);' % flip,
            '  _FC = vec4(_p * (size / out_px), gl_FragCoord.z, gl_FragCoord.w);',
            '  _SN = _p / out_px;',
            '  _frag = vec4(0.0);']
    if gate_code:
        tail.append(gate_code)
    tail += ['  _am_main();',
             '  if (any(isnan(_frag)) || any(isinf(_frag))) _frag = vec4(0.0);',
             '}']
    return '\n'.join(head) + '\n' + body.strip() + '\n' + '\n'.join(tail) + '\n'


# ----------------------------------------------------------------------------------------------------------------------
# graph building
# ----------------------------------------------------------------------------------------------------------------------
def merge_vertex(vtext, ftext):
    """Alight vertex shaders here only compute varyings (matrices) from controls: run them first inside the fragment shader.
    varying declarations become globals, main() becomes _vs_main(), helpers the fragment also defines are dropped."""
    v = strip_comments(vtext)
    v = re.sub(r'(?m)^\s*#\s*version.*$', '', v)
    v = re.sub(r'(?m)^\s*precision\s+\w+\s+\w+\s*;', '', v)
    v = re.sub(r'\bvertShaderInit\s*\(\s*\)\s*;', '', v)
    v = re.sub(r'(?m)^(\s*)varying\s+', r'\1', v)
    v = re.sub(r'\bvoid\s+main\s*\(\s*(?:void)?\s*\)', 'void _vs_main()', v)
    f = strip_comments(ftext)
    f = re.sub(r'(?m)^\s*varying\s+[^;]*;', '', f)
    helpers = [m.group(1) for m in re.finditer(r'(?m)^[ \t]*(?:\w+[ \t]+)+?(\w+)[ \t]*\([^;{]*\)\s*\{', v) if m.group(1) != '_vs_main']
    for h in set(helpers):
        v = re.sub(r'(?<![\w.])%s(?=\s*\()' % re.escape(h), '_vx_' + h, v)
    out = [v]
    return ''.join(out), f


try:
    UNVERIFIED = set(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alight_unverified.json'))))
except Exception:
    UNVERIFIED = set()


class Skip(Exception):
    pass


def decode_ref(ref):
    if re.fullmatch(r'(?:[0-9a-f]{2}){10,}', ref or ''):
        try:
            return binascii.unhexlify(ref).decode()
        except Exception:
            return ref
    return ref


class Builder:
    def __init__(self, library, flip_y=True):
        self.lib = library            # id -> Fx
        self.flip_y = flip_y

    def translate(self, fx):
        ctl = Controls(fx)
        problems = list(ctl.problems)
        notes = list(ctl.notes)
        if fx.script_external:
            raise Skip("external script '%s' (a compiled Alight script, not in the xml)" % fx.script_external)
        frag = [s for s in fx.shaders if s['type'] == 'fragment']
        vert = [s for s in fx.shaders if s['type'] == 'vertex']
        if not frag:
            if fx.script is None:
                raise Skip('no fragment shader and no script')
            return self.translate_script(fx, ctl, notes)
        for tid, t in ctl.textures.items():
            if t['srcType'] == 'image' and not t.get('optional'):
                raise Skip("needs the image texture '%s' (%s); the engine has no image-texture source" % (tid, t.get('src')))
            if t['srcType'] not in ('content', 'buffer', 'comp', 'image', 'noise'):
                raise Skip("texture '%s' has srcType '%s'" % (tid, t['srcType']))
        if problems:
            raise Skip('; '.join(problems))

        alltext = ' '.join(x['text'] for x in fx.shaders)
        for cid, inf in ctl.info.items():
            if inf['tag'] == 'selector' and not inf.get('mapped'):
                nm = re.escape(inf['n'])
                if re.search(r'(?<![\w.])%s\s*[-+*/<>=!]=?\s*-?\d*\.\d+|-?\d*\.\d+\s*[-+*/<>=!]=?\s*%s\b' % (nm, nm), alltext):
                    ctl.decls = [('uniform float %s;' % inf['n']) if d == 'uniform int %s;' % inf['n'] else d for d in ctl.decls]
                    inf['float'] = True
        graph = dict(buffers=[], passes=[], textures=[])
        gids = sorted({s['group'] if s['group'] is not None else 0 for s in frag})
        by_group = {}
        for s in frag:
            by_group.setdefault(s['group'] if s['group'] is not None else 0, []).append(s)

        # script -> gating
        groups_fn = ''
        gating = False
        if fx.script is not None and 'shaderGroups' in fx.script:
            try:
                conv = JsToGlsl(fx.script, {k for k in ctl.defaults}, ctl.defaults)
                groups_fn = conv.convert()
                # swapped control names (reserved words) need the same rewrite
                groups_fn = ctl.apply_swaps(groups_fn)
                gating = True
                if re.fullmatch(r'int _am_groups\(\) \{\s*int _gm = 0;\s*_gm = \d+;\s*return _gm;\s*\}\s*', groups_fn):
                    gating = False
                    mask = int(re.search(r'_gm = (\d+);\s*return', groups_fn).group(1))
                    gids = [g for g in gids if mask >> g & 1] or gids
                    notes.append('script picks shader group(s) %s (constant)' % gids)
                else:
                    notes.append('script group selection compiled to GLSL (_am_groups)')
            except JsUnsupported as e:
                try:
                    chosen = static_groups(fx.script, ctl.defaults)
                    gids = [g for g in gids if g in chosen] or gids
                    notes.append('script outside the supported subset (%s): shader groups fixed to %s from the control defaults' % (e, chosen))
                except Exception as e2:
                    raise Skip('script not translated (%s)' % e)
        elif len(gids) > 1:
            gids = gids[:1]
            notes.append('several shader groups and no script: group %d is used' % gids[0])

        npass = max(1, len(fx.passes))
        pass_specs = fx.passes if fx.passes else [dict()]
        prev_out = 'source'
        sid = lambda base: base
        buffers = {}      # buffer id -> dict
        version = {}      # texture id -> current buffer ref

        def new_buffer(bid, tex_id, scale):
            buffers[bid] = dict(id=bid, scale=scale, format='rgba16f', precision='preferred', filter='linear', wrap='clamp', history=0)

        for gi, g in enumerate(gids):
            shaders = by_group[g]
            body_text = shaders[0]['text']
            has_v = False
            vtext = next((x['text'] for x in vert if x['group'] == g), None) or next((x['text'] for x in vert if x['group'] is None), None)
            if vtext:
                vpre, fsrc = merge_vertex(vtext, body_text)
                body_text = vpre + '\n' + fsrc
                has_v = True
            gin = prev_out                              # what this group sees as the layer image
            version = {}
            final_out = None
            for pi, ps in enumerate(pass_specs):
                is_last = pi == len(pass_specs) - 1
                pid = 'g%d_p%d' % (gi, pi) if len(gids) > 1 else 'p%d' % pi
                target = ps.get('target')
                eff = decode_ref(ps.get('effect', ''))
                if eff:
                    sub = self.lib.get(eff)
                    if sub is None:
                        raise Skip("pass uses effect '%s' that is not in the library" % eff)
                    srcref = version.get(ps.get('src'), gin) if ps.get('src') else gin
                    out_ref = self.inline_sub(sub, ctl, graph, buffers, pid, srcref, notes)
                    # a final result of the sub effect lands in the target buffer
                    if target:
                        t = ctl.textures.get(target)
                        if not t or t['srcType'] != 'buffer':
                            raise Skip("pass target '%s' is not a buffer texture" % target)
                        bid = '%s_%s' % (pid, target)
                        # copy pass keeps the target name meaningful and honours downsample
                        new_buffer(bid, target, 1.0 / t['downsample'])
                        graph['passes'].append(self.copy_pass('%s_copy' % pid, out_ref, bid, flip_y=self.flip_y))
                        version[target] = 'buffer:' + bid
                    continue
                src = fold_pass_branches(strip_comments(body_text), pi)
                src = re.sub(r'(?m)^\s*#\s*version.*$', '', src)
                src = re.sub(r'(?m)^\s*precision\s+\w+\s+\w+\s*;', '', src)
                src = re.sub(r'(?m)^\s*uniform\s+[^;]*;', '', src)
                src = re.sub(r'\bacPass\b', str(pi), src)
                tex_names = {tid: tid + '_tex' for tid in ctl.textures}
                used = [tid for tid in ctl.textures if re.search(r'\b%s\s*\.\s*(texture|size)\b' % re.escape(tid), src)]
                body = rewrite_dialect(src, ctl, tex_names)
                inputs = []
                for tid in used:
                    t = ctl.textures[tid]
                    if t['srcType'] in ('content', 'comp'):
                        ref = gin
                    elif t['srcType'] == 'buffer':
                        if tid == target or tid not in version:
                            ref = self.empty_ref(graph, buffers)
                            notes.append("pass %d samples buffer '%s' before it holds data this frame: bound to a transparent buffer (Alight leaves it empty / previous frame)" % (pi, tid))
                        else:
                            ref = version[tid]
                    else:
                        ref = gin
                    inputs.append(dict(name=tid + '_tex', ref=ref))
                gate = ''
                if gating and is_last:
                    gate = ('  if ((_am_groups() & (1 << %d)) == 0) { _frag = _amTex(_gate_in, _SN); return; }' % g)
                    inputs.append(dict(name='_gate_in', ref=gin))
                samplers = [i['name'] for i in inputs]
                gate_decl = ''
                if has_v:
                    gate = '  _vs_main();' + ('\n' + gate if gate else '')
                text = assemble(ctl, body, samplers, self.flip_y, groups_fn if gating else '', gate)
                p = dict(id=pid, name='%s pass %d' % (fx.name, pi), lang='glsl-es', source=text, inputs=inputs, blend='replace', clear=True)
                if is_last:
                    if gi == len(gids) - 1:
                        p['output'] = ''
                        final_out = pid
                    else:
                        bid = 'gout%d' % gi
                        new_buffer(bid, None, 1.0)
                        p['output'] = bid
                        prev_out = 'buffer:' + bid
                else:
                    if not target:
                        raise Skip('pass %d has no target but is not the last pass' % pi)
                    t = ctl.textures.get(target)
                    if not t or t['srcType'] != 'buffer':
                        raise Skip("pass target '%s' is not a buffer texture" % target)
                    bid = '%s_%s' % (pid, target)
                    new_buffer(bid, target, 1.0 / t['downsample'])
                    p['output'] = bid
                    version[target] = 'buffer:' + bid
                if ps.get('blend') == 'overwrite':
                    pass
                graph['passes'].append(p)
        graph['buffers'] = list(buffers.values())
        if fx.file in UNVERIFIED and any('transparent buffer' in n for n in notes):
            raise Skip('needs buffers that keep data between passes/frames (feedback); with an empty buffer it renders nothing')
        return ctl, graph, notes

    def translate_script(self, fx, ctl, notes):
        try:
            glsl, used = alight_js.compile_animate(fx.script, ctl.js_control)
        except JsErr as e:
            raise Skip('script not translated: %s' % e)
        body = ('uniform float duration;\nfloat _env_time; float _env_abs;\nfloat _el_lx, _el_ly, _el_lz, _el_ang, _el_sx, _el_sy, _el_alpha;\n'
                + alight_js.GLSL_LIB + glsl + '''
void _am_main() {
  _env_time = time / max(duration, 0.0001);
  _env_abs = time;
  _el_lx = 0.0; _el_ly = 0.0; _el_lz = 0.0; _el_ang = 0.0; _el_sx = 1.0; _el_sy = 1.0; _el_alpha = 1.0;
  _am_animate();
  vec2 pd = gl_FragCoord.xy * (size / out_px);
  vec2 c = size * 0.5;
  float a = radians(_el_ang);
  float ca = cos(a), sa = sin(a);
  vec2 d = pd - c - vec2(_el_lx, _el_ly);
  vec2 q = vec2(ca * d.x + sa * d.y, -sa * d.x + ca * d.y);
  q /= vec2(abs(_el_sx) < 1e-5 ? 1e-5 : _el_sx, abs(_el_sy) < 1e-5 ? 1e-5 : _el_sy);
  vec2 uv = (c + q) / size;
  if (uv.x < 0.0 || uv.y < 0.0 || uv.x > 1.0 || uv.y > 1.0) { _frag = vec4(0.0); return; }
  _frag = texture(src_tex, uv) * clamp(_el_alpha, 0.0, 1.0);
}
''')
        text = assemble(ctl, body, ['src_tex'], self.flip_y)
        p = dict(id='p0', name=fx.name, lang='glsl-es', source=text, inputs=[dict(name='src_tex', ref='source')], output='', blend='replace', clear=True)
        notes.append('script compiled to GLSL: the layer image is moved/rotated/scaled/faded inside its own area (location in project pixels, angle in degrees)')
        if 'rpm' in [i['typ'] for i in ctl.info.values()]:
            notes.append('rpm controls are integrated as degrees = rpm * 6 * seconds (constant rate)')
        return ctl, dict(buffers=[], passes=[p], textures=[]), notes

    def empty_ref(self, graph, buffers):
        if '_am_empty' not in buffers:
            buffers['_am_empty'] = dict(id='_am_empty', scale=1.0, format='rgba16f', precision='preferred', filter='linear', wrap='clamp', history=0)
            src = '#version 300 es\nprecision highp float;\nout vec4 _frag;\nvoid main() { _frag = vec4(0.0); }\n'
            graph['passes'].insert(0, dict(id='_am_empty_p', name='empty', lang='glsl-es', source=src, inputs=[], output='_am_empty', blend='replace', clear=True))
        return 'buffer:_am_empty'

    # ---- helpers for <pass effect=...>
    def copy_pass(self, pid, ref, out, flip_y):
        src = ('#version 300 es\nprecision highp float;\nprecision highp sampler2D;\nuniform vec2 size;\nuniform sampler2D s;\nout vec4 _frag;\n'
               'void main() { _frag = texture(s, gl_FragCoord.xy / size); }\n')
        return dict(id=pid, name='copy', lang='glsl-es', source=src, inputs=[dict(name='s', ref=ref)], output=out, blend='replace', clear=True)

    def inline_sub(self, sub, parent_ctl, graph, buffers, prefix, src_ref, notes):
        """Inlines the passes of a referenced effect with its own control defaults as pass constants. Returns the ref of its result."""
        sctl = Controls(sub)
        if sub.script_external or any(s['type'] == 'vertex' for s in sub.shaders):
            raise Skip("sub effect '%s' needs a script or vertex shader" % sub.id)
        frag = [s for s in sub.shaders if s['type'] == 'fragment']
        gs = sorted({s['group'] if s['group'] is not None else 0 for s in frag})
        g = gs[0]
        if sub.script and 'shaderGroups' in sub.script:
            try:
                chosen = static_groups(sub.script, sctl.defaults)
                g = chosen[0] if chosen else g
            except Exception:
                pass
        body_text = next(s['text'] for s in frag if (s['group'] if s['group'] is not None else 0) == g)
        specs = sub.passes if sub.passes else [dict()]
        version = {}
        out_ref = src_ref
        for pi, ps in enumerate(specs):
            pid = '%s_%s_p%d' % (prefix, slug(sub.id.rsplit('.', 1)[-1]), pi)
            src = fold_pass_branches(strip_comments(body_text), pi)
            src = re.sub(r'(?m)^\s*#\s*version.*$', '', src)
            src = re.sub(r'(?m)^\s*precision\s+\w+\s+\w+\s*;', '', src)
            src = re.sub(r'(?m)^\s*uniform\s+[^;]*;', '', src)
            src = re.sub(r'\bacPass\b', str(pi), src)
            tex_names = {tid: tid + '_tex' for tid in sctl.textures}
            used = [tid for tid in sctl.textures if re.search(r'\b%s\s*\.\s*(texture|size)\b' % re.escape(tid), src)]
            body = rewrite_dialect(src, sctl, tex_names)
            inputs = []
            for tid in used:
                t = sctl.textures[tid]
                if t['srcType'] in ('content', 'comp'):
                    ref = src_ref
                elif t['srcType'] == 'buffer':
                    if tid not in version:
                        ref = self.empty_ref(graph, buffers)
                        notes.append("sub effect '%s' samples buffer '%s' before it is written: bound to a transparent buffer" % (sub.id, tid))
                    else:
                        ref = version[tid]
                else:
                    raise Skip("sub effect '%s' needs a %s texture" % (sub.id, t['srcType']))
                inputs.append(dict(name=tid + '_tex', ref=ref))
            text = assemble(sctl, body, [i['name'] for i in inputs], self.flip_y)
            # the sub effect's controls keep their own defaults here (constants), they are not exposed
            consts = {}
            for j in sctl.params:
                if j['type'] in ('float', 'angle'):
                    consts[j['id']] = [float(j['default'])]
                elif j['type'] in ('switch', 'choice'):
                    consts[j['id']] = [float(j['default'])]
                elif j['type'] == 'point':
                    consts[j['id']] = [float(x) for x in j['default']]
                elif j['type'] == 'color':
                    h = j['default'].lstrip('#')
                    if len(h) == 6: h = 'ff' + h
                    a, r, gg, b = [int(h[k:k + 2], 16) / 255.0 for k in (0, 2, 4, 6)]
                    consts[j['id']] = [r, gg, b, a]
            p = dict(id=pid, name='%s pass %d' % (sub.name, pi), lang='glsl-es', source=text, inputs=inputs, blend='replace', clear=True, constants=consts)
            target = ps.get('target')
            if pi < len(specs) - 1:
                t = sctl.textures.get(target)
                if not t or t['srcType'] != 'buffer':
                    raise Skip("sub effect '%s' pass target '%s' is not a buffer" % (sub.id, target))
                bid = '%s_%s' % (pid, target)
                buffers[bid] = dict(id=bid, scale=1.0 / t['downsample'], format='rgba16f', precision='preferred', filter='linear', wrap='clamp', history=0)
                p['output'] = bid
                version[target] = 'buffer:' + bid
            else:
                bid = '%s_out' % pid
                buffers[bid] = dict(id=bid, scale=1.0, format='rgba16f', precision='preferred', filter='linear', wrap='clamp', history=0)
                p['output'] = bid
                out_ref = 'buffer:' + bid
            graph['passes'].append(p)
        notes.append("inlined effect '%s' with its default controls" % sub.id)
        return out_ref


def to_json(fx, ctl, graph, notes, slug_name):
    j = dict(schema=2, id=slug_name, name=fx.name, category=card_for(fx.attrs.get('category'), fx.attrs.get('tags')),
             description=fx.desc, origin=fx.id, params=ctl.params)
    j['buffers'] = graph['buffers']
    j['passes'] = graph['passes']
    if fx.attrs.get('tags'):
        j['tags'] = fx.attrs['tags']
    j['notes'] = notes
    return j


def make_library(root):
    """Loads every .xml under root (for <pass effect=...> lookups). Returns {effect id: Fx}."""
    lib = {}
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith('.xml'):
                try:
                    fx = load_fx(os.path.join(dp, f))
                    lib[fx.id] = fx
                    lib[decode_ref(fx.id)] = fx
                except Exception:
                    pass
    return lib


def translate_file(lib, path, slug_name, card=None, flip_y=True):
    """For fxconvert.py: returns (json dict, None) or (None, reason). The effect id is slug_name (the app stores it as am_<slug_name>)."""
    try:
        fx = load_fx(path)
        ctl, graph, notes = Builder(lib, flip_y=flip_y).translate(fx)
        j = to_json(fx, ctl, graph, notes, slug_name)
        if card:
            j['category'] = card
        return j, None
    except Skip as e:
        return None, str(e)
    except Exception as e:
        return None, 'translator error: %r' % (e,)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src', help='folder with the Alight Motion .xml effects')
    ap.add_argument('out', help='output folder')
    ap.add_argument('--y-down', action='store_true', help='do not emulate the y-up screen space of Alight Motion')
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    lib = {}
    all_fx = {}
    names = sorted(f for f in os.listdir(a.src) if f.endswith('.xml'))
    report = dict(converted=[], skipped=[], gm_skipped=[])
    for f in names:
        if f.lower().startswith('gm'):
            report['gm_skipped'].append(f)
            continue
        try:
            fx = load_fx(os.path.join(a.src, f))
        except Exception as e:
            report['skipped'].append(dict(file=f, reason='not readable: %s' % e))
            continue
        all_fx[f] = fx
        lib[fx.id] = fx
    lib = make_library(a.src)
    b = Builder(lib, flip_y=not a.y_down)
    used = set()
    index = []
    for f, fx in all_fx.items():
        if a.only and a.only not in f:
            continue
        try:
            ctl, graph, notes = b.translate(fx)
        except Skip as e:
            report['skipped'].append(dict(file=f, reason=str(e)))
            continue
        except Exception as e:
            report['skipped'].append(dict(file=f, reason='translator error: %r' % e))
            continue
        base = 'am_' + slug(decode_ref(fx.id).rsplit('.', 1)[-1] or f[:-4])
        s, k = base, 2
        while s in used:
            s = '%s_%d' % (base, k)
            k += 1
        used.add(s)
        j = to_json(fx, ctl, graph, notes, s[3:])
        with open(os.path.join(a.out, s + '.json'), 'w') as fh:
            json.dump(j, fh, indent=1)
        index.append(s + '.json')
        report['converted'].append(dict(file=f, id=s, passes=len(graph['passes']), notes=notes))
    json.dump(sorted(index), open(os.path.join(a.out, 'index.json'), 'w'))
    json.dump(report, open(os.path.join(a.out, 'report.json'), 'w'), indent=1)
    print('converted %d, skipped %d, gm skipped %d' % (len(report['converted']), len(report['skipped']), len(report['gm_skipped'])))


if __name__ == '__main__':
    main()
