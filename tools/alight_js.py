"""
alight_js.py - compiles the numeric JavaScript of an Alight Motion effect <script> (function animate(env, el, p)) to GLSL ES 3.00.

Supported: function declarations, let/var/const, if/else, for/while, ternary, + - * / % (JS fmod), comparisons, && || !, ++ --, compound assignment,
Math.*, AM.simplexNoise / AM.triangle, object literals with fixed keys (become GLSL structs), el.transform.* / el.alpha, env.*, p.* controls.
Not supported (the effect is reported as skipped, never faked): strings, arrays, object lookups by name, el.text, Math.random, recursion.

The script's result is exposed as globals: _el_lx _el_ly _el_lz (location), _el_ang (degrees), _el_sx _el_sy (scale), _el_alpha.
"""
import re


class JsError(Exception):
    pass


TOK = re.compile(r'\s*(?:(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)|(?P<id>[A-Za-z_$][\w$]*)|(?P<str>"[^"]*"|\'[^\']*\')|'
                 r'(?P<op>===|!==|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|[-+*/%<>!=?:;,.(){}\[\]]))')


def tokenize(src):
    src = re.sub(r'/\*.*?\*/', ' ', src, flags=re.S)
    src = re.sub(r'//[^\n]*', '', src)
    pos, out = 0, []
    while pos < len(src):
        if not src[pos:].strip():
            break
        m = TOK.match(src, pos)
        if not m:
            raise JsError('cannot read %r' % src[pos:pos + 24])
        pos = m.end()
        for k in ('num', 'id', 'str', 'op'):
            if m.group(k) is not None:
                out.append((k, m.group(k)))
                break
    return out


# ------------------------------------------------------------------------------------------------ parser
class Parser:
    BIN = {'||': 1, '&&': 2, '==': 3, '!=': 3, '===': 3, '!==': 3, '<': 4, '>': 4, '<=': 4, '>=': 4, '+': 5, '-': 5, '*': 6, '/': 6, '%': 6}

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, v=None):
        tok = self.peek()
        if v is not None and tok[1] != v:
            raise JsError('expected %s, got %s' % (v, tok[1]))
        self.i += 1
        return tok

    def accept(self, v):
        if self.peek()[1] == v and self.peek()[0] != 'str':
            self.i += 1
            return True
        return False

    def semi(self):
        self.accept(';')

    def program(self):
        fns, other = {}, []
        while self.peek()[0] is not None:
            if self.peek()[1] == 'function':
                self.take()
                name = self.take()[1]
                self.take('(')
                ps = []
                while self.peek()[1] != ')':
                    ps.append(self.take()[1])
                    self.accept(',')
                self.take(')')
                fns[name] = (ps, self.block())
            elif self.peek()[1] in ('let', 'var', 'const'):
                self.take()
                tname = self.take()[1]
                self.take('=')
                self.take('{')
                while self.peek()[1] != '}':
                    key = self.take()[1]
                    self.take(':')
                    self.take('function')
                    if self.peek()[0] == 'id':
                        self.take()
                    self.take('(')
                    ps = []
                    while self.peek()[1] != ')':
                        ps.append(self.take()[1])
                        self.accept(',')
                    self.take(')')
                    fns['%s.%s' % (tname, key)] = (ps, self.block())
                    self.accept(',')
                self.take('}')
                self.semi()
            else:
                raise JsError('top-level %s is not a function (only functions and tables of functions are supported)' % self.peek()[1])
        return fns

    def block(self):
        self.take('{')
        out = []
        while self.peek()[1] != '}':
            out.append(self.stmt())
        self.take('}')
        return ('block', out)

    def body(self):
        return self.block() if self.peek()[1] == '{' else ('block', [self.stmt()])

    def stmt(self):
        k, v = self.peek()
        if k == 'op' and v == ';':
            self.take()
            return ('block', [])
        if k == 'id' and v in ('let', 'var', 'const'):
            self.take()
            decls = []
            while True:
                name = self.take()[1]
                init = None
                if self.accept('='):
                    init = self.expr()
                decls.append((name, init))
                if not self.accept(','):
                    break
            self.semi()
            return ('let', decls)
        if v == 'if' and k == 'id':
            self.take(); self.take('(')
            c = self.expr()
            self.take(')')
            a = self.body()
            b = None
            if self.peek()[1] == 'else':
                self.take()
                b = ('block', [self.stmt()]) if self.peek()[1] == 'if' else self.body()
            return ('if', c, a, b)
        if v == 'for' and k == 'id':
            self.take(); self.take('(')
            init = None if self.peek()[1] == ';' else self.stmt_noexpr_semi()
            self.take(';') if self.peek()[1] == ';' else None
            cond = None if self.peek()[1] == ';' else self.expr()
            self.take(';')
            upd = None if self.peek()[1] == ')' else self.expr()
            self.take(')')
            return ('for', init, cond, upd, self.body())
        if v == 'while' and k == 'id':
            self.take(); self.take('(')
            c = self.expr()
            self.take(')')
            return ('while', c, self.body())
        if v == 'return' and k == 'id':
            self.take()
            e = None if self.peek()[1] in (';', '}') else self.expr()
            self.semi()
            return ('ret', e)
        if v == 'break' or v == 'continue':
            self.take(); self.semi()
            return (v,)
        if v == '{':
            return self.block()
        e = self.expr()
        self.semi()
        return ('expr', e)

    def stmt_noexpr_semi(self):
        k, v = self.peek()
        if v in ('let', 'var', 'const'):
            self.take()
            decls = []
            while True:
                name = self.take()[1]
                init = self.expr() if self.accept('=') else None
                decls.append((name, init))
                if not self.accept(','):
                    break
            return ('let', decls)
        return ('expr', self.expr())

    # expressions
    def expr(self):
        return self.assign()

    def assign(self):
        lhs = self.cond()
        k, v = self.peek()
        if k == 'op' and v in ('=', '+=', '-=', '*=', '/=', '%='):
            self.take()
            return ('assign', v, lhs, self.assign())
        return lhs

    def cond(self):
        c = self.binary(0)
        if self.peek()[1] == '?' and self.peek()[0] == 'op':
            self.take()
            a = self.assign()
            self.take(':')
            b = self.assign()
            return ('cond', c, a, b)
        return c

    def binary(self, minp):
        lhs = self.unary()
        while True:
            k, op = self.peek()
            if k != 'op' or op not in self.BIN or self.BIN[op] < minp:
                break
            self.take()
            rhs = self.binary(self.BIN[op] + 1)
            lhs = ('bin', {'===': '==', '!==': '!='}.get(op, op), lhs, rhs)
        return lhs

    def unary(self):
        k, v = self.peek()
        if k == 'op' and v in ('!', '-', '+'):
            self.take()
            return ('un', v, self.unary())
        if k == 'op' and v in ('++', '--'):
            self.take()
            return ('pre', v, self.unary())
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            k, v = self.peek()
            if k == 'op' and v == '.':
                self.take()
                e = ('member', e, self.take()[1])
            elif k == 'op' and v == '(':
                self.take()
                args = []
                while self.peek()[1] != ')':
                    args.append(self.assign())
                    self.accept(',')
                self.take(')')
                e = ('call', e, args)
            elif k == 'op' and v == '[':
                raise JsError('array / table indexing is not supported')
            elif k == 'op' and v in ('++', '--'):
                self.take()
                e = ('post', v, e)
            else:
                break
        return e

    def primary(self):
        k, v = self.take()
        if k == 'num':
            return ('num', float(v))
        if k == 'str':
            return ('str', v[1:-1])
        if k == 'op' and v == '(':
            e = self.expr()
            self.take(')')
            return e
        if k == 'op' and v == '{':
            items = []
            while self.peek()[1] != '}':
                key = self.take()[1]
                self.take(':')
                items.append((key, self.assign()))
                self.accept(',')
            self.take('}')
            return ('obj', items)
        if k == 'op' and v == '[':
            raise JsError('array literals are not supported')
        if k == 'id':
            if v in ('true', 'false'):
                return ('bool', v == 'true')
            return ('id', v)
        raise JsError('unexpected %s' % v)


# ------------------------------------------------------------------------------------------------ emitter
GLSL_LIB = r'''
float _fmod(float a, float b) { return a - b * trunc(a / b); }
float _jsround(float x) { return floor(x + 0.5); }
float _tri(float x) { return 4.0 * abs(fract(x - 0.25) - 0.5) - 1.0; }
vec4 _mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec3 _mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 _perm(vec4 x) { return _mod289(((x * 34.0) + 1.0) * x); }
float _simplex(float px, float py, float pz) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 v = vec3(px, py, pz);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = _mod289(i);
  vec4 p = _perm(_perm(_perm(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = 1.79284291400159 - 0.85373472095314 * vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
'''

MATH1 = {'abs': 'abs', 'sin': 'sin', 'cos': 'cos', 'tan': 'tan', 'floor': 'floor', 'ceil': 'ceil', 'sqrt': 'sqrt', 'trunc': 'trunc', 'sign': 'sign',
         'exp': 'exp', 'log': 'log', 'asin': 'asin', 'acos': 'acos', 'atan': 'atan', 'round': '_jsround'}
MATH2 = {'min': 'min', 'max': 'max', 'pow': 'pow', 'atan2': 'atan'}
EL = {'transform.location.x': '_el_lx', 'transform.location.y': '_el_ly', 'transform.location.z': '_el_lz', 'transform.angle': '_el_ang',
      'transform.scale.x': '_el_sx', 'transform.scale.y': '_el_sy', 'alpha': '_el_alpha'}
ENV = {'time': '_env_time', 'absTime': '_env_abs', 'inTime': '_env_time', 'duration': 'duration', 'frame': 'frame', 'fps': '_AM_FPS',
       'velocity.x': '0.0', 'velocity.y': '0.0', 'scaleVelocity': '0.0', 'angularVelocity': '0.0', 'selected': 'false'}


class Emitter:
    def __init__(self, fns, control):
        """control(id) -> (glsl_expr, 'f'|'b') for a control, or raises."""
        self.fns = fns
        self.control = control
        self.structs = {}      # keys tuple -> name
        self.struct_decl = []
        self.done = {}         # (name, argtypes) -> (glslname, rettype)
        self.out = []
        self.pused = {}        # control id -> type
        self.stack = []

    # types: 'f', 'b', ('s', name)
    @staticmethod
    def glt(t):
        return {'f': 'float', 'b': 'bool'}.get(t) or t[1]

    def struct_for(self, keys):
        keys = tuple(keys)
        if keys not in self.structs:
            n = '_S%d' % len(self.structs)
            self.structs[keys] = n
            self.struct_decl.append('struct %s { %s };' % (n, ' '.join('float %s;' % k for k in keys)))
        return self.structs[keys]

    def fl(self, e):
        c, t = e
        if t == 'f':
            return c
        if t == 'b':
            return 'float(%s)' % c
        raise JsError('object used as a number')

    def bl(self, e):
        c, t = e
        if t == 'b':
            return c
        if t == 'f':
            return '(%s != 0.0)' % c
        raise JsError('object used as a condition')

    # ---- expressions
    def ex(self, n, sc):
        k = n[0]
        if k == 'num':
            return (repr(n[1]) if n[1] == n[1] else '0.0', 'f')
        if k == 'bool':
            return ('true' if n[1] else 'false', 'b')
        if k == 'str':
            raise JsError('strings are not supported')
        if k == 'id':
            name = n[1]
            if name in sc:
                return (name, sc[name])
            if name in sc.get('@hoist', []):
                return (name, 'f')
            if name == 'undefined':
                return ('0.0', 'f')
            raise JsError('unknown identifier %s' % name)
        if k == 'member':
            return self.member(n, sc)
        if k == 'un':
            a = self.ex(n[2], sc)
            if n[1] == '!':
                return ('(!%s)' % self.bl(a), 'b')
            return ('(%s%s)' % (n[1], self.fl(a)), 'f')
        if k in ('pre', 'post'):
            tgt = self.lvalue(n[2], sc)
            return ('(%s%s)' % (n[1], tgt[0]) if k == 'pre' else '(%s%s)' % (tgt[0], n[1]), 'f')
        if k == 'bin':
            op = n[1]
            a, b = self.ex(n[2], sc), self.ex(n[3], sc)
            if op in ('&&', '||'):
                return ('(%s %s %s)' % (self.bl(a), op, self.bl(b)), 'b')
            if op in ('==', '!='):
                if a[1] == 'b' and b[1] == 'b':
                    return ('(%s %s %s)' % (a[0], op, b[0]), 'b')
                return ('(%s %s %s)' % (self.fl(a), op, self.fl(b)), 'b')
            if op in ('<', '>', '<=', '>='):
                return ('(%s %s %s)' % (self.fl(a), op, self.fl(b)), 'b')
            if op == '%':
                return ('_fmod(%s, %s)' % (self.fl(a), self.fl(b)), 'f')
            return ('(%s %s %s)' % (self.fl(a), op, self.fl(b)), 'f')
        if k == 'cond':
            c = self.bl(self.ex(n[1], sc))
            a, b = self.ex(n[2], sc), self.ex(n[3], sc)
            if a[1] == 'b' and b[1] == 'b':
                return ('(%s ? %s : %s)' % (c, a[0], b[0]), 'b')
            if isinstance(a[1], tuple) or isinstance(b[1], tuple):
                if a[1] != b[1]:
                    raise JsError('conditional with different object types')
                return ('(%s ? %s : %s)' % (c, a[0], b[0]), a[1])
            return ('(%s ? %s : %s)' % (c, self.fl(a), self.fl(b)), 'f')
        if k == 'assign':
            return self.assign_expr(n, sc)
        if k == 'obj':
            keys = [x for x, _ in n[1]]
            name = self.struct_for(keys)
            return ('%s(%s)' % (name, ', '.join(self.fl(self.ex(e, sc)) for _, e in n[1])), ('s', name))
        if k == 'call':
            return self.call(n, sc)
        raise JsError('unsupported expression %s' % k)

    def dotted(self, n):
        parts = []
        while n[0] == 'member':
            parts.append(n[2])
            n = n[1]
        if n[0] != 'id':
            return None, []
        return n[1], list(reversed(parts))

    def member(self, n, sc):
        root, path = self.dotted(n)
        if root is None:
            raise JsError('unsupported member access')
        root = sc.get('@alias:' + root, root)
        j = '.'.join(path)
        if root == 'Math' and j == 'PI':
            return ('3.14159265359', 'f')
        if root == 'p':
            if len(path) != 1:
                raise JsError('p.%s' % j)
            try:
                expr, t = self.control(path[0])
            except Exception:
                return ('0.0', 'f')      # JS: an undefined property reads as undefined (falsy / NaN-free zero here)
            self.pused[path[0]] = t
            return ('_p_' + path[0], t)
        if root == 'env':
            if j == 'editMode':
                raise JsError('env.editMode (a string)')
            if j in ENV:
                if j in ('frame',):
                    return ('frame', 'f')
                return (ENV[j], 'b' if j == 'selected' else 'f')
            raise JsError('env.%s' % j)
        if root == 'el':
            if j in EL:
                return (EL[j], 'f')
            if j == 'transform.scale':
                return ('true', 'b')
            raise JsError('el.%s (text and other layer data are not supported)' % j)
        if root in sc and isinstance(sc[root], tuple) and sc[root][0] == 's' and len(path) == 1:
            if path[0] not in next(k for k, v in self.structs.items() if v == sc[root][1]):
                raise JsError('object has no field %s' % path[0])
            return ('%s.%s' % (root, path[0]), 'f')
        raise JsError('unsupported member %s.%s' % (root, j))

    def lvalue(self, n, sc):
        if n[0] == 'id':
            if n[1] not in sc:
                if '@hoist' not in sc:
                    raise JsError('assignment to unknown %s' % n[1])
                if n[1] not in sc['@hoist']:
                    sc['@hoist'].append(n[1])
                return (n[1], 'f')
            return (n[1], sc[n[1]])
        if n[0] == 'member':
            e = self.member(n, sc)
            return e
        raise JsError('unsupported assignment target')

    def assign_expr(self, n, sc):
        op, lhs, rhs = n[1], n[2], n[3]
        t = self.lvalue(lhs, sc)
        r = self.ex(rhs, sc)
        if lhs[0] == 'member':
            root, path = self.dotted(lhs)
            root = sc.get('@alias:' + root, root)
            if root == 'p':
                pass  # p.x is a local copy (_p_x)
        if t[1] == 'b':
            if op != '=':
                raise JsError('compound assignment on a boolean')
            return ('(%s = %s)' % (t[0], self.bl(r)), 'b')
        if isinstance(t[1], tuple):
            if op != '=':
                raise JsError('compound assignment on an object')
            return ('(%s = %s)' % (t[0], r[0]), t[1])
        if op == '%=':
            return ('(%s = _fmod(%s, %s))' % (t[0], t[0], self.fl(r)), 'f')
        return ('(%s %s %s)' % (t[0], op, self.fl(r)), 'f')

    def call(self, n, sc):
        callee, args = n[1], n[2]
        root, path = self.dotted(callee) if callee[0] == 'member' else ((callee[1], []) if callee[0] == 'id' else (None, []))
        if callee[0] == 'member':
            j = '.'.join(path)
            if root == 'Math':
                a = [self.fl(self.ex(x, sc)) for x in args]
                if j in MATH1 and len(a) == 1:
                    return ('%s(%s)' % (MATH1[j], a[0]), 'f')
                if j in MATH2 and len(a) == 2:
                    return ('%s(%s, %s)' % (MATH2[j], a[0], a[1]), 'f')
                if j in ('min', 'max') and len(a) > 2:
                    r = a[0]
                    for x in a[1:]:
                        r = '%s(%s, %s)' % (j, r, x)
                    return (r, 'f')
                raise JsError('Math.%s' % j)
            if root == 'AM':
                a = [self.fl(self.ex(x, sc)) for x in args]
                if j == 'simplexNoise':
                    a = (a + ['0.0', '0.0'])[:3]
                    return ('_simplex(%s)' % ', '.join(a), 'f')
                if j == 'triangle':
                    return ('_tri(%s)' % a[0], 'f')
                raise JsError('AM.%s' % j)
            if '%s.%s' % (root, j) in self.fns:
                return self.user_call('%s.%s' % (root, j), args, sc)
            raise JsError('call of %s.%s' % (root, j))
        name = callee[1]
        if name not in self.fns:
            raise JsError('call of unknown function %s' % name)
        return self.user_call(name, args, sc)

    def user_call(self, name, args, sc):
        params, body = self.fns[name]
        if len(args) != len(params):
            raise JsError('%s called with %d arguments' % (name, len(args)))
        alias, typed = {}, []
        for pn, a in zip(params, args):
            if a[0] == 'id' and sc.get('@alias:' + a[1], a[1]) in ('env', 'el', 'p') and a[1] not in sc:
                alias['@alias:' + pn] = sc.get('@alias:' + a[1], a[1])
            else:
                typed.append((pn, self.ex(a, sc)))
        key = (name, tuple(t for _, (_, t) in typed))
        if key not in self.done:
            if name in self.stack:
                raise JsError('recursion in %s' % name)
            self.compile_fn(name, key, typed, alias)
        gname, rt = self.done[key]
        call_args = ', '.join(self.coerce(c, t) for _, (c, t) in typed)
        return ('%s(%s)' % (gname, call_args), rt)

    def coerce(self, c, t):
        return c

    # ---- statements
    def compile_fn(self, name, key, typed, alias):
        params, body = self.fns[name]
        gname = '_f%d_%s' % (len(self.done), re.sub(r'\W', '_', name))
        sc = dict(alias)
        sc['@hoist'] = []
        for pn, (c, t) in typed:
            sc[pn] = t
        self.stack.append(name)
        self.done[key] = (gname, 'v')       # provisional for the return-type pass
        lines, rets = [], []
        self.block(body, sc, lines, rets, 1)
        lines[0:0] = ['  float %s = 0.0;\n' % h for h in sc['@hoist']]
        self.stack.pop()
        types = {t for t in rets if t is not None}
        rt = 'v' if not types else (types.pop() if len(types) == 1 else None)
        if rt is None:
            if types <= {'f', 'b'}:
                rt = 'f'
            else:
                raise JsError('function %s returns different types' % name)
        self.done[key] = (gname, rt)
        sig = ', '.join('%s %s' % (self.glt(t), pn) for pn, (_, t) in typed)
        rtype = 'void' if rt == 'v' else self.glt(rt)
        # `return el` / bare return in a void function
        self.out.append('%s %s(%s) {\n%s}\n' % (rtype, gname, sig, ''.join(lines)))

    def block(self, n, sc, lines, rets, ind):
        for s in n[1]:
            self.stmt(s, sc, lines, rets, ind)

    def stmt(self, s, sc, lines, rets, ind):
        pad = '  ' * ind
        k = s[0]
        if k == 'block':
            sc2 = dict(sc)
            lines.append(pad + '{\n')
            self.block(s, sc2, lines, rets, ind + 1)
            lines.append(pad + '}\n')
        elif k == 'let':
            for name, init in s[1]:
                if init is None:
                    sc[name] = 'f'
                    lines.append('%sfloat %s = 0.0;\n' % (pad, name))
                else:
                    c, t = self.ex(init, sc)
                    sc[name] = t
                    lines.append('%s%s %s = %s;\n' % (pad, self.glt(t), name, c))
        elif k == 'expr':
            e = s[1]
            if e[0] == 'call' or e[0] in ('assign', 'pre', 'post'):
                c, t = self.ex(e, sc)
                lines.append('%s%s;\n' % (pad, c))
            else:
                self.ex(e, sc)
        elif k == 'if':
            c = self.bl(self.ex(s[1], sc))
            lines.append('%sif (%s) {\n' % (pad, c))
            self.block(s[2], dict(sc), lines, rets, ind + 1)
            if s[3] is not None:
                lines.append('%s} else {\n' % pad)
                self.block(s[3], dict(sc), lines, rets, ind + 1)
            lines.append('%s}\n' % pad)
        elif k in ('for', 'while'):
            sc2 = dict(sc)
            if k == 'for':
                init, cond, upd, body = s[1], s[2], s[3], s[4]
                ini = ''
                if init:
                    tmp = []
                    self.stmt(init, sc2, tmp, rets, 0)
                    ini = ''.join(tmp).strip().rstrip(';')
                cnd = self.bl(self.ex(cond, sc2)) if cond else 'true'
                up = self.ex(upd, sc2)[0] if upd else ''
                lines.append('%sfor (%s; %s; %s) {\n' % (pad, ini, cnd, up))
            else:
                body = s[2]
                lines.append('%swhile (%s) {\n' % (pad, self.bl(self.ex(s[1], sc2))))
            self.block(body, sc2, lines, rets, ind + 1)
            lines.append('%s}\n' % pad)
        elif k == 'ret':
            e = s[1]
            if e is None or (e[0] == 'id' and sc.get('@alias:' + e[1], e[1]) == 'el'):
                rets.append(None)
                lines.append(pad + 'return;\n')
            else:
                c, t = self.ex(e, sc)
                if e[0] == 'call' and t == 'v':
                    rets.append(None)
                    lines.append('%s%s;\n%sreturn;\n' % (pad, c, pad))
                else:
                    rets.append(t)
                    lines.append('%sreturn %s;\n' % (pad, c))
        elif k in ('break', 'continue'):
            lines.append('%s%s;\n' % (pad, k))
        else:
            raise JsError('unsupported statement %s' % k)


def compile_animate(src, control):
    """Returns (glsl, used_controls dict id->type) with `void _am_animate()` and the globals it writes."""
    fns = Parser(tokenize(src)).program()
    if 'animate' not in fns:
        raise JsError('no animate() function')
    em = Emitter(fns, control)
    # animate(env, el, p): all three are globals
    params, body = fns['animate']
    alias = {'@alias:' + p: n for p, n in zip(params, ('env', 'el', 'p'))}
    lines, rets = [], []
    em.stack.append('animate')
    sc0 = dict(alias)
    sc0['@hoist'] = []
    em.block(body, sc0, lines, rets, 1)
    lines[0:0] = ['  float %s = 0.0;\n' % h for h in sc0['@hoist']]
    pre = ''.join('  %s _p_%s = %s;\n' % (em.glt(t), pid, control(pid)[0] if t == 'b' else control(pid)[0]) for pid, t in em.pused.items())
    fn = 'void _am_animate() {\n' + pre + ''.join(lines) + '}\n'
    glsl = '\n'.join(em.struct_decl) + '\n' + ''.join(em.out) + fn
    # the _p_ locals must be globals so called functions see them
    gl_p = ''.join('%s _p_%s;\n' % (em.glt(t), pid) for pid, t in em.pused.items())
    fn_init = 'void _am_load_p() {\n' + ''.join('  _p_%s = %s;\n' % (pid, control(pid)[0]) for pid, t in em.pused.items()) + '}\n'
    fn = 'void _am_animate() {\n  _am_load_p();\n' + ''.join(lines) + '}\n'
    glsl = '\n'.join(em.struct_decl) + '\n' + gl_p + ''.join(em.out) + fn_init + fn
    return glsl, dict(em.pused)
