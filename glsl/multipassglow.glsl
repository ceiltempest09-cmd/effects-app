// @id multipassglow
// @name Multi-Pass Glow
// @category glow
// @description Multi-pass bloom: five stacked glow passes of growing radius with an A/B colour ramp, anamorphic stretch and pulse.
// @param threshold float 0.55 min=0 max=1 step=0.01 label="Threshold"
// @param blur float 6 min=0 max=60 step=0.1 pixels=1 label="Pre-Blur"
// @param radius float 18 min=1 max=120 step=0.1 pixels=1 label="Pass 1 Radius"
// @param spread float 2.2 min=1.2 max=4 step=0.01 label="Radius Growth per Pass"
// @param falloff float 0.7 min=0.2 max=1.2 step=0.01 label="Falloff per Pass"
// @param intensity float 1.5 min=0 max=6 step=0.01 label="Intensity"
// @param colorMix float 0.6 min=0 max=1 step=0.01 percent=1 label="Color Ramp Mix"
// @param glowA color #ffb347 label="Color A (Inner)"
// @param glowB color #ff2e9a label="Color B (Outer)"
// @param stretch float 0 min=0 max=1 step=0.01 label="Anamorphic Stretch"
// @param composite choice 0 options="Add|Screen|Glow Only" label="Composite"
// @param pulse float 0 min=0 max=1 step=0.01 percent=1 label="Pulse"
// @param pulseSpeed float 1 min=0 max=6 step=0.01 label="Pulse Speed"
uniform sampler2D image;
uniform sampler2D bright;
uniform vec2 size;
uniform float time;
uniform float threshold;
uniform float blur;
uniform float radius;
uniform float spread;
uniform float falloff;
uniform float intensity;
uniform float colorMix;
uniform vec4 glowA;
uniform vec4 glowB;
uniform float stretch;
uniform float composite;
uniform float pulse;
uniform float pulseSpeed;

vec3 ring(vec2 uv, float rpx, float rot, vec2 sx) {
    vec3 s = vec3(0.0);
    for (int j = 0; j < 10; j++) {
        float a = float(j) * 0.6283185 + rot;
        vec2 o = vec2(cos(a), sin(a)) * rpx * sx / size;
        s += texture2D(bright, uv + o).rgb;
    }
    return s * 0.1;
}

void main() {
    vec2 uv = gl_FragCoord.xy / size;
    vec4 base = texture2D(image, uv);
    vec2 sx = vec2(1.0 + stretch * 5.0, 1.0);
    float pls = 1.0 - pulse * (0.5 + 0.5 * sin(time * 6.2831853 * pulseSpeed));

    vec3 gsum = vec3(0.0);
    float w = 1.0;
    for (int p = 0; p < 5; p++) {
        float fp = float(p);
        vec3 s;
        if (p == 0) {
            s = texture2D(bright, uv).rgb;
        } else {
            s = ring(uv, radius * pow(spread, fp - 1.0), fp * 0.5, sx);
        }
        vec3 ramp = mix(glowA.rgb, glowB.rgb, fp / 4.0);
        float l = dot(s, vec3(0.2126, 0.7152, 0.0722));
        gsum += mix(s, ramp * l * 1.5, colorMix) * w;
        w *= falloff;
    }
    vec3 g = vec3(1.0) - exp(-gsum * intensity * pls);
    float ga = max(g.r, max(g.g, g.b));

    vec3 rgb;
    float a;
    if (composite < 0.5) {
        rgb = base.rgb + g;
        a = clamp(base.a + ga * (1.0 - base.a), 0.0, 1.0);
    } else if (composite < 1.5) {
        rgb = base.rgb + g - base.rgb * g;
        a = clamp(base.a + ga * (1.0 - base.a), 0.0, 1.0);
    } else {
        rgb = g;
        a = ga;
    }
    gl_FragColor = vec4(min(rgb, vec3(a)), a);
}
