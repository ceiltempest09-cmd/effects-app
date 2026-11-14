// @id colorgrade
// @name Pro Color Grade
// @category other
// @description Advanced grade: exposure, white balance, tone curve, lift/gamma/gain, S-curve, vibrance, hue, split toning, looks, vignette and grain.
// @param exposure float 0 min=-3 max=3 step=0.01 label="Exposure"
// @param temperature float 0 min=-1 max=1 step=0.01 label="Temperature"
// @param tintShift float 0 min=-1 max=1 step=0.01 label="Tint (Green / Magenta)"
// @param toneMap choice 0 options="Off|Filmic|ACES|Soft Roll-off" label="Tone Curve"
// @param toneAmt float 1 min=0 max=1 step=0.01 percent=1 when=toneMap:1,2,3 label="Tone Curve Mix"
// @param lift float 0 min=-0.3 max=0.3 step=0.005 label="Lift (Blacks)"
// @param gamma float 1 min=0.4 max=2.5 step=0.01 label="Gamma (Mids)"
// @param gain float 1 min=0 max=2 step=0.01 label="Gain (Whites)"
// @param shadowsAmt float 0 min=-1 max=1 step=0.01 label="Shadows"
// @param highsAmt float 0 min=-1 max=1 step=0.01 label="Highlights"
// @param contrast float 1.1 min=0.5 max=2 step=0.01 label="Contrast"
// @param curve float 0.3 min=0 max=1 step=0.01 percent=1 label="S-Curve"
// @param saturation float 1.05 min=0 max=2 step=0.01 label="Saturation"
// @param vibrance float 0.3 min=-1 max=1 step=0.01 label="Vibrance"
// @param hue angle 0 label="Hue Shift"
// @param shadowTint color #2a7fa8 label="Shadow Tint"
// @param shadowTintAmt float 0.25 min=0 max=1 step=0.01 percent=1 label="Shadow Tint Amount"
// @param highTint color #ffb26b label="Highlight Tint"
// @param highTintAmt float 0.25 min=0 max=1 step=0.01 percent=1 label="Highlight Tint Amount"
// @param balance float 0 min=-0.5 max=0.5 step=0.01 label="Split Balance"
// @param look choice 0 options="Off|Teal & Orange|Bleach Bypass|Cinematic Fade|Warm Vintage" label="Look"
// @param lookAmt float 0.7 min=0 max=1 step=0.01 percent=1 when=look:1,2,3,4 label="Look Amount"
// @param fade float 0 min=0 max=0.4 step=0.005 label="Black Fade"
// @param vignette float 0.25 min=0 max=1 step=0.01 label="Vignette"
// @param grain float 0.1 min=0 max=1 step=0.01 label="Film Grain"
uniform sampler2D image;
uniform vec2 size;
uniform float time;
uniform float exposure;
uniform float temperature;
uniform float tintShift;
uniform float toneMap;
uniform float toneAmt;
uniform float lift;
uniform float gamma;
uniform float gain;
uniform float shadowsAmt;
uniform float highsAmt;
uniform float contrast;
uniform float curve;
uniform float saturation;
uniform float vibrance;
uniform float hue;
uniform vec4 shadowTint;
uniform float shadowTintAmt;
uniform vec4 highTint;
uniform float highTintAmt;
uniform float balance;
uniform float look;
uniform float lookAmt;
uniform float fade;
uniform float vignette;
uniform float grain;

#define LUMA vec3(0.2126, 0.7152, 0.0722)

float luma(vec3 c) { return dot(c, LUMA); }

vec3 hueRotate(vec3 c, float a) {
    vec3 k = vec3(0.57735);
    float ca = cos(a);
    float sa = sin(a);
    return c * ca + cross(k, c) * sa + k * dot(k, c) * (1.0 - ca);
}

vec3 hable(vec3 x) {
    float A = 0.15;
    float B = 0.50;
    float C = 0.10;
    float D = 0.20;
    float E = 0.02;
    float F = 0.30;
    return ((x * (A * x + C * B) + D * E) / (x * (A * x + B) + D * F)) - E / F;
}

vec3 acesFit(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

vec3 shoulder(vec3 x) {
    float k = 0.6;
    vec3 over = max(x - vec3(k), vec3(0.0));
    return min(x, vec3(k)) + (1.0 - k) * (vec3(1.0) - exp(-over / (1.0 - k)));
}

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec3 applyLook(vec3 c, float id) {
    float l = luma(c);
    if (id < 1.5) {
        c *= mix(vec3(0.80, 1.00, 1.12), vec3(1.15, 1.02, 0.82), smoothstep(0.15, 0.85, l));
        c = mix(vec3(luma(c)), c, 1.15);
    } else if (id < 2.5) {
        c = mix(c, vec3(l), 0.45);
        c = clamp(c, 0.0, 1.0);
        c = mix(c, c * c * (3.0 - 2.0 * c), 0.6);
        c *= 1.05;
    } else if (id < 3.5) {
        c = c * 0.92 + 0.05;
        c = mix(vec3(luma(c)), c, 0.85);
        c *= mix(vec3(0.92, 1.0, 1.06), vec3(1.05, 1.0, 0.94), l);
    } else {
        c = mix(vec3(l), c, 0.8);
        c = c * 0.9 + vec3(0.06, 0.04, 0.02);
        c *= mix(vec3(1.05, 0.98, 1.05), vec3(1.08, 1.0, 0.85), l);
    }
    return c;
}

void main() {
    vec2 uv = gl_FragCoord.xy / size;
    vec4 src = texture2D(image, uv);
    float a = src.a;
    vec3 c = clamp(src.rgb / max(a, 0.0001), 0.0, 1.0);

    // linear stage: exposure, white balance, tone curve
    vec3 lin = pow(c, vec3(2.2));
    lin *= exp2(exposure);
    lin *= vec3(1.0 + 0.3 * temperature, 1.0 - 0.2 * tintShift, 1.0 - 0.3 * temperature);
    if (toneMap > 0.5) {
        vec3 tm;
        if (toneMap < 1.5) {
            tm = hable(lin * 2.0) / hable(vec3(2.0));
        } else if (toneMap < 2.5) {
            tm = acesFit(lin);
        } else {
            tm = shoulder(lin);
        }
        lin = mix(lin, tm, toneAmt);
    }
    c = pow(max(lin, vec3(0.0)), vec3(1.0 / 2.2));

    // display stage: shadows/highlights, lift/gamma/gain, contrast
    float l = luma(c);
    c += shadowsAmt * 0.2 * (1.0 - l) * (1.0 - l);
    c += highsAmt * 0.2 * l * l;
    c = c * gain + lift * (1.0 - c);
    c = pow(max(c, vec3(0.0)), vec3(1.0 / max(gamma, 0.01)));
    c = clamp((c - 0.5) * contrast + 0.5, 0.0, 1.0);
    c = mix(c, c * c * (3.0 - 2.0 * c), curve);

    // saturation, vibrance, hue
    float l2 = luma(c);
    float sat = max(c.r, max(c.g, c.b)) - min(c.r, min(c.g, c.b));
    float vib = max(1.0 + vibrance * (1.0 - sat), 0.0);
    c = mix(vec3(l2), c, saturation * vib);
    c = clamp(hueRotate(c, radians(hue)), 0.0, 1.0);

    // split toning (luminance preserved)
    float l3 = clamp(luma(c), 0.0, 1.0);
    float mid = clamp(0.5 + balance, 0.1, 0.9);
    float sw = 1.0 - smoothstep(0.0, mid, l3);
    float hw = smoothstep(mid, 1.0, l3);
    c *= mix(vec3(1.0), shadowTint.rgb * 2.0, shadowTintAmt * sw);
    c *= mix(vec3(1.0), highTint.rgb * 2.0, highTintAmt * hw);
    c *= clamp(l3 / max(luma(c), 0.001), 0.5, 2.0);
    c = clamp(c, 0.0, 1.0);

    if (look > 0.5) {
        c = mix(c, clamp(applyLook(c, look), 0.0, 1.0), lookAmt);
    }

    c = fade + c * (1.0 - fade);

    vec2 vp = (uv - 0.5) * vec2(size.x / size.y, 1.0);
    c *= 1.0 - vignette * smoothstep(0.35, 0.95, length(vp));

    float gt = mod(floor(time * 24.0), 64.0);
    float gn = hash12(gl_FragCoord.xy + vec2(gt * 37.0, gt * 57.0));
    c += (gn - 0.5) * grain * 0.25 * (1.0 - 0.6 * abs(luma(c) * 2.0 - 1.0));

    c = clamp(c, 0.0, 1.0);
    gl_FragColor = vec4(c * a, a);
}
