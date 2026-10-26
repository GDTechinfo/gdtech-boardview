"""Lógica comum aos dois renderizadores (GPU e CPU): câmera, animação suave,
seleção, testes de clique e eventos de mouse."""
from __future__ import annotations

import math
import time

import numpy as np

from .parsers import TOP, BOTTOM, BOTH
from .theme import component_type

CAT_NORMAL, CAT_TH, CAT_GND, CAT_NC, CAT_TP = range(5)


class ViewOptions:
    def __init__(self):
        self.smooth = True
        self.smooth_speed = 16.0       # quanto maior, mais rápido chega no alvo
        self.wheel_step = 1.0          # multiplicador da sensibilidade da roda
        self.show_part_labels = True
        self.show_pin_labels = True
        self.show_net_names = True
        self.show_netweb = True
        self.netweb_cross = True       # vista dupla: linhas da rede ligam os pinos nas duas placas
        self.show_boxes = True
        self.show_fps = False
        self.part_label_scale = 1.0
        self.pin_label_scale = 1.0
        self.pin_scale = 1.0
        self.square_pins = False
        self.flip_axis = "y"           # bottom espelhado no eixo Y (padrão) ou X
        self.hover_info = True         # caixa de informações ao passar o mouse
        self.gnd_subtle = True         # GND selecionado: só contorno nos pinos (não acende meia placa)
        self.show_loose = True         # destacar pinos soltos e componentes sem rede
        self.show_mech = True          # peças mecânicas (reforço, furos de fixação...): nome e contorno


class CanvasCore:
    """Mixin: a classe do widget define os sinais selectionChanged, cursorMoved, viewChanged
    e implements request_frame() e board_changed()."""

    def _core_init(self):
        self.board = None
        self.opts = ViewOptions()
        self.side = TOP
        self.rotation = 0
        self.mirror_h = False
        self.mirror_v = False
        self.scale = 1.0
        self.cx = 0.0
        self.cy = 0.0
        self.t_scale = 1.0
        self.t_cx = 0.0
        self.t_cy = 0.0
        self._anchor = None
        self._last_tick = None
        self.sel_part = None
        self.sel_pin = None
        self.sel_net = None
        self._press = None
        self._last_pos = None
        self._dragging = False
        self._drag_button = None
        self.mouse_bindings = {}      # nome do botão -> função
        self.theme = None
        self.hover_part = None
        self.hover_pin = None
        self._card = None
        self._mouse_xy = None
        self.dual_view = False    # vista dupla: o outro lado desenhado espelhado logo acima, na mesma cena
        self.lock_side = False    # vista dupla: não troca de lado sozinho ao selecionar
        self._net_cycle = None    # busca de rede: (rede, pinos em ordem, posição atual)
        self._copy_R = None       # durante o desenho da cópia espelhada: reflexão (3x3) em coordenadas da placa
        self.part_marks = {}      # componente -> cor (resultados de busca, comparação...)
        self.note_marks = {}      # componente -> cor da etiqueta da nota
        self.group_nets = set()   # redes destacadas juntas ("seguir a rede")
        self.note_lookup = None   # função(pi) -> (etiqueta, texto, cor) ou None
        self.layer_mode = False   # modo camadas (trilhas de cobre do TVW / GenCAD)
        self._layer_vis = []

    # ------------------------------------------------------------------ dados
    def set_board(self, board):
        self.board = board
        self.sel_part = self.sel_pin = self.sel_net = None
        self.part_marks, self.note_marks, self.group_nets = {}, {}, set()
        self.hover_part = self.hover_pin = None
        if self._card is not None:
            self._card.hide_card()
        self.rotation = 0
        self.mirror_h = self.mirror_v = False
        self.side = TOP
        if board is not None:
            top = int(np.count_nonzero(board.side == TOP))
            bot = int(np.count_nonzero(board.side == BOTTOM))
            if top == 0 and bot > 0:
                self.side = BOTTOM
            self._prepare_board()
            self._layer_vis = self._default_layers()
            self.fit(animate=False)
        self.board_changed()
        self.selectionChanged.emit()
        self.request_frame()

    def _prepare_board(self):
        b = self.board
        n = len(b.parts)
        self.pcx = np.array([p.center[0] for p in b.parts], dtype=np.float64) if n else np.zeros(0)
        self.pcy = np.array([p.center[1] for p in b.parts], dtype=np.float64) if n else np.zeros(0)
        self.psize = np.array([p.size for p in b.parts], dtype=np.float64) if n else np.zeros(0)
        self.pside = np.array([p.side for p in b.parts], dtype=np.int8) if n else np.zeros(0, np.int8)
        self.ptype = [component_type(p.name) for p in b.parts]
        cat = np.full(len(b.x), CAT_NORMAL, dtype=np.int8)
        if len(b.x):
            th = np.array([(not b.parts[pi].smd) if pi >= 0 else False for pi in b.part], dtype=bool)
            cat[th] = CAT_TH
            cat[b.part < 0] = CAT_TP
            cat[b.net_is_gnd[b.net]] = CAT_GND
            cat[b.net_is_nc[b.net]] = CAT_NC
        self.pcat = cat
        self.first_pin = np.array([s in ("1", "A1") for s in b.num], dtype=bool)

    # ------------------------------------------------------------------ câmera
    def _A(self, scale=None):
        """Parte linear (2x2) placa -> tela."""
        s = self.scale if scale is None else scale
        sx, sy = s, -s
        bottom = self.side == BOTTOM
        if bottom:
            if self.opts.flip_axis == "x":
                sy = -sy
            else:
                sx = -sx
        if self.mirror_h:
            sx = -sx
        if self.mirror_v:
            sy = -sy
        a = math.radians(90 * self.rotation)
        c, si = math.cos(a), math.sin(a)
        R = np.array([[c, -si], [si, c]])
        return R @ np.diag([sx, sy])

    def matrix(self, origin=(0.0, 0.0)):
        """Matriz 3x3 placa -> pixels de tela (coordenadas relativas a 'origin').
        Durante o desenho da cópia espelhada (vista dupla) já inclui a reflexão."""
        A = self._A()
        w, h = self.width(), self.height()
        t = np.array([w / 2, h / 2]) - A @ np.array([self.cx - origin[0], self.cy - origin[1]])
        M = np.eye(3)
        M[:2, :2] = A
        M[:2, 2] = t
        if self._copy_R is not None:
            T = np.eye(3)
            T[:2, 2] = origin
            Ti = np.eye(3)
            Ti[:2, 2] = [-origin[0], -origin[1]]
            M = M @ Ti @ self._copy_R @ T
        return M

    def to_board(self, x, y):
        A = self._A()
        v = np.linalg.solve(A, np.array([x - self.width() / 2, y - self.height() / 2]))
        px, py = float(v[0] + self.cx), float(v[1] + self.cy)
        if self._copy_R is not None:
            R = self._copy_R          # reflexão é a própria inversa
            px, py = R[0, 0] * px + R[0, 1] * py + R[0, 2], R[1, 0] * px + R[1, 1] * py + R[1, 2]
        return float(px), float(py)

    def to_screen(self, bx, by):
        M = self.matrix()
        return float(M[0, 0] * bx + M[0, 1] * by + M[0, 2]), float(M[1, 0] * bx + M[1, 1] * by + M[1, 2])

    def visible_rect(self):
        pts = [self.to_board(0, 0), self.to_board(self.width(), 0),
               self.to_board(0, self.height()), self.to_board(self.width(), self.height())]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    # ------------------------------------------------------------------ animação
    def _snap(self):
        self.scale, self.cx, self.cy = self.t_scale, self.t_cx, self.t_cy
        self._anchor = None

    def _start_anim(self):
        if not self.opts.smooth:
            self._finish_anchor()
            self._snap()
        self._last_tick = None
        self.request_frame()
        self.viewChanged.emit()

    # ------------------------------------------------------------------ vista dupla (mesma cena)
    def other_side(self):
        return BOTTOM if self.side == TOP else TOP

    def mirror_beside(self):
        """Vista dupla: a cópia vai ao lado (placa em pé na tela) ou em cima (placa deitada)."""
        x0, y0, x1, y1 = self.board.bounds
        A = self._A(1.0)
        sc = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) @ A.T
        return np.ptp(sc[:, 1]) > np.ptp(sc[:, 0])

    def mirror_R(self):
        """Reflexão (em coordenadas da placa) que leva a placa para a cópia espelhada, como um livro
        aberto: logo ACIMA dela na tela, ou AO LADO (à direita) quando a placa está em pé.
        Depende só da orientação da vista (giro/espelho), não do zoom."""
        b = self.board
        x0, y0, x1, y1 = b.bounds
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        A = self._A(1.0)
        screen_dir = [1.0, 0.0] if self.mirror_beside() else [0.0, -1.0]
        d = np.linalg.solve(A, np.array(screen_dir))       # direção da placa que aponta para lá na tela
        d /= np.linalg.norm(d)
        corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) - [cx, cy]
        ext = float(np.max(corners @ d))
        gap = 0.06 * max(x1 - x0, y1 - y0)
        m = np.array([cx, cy]) + d * (ext + gap / 2)
        L = np.eye(2) - 2 * np.outer(d, d)
        R = np.eye(3)
        R[:2, :2] = L
        R[:2, 2] = m - L @ m
        return R

    def scene_xy(self, x, y, side):
        """Onde um ponto de um lado aparece na cena (na cópia espelhada, se for o outro lado)."""
        if not self.dual_view or side not in (TOP, BOTTOM) or side == self.side:
            return x, y
        R = self.mirror_R()
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        return R[0, 0] * x + R[0, 1] * y + R[0, 2], R[1, 0] * x + R[1, 1] * y + R[1, 2]

    def scene_bounds(self):
        x0, y0, x1, y1 = self.board.bounds
        if not self.dual_view:
            return x0, y0, x1, y1
        R = self.mirror_R()
        c = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], dtype=np.float64)
        rc = c @ R.T
        xs = np.concatenate([c[:, 0], rc[:, 0]])
        ys = np.concatenate([c[:, 1], rc[:, 1]])
        return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())

    # ------------------------------------------------------------------ camadas de cobre
    def has_layers(self):
        return self.board is not None and bool(getattr(self.board, "layers", None))

    def _default_layers(self):
        L = getattr(self.board, "layers", None) or []
        want = "TOP" if self.side == TOP else "BOTTOM"
        vis = [l.name.upper() == want for l in L]
        if L and not any(vis):
            vis[0] = True
        return vis

    def layer_visible(self, i):
        return 0 <= i < len(self._layer_vis) and self._layer_vis[i]

    def layer_color(self, i):
        from .layers import layer_color
        return layer_color(i, self.board.layers[i].name)

    def set_layer_mode(self, on):
        self.layer_mode = bool(on) and self.has_layers()
        self.layers_updated()
        self.request_frame()
        self.viewChanged.emit()

    def set_layer_visible(self, i, on):
        if 0 <= i < len(self._layer_vis):
            self._layer_vis[i] = bool(on)
            self.layers_updated()
            self.request_frame()

    def solo_layer(self, i):
        if 0 <= i < len(self._layer_vis):
            self._layer_vis = [k == i for k in range(len(self._layer_vis))]
            self.layers_updated()
            self.request_frame()
            self.viewChanged.emit()

    def step_layer(self, d):
        """Mostra só a próxima/anterior camada (PgDown/PgUp)."""
        n = len(self._layer_vis)
        if not n:
            return
        cur = [i for i, v in enumerate(self._layer_vis) if v]
        i = (cur[0] + d) % n if len(cur) == 1 else (0 if d > 0 else n - 1)
        if not self.layer_mode:
            self.set_layer_mode(True)
        self.solo_layer(i)

    def hit_trace(self, sx, sy):
        """Rede da trilha/via/área sob o cursor nas camadas visíveis (modo camadas)."""
        if not (self.layer_mode and self.has_layers()):
            return None
        from .layers import seg_distance
        px, py, _side = self._pick(sx, sy)
        tol = 3.0 / self.scale
        best, best_d = None, tol
        for i, L in enumerate(self.board.layers):
            if not self.layer_visible(i):
                continue
            if len(L.seg):
                d = seg_distance(L.seg, px, py)
                j = int(np.argmin(d))
                if d[j] <= best_d and L.seg_net[j] >= 0:
                    best, best_d = int(L.seg_net[j]), d[j]
            if len(L.pad):
                d = np.hypot(L.pad[:, 0] - px, L.pad[:, 1] - py) - np.minimum(L.pad[:, 2], L.pad[:, 3])
                j = int(np.argmin(d))
                if d[j] <= best_d and L.pad_net[j] >= 0:
                    best, best_d = int(L.pad_net[j]), d[j]
        return best

    def layers_updated(self):
        pass

    # ------------------------------------------------------------------ marcações
    def set_part_marks(self, marks):
        self.part_marks = dict(marks or {})
        self.marks_updated()
        self.request_frame()

    def set_note_marks(self, marks):
        self.note_marks = dict(marks or {})
        self.marks_updated()
        self.request_frame()

    def set_group_nets(self, nets):
        self.group_nets = set(nets or ())
        self.marks_updated()
        self.request_frame()

    def marks_updated(self):
        pass

    def net_active(self):
        """Rede selecionada que deve acender (e apagar o resto). GND fica discreto se a opção estiver ligada."""
        b = self.board
        n = self.sel_net
        if b is None or n is None or b.net_is_nc[n]:
            return False
        return not (self.opts.gnd_subtle and b.net_is_gnd[n])

    def net_pin_order(self, ni):
        """Ordem para percorrer os pinos de uma rede (Tab): lado atual primeiro, num caminho curto."""
        b = self.board
        cache = self.__dict__.setdefault("_net_order", {})
        key = (ni, self.side)
        if key in cache:
            return cache[key]
        pins = list(b.net_pins[ni])
        out = []
        for sd in (self.side, BOTH, BOTTOM if self.side == TOP else TOP):
            grp = [i for i in pins if int(b.side[i]) == sd]
            if not grp:
                continue
            pts = np.c_[b.pad_x[grp], b.pad_y[grp]]
            left = list(range(len(grp)))
            cur = int(np.argmin(pts[:, 0] - pts[:, 1]))
            order = [cur]
            left.remove(cur)
            while left:
                rest = np.array(left)
                d = np.hypot(pts[rest, 0] - pts[cur, 0], pts[rest, 1] - pts[cur, 1])
                cur = int(rest[np.argmin(d)])
                order.append(cur)
                left.remove(cur)
            out += [grp[k] for k in order]
        if len(cache) > 64:
            cache.clear()
        cache[key] = out
        return out

    def step_net_pin(self, d=1):
        """Tab / Shift+Tab: vai para o próximo/anterior pino da rede selecionada, mantendo o zoom."""
        b = self.board
        if b is None or self.sel_net is None:
            return
        order = self.net_pin_order(self.sel_net)
        if not order:
            return
        k = order.index(self.sel_pin) if self.sel_pin in order else (-1 if d > 0 else 0)
        i = order[(k + d) % len(order)]
        self.sel_pin = int(i)
        pi = int(b.part[i])
        self.sel_part = pi if pi >= 0 else None
        sd = int(b.side[i])
        if sd in (TOP, BOTTOM) and sd != self.side and not self.lock_side:
            self.flip_side()
        x, y = self.scene_xy(b.pad_x[i], b.pad_y[i], sd)
        x, y = float(x), float(y)
        r = float(max(b.pad_hw[i], b.pad_hh[i]))
        if r * self.t_scale < 6:          # pino pequeno demais na tela: aproxima
            z = r * 30
            self.fit((x - z, y - z, x + z, y + z), margin=0.02)
        else:
            self._finish_anchor()
            self._anchor = None
            self.t_cx, self.t_cy = x, y
            self._start_anim()
        self._sel_changed()
        self.pinStepped.emit(order.index(i) + 1, len(order))

    def focus_part(self, pi, zoom=True):
        """Seleciona e enquadra um componente mantendo a rede destacada (caçar curto, resultados)."""
        part = self.board.parts[pi]
        self.sel_part = int(pi)
        self.sel_pin = None
        if part.side in (TOP, BOTTOM) and part.side != self.side and not self.lock_side:
            self.flip_side()
        if zoom:
            self._zoom_part(pi)
        self._sel_changed()

    def fit_parts(self, pis):
        """Enquadra vários componentes (resultados da busca)."""
        b = self.board
        if not pis:
            return
        xs, ys = [], []
        for pi in pis:
            p = b.parts[pi]
            x, y = self.scene_xy(p.center[0], p.center[1], p.side)
            xs.append(float(x)); ys.append(float(y))
        pad = max(float(np.median([b.parts[pi].size for pi in pis])) * 3, 20.0)
        self.fit((min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad), margin=0.05)

    def netweb_targets(self):
        """Pinos ligados ao pino selecionado (para as linhas da rede)."""
        b = self.board
        if (b is None or self.sel_pin is None or self.sel_net is None or b.net_is_gnd[self.sel_net]
                or b.net_is_nc[self.sel_net]):
            return np.zeros(0, dtype=np.int64)
        t = np.asarray(b.net_pins[self.sel_net][:5000], dtype=np.int64)
        return t[t != self.sel_pin]

    def netweb_cross_active(self):
        return self.dual_view and self.opts.netweb_cross

    def netweb_scene_segments(self):
        """Vista dupla: segmentos (x0, y0, x1, y1) do pino selecionado até cada pino da rede, cada ponta
        na placa onde o pino aparece (de cima ou de baixo)."""
        b = self.board
        t = self.netweb_targets()
        if len(t) == 0:
            return np.zeros((0, 4))
        i = self.sel_pin
        x0, y0 = self.scene_xy(b.pad_x[i], b.pad_y[i], int(b.side[i]))
        xs, ys = b.pad_x[t].astype(np.float64), b.pad_y[t].astype(np.float64)
        oth = b.side[t] == self.other_side()
        if oth.any():
            xs[oth], ys[oth] = self.scene_xy(xs[oth], ys[oth], self.other_side())
        out = np.empty((len(t), 4))
        out[:, 0], out[:, 1], out[:, 2], out[:, 3] = float(x0), float(y0), xs, ys
        return out

    def _pick(self, sx, sy):
        """Ponto da placa e lado sob o cursor (na vista dupla, decide qual das duas cópias)."""
        R0, self._copy_R = self._copy_R, None
        try:
            px, py = self.to_board(sx, sy)
        finally:
            self._copy_R = R0
        if self.dual_view and self.board is not None:
            R = self.mirror_R()
            L = R[:2, :2]
            d = np.linalg.eigh(np.eye(2) - L)[1][:, -1]      # normal da linha do espelho
            m = (R[:2, 2]) / 2                                # um ponto da linha
            # o lado da linha onde fica a cópia é o oposto do centro da placa
            x0, y0, x1, y1 = self.board.bounds
            c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            pt = np.array([px, py])
            if np.sign((pt - m) @ d) != np.sign((c - m) @ d):
                q = L @ pt + R[:2, 2]
                return float(q[0]), float(q[1]), self.other_side()
        return px, py, self.side

    def set_dual(self, on):
        if self.board is None:
            self.dual_view = bool(on)
            self.lock_side = bool(on)
            return
        self.dual_view = bool(on)
        self.lock_side = bool(on)
        self.fit(animate=True)
        self.request_frame()
        self.viewChanged.emit()

    def _finish_anchor(self):
        if self._anchor is not None:
            sx_, sy_, bx, by = self._anchor
            A = self._A(self.t_scale)
            v = np.linalg.solve(A, np.array([sx_ - self.width() / 2, sy_ - self.height() / 2]))
            self.t_cx, self.t_cy = bx - v[0], by - v[1]

    def animating(self):
        if abs(math.log(max(self.scale, 1e-12) / max(self.t_scale, 1e-12))) > 1.5e-3:
            return True
        tol = 0.25 / max(self.scale, 1e-12)
        return abs(self.cx - self.t_cx) > tol or abs(self.cy - self.t_cy) > tol

    def tick(self):
        """Avança a animação. Retorna True se ainda precisa de mais quadros."""
        now = time.perf_counter()
        dt = 1 / 60 if self._last_tick is None else min(0.1, now - self._last_tick)
        self._last_tick = now
        if not self.animating():
            self._snap()
            return False
        return self._tick_step(dt)

    def _tick_step(self, dt):
        k = 1.0 - math.exp(-dt * self.opts.smooth_speed)
        ls = math.log(self.scale)
        lt = math.log(self.t_scale)
        self.scale = math.exp(ls + (lt - ls) * k)
        if self._anchor is not None:
            sx_, sy_, bx, by = self._anchor
            A = self._A()
            v = np.linalg.solve(A, np.array([sx_ - self.width() / 2, sy_ - self.height() / 2]))
            self.cx, self.cy = bx - v[0], by - v[1]
        else:
            self.cx += (self.t_cx - self.cx) * k
            self.cy += (self.t_cy - self.cy) * k
        if not self.animating():
            self._snap()
            return False
        return True

    # ------------------------------------------------------------------ comandos de câmera
    def fit(self, rect=None, margin=0.06, animate=True):
        if self.board is None:
            return
        x0, y0, x1, y1 = self.scene_bounds() if rect is None else rect
        w = max(x1 - x0, 1e-6)
        h = max(y1 - y0, 1e-6)
        if self.rotation % 2:
            w, h = h, w
        W = max(self.width(), 10) * (1 - 2 * margin)
        H = max(self.height(), 10) * (1 - 2 * margin)
        self.t_scale = min(W / w, H / h)
        self.t_cx = (x0 + x1) / 2
        self.t_cy = (y0 + y1) / 2
        self._anchor = None
        if not animate:
            self._snap()
        self._start_anim()

    def zoom_at(self, factor, sx=None, sy=None):
        if self.board is None:
            return
        if sx is None:
            sx, sy = self.width() / 2, self.height() / 2
        # ponto da placa sob o cursor no estado atual (o que o usuário está vendo)
        bx, by = self.to_board(sx, sy)
        self.t_scale = max(1e-6, min(self.t_scale * factor, 1e6))
        self._anchor = (sx, sy, bx, by)
        self._finish_anchor()
        self._start_anim()

    def pan_pixels(self, dx, dy, animate=True):
        """Move a vista dx,dy pixels (positivo = conteúdo vai para a direita/baixo)."""
        A = self._A(self.t_scale)
        v = np.linalg.solve(A, np.array([dx, dy]))
        self._finish_anchor()
        self._anchor = None
        self.t_cx -= v[0]
        self.t_cy -= v[1]
        if not animate:
            self._snap()
        self._start_anim()

    def drag_pixels(self, dx, dy):
        """Arraste com o mouse: imediato (sem animação)."""
        self._finish_anchor()
        self._snap()
        A = self._A()
        v = np.linalg.solve(A, np.array([dx, dy]))
        self.cx -= v[0]
        self.cy -= v[1]
        self.t_cx, self.t_cy = self.cx, self.cy
        self.request_frame()
        self.viewChanged.emit()

    def _keep_center(self, fn):
        """Executa uma troca de orientação mantendo o centro da tela."""
        self._finish_anchor()
        self._snap()
        fn()
        self.request_frame()
        self.viewChanged.emit()

    def flip_side(self, axis=None):
        if self.board is None:
            return

        def f():
            if axis:
                self.opts.flip_axis = axis
            self.side = BOTTOM if self.side == TOP else TOP
        self._keep_center(f)

    def set_side(self, side):
        if side in (TOP, BOTTOM) and side != self.side and not self.lock_side:
            self.flip_side()

    def rotate(self, d=1):
        self._keep_center(lambda: setattr(self, "rotation", (self.rotation + d) % 4))
        if self.dual_view:
            self.fit(animate=False)   # a cópia pode ter passado de cima para o lado: reenquadra na hora

    def mirror(self, horizontal=True):
        if horizontal:
            self._keep_center(lambda: setattr(self, "mirror_h", not self.mirror_h))
        else:
            self._keep_center(lambda: setattr(self, "mirror_v", not self.mirror_v))

    def zoom_to_selection(self):
        b = self.board
        if b is None:
            return
        if self.sel_pin is not None:
            self._zoom_pin(self.sel_pin)
        elif self.sel_part is not None:
            self._zoom_part(self.sel_part)
        elif self.sel_net is not None:
            self._zoom_net(self.sel_net)
        else:
            self.fit()

    def _zoom_pin(self, i):
        b = self.board
        r = float(b.r[i]) * 30
        x, y = self.scene_xy(b.x[i], b.y[i], int(b.side[i]))
        self.fit((float(x) - r, float(y) - r, float(x) + r, float(y) + r), margin=0.02)

    def _zoom_part(self, pi):
        part = self.board.parts[pi]
        xs = [p[0] for p in part.outline] or [part.center[0]]
        ys = [p[1] for p in part.outline] or [part.center[1]]
        pad = max(part.size, 1) * 2.5
        cx, cy = self.scene_xy(sum(xs) / len(xs), sum(ys) / len(ys), part.side)
        cx, cy = float(cx), float(cy)
        self.fit((cx - pad, cy - pad, cx + pad, cy + pad), margin=0.02)

    def _zoom_net(self, ni):
        b = self.board
        if self.dual_view:
            here = list(b.net_pins[ni])
        else:
            here = [i for i in b.net_pins[ni] if b.side[i] in (self.side, BOTH)]
        if here and not b.net_is_gnd[ni] and len(here) < 3000:
            xs, ys = b.x[here].copy(), b.y[here].copy()
            if self.dual_view:
                oth = b.side[here] == self.other_side()
                if oth.any():
                    xs[oth], ys[oth] = self.scene_xy(xs[oth], ys[oth], self.other_side())
            pad = float(np.median(b.r[here])) * 8
            self.fit((float(xs.min()) - pad, float(ys.min()) - pad, float(xs.max()) + pad, float(ys.max()) + pad),
                     margin=0.05)

    # ------------------------------------------------------------------ seleção
    def _sel_changed(self):
        self.selection_updated()
        self.request_frame()
        self.selectionChanged.emit()

    def clear_selection(self):
        self.sel_part = self.sel_pin = self.sel_net = None
        self._sel_changed()

    def select_part(self, pi, zoom=True):
        part = self.board.parts[pi]
        self.sel_part = int(pi)
        self.sel_pin = None
        self.sel_net = None
        if part.side in (TOP, BOTTOM) and part.side != self.side and not self.lock_side:
            self.flip_side()
        if zoom:
            self._zoom_part(pi)
        self._sel_changed()

    def select_net(self, ni, zoom=False, keep_pin=None):
        b = self.board
        self.sel_net = int(ni)
        self.sel_pin = keep_pin
        pins = b.net_pins[ni]
        if pins:
            here = [i for i in pins if b.side[i] in (self.side, BOTH)]
            if not here and not self.lock_side:
                self.flip_side()
        if zoom:
            self._zoom_net(ni)
        self._sel_changed()

    def goto_net(self, ni):
        """Busca de rede: vai até um pino da rede e seleciona ele como se tivesse sido clicado (com as linhas
        para os outros pinos), sem afastar a câmera. Pega o pino mais perto do que está na tela, de preferência
        num componente do lado atual. Buscar de novo a mesma rede vai para o próximo pino (do mais perto ao
        mais longe)."""
        b = self.board
        if b is None:
            return
        ni = int(ni)
        pins = list(b.net_pins[ni])
        if not pins:
            self.select_net(ni)
            return
        cyc = self._net_cycle
        if cyc is not None and cyc[0] == ni and self.sel_net == ni and self.sel_pin == cyc[1][cyc[2]]:
            k = (cyc[2] + 1) % len(cyc[1])
            self._net_cycle = (ni, cyc[1], k)
            self._goto_pin_keep_zoom(cyc[1][k])
            return
        P = np.asarray(pins, dtype=np.int64)
        here = (b.side[P] == self.side) | (b.side[P] == BOTH)
        if self.dual_view:
            here[:] = True
        comp = b.part[P] >= 0
        if comp.any():
            mech = np.array([b.parts[int(pi)].is_mech if pi >= 0 else False for pi in b.part[P]], dtype=bool)
            comp &= ~mech
        # distância até o centro da tela (no lado de cada pino)
        sx, sy = b.pad_x[P].astype(np.float64), b.pad_y[P].astype(np.float64)
        if self.dual_view:
            oth = b.side[P] == self.other_side()
            if oth.any():
                sx[oth], sy[oth] = self.scene_xy(sx[oth], sy[oth], self.other_side())
        d = np.hypot(sx - self.t_cx, sy - self.t_cy)
        # ordem: neste lado e em componente > neste lado > componente no outro lado > resto; depois a distância
        rank = np.where(here & comp, 0, np.where(here, 1, np.where(comp, 2, 3)))
        order = np.lexsort((d, rank))
        lst = [int(P[k]) for k in order]
        self._net_cycle = (ni, lst, 0)
        self._goto_pin_keep_zoom(lst[0])

    def _goto_pin_keep_zoom(self, i):
        """Seleciona o pino (como um clique) e leva a câmera até ele sem afastar; só aproxima se o pad
        estiver pequeno demais para ver."""
        b = self.board
        self.select_pin(i, zoom=False)
        side = int(b.side[i]) if int(b.side[i]) in (TOP, BOTTOM) else self.side
        x, y = self.scene_xy(b.pad_x[i], b.pad_y[i], side)
        self._finish_anchor()
        self._anchor = None
        pad = max(float(b.pad_hw[i]), float(b.pad_hh[i]), 1e-6) * self.opts.pin_scale
        need = 9.0 / pad                         # pad com pelo menos ~18 px na tela
        if self.t_scale < need:
            self.t_scale = need
        self.t_cx, self.t_cy = float(x), float(y)
        self._start_anim()
        self.request_frame()

    def select_pin(self, i, zoom=False):
        b = self.board
        i = int(i)
        self.sel_pin = i
        self.sel_net = int(b.net[i])
        pi = int(b.part[i])
        self.sel_part = pi if pi >= 0 else None
        if int(b.side[i]) in (TOP, BOTTOM) and int(b.side[i]) != self.side and not self.lock_side:
            self.flip_side()
        if zoom:
            self._zoom_pin(i)
        self._sel_changed()

    def highlight_pin(self, i):
        """Destaca um pino (e sua rede) sem trocar o painel de informações."""
        self.sel_pin = int(i)
        self.sel_net = int(self.board.net[i])
        self.selection_updated()
        self.request_frame()

    def hit_pin(self, sx, sy):
        b = self.board
        if b is None or len(b.x) == 0:
            return None
        px, py, side = self._pick(sx, sy)
        vis = (b.side == side) | (b.side == BOTH)
        if not self.opts.show_mech:
            vis = vis & ~b.pin_mech_dead           # furos soltos de peça mecânica escondida
        dx = px - b.pad_x
        dy = py - b.pad_y
        lx = np.abs(dx * b.pad_ux + dy * b.pad_uy)
        ly = np.abs(-dx * b.pad_uy + dy * b.pad_ux)
        g = self.opts.pin_scale
        hw = np.maximum(b.pad_hw * g, 2.5 / self.scale)
        hh = np.maximum(b.pad_hh * g, 2.5 / self.scale)
        tol = 1.0 / self.scale
        ok = vis & (lx <= hw + tol) & (ly <= hh + tol)
        if not ok.any():
            return None
        cand = np.nonzero(ok)[0]
        rnd = (b.pad_round[cand] > 0.5) & (np.abs(b.pad_hw[cand] - b.pad_hh[cand]) < 1e-6 * (b.pad_hw[cand] + 1))
        d = np.where(rnd, np.hypot(lx[cand], ly[cand]) / hw[cand],
                     np.maximum(lx[cand] / hw[cand], ly[cand] / hh[cand]))
        keep = ~rnd | (d <= 1.0 + tol / hw[cand])
        if not keep.any():
            return None
        cand, d = cand[keep], d[keep]
        return int(cand[np.argmin(d)])

    def hit_part(self, sx, sy):
        b = self.board
        if b is None or not b.parts:
            return None
        px, py, side = self._pick(sx, sy)
        side_ok = (self.pside == side) | (self.pside == BOTH)
        near = side_ok & (np.abs(self.pcx - px) <= self.psize) & (np.abs(self.pcy - py) <= self.psize)
        best = None
        hide_mech = not self.opts.show_mech
        for i in np.nonzero(near)[0]:
            if hide_mech and b.parts[int(i)].is_mech:
                continue                    # escondida: clicar dentro do contorno dela não seleciona
            if _point_in_poly(px, py, b.parts[int(i)].outline):
                if best is None or self.psize[i] < self.psize[best]:
                    best = int(i)
        return best

    # ------------------------------------------------------------------ eventos (chamados pelos widgets)
    @staticmethod
    def _button_name(btn):
        from PySide6.QtCore import Qt
        return {Qt.LeftButton: "Left Mouse Button", Qt.MiddleButton: "Middle Mouse Button",
                Qt.RightButton: "Right Mouse Button", Qt.BackButton: "Mouse Button 4",
                Qt.ForwardButton: "Mouse Button 5"}.get(btn)

    def core_wheel(self, e):
        d = e.angleDelta().y()
        if d == 0:
            d = e.pixelDelta().y() * 2
        if d == 0:
            return
        f = 1.0015 ** (d * self.opts.wheel_step)
        p = e.position()
        self.zoom_at(f, p.x(), p.y())
        if self._card is not None:
            self._card.hide_card()

    def core_press(self, e):
        self.setFocus()
        if self._card is not None:
            self._card.hide_card()
        p = e.position()
        self._press = (p.x(), p.y(), e.button())
        self._last_pos = (p.x(), p.y())
        self._dragging = False

    def core_move(self, e):
        from PySide6.QtCore import Qt
        p = e.position()
        x, y = p.x(), p.y()
        if self.board is not None:
            bx, by, _side = self._pick(x, y)
            self.cursorMoved.emit(bx, by)
        if self._press is not None and e.buttons():
            if not self._dragging and abs(x - self._press[0]) + abs(y - self._press[1]) > 4:
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
            if self._dragging:
                self.drag_pixels(x - self._last_pos[0], y - self._last_pos[1])
                self._last_pos = (x, y)
            return
        self._update_hover(x, y)

    # ------------------------------------------------------------------ caixa de informações (hover)
    def _hover_target(self, x, y):
        i = self.hit_pin(x, y)
        if i is not None:
            pi = int(self.board.part[i])
            return (pi if pi >= 0 else None), i
        return self.hit_part(x, y), None

    def _update_hover(self, x, y):
        self._mouse_xy = (x, y)
        if self.board is None:
            return
        pi, pin = self._hover_target(x, y)
        if (pi, pin) != (self.hover_part, self.hover_pin):
            changed_part = pi != self.hover_part
            self.hover_part, self.hover_pin = pi, pin
            if changed_part:
                self.hover_updated()
                self.request_frame()
        if not self.opts.hover_info or (pi is None and pin is None):
            if self._card is not None:
                self._card.hide_card()
            return
        if self._card is None:
            from .hovercard import HoverCard
            self._card = HoverCard(self)
        from . import info
        b = self.board
        if pi is not None:
            ptype = self.ptype[pi]
            note = self.note_lookup(pi) if self.note_lookup else None
            fn = lambda: info.part_card_html(b, pi, pin, ptype, note=note)
        else:
            fn = lambda: info.pin_card_html(b, pin)
        self._card.show_for((pi, pin), fn, x, y)

    def core_leave(self):
        self._mouse_xy = None
        if self._card is not None:
            self._card.hide_card()
        if self.hover_part is not None or self.hover_pin is not None:
            self.hover_part = self.hover_pin = None
            self.hover_updated()
            self.request_frame()

    def core_release(self, e):
        from PySide6.QtCore import Qt
        was_drag = self._dragging
        self._dragging = False
        press = self._press
        self._press = None
        self.unsetCursor()
        if was_drag or self.board is None or press is None:
            return
        name = self._button_name(e.button())
        if name in self.mouse_bindings and name != "Left Mouse Button":
            self.mouse_bindings[name]()
            return
        if e.button() == Qt.RightButton:
            p = e.position()
            i = self.hit_pin(p.x(), p.y())
            pi = int(self.board.part[i]) if i is not None else self.hit_part(p.x(), p.y())
            if pi is not None and pi < 0:
                pi = None
            if self._card is not None:
                self._card.hide_card()
            self.contextRequested.emit(-1 if pi is None else int(pi), -1 if i is None else int(i),
                                       e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        p = e.position()
        i = self.hit_pin(p.x(), p.y())
        if i is not None:
            self.select_pin(i)
            return
        ni = self.hit_trace(p.x(), p.y())
        if ni is not None:
            self.sel_part = self.sel_pin = None
            self.select_net(ni)
            return
        pi = self.hit_part(p.x(), p.y())
        if pi is not None:
            self.select_part(pi, zoom=False)
            return
        self.clear_selection()

    def core_double(self, e):
        if self.board is None:
            return
        p = e.position()
        i = self.hit_pin(p.x(), p.y())
        if i is not None:
            self.select_net(int(self.board.net[i]), zoom=False, keep_pin=i)   # sem mexer na câmera
            pi = int(self.board.part[i])
            # no esquema: vai até o componente do pino (o nome da rede é procurado se não houver componente)
            if pi >= 0:     # pino de componente: o esquema vai até o pino (nº + rede), não só no componente
                prt = self.board.parts[pi]
                self.itemDoubleClicked.emit("pin", "\t".join((prt.name, str(self.board.num[i]),
                                                                self.board.nets[self.board.net[i]], str(len(prt.pins)))))
            else:
                self.itemDoubleClicked.emit("net", self.board.nets[self.board.net[i]])
            return
        pi = self.hit_part(p.x(), p.y())
        if pi is not None:
            self.select_part(pi, zoom=False)                 # sem mexer na câmera
            self.itemDoubleClicked.emit("part", self.board.parts[pi].name)

    # ------------------------------------------------------------------ ganchos (renderizador)
    def board_changed(self):
        pass

    def selection_updated(self):
        pass

    def hover_updated(self):
        pass

    def theme_changed(self):
        self.request_frame()


def _point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xin = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < xin:
                inside = not inside
        j = i
    return inside


def rgba(c, default=(255, 0, 255, 255)):
    return c if c is not None else default
