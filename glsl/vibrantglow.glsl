// @id vibrantglow
// @name Vibrant Cinematic Glow
// @category glow
// @description High intensity soft bloom with colored energy, adjustable radius and pulse.
// @param strength float 1.8 min=0 max=5 step=0.01 label="Glow Strength"
// @param radius float 32 min=1 max=160 step=0.5 pixels=1 label="Glow Radius"
// @param threshold float 0.65 min=0 max=1 step=0.01 percent=1 label="Glow Threshold"
// @param tint color #69d9ff label="Glow Tint"
// @param pulse float 0.15 min=0 max=3 step=0.01 label="Pulse Speed"
// @param mix float 1 min=0 max=1 step=0.01 percent=1 label="Glow Mix"
uniform sampler2D image;
uniform vec2 size;
uniform float time;
uniform float strength;
uniform float radius;
uniform float threshold;
uniform vec4 tint;
uniform float pulse;
uniform float mix;

void main() {
    vec2 uv = gl_FragCoord.xy / size;
    vec4 base = texture2D(image, uv);
    vec2 px = vec2(radius) / size;
    vec3 bloom = vec3(0.0);
    float total = 0.0;

    for (int y = -4; y <= 4; y++) {
        for (int x = -4; x <= 4; x++) {
            vec2 off = vec2(float(x), float(y)) * px * 0.25;
            vec4 s = texture2D(image, clamp(uv + off, 0.0, 1.0));
            float lum = dot(s.rgb, vec3(0.2126, 0.7152, 0.0722));
            float bright = max(lum - threshold, 0.0) / max(1.0 - threshold, 0.001);
            float w = exp(-dot(vec2(float(x), float(y)), vec2(float(x), float(y))) * 0.16);
            bloom += s.rgb * bright * w;
            total += w;
        }
    }

    bloom /= max(total, 0.001);
    float beat = 1.0 + pulse * 0.35 * sin(time * 6.2831853);
    vec3 glow = bloom * tint.rgb * strength * beat * 3.0;
    vec3 straightBase = base.rgb;
    vec3 outRgb = straightBase + glow * mix;
    float a = clamp(max(base.a, dot(glow, vec3(0.2126,0.7152,0.0722)) * 0.35), 0.0, 1.0);
    gl_FragColor = vec4(min(outRgb, vec3(1.0)) * a, a);
}
