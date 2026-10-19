"""Рендер аниматика из etv_animatic.blend (Workbench, чистый кадр без прожига).

Запуск:  python render_animatic.py <сцена> <папка_кадров> [кадр ...]
         без номеров кадров рендерится весь ролик
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    scene_name, out_dir, *frames = argv
    bpy.ops.wm.open_mainfile(filepath=os.path.join(HERE, "etv_animatic.blend"))
    sc = bpy.data.scenes[scene_name]
    bpy.context.window.scene = sc  # frame_set работает только для активной сцены окна
    shot_markers = sorted((m for m in sc.timeline_markers if m.camera), key=lambda m: m.frame)
    os.makedirs(out_dir, exist_ok=True)
    frames = [int(f) for f in frames] or range(sc.frame_start, sc.frame_end + 1)
    for f in frames:
        sc.frame_set(f)
        sc.camera = [m.camera for m in shot_markers if m.frame <= f][-1]
        sc.render.filepath = os.path.join(out_dir, f"{f:04d}.png")
        bpy.ops.render.render(write_still=True, scene=sc.name)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
