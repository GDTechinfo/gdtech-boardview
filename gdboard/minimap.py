"""Minimapa: a placa inteira em miniatura no canto da vista, com o retângulo do que está na tela.
Clicar ou arrastar no minimapa leva a vista para lá. Funciona com giro, espelho, lado de baixo e vista dupla."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QEvent
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPolygonF, QBrush
from PySide6.QtWidgets import QWidget

from .parsers import TOP, BOTTOM, BOTH

MAX_W, MAX_H, MARGIN, PAD = 230, 170, 12, 8


class Minimap(QWidget):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.c = canvas
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self._img = None
        self._img_key = None
        self._state = None
        self._map = None              # (A1, escala, deslocamento) placa -> minimapa
        self._drag = False
        canvas.installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self.hide()

    # ------------------------------------------------------------------ posição/tamanho
    def eventFilter(self, obj, ev):
        if obj is self.c and ev.type() == QEvent.Resize:
            self._place()
        return False

    def _place(self):
        b = self.c.board
        if b is None:
            return
        x0, y0, x1, y1 = self._oriented_bounds()
        w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
        k = min((MAX_W - 2 * PAD) / w, (MAX_H - 2 * PAD) / h)
        W, H = int(w * k + 2 * PAD), int(h * k + 2 * PAD)
        self.resize(W, H)
        self.move(self.c.width() - W - MARGIN, self.c.height() - H - MARGIN)

    def set_enabled(self, on):
        self._enabled = bool(on)
        self._poll(force=True)

    # ------------------------------------------------------------------ geometria
    def _scene_pts(self, x, y, side=None):
        """Pontos da placa -> pontos da cena (cópia espelhada para o outro lado na vista dupla)."""
        if side is not None and self.c.dual_view:
            xs, ys = np.array(x, dtype=np.float64), np.array(y, dtype=np.float64)
            oth = side == self.c.other_side()
            if oth.any():
                xs[oth], ys[oth] = self.c.scene_xy(xs[oth], ys[oth], self.c.other_side())
            return xs, ys
        return np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)

    def _oriented(self, x, y):
        A = self.c._A(1.0)
        return A[0, 0] * x + A[0, 1] * y, A[1, 0] * x + A[1, 1] * y

    def _oriented_bounds(self):
        x0, y0, x1, y1 = self.c.scene_bounds()
        cx = np.array([x0, x1, x0, x1])
        cy = np.array([y0, y0, y1, y1])
        ox, oy = self._oriented(cx, cy)
        return ox.min(), oy.min(), ox.max(), oy.max()

    def _to_mini(self, x, y):
        ox, oy = self._oriented(x, y)
        k, bx, by = self._map
        return PAD + (ox - bx) * k, PAD + (oy - by) * k

    def _from_mini(self, mx, my):
        k, bx, by = self._map
        ox, oy = (mx - PAD) / k + bx, (my - PAD) / k + by
        A = self.c._A(1.0)
        v = np.linalg.solve(A, np.array([ox, oy]))
        return float(v[0]), float(v[1])

    # ------------------------------------------------------------------ desenho
    def _poll(self, force=False):
        c = self.c
        show = getattr(self, "_enabled", True) and c.board is not None
        if self.isVisible() != show:
            self.setVisible(show)
            if show:
                self._place()
        if not show:
            return
        st = (c.scale, c.cx, c.cy, c.side, c.rotation, c.mirror_h, c.mirror_v, c.opts.flip_axis, c.dual_view,
              c.sel_net, c.width(), c.height(), id(c.board))
        if force or st != self._state:
            geom_changed = self._state is None or st[3:9] != self._state[3:9] or st[10:] != self._state[10:]
            self._state = st
            if geom_changed:
                self._place()
            self.update()

    def _build_image(self):
        c = self.c
        b = c.board
        th = c.theme
        key = (id(b), c.side, c.rotation, c.mirror_h, c.mirror_v, c.opts.flip_axis, c.dual_view,
               self.width(), self.height(), id(th))
        if key == self._img_key and self._img is not None:
            return
        self._img_key = key
        x0, y0, x1, y1 = self._oriented_bounds()
        w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
        k = min((self.width() - 2 * PAD) / w, (self.height() - 2 * PAD) / h)
        self._map = (k, x0, y0)
        img = QImage(self.width(), self.height(), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        bg = th.get("Background")
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(bg[0], bg[1], bg[2], 225))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 6, 6)
        copies = [(c.side, None)]
        if c.dual_view:
            copies.append((c.other_side(), c.mirror_R()))
        oc = th.get("BoardOutline")
        pc = th.get("PinsPads")
        gc = th.get("GndColor", "PinsPads")
        for side, R in copies:
            # contorno da placa
            if b.outline_segs:
                seg = np.array([(a[0], a[1], d[0], d[1]) for a, d in b.outline_segs], dtype=np.float64)
                xa, ya, xb, yb = seg[:, 0], seg[:, 1], seg[:, 2], seg[:, 3]
                if R is not None:
                    xa, ya = R[0, 0] * xa + R[0, 1] * ya + R[0, 2], R[1, 0] * xa + R[1, 1] * ya + R[1, 2]
                    xb, yb = R[0, 0] * xb + R[0, 1] * yb + R[0, 2], R[1, 0] * xb + R[1, 1] * yb + R[1, 2]
                ma, na = self._to_mini(xa, ya)
                mb, nb = self._to_mini(xb, yb)
                p.setPen(QPen(QColor(oc[0], oc[1], oc[2], 200), 1))
                for i in range(len(ma)):
                    p.drawLine(QPointF(ma[i], na[i]), QPointF(mb[i], nb[i]))
            # pinos deste lado
            vis = (b.side == side) | (b.side == BOTH)
            x, y = b.pad_x[vis], b.pad_y[vis]
            if R is not None:
                x, y = R[0, 0] * x + R[0, 1] * y + R[0, 2], R[1, 0] * x + R[1, 1] * y + R[1, 2]
            mx, my = self._to_mini(x, y)
            gnd = b.net_is_gnd[b.net[vis]]
            for mask, col in ((~gnd, pc), (gnd, gc)):
                if not mask.any():
                    continue
                pts = QPolygonF([QPointF(a, d) for a, d in zip(mx[mask].tolist(), my[mask].tolist())])
                p.setPen(QPen(QColor(col[0], col[1], col[2], 170), 1.2))
                p.drawPoints(pts)
        p.end()
        self._img = img

    def paintEvent(self, e):
        c = self.c
        if c.board is None:
            return
        self._build_image()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawImage(0, 0, self._img)
        b = c.board
        th = c.theme
        # rede selecionada
        if c.sel_net is not None and not b.net_is_nc[c.sel_net] and len(b.net_pins[c.sel_net]) < 4000:
            idx = np.asarray(b.net_pins[c.sel_net])
            x, y = self._scene_pts(b.pad_x[idx], b.pad_y[idx], b.side[idx])
            if not c.dual_view:
                keep = (b.side[idx] == c.side) | (b.side[idx] == BOTH)
                x, y = x[keep], y[keep]
            mx, my = self._to_mini(x, y)
            hc = th.get("NetHighlight")
            p.setPen(QPen(QColor(hc[0], hc[1], hc[2]), 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawPoints(QPolygonF([QPointF(a, d) for a, d in zip(mx.tolist(), my.tolist())]))
        # o que está na tela
        R0, c._copy_R = c._copy_R, None
        try:
            corners = [c.to_board(0, 0), c.to_board(c.width(), 0), c.to_board(c.width(), c.height()),
                       c.to_board(0, c.height())]
        finally:
            c._copy_R = R0
        xs = np.array([q[0] for q in corners])
        ys = np.array([q[1] for q in corners])
        mx, my = self._to_mini(xs, ys)
        poly = QPolygonF([QPointF(a, d) for a, d in zip(mx.tolist(), my.tolist())])
        p.setClipRect(QRectF(1, 1, self.width() - 2, self.height() - 2))
        p.setPen(QPen(QColor(255, 179, 0), 1.6))
        p.setBrush(QBrush(QColor(255, 179, 0, 40)))
        p.drawPolygon(poly)
        p.end()

    # ------------------------------------------------------------------ mouse
    def _go(self, pos):
        if self._map is None:
            return
        x, y = self._from_mini(pos.x(), pos.y())
        c = self.c
        c._finish_anchor()
        c._anchor = None
        c.cx = c.t_cx = x
        c.cy = c.t_cy = y
        c.scale = c.t_scale
        c.request_frame()
        c.viewChanged.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = True
            self._go(e.position())
        e.accept()

    def mouseMoveEvent(self, e):
        if self._drag:
            self._go(e.position())
        e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = False
        e.accept()

    def wheelEvent(self, e):
        e.accept()
