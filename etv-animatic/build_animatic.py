"""Е ТВ · 3D бродкаст-пакет — аниматик (блокинг в Blender).

Собирает etv_animatic.blend с четырьмя сценами: заставка канала, «Вердикт»,
«Гаджетотека» и отбивка. Тайминг, раскладка стола и планы камеры взяты из
интерактивной версии пакета. Геометрия упрощена до серых болванок, акцентный
цвет (циан / фиолетовый) появляется только на событиях монтажного листа.

Запуск:  python build_animatic.py            (нужен модуль bpy 5.x)
         blender -b -P build_animatic.py
"""
import json
import math
import os

import bpy  # noqa: I001 — bmesh доступен только после bpy
import bmesh
from bpy_extras import anim_utils
from mathutils import Euler, Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BLEND = os.path.join(HERE, "etv_animatic.blend")
FPS = 30
RES = (1280, 720)

FONT_FILES = {
    "title": "/usr/share/fonts/opentype/inter/InterDisplay-ExtraBoldItalic.otf",
    "bold": "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf",
    "mono": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "sym": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}

# цвета болванок (линейные); акценты — фирменные циан и фиолетовый
GROUND = (0.055, 0.058, 0.066)
PANELS = [(0.085, 0.09, 0.1), (0.11, 0.115, 0.125), (0.07, 0.074, 0.084), (0.135, 0.14, 0.15)]
DEEP = (0.04, 0.042, 0.05)
SATIN = (0.2, 0.21, 0.23)
ICE = (0.6, 0.62, 0.66)
STEEL = (0.38, 0.4, 0.43)
FROST = (0.5, 0.56, 0.64)
LIGHT = (0.86, 0.88, 0.9)
EMBOSS = (0.075, 0.08, 0.09)
CYAN = (0.0, 0.62, 1.0)
VIOLET = (0.24, 0.06, 1.0)


# ---------- утилиты ----------
def clamp(x, a=0.0, b=1.0):
    return min(b, max(a, x))


def seg(t, a, b):
    return clamp((t - a) / (b - a))


def lerp(a, b, k):
    return a + (b - a) * k


def mix(c0, c1, k):
    return tuple(lerp(a, b, k) for a, b in zip(c0, c1))


def sine(k):
    return 0.5 - math.cos(k * math.pi) / 2


def soft(k):
    return lerp(k, sine(k), 0.6)


def glide(k):
    return 4 * k ** 3 if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2


def rng(seed):
    """mulberry32 — тот же генератор, что и в веб-версии."""
    s = [seed & 0xFFFFFFFF]

    def imul(a, b):
        return (a * b) & 0xFFFFFFFF

    def r():
        s[0] = (s[0] + 0x6D2B79F5) & 0xFFFFFFFF
        t = s[0]
        t = imul(t ^ (t >> 15), 1 | t)
        t = ((t + imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return r


def B(x, y, z):
    """Координаты веб-версии (Y вверх) → Blender (Z вверх)."""
    return Vector((x, -z, y))


def frame(t):
    return 1 + t * FPS


# ---------- ключи ----------
def fcurves_of(id_data):
    ad = id_data.animation_data
    if not ad or not ad.action:
        return []
    cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    return cb.fcurves if cb else []


def key(ob, t, loc=None, rot=None, scale=None, color=None, hide=None, interp=None, easing=None):
    f = frame(t)
    paths = []
    if loc is not None:
        ob.location = loc
        ob.keyframe_insert("location", frame=f)
        paths.append("location")
    if rot is not None:
        ob.rotation_euler = rot
        ob.keyframe_insert("rotation_euler", frame=f)
        paths.append("rotation_euler")
    if scale is not None:
        ob.scale = scale
        ob.keyframe_insert("scale", frame=f)
        paths.append("scale")
    if color is not None:
        ob.color = (*color[:3], 1.0)
        ob.keyframe_insert("color", frame=f)
        paths.append("color")
    if hide is not None:
        for obj in [ob, *ob.children_recursive]:
            obj.hide_render = hide
            obj.hide_viewport = hide
            obj.keyframe_insert("hide_render", frame=f)
            obj.keyframe_insert("hide_viewport", frame=f)
    if interp:
        for fc in fcurves_of(ob):
            if fc.data_path in paths:
                for kp in fc.keyframe_points:
                    if abs(kp.co.x - f) < 1e-3:
                        kp.interpolation = interp
                        if easing:
                            kp.easing = easing


def flash(ob, t0, t1, base, hi, rest=None):
    """Короткая вспышка цвета: base → hi → rest."""
    key(ob, t0, color=base)
    key(ob, (t0 + t1) / 2, color=hi)
    key(ob, t1, color=base if rest is None else rest)


def visible_between(ob, t0, t1=None):
    key(ob, 0, hide=True)
    key(ob, t0, hide=False)
    if t1 is not None:
        key(ob, t1, hide=True)


# ---------- геометрия ----------
class Piece:
    def __init__(self, sc):
        self.sc = sc

    def link(self, ob, parent=None):
        self.sc.collection.objects.link(ob)
        if parent is not None:
            ob.parent = parent
        return ob

    def empty(self, name, pos=Vector(), parent=None):
        ob = bpy.data.objects.new(name, None)
        ob.empty_display_size = 0.5
        ob.location = pos
        return self.link(ob, parent)

    def mesh(self, name, bm, color, parent=None, bevel=0.0, segs=2):
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(name, me)
        ob.color = (*color, 1.0)
        if bevel > 0:
            m = ob.modifiers.new("Bevel", "BEVEL")
            m.width = bevel
            m.segments = segs
            m.limit_method = "ANGLE"
        return self.link(ob, parent)

    def box(self, name, w, h, d, pos3=(0, 0, 0), color=PANELS[0], parent=None, bevel=None, anchor="bottom", local=False):
        """Плита w×h×d (ширина, высота, глубина) с опорной точкой в центре основания."""
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        ox = 0.5 if anchor == "left" else 0.0
        oz = 0.5 if anchor in ("bottom", "left") else 0.0
        for v in bm.verts:
            v.co = Vector(((v.co.x + ox) * w, v.co.y * d, (v.co.z + oz) * h))
        bev = min(0.06, w * 0.25, d * 0.25, h * 0.3) if bevel is None else bevel
        ob = self.mesh(name, bm, color, parent, bev)
        ob.location = Vector(pos3) if local else B(*pos3)
        return ob

    def cyl(self, name, r, h, pos=Vector(), color=STEEL, parent=None, segs=32, center=False):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r, radius2=r, depth=h)
        if not center:
            bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, h / 2))
        ob = self.mesh(name, bm, color, parent, min(0.03, r * 0.2, h * 0.25))
        ob.location = pos
        return ob

    def sphere(self, name, r, pos=Vector(), color=ICE, parent=None):
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=r)
        for f in bm.faces:
            f.smooth = True
        ob = self.mesh(name, bm, color, parent)
        ob.location = pos
        return ob

    def torus(self, name, R, r, pos=Vector(), color=CYAN, parent=None, nu=64, nv=8):
        bm = bmesh.new()
        grid = []
        for i in range(nu):
            a = 2 * math.pi * i / nu
            grid.append([bm.verts.new(((R + r * math.cos(b)) * math.cos(a), (R + r * math.cos(b)) * math.sin(a), r * math.sin(b)))
                         for b in (2 * math.pi * j / nv for j in range(nv))])
        for i in range(nu):
            for j in range(nv):
                bm.faces.new((grid[i][j], grid[(i + 1) % nu][j], grid[(i + 1) % nu][(j + 1) % nv], grid[i][(j + 1) % nv]))
        for f in bm.faces:
            f.smooth = True
        ob = self.mesh(name, bm, color, parent)
        ob.location = pos
        return ob

    def multibox(self, name, parts, color, parent=None, bevel=0.03):
        """Несколько брусков в одном меше: parts = [(центр, размер, поворот_z)]."""
        bm = bmesh.new()
        for c, s, rz in parts:
            bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.LocRotScale(Vector(c), Euler((0, 0, rz)), Vector(s)))
        return self.mesh(name, bm, color, parent, bevel)

    def frame(self, name, w, d, t, h, pos=Vector(), color=SATIN, parent=None):
        """Прямоугольная рамка (Blender-координаты: w по X, d по Y)."""
        parts = [((0, d / 2 - t / 2, h / 2), (w, t, h), 0), ((0, -d / 2 + t / 2, h / 2), (w, t, h), 0),
                 ((w / 2 - t / 2, 0, h / 2), (t, d - 2 * t, h), 0), ((-w / 2 + t / 2, 0, h / 2), (t, d - 2 * t, h), 0)]
        ob = self.multibox(name, parts, color, parent, bevel=min(0.03, t * 0.3))
        ob.location = pos
        return ob

    def plus(self, name, size, thick, h, pos=Vector(), color=SATIN, parent=None):
        ob = self.multibox(name, [((0, 0, h / 2), (size, thick, h), 0), ((0, 0, h / 2), (thick, size, h), 0)], color, parent)
        ob.location = pos
        return ob

    def text(self, name, body, size, pos=Vector(), color=LIGHT, font="bold", parent=None, extrude=0.02, align="CENTER", rot_z=0.0):
        cu = bpy.data.curves.new(name, "FONT")
        cu.body = body
        cu.font = FONTS[font]
        cu.size = size
        cu.align_x = align
        cu.align_y = "CENTER"
        cu.extrude = extrude
        ob = bpy.data.objects.new(name, cu)
        ob.color = (*color, 1.0)
        ob.location = pos + Vector((0, 0, extrude))
        ob.rotation_euler.z = rot_z
        return self.link(ob, parent)


def text_width(body, size, font):
    """Ширина надписи в единицах сцены (через временный объект в активной сцене)."""
    cu = bpy.data.curves.new("_measure", "FONT")
    cu.body, cu.font, cu.size = body, FONTS[font], size
    ob = bpy.data.objects.new("_measure", cu)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    w = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).dimensions.x
    bpy.data.objects.remove(ob)
    bpy.data.curves.remove(cu)
    return w


# ---------- общие узлы сцены ----------
def make_table(P, seed, res, hover=0.0, x0=-38, z0=-28, x1=40, z1=24):
    """Стол из крупных тонких плит разного размера — та же нарезка, что в веб-версии."""
    r = rng(seed)
    root = P.empty("Table")
    P.box("Table_base", x1 - x0, 0.4, z1 - z0, ((x0 + x1) / 2, -0.4, (z0 + z1) / 2), GROUND, root, bevel=0.2)
    panels = []

    def hits(a, b, c, d):
        return any(not (c <= q[0] or a >= q[2] or d <= q[1] or b >= q[3]) for q in res)

    def leaf(a, b, c, d):
        w, dd = c - a - 0.14, d - b - 0.14
        area, cx, cz = w * dd, (a + c) / 2, (b + d) / 2
        tall = area < 9 and r() < 0.14
        acc = (not tall) and area < 4 and r() < 0.06
        h = 0.5 + r() * 1.1 if tall else 0.06 + r() * 0.2
        if acc:
            col = mix(PANELS[1], CYAN if r() < 0.7 else VIOLET, 0.35)
        elif tall:
            col = DEEP
        else:
            col = PANELS[int(r() * len(PANELS)) % len(PANELS)]
        ob = P.box(f"Panel_{len(panels):03d}", w, h, dd, (cx, 0, cz), col, root, bevel=min(0.05, h * 0.3))
        o = {"ob": ob, "x": cx, "z": cz, "r": r(), "hov": False}
        if hover and not tall and area < 14 and r() < hover:
            o.update(hov=True, y1=1.2 + r() * 3.6, rx=(r() - 0.5) * 0.5, rz=(r() - 0.5) * 0.5)
        panels.append(o)

    def split(a, b, c, d, dep):
        w, h = c - a, d - b
        hit = hits(a, b, c, d)
        if hit and w <= 1.1 and h <= 1.1:
            return
        if not hit and (dep >= 7 or (w < 6.5 and h < 6.5 and r() < 0.55) or (w < 3 and h < 3)):
            leaf(a, b, c, d)
            return
        if w >= h:
            s = a + clamp(round(w * (0.3 + r() * 0.4) * 2) / 2, 0.5, w - 0.5)
            split(a, b, s, d, dep + 1)
            split(s, b, c, d, dep + 1)
        else:
            s = b + clamp(round(h * (0.3 + r() * 0.4) * 2) / 2, 0.5, h - 0.5)
            split(a, b, c, s, dep + 1)
            split(a, s, c, d, dep + 1)

    split(x0 + 0.5, z0 + 0.5, x1 - 0.5, z1 - 0.5, 0)
    return panels


def hover_panels(panels, up, down, fall=False):
    """Плиты взлетают и возвращаются: up(o) / down(o) → (t0, t1)."""
    for o in panels:
        if not o["hov"]:
            continue
        ob, base = o["ob"], o["ob"].location.copy()
        u0, u1 = up(o)
        d0, d1 = down(o)
        lifted = base + Vector((0, 0, o["y1"]))
        tilt = Euler((o["rx"], -o["rz"], 0))
        key(ob, u0, loc=base, rot=Euler())
        key(ob, u1, loc=lifted, rot=tilt)
        key(ob, d0, loc=lifted + Vector((0, 0, 0.2)), rot=tilt, interp="CUBIC" if fall else None, easing="EASE_IN" if fall else None)
        key(ob, d1, loc=base, rot=Euler())


def keycap(P, name, x, z, sym, col, size=1.0):
    """Кнопка геймпада в колодце стола."""
    g = P.empty(name, B(x, 0, z))
    P.cyl(f"{name}_well", (size + 0.28) / 2, 0.1, Vector(), DEEP, g, segs=40)
    cap = P.empty(f"{name}_cap", Vector(), g)
    body = P.cyl(f"{name}_body", size / 2, 0.34, Vector(), SATIN, cap, segs=40)
    P.text(f"{name}_sym", sym, size * 0.62, Vector((0, 0, 0.34)), col, "sym", cap, extrude=0.03)
    return {"cap": cap, "body": body, "col": col}


def press(k, t0, dur=0.4, keep_lit=True):
    t1 = t0 + dur
    key(k["cap"], t0, loc=Vector((0, 0, 0)))
    key(k["cap"], t0 + dur / 2, loc=Vector((0, 0, -0.14)))
    key(k["cap"], t1, loc=Vector((0, 0, 0)))
    flash(k["body"], t0, t1, SATIN, mix(SATIN, k["col"], 0.85), mix(SATIN, k["col"], 0.35) if keep_lit else SATIN)


def tray(P, name, c3, w, d, lid_y=1.0, accent=CYAN, label=""):
    """Лоток с матовым стеклом на стойках (стекло показано сеткой)."""
    g = P.empty(name, B(*c3))
    for a in (-1, 1):
        for b in (-1, 1):
            P.cyl(f"{name}_post", 0.09, lid_y, Vector((a * (w / 2 - 0.16), b * (d / 2 - 0.16), 0)), STEEL, g, segs=16)
    lid = P.empty(f"{name}_lid", Vector((0, 0, lid_y)), g)
    P.frame(f"{name}_frame", w, d, 0.3, 0.12, Vector(), DEEP, lid)
    bars = []
    gw, gd, step = w - 0.6, d - 0.6, 0.45
    n = int(gw / step)
    for i in range(1, n):
        bars.append(((-gw / 2 + i * gw / n, 0, 0.08), (0.025, gd, 0.02), 0))
    n = int(gd / step)
    for i in range(1, n):
        bars.append(((0, -gd / 2 + i * gd / n, 0.08), (gw, 0.025, 0.02), 0))
    P.multibox(f"{name}_glass", bars, FROST, lid, bevel=0)
    if label:
        P.text(f"{name}_label", label, 0.2, Vector((-w / 2 + 0.45, d / 2 - 0.15, 0.12)), LIGHT, "mono", lid, 0.01, "LEFT")
    knob = P.sphere(f"{name}_knob", 0.11, Vector((w / 2 - 0.91, d / 2 - 0.15, 0.26)), ICE, lid)
    return {"g": g, "lid": lid, "knob": knob, "w": w, "accent": accent}


def toggle(tr, t0, t1):
    k, w = tr["knob"], tr["w"]
    key(k, t0, loc=Vector((w / 2 - 0.91, k.location.y, k.location.z)), color=ICE)
    key(k, t1, loc=Vector((w / 2 - 0.59, k.location.y, k.location.z)), color=tr["accent"])


def make_logo(P, S=5.0):
    """Знак E: корпус (кривая с заливкой) + центральная планка."""
    W, H, t, R, m, Wm = 1.6, 1.1, 0.2, 0.4, 0.09, 1.5
    h, c, r = H / 2, H / 2 - R, R - t
    pts = []

    def arc(cx, cy, rad, a0, a1, n=14):
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / n
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))

    pts += [(W, h), (R, h)]
    arc(R, c, R, math.pi / 2, math.pi)
    pts.append((0, -c))
    arc(R, -c, R, math.pi, math.pi * 1.5)
    pts += [(W, -h), (W, -h + t), (R, -h + t)]
    arc(R, -c, r, math.pi * 1.5, math.pi)
    pts.append((t, c))
    arc(R, c, r, math.pi, math.pi / 2)
    pts.append((W, h - t))
    thick = 0.15 * S
    cu = bpy.data.curves.new("E_shell", "CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    cu.extrude = thick / 2
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, (x, y) in zip(sp.points, pts):
        p.co = ((x - W / 2) * S, y * S, 0, 1)
    sp.use_cyclic_u = True
    shell = bpy.data.objects.new("E_shell", cu)
    shell.color = (*SATIN, 1)
    shell.location = (0, 0, thick / 2)
    P.link(shell)
    bar_len = (Wm - t + 0.03) * S
    bar = P.box("E_bar", bar_len, thick, 2 * m * S, (0, 0, 0), FROST, anchor="left", bevel=0.05)
    slot_x = (t - 0.03 - W / 2) * S
    return shell, bar, bar_len, slot_x


def shock(P, name, x, z, col, t0, t1, s0, s1):
    ring = P.torus(name, 1.0, 0.04, B(x, 0.03, z), col)
    visible_between(ring, t0, t1)
    key(ring, t0, scale=(s0, s0, 1))
    key(ring, t1, scale=(s1, s1, 1), interp=None)
    return ring


def title_pair(P, name, c3, w1, w2, t0, h=0.9, stagger=0.5, gap=0.35, lw=None, lz=None, c1=LIGHT, c2=LIGHT,
               line_col=CYAN, sub=None, sub_h=0.3, sz=None, sub_col=(0.7, 0.73, 0.78)):
    """Два слова въезжают навстречу, между ними прочерчивается линия, затем подпись."""
    size = 0.7 * h
    wa, wb = text_width(w1, size, "title"), text_width(w2, size, "title")
    total = wa + h * gap + wb
    cx, cz = c3[0], c3[2]
    xa, xb = cx - total / 2 + wa / 2, cx + total / 2 - wb / 2
    za, zb = cz - h * stagger, cz + h * stagger
    A = P.text(f"{name}_A", w1, size, B(xa - 2.2, 0, za), c1, "title", extrude=0.06)
    Bt = P.text(f"{name}_B", w2, size, B(xb + 2.2, 0, zb), c2, "title", extrude=0.06)
    for ob, x0, x1, ta in ((A, xa - 2.2, xa, t0), (Bt, xb + 2.2, xb, t0 + 0.2)):
        z = ob.location.copy()
        visible_between(ob, ta)
        key(ob, ta, loc=Vector((x0, z.y, z.z)))
        key(ob, ta + 1.2, loc=Vector((x1, z.y, z.z)))
    lw = lw or total * 0.6
    lz = cz + (h * 0.95 if lz is None else lz)
    lx = cx - total * 0.12
    line = P.box(f"{name}_line", lw, 0.04, 0.06, (lx, 0, lz), line_col, bevel=0.01)
    visible_between(line, t0 + 0.8)
    key(line, t0 + 0.8, loc=B(lx - total * 0.3, 0, lz), scale=(0.001, 1, 1))
    key(line, t0 + 1.9, loc=B(lx, 0, lz), scale=(1, 1, 1))
    if sub:
        s = P.text(f"{name}_sub", sub, sub_h * 0.8, B(cx, 0, cz + (h * 1.5 if sz is None else sz)), sub_col, "mono", extrude=0.005)
        visible_between(s, t0 + 1.4)


def shot_cameras(P, shots, look_fns=None):
    """Каждый план — своя камера; склейки делают маркеры с привязанной камерой."""
    cams = []
    for i, s in enumerate(shots, 1):
        name = f"{P.sc['etv_code']}_SH{i:02d}_{s['name']}"
        cd = bpy.data.cameras.new(name)
        cd.sensor_fit = "VERTICAL"
        cd.sensor_height = 24
        cd.clip_start, cd.clip_end = 0.1, 300
        cam = P.link(bpy.data.objects.new(name, cd))
        t0, t1 = s["t0"], s["t1"]
        fov = s.get("fov", (35, 35))
        for t, f in ((t0, fov[0]), (t1, fov[1])):
            cd.lens = 12 / math.tan(math.radians(f) / 2)
            cd.keyframe_insert("lens", frame=frame(t))
        if "fn" in s:
            # проводка по траектории: ключи каждые 3 кадра, направление считается заранее
            prev = None
            n = int(round((t1 - t0) * FPS / 3))
            for j in range(n + 1):
                t = t0 + (t1 - t0) * j / n
                p, look = s["fn"](soft(seg(t, t0, t1)), t)
                pos, tgt = B(*p), B(*look)
                rot = (tgt - pos).to_track_quat("-Z", "Y").to_euler("XYZ", prev) if prev else (tgt - pos).to_track_quat("-Z", "Y").to_euler("XYZ")
                prev = rot
                key(cam, t, loc=pos, rot=rot)
        elif s.get("top"):
            cam.rotation_euler = (0, 0, 0)
            key(cam, t0, loc=B(*s["p0"]))
            key(cam, t1, loc=B(*s.get("p1", s["p0"])))
        else:
            tgt = P.empty(f"{name}_target")
            key(tgt, t0, loc=B(*s["l0"]))
            key(tgt, t1, loc=B(*s.get("l1", s["l0"])))
            key(cam, t0, loc=B(*s["p0"]))
            key(cam, t1, loc=B(*s.get("p1", s["p0"])))
            con = cam.constraints.new("TRACK_TO")
            con.target = tgt
            con.track_axis = "TRACK_NEGATIVE_Z"
            con.up_axis = "UP_Y"
        m = P.sc.timeline_markers.new(name, frame=int(round(frame(t0))))
        m.camera = cam
        cams.append(cam)
    P.sc.camera = cams[0]
    return cams


def setup_scene(sc, code, title, dur, beats):
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.fps = FPS
    sc.frame_start, sc.frame_end = 1, int(round(dur * FPS))
    sc.render.resolution_x, sc.render.resolution_y = RES
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.view_settings.view_transform = "Standard"
    sc.display.render_aa = "8"
    sc.display.light_direction = (-0.55, 0.35, 0.76)
    sc.display.shadow_shift = 0.08
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "OBJECT"
    sh.show_shadows = True
    sh.shadow_intensity = 0.55
    sh.show_cavity = True
    sh.cavity_type = "WORLD"
    sh.show_object_outline = True
    sh.object_outline_color = (0.0, 0.0, 0.0)
    w = bpy.data.worlds.new(f"{title}_world")
    w.color = (0.018, 0.02, 0.026)
    sc.world = w
    sc.render.use_stamp = False  # чистый кадр, без прожига
    sc["etv_code"] = code
    sc["etv_title"] = title
    sc["etv_beats"] = json.dumps(beats, ensure_ascii=False)
    for t, txt in beats:
        sc.timeline_markers.new(f"♦ {txt}", frame=int(round(frame(t))))


# =========================================================
# 1. ЗАСТАВКА КАНАЛА · 15 с — история буквы E
# =========================================================
IDENT_BEATS = [[0, "Нажатие ✕ — PRESS START"], [.95, "Свет бежит по рёбрам ступеней"], [2.6, "Планка знака загорается"],
               [3.4, "Планка поднимается, вместе с ней взлетают плиты"], [5.2, "Планка опускается на дорожку"],
               [6.4, "Полоса загрузки, проход под стеклом сканера"], [8.4, "Плиты возвращаются на место"],
               [9, "Планка въезжает в корпус E"], [10.6, "Замок, цвет заливает знак, комбо △ ○ □"],
               [12.6, "«ИГРОВОЙ» и «КАНАЛ» въезжают навстречу"]]


def build_ident(sc):
    P = Piece(sc)
    setup_scene(sc, "ID", "ЗАСТАВКА КАНАЛА", 15, IDENT_BEATS)
    panels = make_table(P, 5, [[-4.9, -3.4, 4.9, 3.4], [3.6, -1.1, 23, 1.1], [7.9, -2.4, 13.7, 2.4], [4.5, -8.6, 19.6, -2.5],
                               [-10, -8.4, 3.4, -4.4], [-7.2, 3.4, 7.2, 6.6], [-8.6, 2.8, -5.6, 5.4], [-9.8, -2.8, -5.4, 1.6]], hover=0.32)
    hover_panels(panels, lambda o: (3.4 + o["r"] * .8, 4.8 + o["r"] * .8), lambda o: (8.4 + o["r"] * .9, 10 + o["r"] * .9))
    shell, bar, bar_len, slot_x = make_logo(P)
    # панель кнопок геймпада: нажатие ✕ запускает всю историю
    pad = P.cyl("Pad", 1.85, 0.08, B(1.4, 0, -6.7), DEEP, segs=48)
    keys = {"tri": keycap(P, "Key_tri", 1.4, -7.62, "△", CYAN, .78), "circ": keycap(P, "Key_circ", 2.32, -6.7, "○", VIOLET, .78),
            "x": keycap(P, "Key_x", 1.4, -5.78, "✕", CYAN, .78), "sq": keycap(P, "Key_sq", .48, -6.7, "□", ICE, .78)}
    P.text("PRESS_START", "PRESS START", 0.24, B(1.4, 0.01, -4.75), (0.3, 0.33, 0.4), "mono", extrude=0.005)
    press(keys["x"], 0.15)
    link = P.box("Link_light", math.hypot(2.6, 2.1), 0.03, 0.06, (1.9, 0.02, -5.4), CYAN, anchor="left", bevel=0)
    link.rotation_euler.z = math.atan2(-2.1, 2.6)
    key(link, 0.45, scale=(0.001, 1, 1))
    key(link, 0.95, scale=(1, 1, 1))
    # крестовина
    P.cyl("Dpad_base", 1.8, 0.08, B(-7.6, 0, -.6), DEEP, segs=48)
    P.plus("Dpad", 2.5, 0.75, 0.35, B(-7.6, .08, -.6), SATIN)
    # ступени: по рёбрам бежит свет
    top = None
    for i in range(8):
        h = .1 + i * .13
        sw = 7.4 if i == 7 else 2.3
        x = 5.6 + i * 1.0 + (2.55 if i == 7 else 0)
        z = -3.1 - i * .62
        st = P.box(f"Step_{i}", sw, h, 1.05, (x, 0, z), SATIN if i % 2 else PANELS[3])
        t0 = .95 + i * .22
        flash(st, t0, t0 + .7, st.color[:3], mix(st.color[:3], CYAN, .8), mix(st.color[:3], CYAN, .2))
        top = (h, x, z)
    # дорожка к знаку и полоса загрузки
    P.box("Track", 19.4, .06, 1.5, (13.1, 0, 0), DEEP)
    P.text("Track_label", "LOADING", 0.22, B(4.6, .07, -.55), (0.3, 0.33, 0.4), "mono", extrude=0.005, align="LEFT")
    for z in (-.62, .62):
        P.box("Rail", 19, .1, .08, (13.1, .06, z), mix(DEEP, CYAN, .4), bevel=0.02)
    tr = tray(P, "Scanner", (10.8, 0, 0), 5.4, 4.4, 1.0, CYAN, "PLAYER 1  SCAN")
    toggle(tr, 7.0, 7.4)
    P.text("Emboss_ETV", "Е ТВ", 1.55, B(-3.9, 0, -6.4), EMBOSS, "title", extrude=0.03)

    # путь планки: вершина ступеней → над столом → дорожка → сканер → паз
    h7, x7, z7 = top
    P0, P1, P2, P3, P4 = B(x7 - bar_len / 2, h7, z7), B(9.2, 6.2, -3.6), B(15.8, .06, 0), B(6.4, .06, 0), B(slot_x, .06, 0)
    key(bar, 0, loc=P0, rot=Euler())
    key(bar, 2.6, color=FROST)
    key(bar, 3.2, color=CYAN)
    key(bar, 3.4, loc=P0, rot=Euler())
    key(bar, 4.3, loc=(P0 + P1) / 2 + Vector((0, 0, .8)), rot=Euler((0, -.25, 0)))
    key(bar, 5.2, loc=P1, rot=Euler())
    key(bar, 6.4, loc=P2)
    key(bar, 9.0, loc=P3)
    key(bar, 10.6, loc=P4)
    # ячейки загрузки загораются, когда планка проходит мимо
    for i in range(20):
        cx = 4.8 + i * .87
        cell = P.box(f"Cell_{i:02d}", .62, .07, .2, (cx, 0, 1.0), DEEP, bevel=.02)
        tc = next((6.4 + (9.0 - 6.4) * j / 300 for j in range(301) if lerp(15.8, 6.4, soft(j / 300)) < cx - .4), None)
        if tc is not None:
            key(cell, max(0, tc - 1 / FPS), color=DEEP)
            key(cell, tc, color=CYAN)
    # замок: цвет заливает знак, волна по столу, комбо △ ○ □
    key(shell, 10.6, color=SATIN)
    key(shell, 11.6, color=CYAN)
    shock(P, "Shock", .4, 0, CYAN, 10.6, 11.9, 2.5, 17)
    for k, t0 in (("tri", 10.75), ("circ", 10.95), ("sq", 11.15)):
        press(keys[k], t0, .3)
    title_pair(P, "Title", (0, 0, 4.75), "ИГРОВОЙ", "КАНАЛ", 12.6, h=.82, stagger=.32, lz=1.12, lw=4.2,
               sub="E-SPORTS · ENTERTAINMENT · EDUCATION · ENERGY", sub_h=.3, sz=1.68, sub_col=mix(LIGHT, CYAN, .6))
    shot_cameras(P, [
        {"name": "ступени", "t0": 0, "t1": 3.4, "p0": [-.4, 8.6, 1.6], "p1": [1.4, 8, .8], "l0": [5.4, 0, -5.6], "l1": [7.4, .3, -5.6], "fov": [36, 35]},
        {"name": "подъём_сверху", "t0": 3.4, "t1": 6.4, "p0": [10.5, 21, -3.2], "p1": [11.5, 20, -2.6], "fov": [36, 36], "top": True},
        {"name": "сканер_сверху", "t0": 6.4, "t1": 9, "p0": [12.4, 12.5, .4], "p1": [9.4, 12, .4], "fov": [36, 36], "top": True},
        {"name": "макро_паз", "t0": 9, "t1": 11.6, "p0": [9.8, 5.6, 6.4], "p1": [7.6, 5.4, 5.6], "l0": [2.2, .2, 0], "l1": [.6, .2, 0], "fov": [32, 31]},
        {"name": "финал_сверху", "t0": 11.6, "t1": 15, "p0": [.3, 31, 1.9], "p1": [0, 29, 1.9], "fov": [22, 22], "top": True},
    ])


# =========================================================
# 2. ВЕРДИКТ · 15 с — разбор игры и решение
# =========================================================
VERDICT_BEATS = [[0, "Скан картриджа, кнопки △ ○ ✕ □"], [2.6, "Тумблер включается"], [3.6, "Шкала SCORE набирает оценку"],
                 [4.5, "Кнопки «плюс» и «минус»"], [5.8, "Пояс печати заряжается"], [6.4, "Печать поднимается, плиты взлетают"],
                 [8.6, "Печать над лотком"], [9.8, "Резкий удар, оттиск на стекле"], [10.3, "Печать переворачивается"],
                 [11.5, "Финал: название на лице печати"]]


def build_verdict(sc):
    P = Piece(sc)
    setup_scene(sc, "VR", "«ВЕРДИКТ»", 15, VERDICT_BEATS)
    SLAM = 9.82
    panels = make_table(P, 19, [[-7.6, -3.1, -1.4, 3.1], [-11.4, 4.9, 10.6, 7.1], [1.6, -2.8, 9.4, 2.4], [-12, -8, -3, -4.6], [-11.6, -3.4, -8, 1.6]], hover=.3)
    hover_panels(panels, lambda o: (6.3 + o["r"] * .7, 7.7 + o["r"] * .7), lambda o: (SLAM, SLAM + .45), fall=True)
    tr = tray(P, "Tray", (-4.5, 0, 0), 5.6, 5.2, 1.0, VIOLET, "REVIEW  02")
    toggle(tr, 2.6, 3.0)
    # лоток проседает от удара
    lid = tr["lid"]
    for t, dz in ((SLAM, 0), (SLAM + .07, -.14), (SLAM + .2, .04), (SLAM + .4, 0)):
        key(lid, t, loc=Vector((0, 0, 1.0 + dz)))
    cart = P.empty("Cartridge", B(-4.5, .2, 0))
    cart.scale = (1.5, 1.5, 1.5)
    gadget(P, "cartridge", cart)
    scan = P.box("Scan", .06, .04, 4.4, (-6.9, .55, 0), VIOLET, bevel=0)
    visible_between(scan, .8, 2.8)
    key(scan, .8, loc=B(-6.9, .55, 0))
    key(scan, 2.8, loc=B(-2.1, .55, 0))
    # шкала SCORE
    P.box("Score_rail", 15.8, .22, 1.4, (-2.4, 0, 6), DEEP)
    P.text("Score_label", "SCORE", .22, B(-10.1, .23, 5.2), (0.3, 0.33, 0.4), "mono", extrude=.005, align="LEFT")
    for i in range(10):
        pip = P.box(f"Pip_{i}", .86, .2, .86, (-9.6 + i * 1.08, .22, 6), (0.07, 0.075, 0.09))
        key(pip, 3.6 + i * .22, color=(0.07, 0.075, 0.09))
        key(pip, 4.0 + i * .22, color=VIOLET)
    k_plus = keycap(P, "Key_plus", 7.6, 6, "+", CYAN, .95)
    k_minus = keycap(P, "Key_minus", 9.2, 6, "−", VIOLET, .95)
    press(k_plus, 4.5, .35)
    press(k_minus, 5.1, .35)
    P.cyl("Pad", 1.7, .08, B(-9.8, 0, -.9), DEEP, segs=48)
    for i, (sym, dx, dz, col) in enumerate((("△", 0, -.85, CYAN), ("○", .85, 0, VIOLET), ("✕", 0, .85, CYAN), ("□", -.85, 0, ICE))):
        press(keycap(P, f"Key_{i}", -9.8 + dx, -.9 + dz, sym, col, .7), 1.0 + i * .18, .3, keep_lit=False)
    # печать: лежит лицом вниз, на лице рельефом «ВЕРДИКТ»
    stamp = P.empty("Stamp")
    P.box("Stamp_body", 6.8, .9, 4.4, (0, -.45, 0), (0.07, 0.075, 0.09), stamp, bevel=.2)
    band = P.box("Stamp_band", 6.9, .1, 4.5, (0, -.25, 0), mix(DEEP, VIOLET, .15), stamp, bevel=.04)
    key(band, 5.8, color=mix(DEEP, VIOLET, .15))
    key(band, 6.4, color=VIOLET)
    key(band, SLAM + 1, color=mix(DEEP, VIOLET, .55))
    P.box("Stamp_block", 3, .4, 1.8, (0, 0, 0), DEEP, stamp)
    P.text("Stamp_block_label", "VERDICT", .22, B(0, .41, .5), (0.3, 0.33, 0.4), "mono", stamp, .005)
    for x in (-.95, .95):
        P.cyl("Stamp_post", .13, .62, B(x, .39, 0), STEEL, stamp, segs=20)
    grip = P.cyl("Stamp_grip", .42, 3.04, B(0, 1.35, 0), mix(SATIN, VIOLET, .8), stamp, center=True)
    grip.rotation_euler.y = math.pi / 2
    face = P.empty("Stamp_face", B(0, -.45, 0), stamp)
    face.rotation_euler.x = math.pi
    P.box("Face_plate", 6.2, .06, 3.8, (0, 0, 0), DEEP, face)
    P.text("Face_title", "ВЕРДИКТ", .8, B(0, .06, -.5), LIGHT, "title", face, .05)
    P.box("Face_line", 4.6, .04, .06, (0, .08, .35), VIOLET, face, bevel=.01)
    P.text("Face_sub", "НОВЫЕ ИГРЫ · ПЛЮСЫ · МИНУСЫ · ИТОГ", .135, B(0, .07, .82), (0.62, 0.66, 0.72), "mono", face, .005)
    for i in range(10):
        P.box(f"Face_pip_{i}", .3, .1, .3, (-2.6 + i * .58, .06, 1.35), VIOLET, face, bevel=.03)
    H, A, Bp, D, F = B(5.5, .55, -.2), B(2.2, 5.2, 1.4), B(-4.5, 4.4, 0), B(.6, 3.8, -.2), B(5.2, .95, -.2)
    hit = B(-4.5, 1.12 + .51, 0)
    key(stamp, 0, loc=H, rot=Euler())
    key(stamp, 6.4, loc=H, rot=Euler())
    key(stamp, 7.5, loc=(H + A) / 2 + Vector((0, 0, .4)), rot=Euler((.12, 0, 0)))
    key(stamp, 8.6, loc=A, rot=Euler())
    key(stamp, 9.6, loc=Bp, interp="CUBIC", easing="EASE_IN")
    key(stamp, SLAM, loc=hit)
    key(stamp, 10.3, loc=hit, rot=Euler())
    key(stamp, 10.5, rot=Euler())
    key(stamp, 11.5, loc=D)
    key(stamp, 11.6, rot=Euler((math.pi, 0, 0)))
    key(stamp, 12.4, loc=F)
    # оттиск на стекле
    imp = P.empty("Imprint", Vector((0, 0, .14)), lid)
    P.frame("Imprint_frame", 4.9, 3.25, .08, .02, Vector(), VIOLET, imp)
    P.text("Imprint_check", "✓", 2.2, Vector((0, -.1, 0)), VIOLET, "sym", imp, .01)
    visible_between(imp, SLAM)
    shock(P, "Shock", -4.5, 0, VIOLET, SLAM, SLAM + 1.2, 3, 16)
    P.text("Emboss_verdict", "ВЕРДИКТ", 1.0, B(-7.4, 0, -6.3), EMBOSS, "title", extrude=.03)
    shot_cameras(P, [
        {"name": "лоток", "t0": 0, "t1": 3.4, "p0": [-1, 7.6, 6.6], "p1": [-2.2, 7, 5.8], "l0": [-4.5, 0, 0], "l1": [-4.6, .2, -.2], "fov": [34, 33]},
        {"name": "макро_шкала", "t0": 3.4, "t1": 6.4, "p0": [-11.6, 3.6, 10.6], "p1": [.8, 3.8, 10.8], "l0": [-7.4, 0, 5.6], "l1": [7.8, .2, 5.6], "fov": [32, 31]},
        {"name": "подъём_сверху", "t0": 6.4, "t1": 8.8, "p0": [3.6, 22, 1], "p1": [2.6, 21, .8], "fov": [36, 36], "top": True},
        {"name": "удар", "t0": 8.8, "t1": 10.8, "p0": [1.8, 9.8, 8.4], "p1": [1, 9, 7.6], "l0": [-4.5, 1.6, -.4], "l1": [-4.5, 1, -.2], "fov": [38, 38]},
        {"name": "финал_сверху", "t0": 10.8, "t1": 15, "p0": [1.2, 26, .2], "p1": [1, 24.5, .2], "fov": [22, 22], "top": True},
    ])


# =========================================================
# 3. ГАДЖЕТОТЕКА · 15 с — каталог гаджетов
# =========================================================
GADGET_BEATS = [[0, "Отсеки с гаджетами"], [3.4, "Курсор идёт по отсекам"], [5.1, "Тумблер, крышка отъезжает"],
                [5.9, "Геймпад поднимается"], [6.9, "Раскладка на детали"], [8, "Сборка"], [8.9, "Перелёт к прорези"],
                [9.6, "Пьедестал поднимается, геймпад ложится"], [11.8, "«ГАДЖЕТ» и «ОТЕКА» въезжают навстречу"]]


def gadget(P, kind, g):
    """Болванки гаджетов в собственных координатах (X вправо, Y вверх по корпусу, Z — лицевая сторона)."""
    parts = []

    def part(ob, v=(0, 0, 0)):
        parts.append((ob, ob.location.copy(), Vector(v)))
        return ob

    if kind == "gamepad":
        part(P.multibox("GP_body", [((0, .12, 0), (1.7, .72, .42), 0), ((-.78, -.32, 0), (.52, .78, .38), .35), ((.78, -.32, 0), (.52, .78, .38), -.35)], ICE, g, .1), (0, 0, -.4))
        F = .23
        for x, sgn in ((-.33, -1), (.33, 1)):
            part(P.cyl("GP_stick", .13, .2, Vector((x, -.14, F - .05)), (0.08, 0.085, 0.1), g, 24), (sgn * .18, -.25, 1.05))
        part(P.plus("GP_dpad", .3, .09, .08, Vector((-.66, .13, F)), (0.08, 0.085, 0.1), g), (-.6, .45, .95))
        for dx, dy, c in ((0, .12, CYAN), (.12, 0, VIOLET), (0, -.12, ICE), (-.12, 0, STEEL)):
            part(P.cyl("GP_btn", .058, .07, Vector((.66 + dx, .13 + dy, F)), c, g, 20), (.55 + dx * 5, .5 + dy * 5, 1.15))
        part(P.box("GP_touchpad", .5, .04, .28, (0, .22, F - .02), (0.08, 0.085, 0.1), g, local=True), (0, .55, .8))
        part(P.box("GP_lightbar", .36, .03, .035, (0, .43, F - .03), CYAN, g, local=True), (0, .8, .6))
        part(P.cyl("GP_home", .045, .04, Vector((0, -.04, F)), CYAN, g, 20), (0, -.25, .9))
        for x, s in ((-.72, 1), (.72, -1)):
            sh = P.box("GP_shoulder", .46, .24, .1, (0, 0, 0), STEEL, g, local=True)
            sh.location = Vector((x, .5, -.12))
            sh.rotation_euler.z = .12 * s
            part(sh, (-.35 * s, .75, 0))
        return parts
    if kind == "cartridge":
        P.box("Cart_body", 1.1, .24, 1.3, (0, 0, -.12), (0.1, 0.11, 0.13), g, local=True)
        P.box("Cart_label", .82, .04, .62, (0, -.14, .12), mix(SATIN, VIOLET, .7), g, local=True)
        for y in (.38, .46, .54):
            P.box("Cart_rib", .9, .25, .035, (0, y, -.115), STEEL, g, local=True)
    elif kind == "headphones":
        band = P.torus("HP_band", .56, .065, Vector((0, -.1, 0)), STEEL, g, 48, 8)
        band.rotation_euler.x = 0
        for x in (-.56, .56):
            c = P.cyl("HP_cup", .26, .24, Vector((x, -.2, 0)), ICE, g, 32, center=True)
            c.rotation_euler.y = math.pi / 2
    elif kind == "drone":
        P.multibox("DR_body", [((0, 0, 0), (.5, .5, .18), 0), ((0, 0, 0), (1.05, .08, .05), math.pi / 4), ((0, 0, 0), (1.05, .08, .05), -math.pi / 4)], ICE, g, .03)
        for a in (-1, 1):
            for b in (-1, 1):
                P.torus("DR_rotor", .2, .025, Vector((a * .37, b * .37, .05)), CYAN, g, 32, 6)
    elif kind == "joystick":
        P.multibox("JS_base", [((0, 0, -.1), (1.1, .8, .28), 0)], (0.1, 0.11, 0.13), g, .08)
        P.cyl("JS_stick", .06, .7, Vector((0, 0, -.05)), STEEL, g, 16)
        P.sphere("JS_ball", .2, Vector((0, 0, .7)), CYAN, g)
    elif kind == "handheld":
        P.multibox("HH_body", [((0, 0, 0), (1.55, .85, .18), 0)], ICE, g, .07)
        P.multibox("HH_screen", [((0, .02, .1), (.66, .48, .02), 0)], CYAN, g, 0)
        P.plus("HH_dpad", .22, .07, .06, Vector((-.55, .02, .09)), (0.1, 0.11, 0.13), g)
        P.sphere("HH_btn", .06, Vector((.5, .08, .1)), VIOLET, g)
        P.sphere("HH_btn", .06, Vector((.62, -.04, .1)), CYAN, g)
    return parts


def build_gadget(sc):
    P = Piece(sc)
    setup_scene(sc, "GT", "«ГАДЖЕТОТЕКА»", 15, GADGET_BEATS)
    make_table(P, 31, [[-13.4, -7.1, 9.4, -1.9], [-6.2, 2.4, -2.8, 5.8], [-1.4, 1.9, 9.6, 6.6], [-12, 3, -7, 7]])
    bays = [dict(x=-11.6, z=-4.4, s=2.6, kind="joystick"), dict(x=-8.3, z=-4.7, s=3.0, kind="headphones"),
            dict(x=-4.8, z=-4.3, s=2.6, kind="cartridge"), dict(x=-.6, z=-4.5, s=4.0, kind="gamepad"),
            dict(x=3.8, z=-4.4, s=2.8, kind="handheld"), dict(x=7.4, z=-4.6, s=2.8, kind="drone")]
    for i, b in enumerate(bays):
        P.box(f"Bay_{i}_floor", b["s"], .12, b["s"], (b["x"], 0, b["z"]), DEEP, bevel=.05)
        b["frame"] = P.frame(f"Bay_{i}_frame", b["s"], b["s"], .22, .32, B(b["x"], 0, b["z"]), SATIN)
        g = P.empty(f"Gadget_{b['kind']}", B(b["x"], .45, b["z"]))
        sc_ = 1.2 if b["kind"] == "gamepad" else .95
        g.scale = (sc_, sc_, sc_)
        b["g"], b["parts"] = g, gadget(P, b["kind"], g)
    gb = bays[3]
    lid_tray = tray(P, "Lid", (gb["x"], 0, gb["z"]), 4.4, 4.4, .9, CYAN, "P1  SELECT")
    toggle(lid_tray, 5.1, 5.4)
    lid = lid_tray["lid"]
    key(lid, 5.3, loc=Vector((0, 0, .9)))
    key(lid, 5.7, loc=Vector((2.3, 0, 1.65)))
    key(lid, 6.1, loc=Vector((4.6, 0, 1.4)))
    # курсор скользит от отсека к отсеку
    cursor = P.frame("Cursor", 1, 1, .08, .05, B(bays[0]["x"], .36, bays[0]["z"]), CYAN)
    visible_between(cursor, 3.1, 6.0)

    def cur_at(i):
        b = bays[i]
        return B(b["x"], .36 + (.72 if i == 3 else 0), b["z"]), (b["s"] + .4, b["s"] + .4, 1)

    p0, s0 = cur_at(0)
    key(cursor, 3.1, loc=p0, scale=(.01, .01, 1))
    key(cursor, 3.4, loc=p0, scale=s0)
    for i, ts in enumerate((3.4, 4.0, 4.6)):
        pa, sa = cur_at(i)
        pb, sb = cur_at(i + 1)
        key(cursor, ts, loc=pa, scale=sa)
        key(cursor, ts + .45, loc=pb, scale=sb)
        fr = bays[i + 1]["frame"]
        if i < 2:
            flash(fr, ts + .25, ts + .85, SATIN, mix(SATIN, CYAN, .7))
    p3, s3 = cur_at(3)
    key(cursor, 5.6, loc=p3, scale=s3)
    key(cursor, 6.0, loc=p3, scale=(.01, .01, 1))
    flash(bays[0]["frame"], 3.2, 3.8, SATIN, mix(SATIN, CYAN, .7))
    key(gb["frame"], 4.9, color=SATIN)
    key(gb["frame"], 5.3, color=mix(SATIN, CYAN, .8))
    key(gb["frame"], 9.2, color=mix(SATIN, CYAN, .8))
    key(gb["frame"], 10, color=SATIN)
    # прорезь и пьедестал
    P.frame("Slot", 2.6, 2.6, .16, .04, B(-4.5, 0, 4.1), mix(DEEP, CYAN, .3))
    ped = P.box("Pedestal", 2.2, 1.4, 2.2, (-4.5, -1.4, 4.1), SATIN, bevel=.12)
    P.frame("Pedestal_ring", 2.2, 2.2, .1, .03, Vector((0, 0, 1.4)), CYAN, ped)
    key(ped, 9.6, loc=B(-4.5, -1.4, 4.1))
    key(ped, 10.6, loc=B(-4.5, 0, 4.1))
    # геймпад: подъём, раскладка, сборка, перелёт на пьедестал
    gp = gb["g"]
    G0, G1, G2 = B(gb["x"], .45, gb["z"]), B(gb["x"], 2.9, gb["z"]), B(-4.5, 3.4, 4.1)
    stand = Euler((math.pi / 2 - .32, 0, 0))
    key(gp, 0, loc=G0, rot=Euler())
    key(gp, 5.9, loc=G0)
    key(gp, 6.0, rot=Euler())
    key(gp, 6.8, loc=G1, rot=stand)
    key(gp, 7.95, loc=G1 + Vector((0, 0, .1)))
    key(gp, 9.1, loc=G1)
    key(gp, 9.6, rot=stand)
    key(gp, 9.75, loc=(G1 + G2) / 2 + Vector((0, 0, .6)))
    key(gp, 10.4, loc=G2)
    key(gp, 10.5, loc=G2)
    key(gp, 10.9, rot=Euler())
    key(gp, 11.3, loc=B(-4.5, 1.7, 4.1))
    for ob, base, v in gb["parts"]:
        key(ob, 6.9, loc=base)
        key(ob, 7.6, loc=base + v * .6)
        key(ob, 8.4, loc=base + v * .6)
        key(ob, 9.0, loc=base)
    # сноски, как на технической схеме
    for pi, txt, ox, oz in ((1, "L-STICK", -1.4, .9), (2, "R-STICK", 1.4, .9), (3, "D-PAD", -1.5, -.4), (4, "ACTION", 1.6, -.4),
                            (8, "TOUCHPAD", 0, -1.4), (11, "L1", -1.7, -1.3), (12, "R1", 1.7, -1.3)):
        ob, base, v = gb["parts"][pi]
        lab = P.text(f"Callout_{txt}", txt, .17, base + v * .6 + Vector((ox * .42, .28 - oz * .22, .25)), CYAN, "mono", gp, .005)
        visible_between(lab, 7.4, 8.5)
    P.text("Emboss_gadgets", "ГАДЖЕТЫ", .95, B(-9.6, 0, 5.2), EMBOSS, "title", extrude=.03, rot_z=math.pi / 2)
    title_pair(P, "Title", (4.1, 0, 3.7), "ГАДЖЕТ", "ОТЕКА", 11.8, h=1.0, stagger=0, gap=0, lw=6.6, c1=CYAN,
               sub="НЕОБЫЧНЫЕ ГАДЖЕТЫ ЧЕЛОВЕЧЕСТВА", sub_h=.36)

    def gp_pos(t):
        g0, g1, g2 = (gb["x"], .45, gb["z"]), (gb["x"], 2.9, gb["z"]), (-4.5, 3.4, 4.1)
        if t < 5.9:
            return g0
        if t < 6.8:
            k = glide(seg(t, 5.9, 6.8))
            return (g0[0], lerp(g0[1], g1[1], k), g0[2])
        if t < 9.1:
            return (g1[0], g1[1] + math.sin((t - 6.8) * 1.2) * .1, g1[2])
        if t < 10.4:
            k = glide(seg(t, 9.1, 10.4))
            return (lerp(g1[0], g2[0], k), lerp(g1[1], g2[1], k) + math.sin(k * math.pi) * .6, lerp(g1[2], g2[2], k))
        k = glide(seg(t, 10.5, 11.3))
        return (g2[0], lerp(g2[1], 1.7, k), g2[2])

    def orbit(k, t):
        a, p = lerp(-.8, .5, k), gp_pos(t)
        return (p[0] + math.sin(a) * 7.4, 7.4, p[2] + math.cos(a) * 7.4), (p[0], p[1] + .35, p[2])

    def follow(k, t):
        p = gp_pos(t)
        return (lerp(4.6, 1.4, k), lerp(8.2, 7.4, k), lerp(5.6, 9.4, k)), (p[0], p[1] * .45, p[2])

    shot_cameras(P, [
        {"name": "отсеки", "t0": 0, "t1": 3.2, "p0": [-15.5, 7, 1.8], "p1": [-8.5, 6.4, 1.2], "l0": [-11.2, 0, -4.5], "l1": [-4.2, 0, -4.5], "fov": [34, 33]},
        {"name": "курсор_сверху", "t0": 3.2, "t1": 5.9, "p0": [-6.8, 14, -4.4], "p1": [-2.4, 13.2, -4.4], "fov": [34, 34], "top": True},
        {"name": "облёт_геймпада", "t0": 5.9, "t1": 8.9, "fn": orbit, "fov": [34, 34]},
        {"name": "перелёт", "t0": 8.9, "t1": 11.4, "fn": follow, "fov": [36, 36]},
        {"name": "финал_сверху", "t0": 11.4, "t1": 15, "p0": [.6, 25.5, 2.5], "p1": [.4, 24, 2.5], "fov": [22, 22], "top": True},
    ])


# =========================================================
# 4. ОТБИВКА · 5 с — поток пикселей собирает планку
# =========================================================
BUMPER_BEATS = [[0, "Пиксели едут к пазу, планка растёт"], [2.3, "Цвет заливает знак"], [3.4, "Финал сверху, подпись канала"]]


def build_bumper(sc):
    P = Piece(sc)
    setup_scene(sc, "BM", "ОТБИВКА", 5, BUMPER_BEATS)
    make_table(P, 43, [[-4.9, -3.4, 4.9, 3.4], [3.6, -1.1, 26, 1.1], [-6, 3.4, 6, 6.4]])
    shell, bar, bar_len, slot_x = make_logo(P)
    P.box("Track", 22, .06, 1.5, (14.6, 0, 0), DEEP)
    P.text("Track_label", "E2 E4", .22, B(4.6, .07, -.55), (0.3, 0.33, 0.4), "mono", extrude=.005, align="LEFT")
    bar.location = B(slot_x, .06, 0)
    bar.color = (*CYAN, 1)
    N, r = 34, rng(9)
    for i in range(N):
        s = .32 + r() * .32
        a = r()
        col = CYAN if a < .55 else VIOLET if a < .7 else ICE if a < .85 else DEEP
        cube = P.box(f"Pixel_{i:02d}", s, s, s, (0, 0, 0), col, bevel=s * .15)
        t0, dz, x0 = .1 + i * .052, (r() - .5) * .7, 21 + r() * 3
        end = slot_x + (i + 1) / N * bar_len
        visible_between(cube, t0, t0 + 1.1)
        key(cube, t0, loc=B(x0, .06, dz))
        key(cube, t0 + 1.1, loc=B(end, .06, 0))
    first, last = .1 + 1.1, .1 + (N - 1) * .052 + 1.1
    visible_between(bar, first)
    key(bar, first, scale=(1 / N, 1, 1), interp="LINEAR")
    key(bar, last, scale=(1, 1, 1))
    key(shell, 2.35, color=SATIN)
    key(shell, 3.25, color=CYAN)
    title_pair(P, "Title", (0, 0, 4.75), "ИГРОВОЙ", "КАНАЛ", 3.6, h=.82, stagger=.32, lz=1.12, lw=4.2)
    shot_cameras(P, [
        {"name": "поток_сверху", "t0": 0, "t1": 2.3, "p0": [8.6, 15, 0], "p1": [7, 14.4, 0], "fov": [34, 34], "top": True},
        {"name": "макро_знак", "t0": 2.3, "t1": 3.4, "p0": [6.2, 5.4, 5.6], "p1": [4.4, 5.2, 5.2], "l0": [-.6, .1, 0], "l1": [-1.4, .1, 0], "fov": [32, 31]},
        {"name": "финал_сверху", "t0": 3.4, "t1": 5, "p0": [.2, 30, 1.7], "p1": [0, 29, 1.7], "fov": [22, 22], "top": True},
    ])


PIECES = [("01_Заставка", build_ident), ("02_Вердикт", build_verdict), ("03_Гаджетотека", build_gadget), ("04_Отбивка", build_bumper)]


def main():
    global FONTS
    FONTS = {k: bpy.data.fonts.load(p, check_existing=True) for k, p in FONT_FILES.items()}
    first = bpy.context.scene
    # стартовая сцена: убрать куб, свет, камеру и их коллекцию
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for coll in list(first.collection.children):
        bpy.data.collections.remove(coll)
    for i, (name, build) in enumerate(PIECES):
        sc = first if i == 0 else bpy.data.scenes.new(name)
        sc.name = name
        build(sc)
        print(f"built {name}: {len(sc.objects)} objects")
    # вьюпорт как в аниматике: Solid, цвет по объекту, вид из камеры плана
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    sh = space.shading
                    sh.type, sh.light, sh.color_type = "SOLID", "STUDIO", "OBJECT"
                    sh.show_shadows, sh.show_cavity, sh.show_object_outline = True, True, True
                    space.region_3d.view_perspective = "CAMERA"
    for f in FONTS.values():
        f.pack()
    bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, compress=True)
    print("saved", OUT_BLEND)


FONTS = {}
if __name__ == "__main__":
    main()
