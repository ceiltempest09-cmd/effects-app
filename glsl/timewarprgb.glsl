// @id timewarprgb
// @name Timewarp RGB
// @category glitch
// @description AE-style RGB time warp: red, green and blue run on offset clocks with speed ramps, echo trails, zoom/wave warp and optional frame hold.
// @param amount float 0.04 min=0 max=0.3 step=0.001 label="RGB Offset"
// @param dir angle 0 label="Direction"
// @param delay float 0.12 min=0 max=1 step=0.005 label="Channel Delay (sec)"
// @param warpSpeed float 0.6 min=0 max=6 step=0.01 label="Warp Speed"
// @param warpMode choice 2 options="Sine|Ping-Pong|Speed Ramp|Pulse" label="Warp Curve"
// @param trail float 0.5 min=0 max=1 step=0.01 label="Echo Trail"
// @param trailFade float 0.6 min=0 max=1 step=0.01 label="Echo Falloff"
// @param zoomWarp float 0.04 min=-0.3 max=0.3 step=0.001 label="Zoom Warp"
// @param wave float 0.3 min=0 max=2 step=0.01 label="Wave Warp"
// @param waveFreq float 6 min=1 max=40 step=0.1 label="Wave Frequency"
// @param holdFps float 0 min=0 max=60 step=1 label="Frame Hold FPS (0 = off)"
// @param frameBlend float 0.5 min=0 max=1 step=0.01 percent=1 label="Frame Blend"
// @param mixAmt float 1 min=0 max=1 step=0.01 percent=1 label="Effect Amount"
uniform sampler2D image;
uniform vec2 size;
uniform float time;
uniform float amount;
uniform float dir;
uniform float delay;
uniform float warpSpeed;
uniform float warpMode;
uniform float trail;
uniform float trailFade;
uniform float zoomWarp;
uniform float wave;
uniform float waveFreq;
uniform float holdFps;
uniform float frameBlend;
uniform float mixAmt;

vec4 tap(vec2 p) {
    float inside = step(0.0, p.x) * step(p.x, 1.0) * step(0.0, p.y) * step(p.y, 1.0);
    return texture2D(image, clamp(p, 0.0, 1.0)) * inside;
}

// roughly zero-mean curve in about -1..1
float warpCurve(float t) {
    float x = t * warpSpeed;
    float f = fract(x);
    if (warpMode < 0.5) {
        return sin(x * 6.2831853);
    }
    if (warpMode < 1.5) {
        return abs(f * 2.0 - 1.0) * 2.0 - 1.0;
    }
    if (warpMode < 2.5) {
        return f * f * f * 2.0 - 0.5;
    }
    float p = abs(sin(x * 3.14159265));
    return pow(p, 8.0) * 2.0 - 0.55;
}

vec4 chan(vec2 uv, float t) {
    float m = warpCurve(t);
    vec2 asp = vec2(size.y / size.x, 1.0);
    float da = radians(dir);
    vec2 dv = vec2(cos(da), sin(da)) * asp;
    vec2 p = (uv - 0.5) / (1.0 + zoomWarp * m) + 0.5;
    p += dv * amount * m;
    p.x += sin(p.y * waveFreq + t * warpSpeed * 6.2831853) * wave * 0.01;
    if (trail < 0.001) {
        return tap(p);
    }
    vec4 acc = vec4(0.0);
    float ws = 0.0;
    for (int j = 0; j < 8; j++) {
        float fj = float(j) / 7.0;
        float w = exp(-fj * (0.5 + (1.0 - trailFade) * 4.0));
        acc += tap(p - dv * (trail * 0.15 * m) * fj) * w;
        ws += w;
    }
    return acc / ws;
}

void main() {
    vec2 uv = gl_FragCoord.xy / size;
    vec4 base = texture2D(image, uv);

    float te = time;
    if (holdFps > 0.5) {
        float ft = time * holdFps;
        float t0 = floor(ft) / holdFps;
        float s = clamp((fract(ft) - (1.0 - frameBlend)) / max(frameBlend, 0.001), 0.0, 1.0);
        s = s * s * (3.0 - 2.0 * s);
        te = t0 + s / holdFps;
    }

    vec4 r = chan(uv, te + delay);
    vec4 g = chan(uv, te);
    vec4 b = chan(uv, te - delay);
    float a = max(r.a, max(g.a, b.a));
    vec4 fx = vec4(min(vec3(r.r, g.g, b.b), vec3(a)), a);
    gl_FragColor = mix(base, fx, mixAmt);
}
