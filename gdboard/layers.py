"""Cores e geometria auxiliar das camadas de cobre (modo camadas)."""
from __future__ import annotations

import numpy as np

_INNER = [(92, 192, 222), (92, 184, 92), (240, 173, 78), (155, 89, 182), (26, 188, 156), (230, 126, 34),
          (52, 152, 219), (241, 196, 15), (231, 76, 60), (149, 165, 166), (211, 84, 0), (46, 204, 113),
          (142, 68, 173), (22, 160, 133)]


def layer_color(index, name):
    u = name.upper()
    if u == "TOP":
        return (226, 88, 72)
    if u == "BOTTOM":
        return (70, 140, 240)
    return _INNER[index % len(_INNER)]


def trace_instances(seg, origin=(0.0, 0.0)):
    """Trilhas como 'pads' em forma de cápsula (centro, meia-medida, eixo, lado 0, redondo)."""
    n = len(seg)
    out = np.zeros((n, 8), dtype=np.float32)
    if n == 0:
        return out
    x1, y1, x2, y2, w = seg[:, 0], seg[:, 1], seg[:, 2], seg[:, 3], seg[:, 4]
    dx, dy = x2 - x1, y2 - y1
    ln = np.hypot(dx, dy)
    safe = np.where(ln > 0, ln, 1.0)
    ux = np.where(ln > 0, dx / safe, 1.0)
    uy = np.where(ln > 0, dy / safe, 0.0)
    out[:, 0] = (x1 + x2) / 2 - origin[0]
    out[:, 1] = (y1 + y2) / 2 - origin[1]
    out[:, 2] = ln / 2 + w / 2
    out[:, 3] = w / 2
    out[:, 4] = ux
    out[:, 5] = uy
    out[:, 6] = 0.0
    out[:, 7] = 1.0
    return out


def pad_instances(pad, origin=(0.0, 0.0)):
    n = len(pad)
    out = np.zeros((n, 8), dtype=np.float32)
    if n == 0:
        return out
    out[:, 0] = pad[:, 0] - origin[0]
    out[:, 1] = pad[:, 1] - origin[1]
    out[:, 2] = pad[:, 2]
    out[:, 3] = pad[:, 3]
    out[:, 4] = 1.0
    out[:, 7] = pad[:, 4]
    return out


def ring_fans(rings, origin=(0.0, 0.0)):
    """Triângulos em leque de cada anel (contorno ou furo) — preenchidos depois com par/ímpar no stencil.
    Vetorizado: todos os anéis de uma vez."""
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return np.zeros((0, 2))
    lens = np.fromiter((len(r) for r in rings), dtype=np.int64, count=len(rings))
    pts = np.concatenate(rings).astype(np.float64) - np.asarray(origin, dtype=np.float64)
    starts = np.concatenate([[0], np.cumsum(lens)[:-1]])
    ntri = lens - 2
    first = np.repeat(starts, ntri)
    # índice i (1..m-2) dentro de cada anel
    k = np.arange(ntri.sum()) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
    idx = np.empty((len(first), 3), dtype=np.int64)
    idx[:, 0] = first
    idx[:, 1] = first + k
    idx[:, 2] = first + k + 1
    return pts[idx.reshape(-1)]


def seg_distance(seg, px, py):
    """Distância do ponto até cada segmento (menos a meia-largura)."""
    x1, y1, x2, y2, w = seg[:, 0], seg[:, 1], seg[:, 2], seg[:, 3], seg[:, 4]
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    t = np.clip(((px - x1) * dx + (py - y1) * dy) / np.where(l2 > 0, l2, 1.0), 0.0, 1.0)
    qx, qy = x1 + t * dx, y1 + t * dy
    return np.hypot(px - qx, py - qy) - w / 2
