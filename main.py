#!/usr/bin/env python3
"""One ripe strawberry, cut in half -- macro close-up, 1024x1024 PNG.

Standard library only (zlib + struct for the PNG encoder). Deterministic:
repeated runs produce byte-identical output. Writes /output/entry.png
by default (created if needed); an optional argv[1] overrides the path.
"""
import math
import os
import struct
import sys
import zlib

W = H = 1024

# ---------------------------------------------------------------- utilities
def clamp255(v):
    return 0 if v < 0 else 255 if v > 255 else int(v)


_seed = [987654321]


def rnd():
    _seed[0] = (_seed[0] * 1103515245 + 12345) & 0x7FFFFFFF
    return _seed[0] / 0x7FFFFFFF


def mix(a, b, t):
    return a + (b - a) * t


def lerp3(c0, c1, t):
    return (mix(c0[0], c1[0], t), mix(c0[1], c1[1], t), mix(c0[2], c1[2], t))


# noise table for grain (deterministic, +/-1 to keep the PNG small)
NOISE = [int(rnd() * 3) - 1 for _ in range(4096)]

buf = bytearray(W * H * 3)


def put(x, y, r, g, b):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 3
        buf[i] = clamp255(r)
        buf[i + 1] = clamp255(g)
        buf[i + 2] = clamp255(b)


def blend_px(x, y, r, g, b, a):
    """Alpha-blend src colour over the buffer pixel. a in [0,1]."""
    if a <= 0.0 or not (0 <= x < W and 0 <= y < H):
        return
    if a >= 1.0:
        put(x, y, r, g, b)
        return
    i = (y * W + x) * 3
    buf[i] = clamp255(buf[i] + (r - buf[i]) * a)
    buf[i + 1] = clamp255(buf[i + 1] + (g - buf[i + 1]) * a)
    buf[i + 2] = clamp255(buf[i + 2] + (b - buf[i + 2]) * a)


def fill_span(y, x0, x1, c0, c1):
    """Fill row y from x0..x1 with a horizontal gradient c0 -> c1."""
    if y < 0 or y >= H:
        return
    x0 = max(0, int(x0))
    x1 = min(W - 1, int(x1))
    if x1 < x0:
        return
    n = x1 - x0
    base = y * W * 3
    for x in range(x0, x1 + 1):
        t = (x - x0) / n if n else 0.0
        i = base + x * 3
        buf[i] = clamp255(mix(c0[0], c1[0], t))
        buf[i + 1] = clamp255(mix(c0[1], c1[1], t))
        buf[i + 2] = clamp255(mix(c0[2], c1[2], t))


def blend_span(y, x0, x1, r, g, b, a):
    """Blend a flat colour over a horizontal span."""
    if y < 0 or y >= H or a <= 0.0:
        return
    x0 = max(0, int(x0))
    x1 = min(W - 1, int(x1))
    if x1 < x0:
        return
    base = y * W * 3
    for x in range(x0, x1 + 1):
        i = base + x * 3
        buf[i] = clamp255(buf[i] + (r - buf[i]) * a)
        buf[i + 1] = clamp255(buf[i + 1] + (g - buf[i + 1]) * a)
        buf[i + 2] = clamp255(buf[i + 2] + (b - buf[i + 2]) * a)


def ellipse_rows(cx, cy, rx, ry):
    """Yield (y, x0, x1, row_falloff) for rows of an ellipse."""
    y0 = max(0, int(math.ceil(cy - ry)))
    y1 = min(H - 1, int(math.floor(cy + ry)))
    for y in range(y0, y1 + 1):
        dy = (y - cy) / ry
        rem = 1.0 - dy * dy
        if rem <= 0.0:
            continue
        dx = rx * math.sqrt(rem)
        yield y, cx - dx, cx + dx, rem


def blend_ellipse(cx, cy, rx, ry, r, g, b, a0, soft=1.0):
    """Soft radial-gradient ellipse blend (alpha peaks at centre)."""
    for y, x0, x1, rem in ellipse_rows(cx, cy, rx, ry):
        # per-pixel falloff across the row
        xi0 = max(0, int(math.ceil(x0)))
        xi1 = min(W - 1, int(math.floor(x1)))
        if xi1 < xi0:
            continue
        base = y * W * 3
        for x in range(xi0, xi1 + 1):
            dxn = (x - cx) / rx
            f = rem - dxn * dxn
            if f <= 0.0:
                continue
            a = a0 * (f ** soft)
            i = base + x * 3
            buf[i] = clamp255(buf[i] + (r - buf[i]) * a)
            buf[i + 1] = clamp255(buf[i + 1] + (g - buf[i + 1]) * a)
            buf[i + 2] = clamp255(buf[i + 2] + (b - buf[i + 2]) * a)


def fill_triangle(p0, p1, p2, c_top, c_bot):
    """Scanline-fill a triangle, vertical gradient c_top -> c_bot."""
    _tri_rows(p0, p1, p2, lambda y, x0, x1, t: fill_span(
        y, x0, x1, lerp3(c_top, c_bot, t), lerp3(c_top, c_bot, t)))


def blend_triangle(p0, p1, p2, r, g, b, a):
    """Scanline-fill a triangle with a flat colour at alpha a."""
    _tri_rows(p0, p1, p2, lambda y, x0, x1, t: blend_span(y, x0, x1, r, g, b, a))


def _tri_rows(p0, p1, p2, fill):
    pts = sorted((p0, p1, p2), key=lambda p: p[1])
    (x0, y0), (x1, y1), (x2, y2) = pts
    if y2 - y0 < 1:
        return

    def edge_x(pa, pb, y):
        if pb[1] == pa[1]:
            return pa[0]
        return pa[0] + (pb[0] - pa[0]) * (y - pa[1]) / (pb[1] - pa[1])

    y_start = max(0, int(math.ceil(y0)))
    y_end = min(H - 1, int(math.floor(y2)))
    for y in range(y_start, y_end + 1):
        xs = []
        if y0 <= y <= y1:
            xs.append(edge_x((x0, y0), (x1, y1), y))
        if y1 <= y <= y2:
            xs.append(edge_x((x1, y1), (x2, y2), y))
        if y0 <= y <= y2:
            xs.append(edge_x((x0, y0), (x2, y2), y))
        if len(xs) < 2:
            continue
        t = (y - y0) / (y2 - y0) if y2 > y0 else 0.0
        fill(y, min(xs), max(xs), t)


# ---------------------------------------------------------------- background
def draw_background():
    # vertical slate gradient, warm glow upper-left, cool glow lower-right
    for y in range(H):
        t = y / (H - 1)
        base = lerp3((36, 41, 50), (11, 13, 16), t)
        # warm radial glow centred (300, 230)
        dy = (y - 230) / 720.0
        warm = max(0.0, 1.0 - dy * dy)
        c = (base[0] + 52 * warm, base[1] + 30 * warm, base[2] + 8 * warm)
        # cool glow lower right
        dy2 = (y - 820) / 620.0
        cool = max(0.0, 1.0 - dy2 * dy2)
        c = (c[0] + 2 * cool, c[1] + 9 * cool, c[2] + 16 * cool)
        fill_span(y, 0, W - 1, c, c)
    # soft warm halo behind the berry (upper left)
    blend_ellipse(330, 300, 420, 380, 96, 62, 34, 0.16)
    blend_ellipse(330, 300, 260, 230, 120, 80, 44, 0.10)
    # bokeh dots
    bokeh = [
        (120, 130, 34, 150, 110, 60, 0.10),
        (880, 110, 26, 120, 130, 160, 0.08),
        (940, 700, 44, 140, 100, 55, 0.07),
        (80, 820, 30, 110, 120, 150, 0.07),
        (700, 950, 22, 150, 115, 60, 0.08),
        (180, 480, 18, 130, 110, 70, 0.06),
    ]
    for cx, cy, r, rr, gg, bb, a in bokeh:
        blend_ellipse(cx, cy, r, r, rr, gg, bb, a)


# ---------------------------------------------------------------- strawberry
def half_width(s):
    """Berry half-width profile, s in [0,1] top->tip. Rounded shoulder
    (narrow at the very top where the calyx sits, widest near s~0.25),
    plump middle, tapering to a rounded tip."""
    if s <= 0.0:
        return 0.30
    if s >= 1.0:
        return 0.0
    shoulder = 0.30 + 0.70 * (1.0 - math.exp(-s * 7.0))
    return (1.0 - s ** 2.6) ** 0.55 * shoulder


def berry_rows(cx, top, height, halfw):
    """Yield (y, s, x_left, x_right) for a berry outline."""
    y0 = max(0, int(math.ceil(top)))
    y1 = min(H - 1, int(math.floor(top + height)))
    for y in range(y0, y1 + 1):
        s = (y - top) / height
        hw = halfw * half_width(s)
        if hw < 0.5:
            continue
        lean = 18.0 * math.sin(s * 1.1)  # gentle lean to the right
        yield y, s, cx + lean - hw, cx + lean + hw


# skin palette (deep -> bright)
SKIN_DARK = (109, 10, 18)
SKIN_MID = (176, 18, 30)
SKIN_BRIGHT = (226, 42, 46)
SKIN_HI = (244, 84, 66)


def draw_berry(cx, top, height, halfw, dark, face=False):
    """Draw one whole berry (skin). If face=True, also draw the cut face."""
    rows = list(berry_rows(cx, top, height, halfw))
    for y, s, xl, xr in rows:
        # vertical light: brightest near the shoulder, darker at the tip
        light = 1.0 - 0.55 * s
        # horizontal light: warm from the left
        c_left = lerp3(SKIN_DARK, SKIN_BRIGHT, 0.55 * light + 0.25)
        c_right = lerp3(SKIN_DARK, SKIN_MID, 0.35 * light)
        c_mid = lerp3(SKIN_MID, SKIN_HI, 0.35 * light)
        if dark:
            c_left = tuple(v * 0.50 for v in c_left)
            c_right = tuple(v * 0.50 for v in c_right)
            c_mid = tuple(v * 0.50 for v in c_mid)
        mid = (xl + xr) / 2.0
        fill_span(y, xl, mid, c_left, c_mid)
        fill_span(y, mid, xr, c_mid, c_right)
    if face:
        draw_cut_face(cx, top, height, halfw)


def draw_cut_face(cx, top, height, halfw):
    """The cut face: lighter juicy flesh, pale core, juice veins, gloss."""
    ftop = top + 58
    fheight = height * 0.86
    fhalfw = halfw * 0.83
    rows = list(berry_rows(cx, ftop, fheight, fhalfw))
    core_cx = cx + 18.0 * math.sin(0.55 * 1.1)
    core_cy = ftop + fheight * 0.52
    for y, s, xl, xr in rows:
        # flesh gradient: bright juicy red upper-left, deep red at rim/bottom
        t = s
        c_edge = lerp3((198, 26, 36), (150, 12, 24), t)
        c_centre = lerp3((238, 74, 66), (214, 40, 44), t)
        mid = (xl + xr) / 2.0
        fill_span(y, xl, mid, c_edge, c_centre)
        fill_span(y, mid, xr, c_centre, c_edge)
    # subtle fibre streaks: thin vertical lighter lines inside the face
    for k in range(14):
        fx = cx - fhalfw * 0.72 + k * (fhalfw * 1.44 / 13.0)
        fy0 = ftop + 40
        fy1 = ftop + fheight * 0.92
        for y in range(int(fy0), int(fy1)):
            s = (y - ftop) / fheight
            hw = fhalfw * half_width(s)
            if abs(fx - (cx + 18.0 * math.sin(s * 1.1))) < hw * 0.94:
                blend_px(int(fx), y, 255, 120, 100, 0.03)
    # juice veins radiating from the core: few, short, faint, lighter pink
    for k in range(5):
        ang = -60 + k * 30.0  # degrees, fanning downward-out
        rad = math.radians(ang)
        tipx = core_cx + math.cos(rad) * fhalfw * 0.45
        tipy = core_cy + math.sin(rad) * fheight * 0.24
        perp = (math.sin(rad), -math.cos(rad))
        wdt = 4.0
        b0 = (core_cx + perp[0] * wdt, core_cy + perp[1] * wdt)
        b1 = (core_cx - perp[0] * wdt, core_cy - perp[1] * wdt)
        blend_triangle(b0, b1, (tipx, tipy), 250, 130, 112, 0.15)
    # pale central core (pith), soft-edged, muted -- a hint, not a glow
    blend_ellipse(core_cx, core_cy, 40, 85, 244, 178, 158, 0.32)
    blend_ellipse(core_cx, core_cy, 26, 62, 252, 210, 190, 0.28)
    # tiny pith dots
    for k in range(7):
        px = core_cx + (rnd() - 0.5) * 55
        py = core_cy + (rnd() - 0.5) * 160
        blend_ellipse(px, py, 4, 6, 240, 170, 140, 0.30)
    # gloss: one modest juicy sheen upper-left of the face, one faint low
    blend_ellipse(cx - 130, ftop + 210, 105, 62, 255, 235, 220, 0.12)
    blend_ellipse(cx - 95, ftop + 185, 55, 34, 255, 250, 245, 0.15)
    blend_ellipse(cx + 130, ftop + 560, 80, 48, 255, 225, 205, 0.10)


def draw_seed(x, y, rx, ry, pale=True):
    """One achene: dark rim, pale cream body, tiny specular dot."""
    blend_ellipse(x, y, rx + 1.5, ry + 1.5, 120, 70, 30, 0.55)
    blend_ellipse(x, y, rx, ry, 246, 231, 160, 0.95)
    blend_ellipse(x - rx * 0.25, y - ry * 0.35, rx * 0.45, ry * 0.4,
                  255, 248, 210, 0.8)
    blend_px(int(x - rx * 0.2), int(y - ry * 0.3), 255, 255, 255, 0.9)


def draw_seeds(cx, top, height, halfw, scale=1.0, dark=False):
    """Achenes in rings following the berry outline."""
    for ring in range(11):
        s = 0.10 + ring * 0.08
        y = top + s * height
        hw = halfw * half_width(s)
        lean = 18.0 * math.sin(s * 1.1)
        n = 3 + ring % 2
        for j in range(n):
            # positions across the berry width, kept inside the skin ring
            frac = -0.88 + (1.76 * (j + 0.5) / n)
            jx = (rnd() - 0.5) * 14
            jy = (rnd() - 0.5) * 10
            x = cx + lean + frac * hw + jx
            # skip seeds that would sit on the cut face (face berries)
            if abs(frac) < 0.62:
                continue
            rx = 8.5 * scale
            ry = 12.0 * scale
            draw_seed(x, y + jy, rx, ry)


def draw_calyx(cx, top):
    """Green sepals fanning from the top of the berry + short stem."""
    base = (cx + 6, top + 34)
    sepal_angles = [-168, -138, -108, -78, -48, -18]
    sepal_lens = [150, 128, 108, 118, 132, 148]
    for ang, ln in zip(sepal_angles, sepal_lens):
        rad = math.radians(ang)
        dx, dy = math.cos(rad), math.sin(rad)
        tip = (base[0] + dx * ln, base[1] + dy * ln)
        # base width perpendicular to the sepal direction
        px, py = -dy, dx
        wdt = 17.0
        b0 = (base[0] + px * wdt, base[1] + py * wdt)
        b1 = (base[0] - px * wdt, base[1] - py * wdt)
        fill_triangle(b0, b1, tip, (30, 96, 34), (96, 168, 74))
        # centre vein
        fill_triangle((base[0] + px * 3, base[1] + py * 3),
                      (base[0] - px * 3, base[1] - py * 3),
                      (base[0] + dx * ln * 0.85, base[1] + dy * ln * 0.85),
                      (20, 66, 22), (52, 110, 44))
    # stem
    fill_triangle((cx - 4, top - 46), (cx + 10, top - 46),
                  (cx + 8, top + 30), (96, 116, 52), (58, 78, 30))
    # soft shadow where the calyx meets the berry
    blend_ellipse(cx + 6, top + 40, 120, 26, 60, 8, 10, 0.35)


def draw_droplet(x, y, r):
    """A glossy juice/water droplet: pale centre, bright specular dot."""
    blend_ellipse(x, y, r, r * 0.92, 255, 200, 185, 0.22)
    blend_ellipse(x, y + r * 0.18, r * 0.82, r * 0.72, 210, 60, 60, 0.18)
    blend_ellipse(x - r * 0.34, y - r * 0.38, r * 0.24, r * 0.17,
                  255, 255, 255, 0.8)
    blend_ellipse(x + r * 0.45, y + r * 0.42, r * 0.28, r * 0.18,
                  255, 255, 255, 0.25)


# ---------------------------------------------------------------- finish
def draw_shadow_and_finish():
    # soft ground shadow under the berry
    blend_ellipse(470, 952, 400, 52, 0, 0, 0, 0.45)
    blend_ellipse(470, 948, 300, 38, 0, 0, 0, 0.35)
    # vignette
    cx, cy = W / 2.0, H / 2.0
    maxd = math.hypot(cx, cy)
    for y in range(H):
        dy = (y - cy) / maxd
        base = y * W * 3
        for x in range(W):
            dx = (x - cx) / maxd
            d = math.sqrt(dx * dx + dy * dy)
            f = 1.0 - 0.30 * (d ** 2.4)
            n = NOISE[(x * 7 + y * 13) & 4095]
            i = base + x * 3
            buf[i] = clamp255(buf[i] * f + n)
            buf[i + 1] = clamp255(buf[i + 1] * f + n)
            buf[i + 2] = clamp255(buf[i + 2] * f + n)


def render():
    draw_background()
    # back berry (right, smaller, darker) -- mostly hidden behind the main one
    draw_berry(795, 150, 520, 215, dark=True)
    draw_seeds(795, 150, 520, 215, scale=0.85, dark=True)
    # main berry, cut face toward the viewer
    draw_berry(470, 148, 800, 330, dark=False, face=True)
    draw_seeds(470, 148, 800, 330, scale=1.0)
    # calyx on top of the main berry
    draw_calyx(470, 148)
    # dew / juice droplets on the cut face and skin
    draw_droplet(360, 420, 15)
    draw_droplet(560, 560, 12)
    draw_droplet(430, 700, 17)
    draw_droplet(610, 380, 10)
    draw_droplet(300, 600, 11)
    draw_droplet(700, 700, 13)
    draw_shadow_and_finish()


# ---------------------------------------------------------------- PNG encode
def chunk(typ, data):
    c = struct.pack(">I", len(data)) + typ + data
    c += struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    return c


def write_png(path):
    # Try several row filters, keep the smallest encoding (deterministic).
    rows = [bytes(buf[y * W * 3:(y + 1) * W * 3]) for y in range(H)]
    variants = []
    # filter 0: none
    raw0 = bytearray()
    for r in rows:
        raw0.append(0)
        raw0 += r
    variants.append(bytes(raw0))
    # filter 2: Up
    raw2 = bytearray()
    prev = bytes(W * 3)
    for r in rows:
        raw2.append(2)
        raw2 += bytes((a - b) & 0xFF for a, b in zip(r, prev))
        prev = r
    variants.append(bytes(raw2))
    # filter 1: Sub
    raw1 = bytearray()
    for r in rows:
        raw1.append(1)
        out = bytearray(r)
        for i in range(3, len(out)):
            out[i] = (out[i] - r[i - 3]) & 0xFF
        raw1 += out
    variants.append(bytes(raw1))
    best = min((zlib.compress(v, 9) for v in variants), key=len)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", best)
    png += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/output/entry.png"
    render()
    n = write_png(out)
    print(f"wrote {out}: {n} bytes, {W}x{H}")


if __name__ == "__main__":
    main()
