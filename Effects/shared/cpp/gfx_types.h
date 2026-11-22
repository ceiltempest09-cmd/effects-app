// gfx_types.h — small replacement types for Skia's SkPoint/SkRect/SkMatrix/SkM44/SkPath/SkColor4f.
// No Skia, GL or Android dependency. Header-only. Names are chosen so later ports are mechanical:
//   SkPoint -> gfx::Vec2   SkRect -> gfx::Rect   SkIRect -> gfx::IRect   SkMatrix -> gfx::Mat3
//   SkM44 -> gfx::Mat4     SkV3/SkV4 -> gfx::Vec3/Vec4   SkColor4f -> gfx::Color4f   SkPath -> gfx::Path
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace gfx {

struct Vec2 { float x = 0, y = 0; };
struct Vec3 { float x = 0, y = 0, z = 0; };
struct Vec4 { float x = 0, y = 0, z = 0, w = 0; };
inline Vec2 operator+(Vec2 a, Vec2 b) { return {a.x + b.x, a.y + b.y}; }
inline Vec2 operator-(Vec2 a, Vec2 b) { return {a.x - b.x, a.y - b.y}; }
inline Vec2 operator*(Vec2 a, float s) { return {a.x * s, a.y * s}; }
inline float Length(Vec2 a) { return std::sqrt(a.x * a.x + a.y * a.y); }

struct IRect {
    int l = 0, t = 0, r = 0, b = 0;
    int width() const { return r - l; }
    int height() const { return b - t; }
    bool empty() const { return r <= l || b <= t; }
};

struct Rect {
    float l = 0, t = 0, r = 0, b = 0;
    static Rect XYWH(float x, float y, float w, float h) { return {x, y, x + w, y + h}; }
    static Rect LTRB(float l, float t, float r, float b) { return {l, t, r, b}; }
    float width() const { return r - l; }
    float height() const { return b - t; }
    float centerX() const { return (l + r) * 0.5f; }
    float centerY() const { return (t + b) * 0.5f; }
    bool empty() const { return !(r > l && b > t); }
    bool contains(Vec2 p) const { return p.x >= l && p.x < r && p.y >= t && p.y < b; }
    void outset(float dx, float dy) { l -= dx; r += dx; t -= dy; b += dy; }
    void join(const Rect& o) {
        if (o.empty()) return;
        if (empty()) { *this = o; return; }
        l = std::min(l, o.l); t = std::min(t, o.t); r = std::max(r, o.r); b = std::max(b, o.b);
    }
    bool intersect(const Rect& o) {
        Rect n{std::max(l, o.l), std::max(t, o.t), std::min(r, o.r), std::min(b, o.b)};
        if (n.empty()) return false;
        *this = n; return true;
    }
    IRect roundOut() const { return {(int)std::floor(l), (int)std::floor(t), (int)std::ceil(r), (int)std::ceil(b)}; }
};

// 3x3 affine/perspective matrix, row-major, acts on column vectors (x' = m * [x y 1]).
struct Mat3 {
    float m[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
    static Mat3 Identity() { return {}; }
    static Mat3 Translate(float tx, float ty) { Mat3 a; a.m[2] = tx; a.m[5] = ty; return a; }
    static Mat3 Scale(float sx, float sy) { Mat3 a; a.m[0] = sx; a.m[4] = sy; return a; }
    static Mat3 Rotate(float radians) {
        Mat3 a; const float c = std::cos(radians), s = std::sin(radians);
        a.m[0] = c; a.m[1] = -s; a.m[3] = s; a.m[4] = c; return a;
    }
    // this * o : o is applied first
    Mat3 operator*(const Mat3& o) const {
        Mat3 r;
        for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) {
            float v = 0; for (int k = 0; k < 3; k++) v += m[i * 3 + k] * o.m[k * 3 + j];
            r.m[i * 3 + j] = v;
        }
        return r;
    }
    Vec2 map(Vec2 p) const {
        const float x = m[0] * p.x + m[1] * p.y + m[2], y = m[3] * p.x + m[4] * p.y + m[5];
        const float w = m[6] * p.x + m[7] * p.y + m[8];
        return (w != 0 && w != 1) ? Vec2{x / w, y / w} : Vec2{x, y};
    }
    Rect mapBounds(const Rect& r) const {
        const Vec2 c[4] = {map({r.l, r.t}), map({r.r, r.t}), map({r.r, r.b}), map({r.l, r.b})};
        Rect o{c[0].x, c[0].y, c[0].x, c[0].y};
        for (int i = 1; i < 4; i++) { o.l = std::min(o.l, c[i].x); o.r = std::max(o.r, c[i].x); o.t = std::min(o.t, c[i].y); o.b = std::max(o.b, c[i].y); }
        return o;
    }
    float det() const {
        return m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6]);
    }
    bool invert(Mat3* out) const {
        const float d = det();
        if (std::fabs(d) < 1e-20f) return false;
        const float i = 1.0f / d; Mat3 r;
        r.m[0] = (m[4] * m[8] - m[5] * m[7]) * i; r.m[1] = (m[2] * m[7] - m[1] * m[8]) * i; r.m[2] = (m[1] * m[5] - m[2] * m[4]) * i;
        r.m[3] = (m[5] * m[6] - m[3] * m[8]) * i; r.m[4] = (m[0] * m[8] - m[2] * m[6]) * i; r.m[5] = (m[2] * m[3] - m[0] * m[5]) * i;
        r.m[6] = (m[3] * m[7] - m[4] * m[6]) * i; r.m[7] = (m[1] * m[6] - m[0] * m[7]) * i; r.m[8] = (m[0] * m[4] - m[1] * m[3]) * i;
        *out = r; return true;
    }
    // Column-major 3x3 for glUniformMatrix3fv(transpose=GL_FALSE).
    void toGL(float out[9]) const { for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) out[c * 3 + r] = m[r * 3 + c]; }
};

// 4x4 matrix, row-major storage, column vectors.
struct Mat4 {
    float m[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    static Mat4 Identity() { return {}; }
    static Mat4 Translate(float x, float y, float z = 0) { Mat4 a; a.m[3] = x; a.m[7] = y; a.m[11] = z; return a; }
    static Mat4 Scale(float x, float y, float z = 1) { Mat4 a; a.m[0] = x; a.m[5] = y; a.m[10] = z; return a; }
    static Mat4 RotateZ(float rad) { Mat4 a; a.m[0] = std::cos(rad); a.m[1] = -std::sin(rad); a.m[4] = std::sin(rad); a.m[5] = std::cos(rad); return a; }
    static Mat4 RotateY(float rad) { Mat4 a; a.m[0] = std::cos(rad); a.m[2] = std::sin(rad); a.m[8] = -std::sin(rad); a.m[10] = std::cos(rad); return a; }
    static Mat4 RotateX(float rad) { Mat4 a; a.m[5] = std::cos(rad); a.m[6] = -std::sin(rad); a.m[9] = std::sin(rad); a.m[10] = std::cos(rad); return a; }
    static Mat4 Perspective(float fovyRad, float aspect, float zn, float zf) {
        Mat4 a; const float f = 1.0f / std::tan(fovyRad * 0.5f);
        for (float& v : a.m) v = 0;
        a.m[0] = f / aspect; a.m[5] = f; a.m[10] = (zf + zn) / (zn - zf); a.m[11] = 2 * zf * zn / (zn - zf); a.m[14] = -1;
        return a;
    }
    Mat4 operator*(const Mat4& o) const {
        Mat4 r;
        for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) {
            float v = 0; for (int k = 0; k < 4; k++) v += m[i * 4 + k] * o.m[k * 4 + j];
            r.m[i * 4 + j] = v;
        }
        return r;
    }
    Vec4 map(Vec4 v) const {
        return {m[0] * v.x + m[1] * v.y + m[2] * v.z + m[3] * v.w, m[4] * v.x + m[5] * v.y + m[6] * v.z + m[7] * v.w,
                m[8] * v.x + m[9] * v.y + m[10] * v.z + m[11] * v.w, m[12] * v.x + m[13] * v.y + m[14] * v.z + m[15] * v.w};
    }
    void toGL(float out[16]) const { for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) out[c * 4 + r] = m[r * 4 + c]; }
    // Drop z: the 2D affine part used for layers.
    Mat3 asMat3() const { Mat3 a; a.m[0] = m[0]; a.m[1] = m[1]; a.m[2] = m[3]; a.m[3] = m[4]; a.m[4] = m[5]; a.m[5] = m[7]; a.m[6] = m[12]; a.m[7] = m[13]; a.m[8] = m[15]; return a; }
};

struct Color4f {
    float r = 0, g = 0, b = 0, a = 1;
    static Color4f FromARGB(uint32_t argb) {
        return {((argb >> 16) & 255) / 255.f, ((argb >> 8) & 255) / 255.f, (argb & 255) / 255.f, ((argb >> 24) & 255) / 255.f};
    }
    uint32_t toARGB() const {
        auto q = [](float v) { return (uint32_t)(std::min(1.f, std::max(0.f, v)) * 255.f + 0.5f); };
        return (q(a) << 24) | (q(r) << 16) | (q(g) << 8) | q(b);
    }
    Color4f premul() const { return {r * a, g * a, b * a, a}; }
};

// Path: move/line/quad/cubic/close. Curves are flattened to lines for stroking, filling and hit tests;
// the GL renderer tessellates the flattened polylines.
enum class Verb : uint8_t { Move, Line, Quad, Cubic, Close };

struct Path {
    std::vector<Verb> verbs;
    std::vector<Vec2> pts;   // Move:1 Line:1 Quad:2 Cubic:3 Close:0

    Path& moveTo(float x, float y) { verbs.push_back(Verb::Move); pts.push_back({x, y}); return *this; }
    Path& lineTo(float x, float y) { verbs.push_back(Verb::Line); pts.push_back({x, y}); return *this; }
    Path& quadTo(float cx, float cy, float x, float y) { verbs.push_back(Verb::Quad); pts.push_back({cx, cy}); pts.push_back({x, y}); return *this; }
    Path& cubicTo(float c1x, float c1y, float c2x, float c2y, float x, float y) {
        verbs.push_back(Verb::Cubic); pts.push_back({c1x, c1y}); pts.push_back({c2x, c2y}); pts.push_back({x, y}); return *this;
    }
    Path& close() { verbs.push_back(Verb::Close); return *this; }
    Path& addRect(const Rect& r) { return moveTo(r.l, r.t).lineTo(r.r, r.t).lineTo(r.r, r.b).lineTo(r.l, r.b).close(); }
    bool empty() const { return verbs.empty(); }

    Path transformed(const Mat3& mat) const { Path o; o.verbs = verbs; o.pts.reserve(pts.size()); for (Vec2 p : pts) o.pts.push_back(mat.map(p)); return o; }

    // Control-point bounds (a conservative superset of the curve bounds, same as SkPath::getBounds).
    Rect bounds() const {
        if (pts.empty()) return {};
        Rect r{pts[0].x, pts[0].y, pts[0].x, pts[0].y};
        for (Vec2 p : pts) { r.l = std::min(r.l, p.x); r.r = std::max(r.r, p.x); r.t = std::min(r.t, p.y); r.b = std::max(r.b, p.y); }
        return r;
    }

    struct Contour { std::vector<Vec2> pts; bool closed = false; };
    // tolerance: max distance (in path units) between the curve and its polyline.
    std::vector<Contour> flatten(float tolerance = 0.25f) const {
        std::vector<Contour> out; size_t pi = 0; Vec2 cur{}, start{};
        auto steps = [&](float len) { return std::max(1, std::min(256, (int)std::ceil(std::sqrt(len / std::max(tolerance, 1e-3f)) * 1.5f))); };
        for (Verb v : verbs) {
            switch (v) {
                case Verb::Move: out.emplace_back(); cur = start = pts[pi++]; out.back().pts.push_back(cur); break;
                case Verb::Line: { if (out.empty()) { out.emplace_back(); out.back().pts.push_back(cur); } cur = pts[pi++]; out.back().pts.push_back(cur); break; }
                case Verb::Quad: {
                    if (out.empty()) { out.emplace_back(); out.back().pts.push_back(cur); }
                    const Vec2 c = pts[pi], e = pts[pi + 1]; pi += 2;
                    const int n = steps(Length(c - cur) + Length(e - c));
                    for (int i = 1; i <= n; i++) { const float t = (float)i / n, u = 1 - t; out.back().pts.push_back({u * u * cur.x + 2 * u * t * c.x + t * t * e.x, u * u * cur.y + 2 * u * t * c.y + t * t * e.y}); }
                    cur = e; break;
                }
                case Verb::Cubic: {
                    if (out.empty()) { out.emplace_back(); out.back().pts.push_back(cur); }
                    const Vec2 c1 = pts[pi], c2 = pts[pi + 1], e = pts[pi + 2]; pi += 3;
                    const int n = steps(Length(c1 - cur) + Length(c2 - c1) + Length(e - c2));
                    for (int i = 1; i <= n; i++) {
                        const float t = (float)i / n, u = 1 - t;
                        const float a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
                        out.back().pts.push_back({a * cur.x + b * c1.x + c * c2.x + d * e.x, a * cur.y + b * c1.y + c * c2.y + d * e.y});
                    }
                    cur = e; break;
                }
                case Verb::Close: if (!out.empty()) { out.back().closed = true; cur = start; } break;
            }
        }
        return out;
    }
    // Total length of the flattened contours.
    float length(float tolerance = 0.25f) const {
        float len = 0;
        for (const Contour& c : flatten(tolerance)) {
            for (size_t i = 1; i < c.pts.size(); i++) len += Length(c.pts[i] - c.pts[i - 1]);
            if (c.closed && c.pts.size() > 1) len += Length(c.pts.front() - c.pts.back());
        }
        return len;
    }
    // Non-zero winding point-in-path test on the flattened path.
    bool contains(Vec2 p, float tolerance = 0.25f) const {
        int wn = 0;
        for (const Contour& c : flatten(tolerance)) {
            const size_t n = c.pts.size(); if (n < 2) continue;
            for (size_t i = 0; i < n; i++) {
                const Vec2 a = c.pts[i], b = c.pts[(i + 1) % n];
                const float cross = (b.x - a.x) * (p.y - a.y) - (p.x - a.x) * (b.y - a.y);
                if (a.y <= p.y) { if (b.y > p.y && cross > 0) wn++; }
                else if (b.y <= p.y && cross < 0) wn--;
            }
        }
        return wn != 0;
    }
};

}  // namespace gfx
