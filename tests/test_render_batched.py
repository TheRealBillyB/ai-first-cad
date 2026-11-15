"""A frame is painted in batches, on several threads, and looks the same.

Painted one triangle at a time, a 1080p frame at 2x supersampling took about
2.3 s on one core. The reference painter below is that code, kept here so the
batched painter is compared with it pixel for pixel.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from aicad import kernel, render
from aicad.kernel import TriangleMesh

_METAL = render._METAL


def _paint_triangle(
    image: np.ndarray,
    depth: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    shades: tuple[float, float, float],
) -> None:
    height, width = depth.shape
    min_x = max(int(math.floor(min(p0[0], p1[0], p2[0]))), 0)
    max_x = min(int(math.ceil(max(p0[0], p1[0], p2[0]))), width - 1)
    min_y = max(int(math.floor(min(p0[1], p1[1], p2[1]))), 0)
    max_y = min(int(math.ceil(max(p0[1], p1[1], p2[1]))), height - 1)
    if min_x > max_x or min_y > max_y:
        return
    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(denom) < 1e-8:
        return

    ys, xs = np.mgrid[min_y : max_y + 1, min_x : max_x + 1]
    w0 = ((p1[1] - p2[1]) * (xs - p2[0]) + (p2[0] - p1[0]) * (ys - p2[1])) / denom
    w1 = ((p2[1] - p0[1]) * (xs - p2[0]) + (p0[0] - p2[0]) * (ys - p2[1])) / denom
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
    z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    region = depth[min_y : max_y + 1, min_x : max_x + 1]
    update = inside & (z > region)
    if not np.any(update):
        return
    region[update] = z[update]
    shade = w0 * shades[0] + w1 * shades[1] + w2 * shades[2]
    rgb = np.clip(_METAL * shade[..., None], 0, 255).astype(np.uint8)
    pixels = image[min_y : max_y + 1, min_x : max_x + 1]
    pixels[update] = rgb[update]


def _paint_triangle_color(
    image: np.ndarray,
    depth: np.ndarray,
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    colors: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> None:
    height, width = depth.shape
    min_x = max(int(math.floor(min(p0[0], p1[0], p2[0]))), 0)
    max_x = min(int(math.ceil(max(p0[0], p1[0], p2[0]))), width - 1)
    min_y = max(int(math.floor(min(p0[1], p1[1], p2[1]))), 0)
    max_y = min(int(math.ceil(max(p0[1], p1[1], p2[1]))), height - 1)
    if min_x > max_x or min_y > max_y:
        return
    denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
    if abs(denom) < 1e-8:
        return
    ys, xs = np.mgrid[min_y : max_y + 1, min_x : max_x + 1]
    w0 = ((p1[1] - p2[1]) * (xs - p2[0]) + (p2[0] - p1[0]) * (ys - p2[1])) / denom
    w1 = ((p2[1] - p0[1]) * (xs - p2[0]) + (p0[0] - p2[0]) * (ys - p2[1])) / denom
    w2 = 1.0 - w0 - w1
    inside = (w0 >= 0.0) & (w1 >= 0.0) & (w2 >= 0.0)
    z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
    region = depth[min_y : max_y + 1, min_x : max_x + 1]
    update = inside & (z > region)
    if not np.any(update):
        return
    region[update] = z[update]
    rgb = w0[..., None] * colors[0] + w1[..., None] * colors[1] + w2[..., None] * colors[2]
    pixels = image[min_y : max_y + 1, min_x : max_x + 1]
    pixels[update] = np.clip(rgb[update], 0, 255).astype(np.uint8)


def _reference_rasterize(mesh, basis, scale, width, height, center=None):
    """The painter as it was: one triangle after another, in order."""
    x_cam, y_cam, z_cam = basis
    if center is None:
        center = render._projection_center(mesh.vertices, x_cam, y_cam)
    screen = render._project(mesh.vertices, x_cam, y_cam, z_cam, scale, width, height, center)
    key = render._unit(np.array([0.35, -0.55, 0.85]))
    fill = render._unit(np.array([-0.7, 0.25, 0.35]))
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = render._PAPER
    depth = np.full((height, width), -1e30, dtype=np.float64)
    for tri in mesh.triangles:
        i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
        shades = tuple(render._shade(mesh.normals[i], key, fill) for i in (i0, i1, i2))
        if mesh.colors is None:
            _paint_triangle(image, depth, screen[i0], screen[i1], screen[i2], shades)
        else:
            colored = tuple(mesh.colors[i] * shade for i, shade in zip((i0, i1, i2), shades))
            _paint_triangle_color(image, depth, screen[i0], screen[i1], screen[i2], colored)
    return image, depth


def _drilled_disc() -> TriangleMesh:
    ops = [
        {"op": "add", "shape": "cylinder", "radius": 40, "height": 5},
        {"op": "cut", "shape": "cylinder", "radius": 18, "height": 20},
    ]
    for index in range(24):
        angle = 2 * math.pi * index / 24
        ops.append({"op": "cut", "shape": "cylinder", "radius": 1.5, "height": 20, "center": [30 * math.cos(angle), 30 * math.sin(angle), 0]})
    mesh = kernel.tessellate(kernel.make_from_ops(ops))
    return TriangleMesh(vertices=mesh.vertices, normals=mesh.normals, triangles=mesh.triangles, edges=[], colors=None)


def _faces_only(mesh, width, height, center=None):
    basis = render._camera_basis((1.0, -1.0, 0.85), (0.0, 0.0, 1.0))
    scale = render.frame_scale([mesh], [basis], width, height, 1)
    ours, our_depth = render._rasterize(mesh, basis, scale, width, height, center=center)
    theirs, their_depth = _reference_rasterize(mesh, basis, scale, width, height, center=center)
    return ours, theirs, our_depth, their_depth


@pytest.mark.parametrize("threads", [1, 4])
def test_the_batched_painter_paints_what_one_triangle_at_a_time_did(monkeypatch, threads):
    monkeypatch.setattr(render, "_RENDER_THREADS", threads)
    monkeypatch.setattr(render, "_BATCH_PIXELS", 20_000)
    ours, theirs, our_depth, their_depth = _faces_only(_drilled_disc(), 640, 400)
    assert np.array_equal(ours, theirs)
    assert np.array_equal(our_depth, their_depth)


def test_colored_triangles_paint_the_same(monkeypatch):
    monkeypatch.setattr(render, "_RENDER_THREADS", 3)
    mesh = _drilled_disc()
    colors = np.stack([np.linspace(30, 220, len(mesh.vertices)), np.full(len(mesh.vertices), 120.0), np.linspace(220, 30, len(mesh.vertices))], axis=1)
    colored = TriangleMesh(vertices=mesh.vertices, normals=mesh.normals, triangles=mesh.triangles, edges=[], colors=colors)
    ours, theirs, _, _ = _faces_only(colored, 500, 300)
    assert np.array_equal(ours, theirs)


def test_on_an_exact_tie_the_first_triangle_wins(monkeypatch):
    """Two copies of one triangle, red then blue: painting in order kept red."""
    monkeypatch.setattr(render, "_RENDER_THREADS", 2)
    vertices = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 0], [10, 0, 0], [0, 10, 0]], dtype=np.float64)
    normals = np.tile([0.0, 0.0, 1.0], (6, 1))
    colors = np.array([[220, 0, 0]] * 3 + [[0, 0, 220]] * 3, dtype=np.float64)
    triangles = np.array([[0, 1, 2], [3, 4, 5]])
    mesh = TriangleMesh(vertices=vertices, normals=normals, triangles=triangles, edges=[], colors=colors)
    ours, theirs, _, _ = _faces_only(mesh, 200, 200)
    assert np.array_equal(ours, theirs)
    painted = ours.reshape(-1, 3)[(ours.reshape(-1, 3) != render._PAPER).any(axis=1)]
    assert len(painted) and (painted[:, 0] > painted[:, 2]).all()
