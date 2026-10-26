"""Modelo da placa (pronto para desenhar), montado a partir do RawBoard dos leitores."""
from __future__ import annotations

import math
import re

import numpy as np

from .parsers import RawBoard, BOTH, TOP, BOTTOM

GROUND_NAMES = {"GND", "GROUND", "VSS", "PGND", "AGND", "DGND", "GND_", "SGND"}


def is_ground(name: str) -> bool:
    u = name.upper()
    if u in GROUND_NAMES:
        return True
    return u.startswith("GND") and (len(u) == 3 or not u[3].isalpha())


class Part:
    __slots__ = ("name", "side", "smd", "mfg", "pins", "outline", "center", "size", "index", "footprint",
                 "body", "axis", "dims", "pitch", "is_mech", "label_w")

    def __init__(self, name, side, smd, mfg, index):
        self.footprint = ""
        self.body: list[tuple[float, float]] = []   # área do corpo (entre os pads) — 2 pinos
        self.axis = (1.0, 0.0)                       # direção do lado maior (para o nome)
        self.dims = (0.0, 0.0)                       # (comprimento, largura) do contorno
        self.pitch = 0.0                             # menor distância entre pinos
        self.is_mech = False                         # reforço, furo de fixação, blindagem...
        self.label_w = 0.0                           # largura usada para o tamanho do nome
        self.name = name
        self.side = side
        self.smd = smd
        self.mfg = mfg
        self.pins: list[int] = []
        self.outline: list[tuple[float, float]] = []
        self.center = (0.0, 0.0)
        self.size = 0.0
        self.index = index


_MECH_NAME = re.compile(r"^(MEC|MECH|MH|MT|HOLE|SCREW|STF|STIFF|BKT|BRKT|BRACKET|SHIELD|SHD|NUT|STANDOFF|"
                        r"FIX|HS|HEATSINK|SPRING|BACKPLATE)[_-]?\d", re.I)
_MECH_DESC = re.compile(r"stiffener|bracket|screw|standoff|heat\s*sink|heatsink|shield|back\s*plate|"
                        r"mount(ing)?[_\s-]*hole|^mech|[_\s/]mech[_\s/]|^MH[-_]|spring|thermal\s*pad", re.I)


_CONN_NAME = re.compile(r"^(J|CN|CON|CONN|P|JP|HDR|USB|HDMI|DP|DVI|VGA)[_-]?\d", re.I)
_CONN_DESC = re.compile(r"conn|connector|header|socket|receptacle|\bhdr", re.I)


def is_mechanical(name, mfg):
    """Peça mecânica (reforço, furo de fixação, blindagem, dissipador...), pelo nome ou pela descrição."""
    if _MECH_NAME.match(name or ""):
        return True
    if not _MECH_DESC.search(mfg or ""):
        return False
    # conector com parafuso na descrição ("DDC CONN ... WO/SCREW") continua sendo conector
    return not (_CONN_NAME.match(name or "") or _CONN_DESC.search(mfg or ""))


def _min_dist2_f32(xs, ys):
    """Menor distância² entre dois pontos diferentes (contas em float32, iguais às de antes), sem comparar
    todos com todos: ordena por x, pega um limite pelos vizinhos e só compara quem está perto em x."""
    n = len(xs)
    o = np.argsort(xs, kind="stable")
    x, y = xs[o], ys[o]
    best = np.inf
    for k in (1, 2, 3):
        if k < n:
            dx = x[k:] - x[:-k]
            dy = y[k:] - y[:-k]
            d = dx * dx + dy * dy
            d = d[d > 0]
            if d.size:
                best = min(best, float(d.min()))
    if not math.isfinite(best):
        # todos no mesmo lugar dos vizinhos (raro): compara todos com todos
        for s in range(0, n, 512):
            dx = xs[s:s + 512, None] - xs[None, :]
            dy = ys[s:s + 512, None] - ys[None, :]
            d = dx * dx + dy * dy
            d[d == 0] = np.inf
            best = min(best, float(d.min()))
        return best
    lim = math.sqrt(best) * 1.001 + 1e-12
    hi = np.searchsorted(x.astype(np.float64), x.astype(np.float64) + lim, side="right")
    K = int((hi - np.arange(n)).max())
    for k in range(4, min(K, n - 1) + 1):
        dx = x[k:] - x[:-k]
        dy = y[k:] - y[:-k]
        d = dx * dx + dy * dy
        d = d[d > 0]
        if d.size:
            best = min(best, float(d.min()))
    return best


class Layer:
    """Camada de cobre pronta para desenhar."""

    def __init__(self, name):
        self.name = name
        self.seg = np.zeros((0, 5))                    # x1, y1, x2, y2, largura
        self.seg_net = np.zeros(0, dtype=np.int32)
        self.pad = np.zeros((0, 5))                    # x, y, meia-larg, meia-alt, arredondamento
        self.pad_net = np.zeros(0, dtype=np.int32)
        self.polys: list[tuple[int, list[np.ndarray]]] = []   # (rede, [contorno, furos...])

    @property
    def side(self):
        u = self.name.upper()
        return 1 if u in ("TOP", "SOLDERTOP") else 2 if u in ("BOTTOM", "SOLDERBOTTOM") else 0


class Board:
    def __init__(self, raw: RawBoard, path: str = ""):
        self.path = path
        self.format_name = raw.format_name
        self._build(raw)

    # ------------------------------------------------------------------ montagem
    def _build(self, raw: RawBoard):
        rparts = raw.parts
        rpins = raw.pins
        n = len(rpins)

        self.parts: list[Part] = []
        part_map: dict[int, int] = {}   # índice 1-based bruto -> índice em self.parts (ou -1 p/ dummy)
        for i, rp in enumerate(rparts):
            if rp.name.startswith("..."):
                part_map[i + 1] = -1
                continue
            part_map[i + 1] = len(self.parts)
            p = Part(rp.name, rp.side, rp.smd, rp.mfg, len(self.parts))
            p.footprint = getattr(rp, "footprint", "") or ""
            p.outline = []
            if not any(rp.p1) and not any(rp.p2):
                p.center = None
            else:
                p.center = ((rp.p1[0] + rp.p2[0]) / 2, (rp.p1[1] + rp.p2[1]) / 2)
                p.outline = [(rp.p1[0], rp.p1[1]), (rp.p2[0], rp.p1[1]), (rp.p2[0], rp.p2[1]), (rp.p1[0], rp.p2[1])]
            self.parts.append(p)

        self.num: list[str] = [""] * n
        self.pname: list[str] = [""] * n

        net_index: dict[str, int] = {}
        self.nets: list[str] = []
        self.net_pins: list[list[int]] = []

        def get_net(name: str) -> int:
            if not name or name.upper().startswith("UNCONNECTED"):
                name = "UNCONNECTED"
            i = net_index.get(name)
            if i is None:
                i = len(self.nets)
                net_index[name] = i
                self.nets.append(name)
                self.net_pins.append([])
            return i

        get_net("UNCONNECTED")
        # listas comuns e um numpy.array no fim (bem mais rápido que escrever no array pino a pino)
        xl, yl, sl, prl, rl, pl, nl = [], [], [], [], [], [], []
        fpad_i, fpad_v = [], []
        num, pname, net_pins, parts = self.num, self.pname, self.net_pins, self.parts
        net_cache: dict[str, int] = {}
        pin_idx = 0
        part_idx = 1
        for i, rp in enumerate(rpins):
            xl.append(rp.x)
            yl.append(rp.y)
            sl.append(rp.side)
            prl.append(rp.probe)
            rl.append(rp.radius)
            if rp.pad is not None:
                fpad_i.append(i)
                fpad_v.append(rp.pad)
            pi = part_map.get(rp.part, -1)
            pl.append(pi)
            pin_idx += 1
            if rp.part != part_idx:
                part_idx = rp.part
                pin_idx = 1
            nm = rp.snum if rp.snum else str(pin_idx)
            num[i] = nm
            pname[i] = rp.name if rp.name else nm
            ni = net_cache.get(rp.net)
            if ni is None:
                ni = net_cache[rp.net] = get_net(rp.net)
            nl.append(ni)
            net_pins[ni].append(i)
            if pi >= 0:
                parts[pi].pins.append(i)
        self.x = np.array(xl, dtype=np.float64).reshape(n)
        self.y = np.array(yl, dtype=np.float64).reshape(n)
        self.r = np.zeros(n)
        self.side = np.array(sl, dtype=np.int8).reshape(n)
        self.part = np.array(pl, dtype=np.int32).reshape(n)
        self.net = np.array(nl, dtype=np.int32).reshape(n)
        self.probe = np.array(prl, dtype=np.int32).reshape(n)
        file_r = np.array(rl, dtype=np.float64).reshape(n)
        self.file_pad = np.zeros((n, 7))
        self.has_file_pad = np.zeros(n, dtype=bool)
        if fpad_i:
            self.file_pad[fpad_i] = np.array(fpad_v, dtype=np.float64)
            self.has_file_pad[fpad_i] = True

        self.net_index = net_index
        self.part_index = {}
        for p in self.parts:
            self.part_index.setdefault(p.name.upper(), p.index)
        self.net_is_gnd = np.array([is_ground(nm) for nm in self.nets], dtype=bool)
        self.net_is_nc = np.array([nm == "UNCONNECTED" for nm in self.nets], dtype=bool)
        # pino "solto": sem rede, rede com nome de NC, ou rede que só tem esse pino (não liga em nada)
        nc_name = re.compile(r"^(N/?C\d*|NC[_\-].*|.*[_\-]NC\d*|UNNAMED.*|N\$\d+)$", re.I)
        net_single = np.array([len(p) <= 1 for p in self.net_pins], dtype=bool)
        net_loose = self.net_is_nc | net_single | np.array([bool(nc_name.match(nm)) for nm in self.nets], dtype=bool)
        self.pin_loose = net_loose[self.net] if n else np.zeros(0, dtype=bool)
        self.part_no_net = [bool(p.pins) and bool(self.pin_loose[p.pins].all()) for p in self.parts]

        self._build_layers(raw)
        self._compute_radii(file_r)
        self._compute_pads()
        self._compute_outlines()
        self._compute_label_sizes()
        self._compute_board_outline(raw)
        self._compute_bounds()

    def _build_layers(self, raw: RawBoard):
        """Camadas de cobre (TVW, GenCAD): trilhas, áreas de cobre e vias, com a rede de cada item."""
        self.layers: list[Layer] = []
        rl = getattr(raw, "layers", None) or []
        if not rl:
            return
        lut = np.array([self.net_index.get(n if n and not n.upper().startswith("UNCONNECTED") else "UNCONNECTED", -1)
                        for n in raw.layer_nets] + [-1], dtype=np.int32)

        def map_net(v):
            v = np.asarray(v, dtype=np.int64)
            v = np.where((v >= 0) & (v < len(lut) - 1), v, len(lut) - 1)
            return lut[v]
        for L in rl:
            lay = Layer(L.name)
            if L.segs:
                seg = np.concatenate(L.segs)
                lay.seg = seg[:, :5].copy()
                lay.seg_net = map_net(seg[:, 5])
            if L.pads:
                pd = np.concatenate(L.pads)
                lay.pad = pd[:, :5].copy()
                lay.pad_net = map_net(pd[:, 5])
            for net, outer, holes in L.polys:
                lay.polys.append((int(map_net([net])[0]), [np.asarray(outer, dtype=np.float64)] +
                                  [np.asarray(h, dtype=np.float64) for h in holes]))
            self.layers.append(lay)

    def _compute_radii(self, file_r: np.ndarray):
        n = len(self.x)
        if n == 0:
            return
        r = np.full(n, np.nan)
        mins = []
        # menor distância entre pinos de cada componente. Componentes pequenos vão em lote (mesmo nº de pinos
        # juntos, numpy de uma vez); os grandes (BGA) em blocos de 512 linhas.
        groups: dict[int, list] = {}
        big = []
        for part in self.parts:
            k = len(part.pins)
            if k < 2:
                continue
            (groups.setdefault(k, []) if k <= 48 else big).append(part)
        for k, plist in groups.items():
            ar = np.arange(k)
            step = max(1, 2_000_000 // (k * k))
            for c0 in range(0, len(plist), step):
                chunk = plist[c0:c0 + step]
                idx = np.array([p.pins for p in chunk], dtype=np.int64)
                xs = self.x[idx].astype(np.float32)
                ys = self.y[idx].astype(np.float32)
                dx = xs[:, :, None] - xs[:, None, :]
                dy = ys[:, :, None] - ys[:, None, :]
                d = dx * dx + dy * dy
                d[:, ar, ar] = np.inf
                d[d == 0] = np.inf
                dm = d.reshape(len(chunk), -1).min(axis=1)
                for part, m, row in zip(chunk, dm.tolist(), idx):
                    if not math.isfinite(m):
                        continue
                    m = math.sqrt(m)
                    part.pitch = m
                    mins.append(m)
                    r[row] = 0.42 * m
        for part in big:
            idx = part.pins
            dmin = _min_dist2_f32(self.x[idx].astype(np.float32), self.y[idx].astype(np.float32))
            if not np.isfinite(dmin):
                continue
            dmin = math.sqrt(dmin)
            part.pitch = dmin
            mins.append(dmin)
            r[idx] = 0.42 * dmin
        if mins:
            med = float(np.median(mins)) * 0.42
        else:
            # sem componentes multi-pino: usa ~0,3% do tamanho da placa
            span = max(np.ptp(self.x), np.ptp(self.y), 1.0)
            med = span * 0.003
        cap = max(med * 4.0, 1e-6)
        r = np.where(np.isnan(r), med, np.minimum(r, cap))
        # raio informado pelo próprio arquivo (FZ, BVR3), quando existir
        has = file_r > 0.5001
        if has.any():
            r = np.where(has, np.minimum(file_r, np.maximum(r, file_r * 0.25)), r)
        self.r = np.maximum(r, 1e-6)

    def _compute_pads(self):
        """Pads de cada pino: centro, meia-largura/altura, eixo e arredondamento (0 = retângulo, 1 = redondo/oval).

        Quando o arquivo traz a geometria dos pads (GenCAD, TVW) ela é usada como está. Senão é estimada:
        2 pinos -> um pad em cada ponta e o corpo no meio; vários pinos nas bordas (SOIC, QFP, QFN, SOT) ->
        pads alongados; BGA, furo passante e pontos de teste -> redondos."""
        n = len(self.x)
        self.pad_x = self.x.astype(np.float64).copy()
        self.pad_y = self.y.astype(np.float64).copy()
        self.pad_hw = self.r.astype(np.float64).copy()
        self.pad_hh = self.r.astype(np.float64).copy()
        self.pad_ux = np.ones(n)
        self.pad_uy = np.zeros(n)
        self.pad_round = np.ones(n)
        # 2 pinos (a maioria): todos de uma vez
        two = [p for p in self.parts if len(p.pins) == 2]
        # peça mecânica de 2 "pinos" (dissipador, blindagem: os 2 furos de fixação, às vezes a 10 cm um do
        # outro) não é um componente de 2 pinos: com a estimativa de resistor os pads ficavam gigantes.
        # Os furos viram pads redondos pequenos e o contorno fica em volta deles (como nas outras mecânicas).
        if two:
            T = np.array([p.pins for p in two], dtype=np.int64)
            ln2 = np.hypot(self.x[T[:, 0]] - self.x[T[:, 1]], self.y[T[:, 0]] - self.y[T[:, 1]])
            pos = ln2[ln2 > 0]
            typ = float(np.median(pos)) if pos.size else 0.0
            dead = self.net_is_nc[self.net[T]].all(axis=1) if len(self.nets) else np.ones(len(two), bool)
            spread = [is_mechanical(p.name, p.mfg) or (bool(dead[j]) and typ > 0 and ln2[j] > 8 * typ)
                      for j, p in enumerate(two)]
            if any(spread):
                hole_r = 0.45 * typ if typ > 0 else None
                for j, p in enumerate(two):
                    if spread[j] and hole_r:
                        self.pad_hw[p.pins] = np.maximum(self.r[p.pins], hole_r)
                        self.pad_hh[p.pins] = self.pad_hw[p.pins]
                two = [p for j, p in enumerate(two) if not spread[j]]
        I2 = np.array([p.pins for p in two], dtype=np.int64).reshape(-1, 2)
        if len(I2):
            i0, i1 = I2[:, 0], I2[:, 1]
            x0, y0, x1, y1 = self.x[i0], self.y[i0], self.x[i1], self.y[i1]
            ok = (x0 != x1) | (y0 != y1)
            i0, i1, x0, y0, x1, y1 = i0[ok], i1[ok], x0[ok], y0[ok], x1[ok], y1[ok]
            ax, ay = x1 - x0, y1 - y0
            ln = np.hypot(ax, ay)
            ux, uy = ax / ln, ay / ln
            ext = 0.25 * ln                         # quanto o componente passa do centro do pino
            L = ln + 2 * ext
            hwid = 0.3 * ln
            plen = 0.32 * L
            off = plen / 2 - ext
            self.pad_x[i0] = x0 + ux * off
            self.pad_y[i0] = y0 + uy * off
            self.pad_x[i1] = x1 - ux * off
            self.pad_y[i1] = y1 - uy * off
            for ii in (i0, i1):
                self.pad_hw[ii] = plen / 2
                self.pad_hh[ii] = hwid
                self.pad_ux[ii] = ux
                self.pad_uy[ii] = uy
                self.pad_round[ii] = 0.0
        for part in self.parts:
            idx = part.pins
            k = len(idx)
            if k < 3:
                continue
            xs = self.x[idx]
            ys = self.y[idx]
            rs = self.r[idx]
            arr = np.asarray(idx)
            xmin, xmax = xs.min(), xs.max()
            ymin, ymax = ys.min(), ys.max()
            tol = rs * 0.5
            onx = (xs - xmin <= tol) | (xmax - xs <= tol)
            ony = (ys - ymin <= tol) | (ymax - ys <= tol)
            interior = ~(onx | ony)
            grid = k >= 9 and interior.sum() > 0.25 * k
            if grid or not part.smd:
                continue                    # redondos
            cnt_x = int(onx.sum())
            cnt_y = int(ony.sum())
            along_x = onx & (~ony | (cnt_x >= cnt_y))     # pino na borda esquerda/direita
            along_y = ony & ~along_x
            self.pad_round[arr] = 0.0
            hw = rs.copy()
            hh = rs.copy()
            hw[along_x] = rs[along_x] * 1.45
            hh[along_x] = rs[along_x] * 0.78
            hw[along_y] = rs[along_y] * 0.78
            hh[along_y] = rs[along_y] * 1.45
            self.pad_hw[arr] = hw
            self.pad_hh[arr] = hh

        # geometria real do arquivo
        fp = self.has_file_pad
        if fp.any():
            P = self.file_pad
            self.pad_x[fp] = P[fp, 0]
            self.pad_y[fp] = P[fp, 1]
            self.pad_hw[fp] = P[fp, 2]
            self.pad_hh[fp] = P[fp, 3]
            self.pad_ux[fp] = P[fp, 4]
            self.pad_uy[fp] = P[fp, 5]
            self.pad_round[fp] = P[fp, 6]
            # quadrado "redondo" com lados diferentes vira oval; círculo continua círculo

        # corpo dos componentes de 2 pinos (entre os pads), todos de uma vez
        if len(I2):
            i0, i1 = I2[:, 0], I2[:, 1]
            px0, py0 = self.pad_x[i0], self.pad_y[i0]
            ax, ay = self.pad_x[i1] - px0, self.pad_y[i1] - py0
            ln = np.hypot(ax, ay)
            ok = ln > 0
            with np.errstate(invalid="ignore", divide="ignore"):
                ux, uy = ax / ln, ay / ln
            vx, vy = -uy, ux

            def ext(i, dx, dy):
                a1 = np.abs(self.pad_ux[i] * dx + self.pad_uy[i] * dy)
                a2 = np.abs(-self.pad_uy[i] * dx + self.pad_ux[i] * dy)
                return a1 * self.pad_hw[i] + a2 * self.pad_hh[i]
            e0u, e1u = ext(i0, ux, uy), ext(i1, ux, uy)
            hwid = np.maximum(ext(i0, vx, vy), ext(i1, vx, vy))
            outer0, inner0 = -e0u, e0u.copy()
            inner1, outer1 = ln - e1u, ln + e1u
            touch = inner1 - inner0 < 0.05 * ln     # pads se encostam: sem corpo visível
            mid = (inner0 + inner1) / 2
            inner0 = np.where(touch, mid, inner0)
            inner1 = np.where(touch, mid, inner1)

            def P(t, w):
                return (px0 + ux * t + vx * w).tolist(), (py0 + uy * t + vy * w).tolist()
            corners = [P(inner0, hwid), P(inner1, hwid), P(inner1, -hwid), P(inner0, -hwid),
                       P(outer0, hwid), P(outer1, hwid), P(outer1, -hwid), P(outer0, -hwid)]
            has_body = (inner1 > inner0).tolist()
            cx = ((outer0 + outer1) / 2 * ux + px0).tolist()
            cy = ((outer0 + outer1) / 2 * uy + py0).tolist()
            L_ = (outer1 - outer0).tolist()
            W_ = (2 * hwid).tolist()
            uxl, uyl, okl = ux.tolist(), uy.tolist(), ok.tolist()
            for j, part in enumerate(two):
                if not okl[j]:
                    continue
                c = [(cs[0][j], cs[1][j]) for cs in corners]
                part.body = c[0:4] if has_body[j] else []
                part.outline = c[4:8]
                part.axis = (uxl[j], uyl[j])
                part.dims = (L_[j], W_[j])
                part.size = L_[j]
                part.center = (cx[j], cy[j])

    def pad_extents(self, idx):
        """Meia-extensão (x, y) dos pads, já considerando a rotação."""
        ux = np.abs(self.pad_ux[idx])
        uy = np.abs(self.pad_uy[idx])
        hw = self.pad_hw[idx]
        hh = self.pad_hh[idx]
        return ux * hw + uy * hh, uy * hw + ux * hh

    def _compute_outlines(self):
        # mínimos/máximos por componente de uma vez (numpy reduceat) em vez de várias contas por componente
        withpins = [p for p in self.parts if p.pins]
        agg = {}
        if withpins:
            order = np.concatenate([np.asarray(p.pins, dtype=np.int64) for p in withpins])
            starts = np.cumsum([0] + [len(p.pins) for p in withpins[:-1]])
            ex, ey = self.pad_extents(order)
            px, py = self.pad_x[order], self.pad_y[order]
            sd = self.side[order]
            agg = dict(zip((p.index for p in withpins), zip(
                np.maximum.reduceat(self.r[order], starts).tolist(),
                np.minimum.reduceat(px - ex, starts).tolist(), np.maximum.reduceat(px + ex, starts).tolist(),
                np.minimum.reduceat(py - ey, starts).tolist(), np.maximum.reduceat(py + ey, starts).tolist(),
                np.minimum.reduceat(sd, starts).tolist(), np.maximum.reduceat(sd, starts).tolist())))
        for part in self.parts:
            idx = part.pins
            if not idx:
                if part.center is None:
                    part.center = (0.0, 0.0)
                xs = [p[0] for p in part.outline] or [0]
                ys = [p[1] for p in part.outline] or [0]
                part.size = max(max(xs) - min(xs), max(ys) - min(ys))
                part.dims = (part.size, min(max(xs) - min(xs), max(ys) - min(ys)))
                continue
            rmax, mnx, mxx, mny, mxy, smin, smax = agg[part.index]
            if len(idx) == 2 and part.dims[0] > 0:
                pass        # 2 pinos: contorno já calculado em _compute_pads (pads + corpo)
            else:
                m = rmax * 0.35
                x0, x1 = mnx - m, mxx + m
                y0, y1 = mny - m, mxy + m
                if len(idx) == 1:
                    rr = rmax * 1.35
                    x_, y_ = float(self.x[idx[0]]), float(self.y[idx[0]])
                    x0, x1, y0, y1 = x_ - rr, x_ + rr, y_ - rr, y_ + rr
                if not part.outline:
                    part.outline = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                ox = [p[0] for p in part.outline]
                oy = [p[1] for p in part.outline]
                w, h = max(ox) - min(ox), max(oy) - min(oy)
                part.size = max(w, h)
                part.axis = (1.0, 0.0) if w >= h else (0.0, 1.0)
                part.dims = (max(w, h), min(w, h))
            cx = sum(p[0] for p in part.outline) / len(part.outline)
            cy = sum(p[1] for p in part.outline) / len(part.outline)
            part.center = (cx, cy)
            # componente sem lado definido: herda dos pinos
            if part.side == BOTH and part.smd:
                if smin == smax == TOP:
                    part.side = TOP
                elif smin == smax == BOTTOM:
                    part.side = BOTTOM

    def _compute_label_sizes(self):
        """Largura usada para o tamanho do nome de cada componente (part.label_w; normalmente o lado menor).
        Peças mecânicas (reforço/stiffener, furos de fixação, blindagem, dissipador...) têm um contorno enorme e
        quase vazio: o nome delas ficava do tamanho do da GPU. Para elas o nome tem o tamanho do de um CI comum
        da própria placa."""
        ws = sorted(p.dims[1] for p in self.parts
                    if len(p.pins) >= 3 and p.dims[1] > 0 and not is_mechanical(p.name, p.mfg))
        ref = 1.2 * ws[len(ws) // 2] if ws else None      # ~ um CI pequeno típico desta placa
        for p in self.parts:
            p.is_mech = is_mechanical(p.name, p.mfg)
            w = p.dims[1] if p.dims else 0.0
            if p.is_mech and ref is not None:
                w = min(w, ref)
            p.label_w = w
        # pinos de peça mecânica sem ligação nenhuma (furos do dissipador, da blindagem...): somem junto com a
        # peça quando ela é escondida. Os de reforço ligados ao GND continuam (são bons pontos para medir).
        dead = np.zeros(len(self.x), dtype=bool)
        for p in self.parts:
            if p.is_mech and p.pins and bool(self.net_is_nc[self.net[p.pins]].all()):
                dead[p.pins] = True
        self.pin_mech_dead = dead

    def _compute_board_outline(self, raw: RawBoard):
        segs = [((a[0], a[1]), (b[0], b[1])) for a, b in raw.outline_segments]
        pts = raw.format
        if len(pts) >= 2:
            jump = True
            fp = pts[0]
            i = 0
            while i < len(pts) - 1:
                pa, pb = pts[i], pts[i + 1]
                if pa == pb:
                    i += 1
                    continue
                if not jump and fp == pb:
                    if i < len(pts) - 2:
                        segs.append((pa, pb))
                        fp = pts[i + 2]
                        jump = True
                        i += 2
                        continue
                else:
                    jump = False
                segs.append((pa, pb))
                i += 1
        self.outline_segs = segs

    def _compute_bounds(self):
        xs, ys = [], []
        if len(self.x):
            xs += [float(self.x.min()), float(self.x.max())]
            ys += [float(self.y.min()), float(self.y.max())]
        for a, b in self.outline_segs:
            xs += [a[0], b[0]]
            ys += [a[1], b[1]]
        for p in self.parts:
            for q in p.outline:
                xs.append(q[0]); ys.append(q[1])
        if not xs:
            xs, ys = [0, 1000], [0, 1000]
        self.bounds = (min(xs), min(ys), max(xs), max(ys))

    # ------------------------------------------------------------------ consultas
    def pin_visible_mask(self, side: int) -> np.ndarray:
        return (self.side == side) | (self.side == BOTH)

    def part_on_side(self, part: Part, side: int) -> bool:
        return part.side == side or part.side == BOTH

    def pin_label(self, i: int) -> str:
        pi = int(self.part[i])
        pn = self.parts[pi].name if pi >= 0 else "TP"
        return f"{pn}.{self.num[i]}"

    def stats(self) -> str:
        return f"{len(self.parts)} componentes · {len(self.x)} pinos · {len(self.nets)} redes"
