// @id particles3d
// @name Glow Particles 3D
// @category sparkle
// @description 3D: glowing depth-of-field orbs flying through space, linked into node clusters.
// @param count float 32 min=4 max=48 step=1 label="Particles"
// @param partSize float 1 min=0.2 max=4 step=0.01 label="Particle Size"
// @param speed float 0.5 min=0 max=4 step=0.01 label="Drift Speed"
// @param flow float 0.35 min=-2 max=2 step=0.01 label="Fly-Through Speed"
// @param spread float 1.6 min=0.4 max=4 step=0.01 label="Spread"
// @param cluster float 0.5 min=0 max=1.5 step=0.01 label="Cluster Size"
// @param orbit float 12 min=-90 max=90 step=1 label="Camera Orbit (deg/s)"
// @param tilt angle 10 label="Camera Tilt"
// @param focus float 5 min=1 max=9 step=0.01 label="Focus Distance"
// @param dof float 1 min=0 max=4 step=0.01 label="Depth Blur"
// @param glow float 1 min=0 max=4 step=0.01 label="Glow"
// @param intensity float 1.2 min=0 max=4 step=0.01 label="Brightness"
// @param twinkle float 0.5 min=0 max=1 step=0.01 percent=1 label="Twinkle"
// @param links float 0.6 min=0 max=2 step=0.01 label="Node Links"
// @param colorA color #4cc9ff label="Near Color"
// @param colorB color #ff3df0 label="Far Color"
// @param bgAmount float 1 min=0 max=1 step=0.01 percent=1 label="Layer Visible"
uniform sampler2D image;
uniform vec2 size;
uniform float time;
uniform float count;
uniform float partSize;
uniform float speed;
uniform float flow;
uniform float spread;
uniform float cluster;
uniform float orbit;
uniform float tilt;
uniform float focus;
uniform float dof;
uniform float glow;
uniform float intensity;
uniform float twinkle;
uniform float links;
uniform vec4 colorA;
uniform vec4 colorB;
uniform float bgAmount;

mat3 rotY(float a) { float c = cos(a); float s = sin(a); return mat3(c, 0.0, -s, 0.0, 1.0, 0.0, s, 0.0, c); }
mat3 rotX(float a) { float c = cos(a); float s = sin(a); return mat3(1.0, 0.0, 0.0, 0.0, c, s, 0.0, -s, c); }

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

vec3 hash31(float n) {
    return vec3(hash11(n), hash11(n + 17.31), hash11(n + 41.77));
}

// x,y = screen position, z = distance from camera, w = fade at the ends of the fly-through loop
vec4 nodePos(float i, float t, mat3 rot) {
    float g = floor(i / 4.0);
    vec3 hc = hash31(g * 3.7 + 1.0);
    float ho = hash11(i * 5.3 + 2.0);
    vec3 ph = hash31(i * 1.7 + 9.0);
    float zr = fract(hc.z + t * flow / 6.0);
    vec3 c = vec3((hc.x - 0.5) * 2.0 * spread, (hc.y - 0.5) * 1.4 * spread, (zr - 0.5) * 6.0);
    float s = t * speed;
    vec3 off = vec3(sin(s * (0.6 + ph.x) + ph.y * 6.2831853),
                    cos(s * (0.5 + ph.y) + ph.z * 6.2831853),
                    sin(s * (0.4 + ph.z) + ph.x * 6.2831853)) * cluster * (0.4 + ho);
    vec3 p = rot * (c + off);
    float zc = max(p.z + 5.0, 0.3);
    float fade = smoothstep(0.0, 0.12, zr) * (1.0 - smoothstep(0.88, 1.0, zr));
    return vec4(p.xy * (1.6 / zc), zc, fade);
}

float seg(vec2 p, vec2 a, vec2 b, float w) {
    vec2 pa = p - a;
    vec2 ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 0.000001), 0.0, 1.0);
    vec2 d = pa - ba * h;
    return exp(-dot(d, d) / (w * w));
}

void main() {
    vec2 uv = gl_FragCoord.xy / size;
    vec4 base = texture2D(image, uv) * bgAmount;
    vec2 q = (gl_FragCoord.xy - 0.5 * size) / size.y;
    q.y = -q.y;
    mat3 rot = rotX(radians(tilt)) * rotY(radians(orbit) * time);

    vec3 acc = vec3(0.0);
    vec4 head = vec4(0.0, 0.0, 9.0, 0.0);
    vec4 prev = head;

    for (int k = 0; k < 48; k++) {
        float fi = float(k);
        if (fi >= count) break;

        vec4 n = nodePos(fi, time, rot);
        float zc = n.z;
        float sc = 1.6 / zc;
        float hc = hash11(fi * 7.7 + 3.0);
        float dep = clamp((zc - 2.0) / 6.0, 0.0, 1.0);
        float vis = n.w * smoothstep(0.3, 1.0, zc);
        float tw = 1.0 - twinkle * (0.5 + 0.5 * sin(time * (1.0 + hc * 3.0) + hc * 40.0));
        float depDim = mix(1.0, 0.5, dep);
        float lum = vis * tw * depDim;
        vec3 col = mix(colorA.rgb, colorB.rgb, clamp(dep + (hc - 0.5) * 0.5, 0.0, 1.0));

        float r = partSize * 0.04 * sc;
        float coc = dof * 0.02 * abs(zc - focus) * sc;
        float R = r + coc;
        vec2 d2 = q - n.xy;
        float dd = dot(d2, d2);

        if (dd < R * R * 90.0 && lum > 0.002) {
            float dist = sqrt(dd);
            float inF = r / R;
            float e = min(R * 0.9, coc * 0.6 + r * 0.15);
            float disc = 1.0 - smoothstep(R - e, R, dist);
            float rn = clamp(dist / R, 0.0, 1.0);
            float nz = sqrt(1.0 - rn * rn);
            vec2 hp = d2 + vec2(0.3, -0.3) * R;
            float hl = pow(max(0.0, 1.0 - length(hp) / (R * 0.55)), 2.0);
            float sphere = 0.55 + 0.45 * nz + 0.7 * hl;
            float bokeh = 0.7 + 0.5 * rn * rn * rn;
            float shade = mix(bokeh, sphere, inF * inF);
            float discB = 0.2 + 0.8 * inF * inF;
            float halo = (0.35 * exp(-dist / (R * 1.6)) + 0.12 * exp(-dist / (R * 5.0))) * glow;
            float core = exp(-dd / (0.25 * r * r + 0.5 * coc * coc + 0.000001)) * inF;
            acc += (col * (disc * shade * discB + halo) + vec3(core * 0.7)) * lum;
        }

        float gi = mod(fi, 4.0);
        if (links > 0.001 && gi > 0.5) {
            float visH = head.w * smoothstep(0.3, 1.0, head.z);
            float lv = min(vis, visH) * depDim;
            float w1 = 0.002 * (sc + 1.6 / head.z);
            float l1 = 1.0 - smoothstep(0.1, 0.35, length(n.xy - head.xy));
            float ln = seg(q, n.xy, head.xy, w1) * l1;
            if (gi > 1.5) {
                float visP = prev.w * smoothstep(0.3, 1.0, prev.z);
                float w2 = 0.002 * (sc + 1.6 / prev.z);
                float l2 = 1.0 - smoothstep(0.1, 0.35, length(n.xy - prev.xy));
                ln += seg(q, n.xy, prev.xy, w2) * l2 * min(visP, 1.0);
            }
            acc += col * ln * lv * links * 0.5;
        }

        if (gi < 0.5) { head = n; }
        prev = n;
    }

    vec3 add = vec3(1.0) - exp(-acc * intensity);
    float pa = max(add.r, max(add.g, add.b));
    float a = clamp(base.a + pa * (1.0 - base.a), 0.0, 1.0);
    gl_FragColor = vec4(min(base.rgb + add, vec3(a)), a);
}
