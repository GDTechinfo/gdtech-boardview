"""Renderizador por GPU (OpenGL 3.3 core), sincronizado com o monitor (vsync).

Tudo o que é desenhado fica em buffers na placa de vídeo; a cada quadro só a matriz da
câmera muda. Pinos e letras são desenhados por instancing (um quadrado por item), então
mexer na placa custa praticamente nada para o Python.
"""
from __future__ import annotations

import ctypes
import math
import time

import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QImage, QPainter, QFont, QFontMetricsF, QColor, QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .core import CanvasCore, CAT_NORMAL, CAT_TH, CAT_GND, CAT_NC, CAT_TP
from .parsers import TOP, BOTTOM, BOTH
from .theme import BUILTIN
from . import layers as LY

GL = None   # módulo OpenGL.GL carregado sob demanda


def default_format(vsync=True, samples=4):
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    fmt.setSwapInterval(1 if vsync else 0)
    fmt.setSamples(samples)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(8)
    return fmt


def gl_available():
    try:
        import OpenGL  # noqa
        return True
    except ImportError:
        return False


# ------------------------------------------------------------------ shaders

VS_GEOM = """
#version 330 core
layout(location=0) in vec2 pos;
layout(location=1) in vec4 color;
layout(location=2) in float side;
uniform mat3 mvp;
uniform float curSide;
out vec4 vcol;
void main(){
    vec3 p = mvp * vec3(pos, 1.0);
    if (side > 0.5 && abs(side - curSide) > 0.5) p.xy = vec2(-10.0);
    gl_Position = vec4(p.xy, 0.0, 1.0);
    vcol = color;
}
"""
FS_GEOM = """
#version 330 core
in vec4 vcol;
uniform vec4 tint;
uniform float useTint;
out vec4 frag;
void main(){ frag = mix(vcol, tint, useTint); }
"""

VS_PIN = """
#version 330 core
layout(location=0) in vec2 corner;
layout(location=1) in vec2 center;
layout(location=2) in vec2 halfsz;
layout(location=3) in vec2 axis;
layout(location=4) in vec4 color;
layout(location=5) in vec2 sr;        // lado, redondo
uniform mat3 mvp;
uniform float scale;
uniform float curSide;
uniform float minPx;
uniform float grow;
uniform float extraPx;
uniform float squareAll;
out vec2 vloc;
out vec4 vcol;
flat out vec2 vhalf;
flat out float vround;
void main(){
    vec2 hs = max(halfsz * grow * scale, vec2(minPx * 0.5));
    float rr = squareAll > 0.5 ? 0.0 : clamp(sr.y, 0.0, 1.0) * min(hs.x, hs.y);
    hs += vec2(extraPx);
    rr += extraPx * step(0.001, rr);
    vec2 q = corner * (hs + 1.5);
    vec2 qb = q / scale;
    vec2 v = vec2(-axis.y, axis.x);
    vec2 bp = center + axis * qb.x + v * qb.y;
    vec3 a = mvp * vec3(bp, 1.0);
    vec2 ndc = a.xy;
    if (sr.x > 0.5 && abs(sr.x - curSide) > 0.5) ndc = vec2(-10.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
    vloc = q;
    vhalf = hs;
    vround = rr;
    vcol = color;
}
"""
FS_PIN = """
#version 330 core
in vec2 vloc;
in vec4 vcol;
flat in vec2 vhalf;
flat in float vround;
uniform float ring;      // 0 = preenchido, >0 = espessura do contorno em px
uniform float border;    // borda clara do pad (px)
uniform vec4 tint;
uniform float useTint;
out vec4 frag;
void main(){
    // retângulo com cantos arredondados (raio vround em px; = metade do lado menor -> círculo/oval)
    vec2 q = abs(vloc) - (vhalf - vec2(vround));
    float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - vround;
    float a = clamp(0.5 - d, 0.0, 1.0);
    if (ring > 0.0) a *= clamp(d + ring + 0.5, 0.0, 1.0);
    if (a <= 0.0) discard;
    vec4 c = mix(vcol, tint, useTint);
    vec3 rgb = c.rgb;
    if (border > 0.0 && ring <= 0.0 && min(vhalf.x, vhalf.y) > 3.0) {
        float lum = dot(rgb, vec3(0.299, 0.587, 0.114));
        vec3 bc = lum > 0.62 ? rgb * 0.7 : mix(rgb, vec3(1.0), 0.32);
        rgb = mix(rgb, bc, clamp(d + border + 0.5, 0.0, 1.0));
    }
    frag = vec4(rgb, c.a * a);
}
"""

VS_TEXT = """
#version 330 core
layout(location=0) in vec2 corner;
layout(location=1) in vec2 anchor;
layout(location=2) in vec4 glyph;   // posição (em, relativa ao centro do rótulo) + tamanho (em)
layout(location=3) in vec4 uv;
layout(location=4) in vec4 p0;      // tipo, lado, a, b
layout(location=5) in vec4 p1;      // eixo.x, eixo.y, largura do rótulo (em), fim da letra (em) / flag
layout(location=6) in vec2 ext;     // reticências: início e quantidade das letras do rótulo na tabela penTex
uniform mat3 mvp;
uniform sampler2D penTex;           // fim de cada letra dos nomes de rede (em), para cortar com ".."
uniform float ellEm;                // largura do ".." (em)
uniform mat2 lin;                   // placa -> pixels (só a parte linear)
uniform vec2 px2ndc;
uniform float scale;
uniform float curSide;
uniform vec3 kindOn;
uniform vec3 kindScale;
uniform vec2 shadow;
uniform float pinGrow;
out vec2 vuv;
flat out int vkind;
void main(){
    float kind = p0.x;
    float vis = 1.0;
    if (p0.y > 0.5 && abs(p0.y - curSide) > 0.5) vis = 0.0;
    float lw = max(p1.z, 0.01);
    float fpx = 10.0;
    float shift = 0.0;
    vec2 line = vec2(0.0);
    vec2 dir = vec2(1.0, 0.0);
    if (kind < 0.5) {
        // nome do componente: cabe na largura do corpo e no comprimento disponível
        fpx = min(p0.z * scale * 0.4 * kindScale.x, p0.w * scale * 0.86 / lw);
        if (p1.w > 0.5) {
            // componente de 1 pino (test point): nome pequeno, acima do pad
            fpx = min(p0.z * scale * 0.3, 13.0) * kindScale.x;
            line.y = -(p0.z * scale * 0.5 + fpx * 0.75);
        }
        vis *= step(6.0, fpx) * kindOn.x;
        fpx = min(fpx, 110.0);
        if (p1.w < 0.5 && dot(p1.xy, p1.xy) > 0.25) {
            dir = normalize(lin * p1.xy);
            if (dir.x < -0.02 || (abs(dir.x) <= 0.02 && dir.y > 0.0)) dir = -dir;
        }
    } else {
        // número do pino e nome da rede dentro do pad (sempre na horizontal)
        vec2 hs = p0.zw * scale * pinGrow;
        vec2 ax = normalize(lin * p1.xy);
        float availW = 2.0 * (abs(ax.x) * hs.x + abs(ax.y) * hs.y);
        float padp = 2.0 * min(hs.x, hs.y);
        float fnum0 = min(padp * 0.42, 18.0) * kindScale.y;
        float fnum = min(fnum0, availW * 0.9 / lw * kindScale.y);
        float fnet = min(padp * 0.2, 10.0) * kindScale.z;
        float numVis = step(5.0, fnum0) * kindOn.y;
        float netVis = step(26.0, padp) * step(5.0, fnet) * kindOn.z;
        if (kind < 1.5) {
            fpx = fnum;
            vis *= numVis * step(5.0, fnum);
            line.y = -netVis * fnet * 0.62;
        } else {
            // nome da rede: tamanho fixo; se não couber, mostra o começo + ".." —
            // quanto mais zoom, mais letras aparecem, até o nome inteiro
            fpx = fnet;
            vis *= netVis;
            line.y = numVis * fnum0 * 0.55;
            float avail = availW * 1.15 / fpx;          // espaço disponível, em em
            float lim = avail - ellEm;
            if (kind < 2.5) {
                if (lw > avail) {
                    vis *= step(p1.w, lim);               // só as letras que cabem antes do ".."
                    shift = lw * 0.5 - avail * 0.5;       // alinhado à esquerda do espaço
                }
            } else {
                float cut = -1.0;
                if (lw > avail) {
                    int n = int(ext.y + 0.5);
                    int o = int(ext.x + 0.5);
                    for (int k = 0; k < 96; k++) {
                        if (k >= n) break;
                        int t = o + k;
                        float e = texelFetch(penTex, ivec2(t % 4096, t / 4096), 0).r;
                        if (e <= lim) cut = e; else break;
                    }
                }
                vis *= step(0.0, cut);
                shift = cut - avail * 0.5;
            }
        }
    }
    vec2 nrm = vec2(-dir.y, dir.x);
    vec2 g = (glyph.xy + vec2(shift, 0.0) + corner * glyph.zw) * fpx;
    vec2 off = dir * g.x + nrm * g.y + line + shadow;
    vec3 a = mvp * vec3(anchor, 1.0);
    vec2 ndc = a.xy + off * px2ndc;
    if (vis < 0.5) ndc = vec2(-10.0);
    gl_Position = vec4(ndc, 0.0, 1.0);
    vuv = mix(uv.xy, uv.zw, corner);
    vkind = min(int(kind + 0.5), 2);
}
"""
FS_TEXT = """
#version 330 core
in vec2 vuv;
flat in int vkind;
uniform sampler2D atlas;
uniform vec4 colors[3];
uniform vec4 shadowColors[3];
uniform float isShadow;
out vec4 frag;
void main(){
    float a = texture(atlas, vuv).a;
    vec4 c = isShadow > 0.5 ? shadowColors[vkind] : colors[vkind];
    a *= c.a;
    if (a < 0.01) discard;
    frag = vec4(c.rgb, a);
}
"""


def _f(c, mul=1.0):
    return [c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, c[3] / 255.0 * mul]


# ------------------------------------------------------------------ atlas de letras

class GlyphAtlas:
    EM = 40

    def __init__(self, family=None):
        font = QFont(family) if family else QFont()
        font.setPixelSize(self.EM)
        font.setBold(True)
        font.setStyleStrategy(QFont.PreferAntialias)
        fm = QFontMetricsF(font)
        self.ascent = fm.ascent()
        self.descent = fm.descent()
        chars = [chr(c) for c in range(32, 127)] + [chr(c) for c in range(160, 256)]
        pad = 4
        cell_h = int(math.ceil(self.ascent + self.descent)) + 2 * pad
        widths = {ch: fm.horizontalAdvance(ch) for ch in chars}
        cell_w = int(math.ceil(max(widths.values()))) + 2 * pad
        cols = 16
        rows = int(math.ceil(len(chars) / cols))
        W = cols * cell_w
        H = rows * cell_h
        img = QImage(W, H, QImage.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 255))
        self.table = np.zeros((256, 7), dtype=np.float32)   # adv, x0, y0, w, h, u0..(guardado à parte)
        self.uv = np.zeros((256, 4), dtype=np.float32)
        for k, ch in enumerate(chars):
            cx = (k % cols) * cell_w
            cy = (k // cols) * cell_h
            p.drawText(cx + pad, int(cy + pad + self.ascent), ch)
            o = ord(ch)
            em = float(self.EM)
            self.table[o, 0] = widths[ch] / em                       # avanço
            self.table[o, 1] = -pad / em                             # x do quadrado
            self.table[o, 2] = -(self.ascent + pad) / em             # y do topo (relativo à base)
            self.table[o, 3] = cell_w / em
            self.table[o, 4] = cell_h / em
            self.uv[o] = [cx / W, cy / H, (cx + cell_w) / W, (cy + cell_h) / H]
        p.end()
        q = self.table[ord("?")].copy()
        quv = self.uv[ord("?")].copy()
        for o in range(256):
            if self.table[o, 3] == 0:
                self.table[o] = q
                self.uv[o] = quv
        self.image = img
        self.baseline = (self.ascent - self.descent) / 2 / self.EM   # base para centralizar verticalmente

    TEXT_FLOATS = 20

    def layout(self, labels, anchors, p0, p1):
        """Gera instâncias (uma por letra), totalmente vetorizado com numpy.
        p0 = (tipo, lado, a, b) e p1 = (eixo.x, eixo.y, -, -) por rótulo; p1[2] recebe a largura em em.
        Nomes de rede (tipo 2) também ganham: o fim de cada letra (p1[3]), uma tabela com esses fins
        (self.pen_table, para o shader saber onde cortar) e duas instâncias de '.' (tipo 5) para o "..".
        Devolve (instâncias, tabela)."""
        F = self.TEXT_FLOATS
        empty = (np.zeros((0, F), dtype=np.float32), np.zeros(1, dtype=np.float32))
        if not labels:
            return empty
        enc = [s.encode("latin-1", "replace") for s in labels]
        lens = np.fromiter((len(s) for s in enc), dtype=np.int64, count=len(enc))
        codes = np.frombuffer(b"".join(enc), dtype=np.uint8).astype(np.int64)
        n = len(codes)
        if n == 0:
            return empty
        lab = np.repeat(np.arange(len(enc)), lens)
        adv = self.table[codes, 0].astype(np.float64)
        csum = np.cumsum(adv)
        starts = np.concatenate([[0], np.cumsum(lens)[:-1]])
        base = np.repeat(csum[starts] - adv[starts], lens)
        pen = csum - adv - base                          # posição de cada letra no rótulo
        width = np.bincount(lab, weights=adv, minlength=len(enc))     # soma por rótulo (np.add.at é lento)
        x = pen - width[lab] / 2 + self.table[codes, 1]
        y = self.baseline + self.table[codes, 2]
        out = np.empty((n, F), dtype=np.float32)
        out[:, 0] = anchors[lab, 0]
        out[:, 1] = anchors[lab, 1]
        out[:, 2] = x
        out[:, 3] = y
        out[:, 4] = self.table[codes, 3]
        out[:, 5] = self.table[codes, 4]
        out[:, 6:10] = self.uv[codes]
        out[:, 10:14] = p0[lab]
        out[:, 14:18] = p1[lab]
        out[:, 16] = width[lab]
        out[:, 18:20] = 0.0
        # nomes de rede: fim de cada letra e tabela para o corte com ".."
        is_net = (p0[:, 0] == 2)
        net_char = is_net[lab]
        pen_end = (pen + adv).astype(np.float32)
        out[net_char, 17] = pen_end[net_char]
        table = pen_end[net_char]
        net_labels = np.nonzero(is_net)[0]
        tab_off = np.concatenate([[0], np.cumsum(lens[net_labels])[:-1]]) if len(net_labels) else np.zeros(0)
        keep = codes != 32
        out = out[keep]
        if len(net_labels):
            dot = ord(".")
            dadv = float(self.table[dot, 0])
            ell = np.zeros((2 * len(net_labels), F), dtype=np.float32)
            for k in range(2):
                e = ell[k::2]
                e[:, 0] = anchors[net_labels, 0]
                e[:, 1] = anchors[net_labels, 1]
                e[:, 2] = self.table[dot, 1] + k * dadv
                e[:, 3] = self.baseline + self.table[dot, 2]
                e[:, 4] = self.table[dot, 3]
                e[:, 5] = self.table[dot, 4]
                e[:, 6:10] = self.uv[dot]
                e[:, 10:14] = p0[net_labels]
                e[:, 10] = 5.0
                e[:, 14:18] = p1[net_labels]
                e[:, 16] = width[net_labels]
                e[:, 18] = tab_off
                e[:, 19] = lens[net_labels]
            out = np.concatenate([out, ell])
        self.ell_em = 2.0 * float(self.table[ord("."), 0])
        return out, (table if len(table) else np.zeros(1, dtype=np.float32))


# ------------------------------------------------------------------ widget

class GLBoardCanvas(QOpenGLWidget, CanvasCore):
    selectionChanged = Signal()
    cursorMoved = Signal(float, float)
    viewChanged = Signal()
    fpsChanged = Signal(float)
    itemDoubleClicked = Signal(str, str)     # ("part" | "net", nome) — usado pelo esquema em PDF
    contextRequested = Signal(int, int, object)   # componente, pino (-1 = nenhum), posição global
    pinStepped = Signal(int, int)            # Tab entre pinos da rede: posição, total
    glFailed = Signal(str)
    renderer_name = "GPU"

    def __init__(self, parent=None):
        QOpenGLWidget.__init__(self, parent)
        self._core_init()
        self.theme = BUILTIN["GDTech Escuro"]
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(300, 200)
        self._ok = False
        self._dirty_board = True
        self._dirty_colors = True
        self._dirty_sel = True
        self._dirty_hover = True
        self._dirty_layers = True
        self._dirty_laysel = True
        self._dirty_marks = True
        self._frames = 0
        self._fps_t = time.perf_counter()
        self._continuous = False
        self.frameSwapped.connect(self._on_swapped)
        self.vbo = {}
        self.counts = {}

    # ------------------------------------------------------------------ CanvasCore
    def request_frame(self):
        self.update()

    def board_changed(self):
        self._dirty_board = True
        self._dirty_colors = True
        self._dirty_sel = True
        self._dirty_layers = True
        self._dirty_marks = True

    def layers_updated(self):
        self._dirty_laysel = True
        self.update()

    def marks_updated(self):
        self._dirty_marks = True
        self.update()

    def selection_updated(self):
        self._dirty_colors = True
        self._dirty_sel = True
        self._dirty_laysel = True

    def theme_changed(self):
        self._dirty_board = True
        self._dirty_colors = True
        self._dirty_sel = True
        self._dirty_marks = True              # sem rede / peças mecânicas escondidas
        self.update()

    def set_continuous(self, on):
        """Renderiza sem parar (para medir FPS)."""
        self._continuous = on
        self.update()

    def _on_swapped(self):
        self._frames += 1
        now = time.perf_counter()
        if now - self._fps_t >= 0.5:
            self.fpsChanged.emit(self._frames / (now - self._fps_t))
            self._frames = 0
            self._fps_t = now
        if self._continuous or self.animating():
            self.update()

    # ------------------------------------------------------------------ eventos
    def wheelEvent(self, e):
        self.core_wheel(e)

    def mousePressEvent(self, e):
        self.core_press(e)

    def mouseMoveEvent(self, e):
        self.core_move(e)

    def mouseReleaseEvent(self, e):
        self.core_release(e)

    def mouseDoubleClickEvent(self, e):
        self.core_double(e)

    def focusNextPrevChild(self, nxt):
        return False        # Tab é usado para ir ao próximo pino da rede, não para trocar o foco

    def leaveEvent(self, e):
        self.core_leave()

    # ------------------------------------------------------------------ OpenGL
    def initializeGL(self):
        global GL
        try:
            import OpenGL
            OpenGL.ERROR_CHECKING = False
            OpenGL.ERROR_LOGGING = False
            from OpenGL import GL as _GL
            GL = _GL
            fmt = self.format()
            if (fmt.majorVersion(), fmt.minorVersion()) < (3, 3):
                raise RuntimeError(f"OpenGL {fmt.majorVersion()}.{fmt.minorVersion()} (precisa 3.3)")
            self.p_geom = self._program(VS_GEOM, FS_GEOM)
            self.p_pin = self._program(VS_PIN, FS_PIN)
            self.p_text = self._program(VS_TEXT, FS_TEXT)
            self._loc = {}
            self.atlas = GlyphAtlas()
            self.tex = self._upload_texture(self.atlas.image)
            quad = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=np.float32)
            quad_c = np.array([[-1, -1], [1, -1], [-1, 1], [1, 1]], dtype=np.float32)
            self.vbo["quad01"] = self._buffer(quad)
            self.vbo["quadc"] = self._buffer(quad_c)
            self.vao = {}
            self._ok = True
            vendor = GL.glGetString(GL.GL_RENDERER)
            self.gl_info = (vendor.decode(errors="replace") if vendor else "?")
        except Exception as e:  # noqa
            self._ok = False
            import traceback
            traceback.print_exc()
            QTimer.singleShot(0, lambda: self.glFailed.emit(str(e)))

    def _program(self, vs, fs):
        def compile_(src, typ):
            sh = GL.glCreateShader(typ)
            GL.glShaderSource(sh, src)
            GL.glCompileShader(sh)
            if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
                log = GL.glGetShaderInfoLog(sh)
                raise RuntimeError("Erro no shader: " + (log.decode(errors="replace") if isinstance(log, bytes) else str(log)))
            return sh
        v = compile_(vs, GL.GL_VERTEX_SHADER)
        f = compile_(fs, GL.GL_FRAGMENT_SHADER)
        prog = GL.glCreateProgram()
        GL.glAttachShader(prog, v)
        GL.glAttachShader(prog, f)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            log = GL.glGetProgramInfoLog(prog)
            raise RuntimeError("Erro ao ligar shaders: " + (log.decode(errors="replace") if isinstance(log, bytes) else str(log)))
        GL.glDetachShader(prog, v)
        GL.glDetachShader(prog, f)
        GL.glDeleteShader(v)
        GL.glDeleteShader(f)
        return prog

    def _u(self, prog, name):
        key = (int(prog), name)
        loc = self._loc.get(key)
        if loc is None:
            loc = GL.glGetUniformLocation(prog, name)
            self._loc[key] = loc
        return loc

    def _buffer(self, arr, usage=None, buf=None):
        arr = np.ascontiguousarray(arr)
        if buf is None:
            buf = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buf)
        if arr.nbytes:
            GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, usage or GL.GL_STATIC_DRAW)
        else:
            GL.glBufferData(GL.GL_ARRAY_BUFFER, 4, None, usage or GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        return buf

    def _upload_texture(self, img):
        img = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = img.width(), img.height()
        ptr = img.constBits()
        data = bytes(ptr)[: w * h * 4] if not hasattr(ptr, "tobytes") else ptr.tobytes()[: w * h * 4]
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return tex

    # VAO para geometria simples: pos(2) cor(4 ubyte) lado(1) -> stride 16 bytes
    def _geom_vao(self, name, verts):
        """verts: array estruturado com x,y (f4), rgba (u1 x4), side (f4)."""
        buf = self._buffer(verts, buf=self.vbo.get(name))
        self.vbo[name] = buf
        vao = self.vao.get(name)
        if vao is None:
            vao = GL.glGenVertexArrays(1)
            self.vao[name] = vao
            GL.glBindVertexArray(vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buf)
            stride = 16
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(0))
            GL.glEnableVertexAttribArray(1)
            GL.glVertexAttribPointer(1, 4, GL.GL_UNSIGNED_BYTE, GL.GL_TRUE, stride, ctypes.c_void_p(8))
            GL.glEnableVertexAttribArray(2)
            GL.glVertexAttribPointer(2, 1, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(12))
            GL.glBindVertexArray(0)
        self.counts[name] = len(verts)

    # VAO de pads: centro(2) meia-medida(2) eixo(2) lado(1) redondo(1) = 32 bytes + cor (outro buffer)
    PAD_FLOATS = 8

    def _pin_vao(self, name, inst, colors):
        buf = self._buffer(inst, buf=self.vbo.get(name))
        self.vbo[name] = buf
        cbuf = self._buffer(colors, usage=GL.GL_DYNAMIC_DRAW, buf=self.vbo.get(name + "_c"))
        self.vbo[name + "_c"] = cbuf
        vao = self.vao.get(name)
        if vao is None:
            vao = GL.glGenVertexArrays(1)
            self.vao[name] = vao
            GL.glBindVertexArray(vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo["quadc"])
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 8, ctypes.c_void_p(0))
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buf)
            for loc, off in ((1, 0), (2, 2), (3, 4), (5, 6)):
                GL.glEnableVertexAttribArray(loc)
                GL.glVertexAttribPointer(loc, 2, GL.GL_FLOAT, GL.GL_FALSE, 32, ctypes.c_void_p(off * 4))
                GL.glVertexAttribDivisor(loc, 1)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, cbuf)
            GL.glEnableVertexAttribArray(4)
            GL.glVertexAttribPointer(4, 4, GL.GL_UNSIGNED_BYTE, GL.GL_TRUE, 4, ctypes.c_void_p(0))
            GL.glVertexAttribDivisor(4, 1)
            GL.glBindVertexArray(0)
        self.counts[name] = len(inst)

    def _upload_pen_table(self, pens):
        """Tabela (textura R32F, 4096 de largura) com o fim de cada letra dos nomes de rede."""
        W = 4096
        rows = max(1, (len(pens) + W - 1) // W)
        data = np.zeros(W * rows, dtype=np.float32)
        data[:len(pens)] = pens
        if getattr(self, "pen_tex", None) is None:
            self.pen_tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.pen_tex)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R32F, W, rows, 0, GL.GL_RED, GL.GL_FLOAT, data)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def _text_vao(self, inst):
        buf = self._buffer(inst, buf=self.vbo.get("text"))
        self.vbo["text"] = buf
        vao = self.vao.get("text")
        if vao is None:
            vao = GL.glGenVertexArrays(1)
            self.vao["text"] = vao
            GL.glBindVertexArray(vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo["quad01"])
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 8, ctypes.c_void_p(0))
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buf)
            stride = GlyphAtlas.TEXT_FLOATS * 4
            for loc, size, off in ((1, 2, 0), (2, 4, 2), (3, 4, 6), (4, 4, 10), (5, 4, 14), (6, 2, 18)):
                GL.glEnableVertexAttribArray(loc)
                GL.glVertexAttribPointer(loc, size, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(off * 4))
                GL.glVertexAttribDivisor(loc, 1)
            GL.glBindVertexArray(0)
        self.counts["text"] = len(inst)

    # ------------------------------------------------------------------ montagem dos buffers
    GEOM_DT = np.dtype([("x", "<f4"), ("y", "<f4"), ("c", "u1", 4), ("s", "<f4")])

    def _geom(self, pts, colors, sides):
        a = np.zeros(len(pts), dtype=self.GEOM_DT)
        if len(pts):
            pts = np.asarray(pts, dtype=np.float64)
            a["x"] = pts[:, 0] - self.origin[0]
            a["y"] = pts[:, 1] - self.origin[1]
            a["c"] = colors
            a["s"] = sides
        return a

    def _rebuild_board(self):
        b = self.board
        th = self.theme
        x0, y0, x1, y1 = b.bounds
        self.origin = ((x0 + x1) / 2, (y0 + y1) / 2)

        # contorno da placa (linhas)
        segs = b.outline_segs
        pts = [p for s in segs for p in s]
        col = th.get("BoardOutline")
        self._geom_vao("outline", self._geom(pts, [col] * len(pts), [0] * len(pts)))

        # preenchimento da placa (envoltória convexa do contorno + pinos)
        fill = th.get("BoardFill")
        tri = []
        if fill is not None:
            cand = np.array(pts if pts else [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], dtype=np.float64)
            hull = _convex_hull(cand)
            for i in range(1, len(hull) - 1):
                tri += [hull[0], hull[i], hull[i + 1]]
        self._geom_vao("boardfill", self._geom(tri, [fill or (0, 0, 0, 0)] * len(tri), [0] * len(tri)))

        # componentes: preenchimento (corpo, triângulos) e contorno (linhas)
        tri_p, tri_c, tri_s = [], [], []
        lin_p, lin_s = [], []
        box = th.get("ComponentBox")
        hide_mech = not self.opts.show_mech
        for i, part in enumerate(b.parts):
            poly = part.outline
            if len(poly) < 3 or (hide_mech and part.is_mech):
                continue
            side = part.side
            fillc = th.get(self.ptype[i]) if (th.colorize and self.ptype[i]) else None
            body = part.body if part.body else poly
            if fillc is not None:
                for k in range(1, len(body) - 1):
                    tri_p += [body[0], body[k], body[k + 1]]
                tri_c += [fillc] * (3 * (len(body) - 2))
                tri_s += [side] * (3 * (len(body) - 2))
            if box is not None:
                for k in range(len(poly)):
                    lin_p += [poly[k], poly[(k + 1) % len(poly)]]
                lin_s += [side] * (2 * len(poly))
        self._geom_vao("partfill", self._geom(tri_p, tri_c, tri_s))
        self._geom_vao("partline", self._geom(lin_p, [box or (0, 0, 0, 0)] * len(lin_p), lin_s))

        # pads
        n = len(b.x)
        inst = np.zeros((n, self.PAD_FLOATS), dtype=np.float32)
        inst[:, 0] = b.pad_x - self.origin[0]
        inst[:, 1] = b.pad_y - self.origin[1]
        inst[:, 2] = b.pad_hw
        inst[:, 3] = b.pad_hh
        inst[:, 4] = b.pad_ux
        inst[:, 5] = b.pad_uy
        inst[:, 6] = b.side
        gone = b.pin_mech_dead if hide_mech else None
        if gone is not None and gone.any():
            inst[gone, 6] = 9                    # lado que não existe: não desenha (furo de peça mecânica escondida)
        inst[:, 7] = b.pad_round
        self._pin_inst = inst
        self._pin_vao("pins", inst, np.zeros((n, 4), dtype=np.uint8))

        # textos
        labels, anchors, P0, P1 = [], [], [], []
        bx0, by0, bx1, by1 = b.bounds
        big = 0.35 * max(bx1 - bx0, by1 - by0)     # peças do tamanho da placa (mecânicas): sem nome gigante
        for i, part in enumerate(b.parts):
            if part.name and 0 < part.size < big and not (hide_mech and part.is_mech):
                labels.append(part.name)
                anchors.append((part.center[0] - self.origin[0], part.center[1] - self.origin[1]))
                L = part.dims[0]
                W = getattr(part, "label_w", part.dims[1])    # peça mecânica: nome do tamanho de um CI comum
                avail = L
                if part.body:
                    # corpo entre os pads
                    (q0, q1) = part.body[0], part.body[1]
                    avail = math.hypot(q1[0] - q0[0], q1[1] - q0[1])
                P0.append((0, part.side, W, max(avail, W * 0.5)))
                P1.append((part.axis[0], part.axis[1], 0, 1.0 if len(part.pins) == 1 else 0.0))
        # pinos: listas comuns (acesso bem mais rápido que elemento a elemento no numpy), mesma ordem de antes
        pnames, nums, nets = b.pname, b.num, b.nets
        axl = (b.pad_x - self.origin[0]).tolist()
        ayl = (b.pad_y - self.origin[1]).tolist()
        sdl = (np.where(gone, 9, b.side) if gone is not None else b.side).astype(np.int64).tolist()
        hwl, hhl, rndl = b.pad_hw.tolist(), b.pad_hh.tolist(), b.pad_round.tolist()
        uxl, uyl, netl = b.pad_ux.tolist(), b.pad_uy.tolist(), b.net.tolist()
        for i in range(n):
            pn = pnames[i]
            nm = pn if pn and len(pn) <= 5 else nums[i]
            a = (axl[i], ayl[i])
            hw, hh = hwl[i], hhl[i]
            if rndl[i]:
                hw = hh = min(hw, hh) * 0.75     # texto dentro do círculo
            sd = sdl[i]
            pp1 = (uxl[i], uyl[i], 0, 0)
            if nm:
                labels.append(nm)
                anchors.append(a)
                P0.append((1, sd, hw, hh))
                P1.append(pp1)
            net = nets[netl[i]]
            if net and net != "UNCONNECTED":
                labels.append(net)
                anchors.append(a)
                P0.append((2, sd, hw, hh))
                P1.append(pp1)
        inst_t, pens = self.atlas.layout(labels, np.array(anchors, dtype=np.float64).reshape(-1, 2),
                                         np.array(P0, dtype=np.float32).reshape(-1, 4),
                                         np.array(P1, dtype=np.float32).reshape(-1, 4))
        self._text_vao(inst_t)
        self._upload_pen_table(pens)
        self._dirty_board = False

    def _rebuild_colors(self):
        b = self.board
        th = self.theme
        n = len(b.x)
        cols = np.empty((n, 4), dtype=np.uint8)
        cols[:] = th.get("PinsPads")
        cats = [(CAT_GND, "GndColor"), (CAT_NC, "NcColor"), (CAT_TP, "TestPinColor")]
        for cat, key in cats:
            c = th.get(key)
            if c is not None:
                cols[self.pcat == cat] = c
        fp = th.get("FirstPinColor")
        if fp is not None:
            cols[self.first_pin & (self.pcat == CAT_NORMAL)] = fp
        if self.net_active():
            cols[:, 3] = (cols[:, 3].astype(np.float32) * 0.45).astype(np.uint8)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo["pins_c"])
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, cols.nbytes, cols)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self._dirty_colors = False

    def _rebuild_selection(self):
        b = self.board
        th = self.theme
        # pinos da rede selecionada (preenchidos, cor de destaque)
        hl = []
        if self.sel_net is not None and not b.net_is_nc[self.sel_net]:
            hl = b.net_pins[self.sel_net]
        idx = np.asarray(hl, dtype=np.int64)
        inst = self._pin_inst[idx] if len(idx) else np.zeros((0, self.PAD_FLOATS), np.float32)
        cols = np.empty((len(idx), 4), dtype=np.uint8)
        cols[:] = th.get("NetHighlight")
        self._pin_vao("netpins", inst, cols)
        # anéis: pinos do componente selecionado + pino selecionado
        ring_idx = list(b.parts[self.sel_part].pins) if self.sel_part is not None else []
        inst_r = self._pin_inst[np.asarray(ring_idx, dtype=np.int64)] if ring_idx else np.zeros((0, self.PAD_FLOATS), np.float32)
        cols_r = np.empty((len(ring_idx), 4), dtype=np.uint8)
        cols_r[:] = th.get("SelectedPart")
        self._pin_vao("rings", inst_r, cols_r)
        if self.sel_pin is not None:
            one = self._pin_inst[[self.sel_pin]]
            c1 = np.array([th.get("SelectedPin")], dtype=np.uint8)
        else:
            one = np.zeros((0, self.PAD_FLOATS), np.float32)
            c1 = np.zeros((0, 4), np.uint8)
        self._pin_vao("selpin", one, c1)

        # linhas da rede (do pino selecionado até os outros); cada linha só aparece no lado do pino de destino
        web, wside = [], []
        t = self.netweb_targets()
        if len(t):
            x0, y0 = b.pad_x[self.sel_pin], b.pad_y[self.sel_pin]
            for i in t:
                web += [(x0, y0), (b.pad_x[i], b.pad_y[i])]
                wside += [int(b.side[i])] * 2
        wc = th.get("NetwebCurrent")
        self._geom_vao("netweb", self._geom(web, [wc] * len(web), wside))
        self._webx_key = None

        # componentes na rede selecionada + componente selecionado (contornos e preenchimento)
        lines, lcol, lside = [], [], []
        tri, tcol, tside = [], [], []
        if self.sel_net is not None and not b.net_is_gnd[self.sel_net]:
            parts = {int(b.part[i]) for i in b.net_pins[self.sel_net] if b.part[i] >= 0}
            hc = th.get("HighlightedPart")
            for pi in parts:
                poly = b.parts[pi].outline
                for k in range(len(poly)):
                    lines += [poly[k], poly[(k + 1) % len(poly)]]
                lcol += [hc] * (2 * len(poly))
                lside += [b.parts[pi].side] * (2 * len(poly))
        if self.sel_part is not None:
            part = b.parts[self.sel_part]
            poly = part.outline
            sc = th.get("SelectedPart")
            fc = (sc[0], sc[1], sc[2], int(sc[3] * 0.18))
            for k in range(len(poly)):
                lines += [poly[k], poly[(k + 1) % len(poly)]]
            lcol += [sc] * (2 * len(poly))
            lside += [part.side] * (2 * len(poly))
            for k in range(1, len(poly) - 1):
                tri += [poly[0], poly[k], poly[k + 1]]
            tcol += [fc] * (3 * max(len(poly) - 2, 0))
            tside += [part.side] * (3 * max(len(poly) - 2, 0))
        self._geom_vao("selline", self._geom(lines, lcol, lside))
        self._geom_vao("selfill", self._geom(tri, tcol, tside))
        self._dirty_sel = False
        self._dirty_hover = True

    def _rebuild_netweb_cross(self, R):
        """Linhas da rede ligando as duas placas (em coordenadas da cena)."""
        wc = self.theme.get("NetwebCurrent")
        key = (self.sel_pin, self.sel_net, R.tobytes(), self.side, wc)
        if key == getattr(self, "_webx_key", None):
            return
        self._webx_key = key
        seg = self.netweb_scene_segments()
        pts = seg.reshape(-1, 2) if len(seg) else np.zeros((0, 2))
        self._geom_vao("netweb_x", self._geom(pts, [wc] * len(pts), [0] * len(pts)))

    # ------------------------------------------------------------------ camadas de cobre
    def _rebuild_layers(self):
        b = self.board
        o = self.origin
        self._lay = []
        x0, y0, x1, y1 = b.bounds
        m = 0.05 * max(x1 - x0, y1 - y0)
        quad = np.array([(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m),
                         (x0 - m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m)])
        self._geom_vao("lay_cover", self._geom(quad, [(255, 255, 255, 255)] * 6, [0] * 6))
        for i, L in enumerate(b.layers):
            tag = f"lay{i}"
            tr = LY.trace_instances(L.seg, o)
            self._pin_vao(tag + "_t", tr, np.zeros((len(tr), 4), np.uint8))
            pd = LY.pad_instances(L.pad, o)
            self._pin_vao(tag + "_p", pd, np.zeros((len(pd), 4), np.uint8))
            rings = [r for _net, rs in L.polys for r in rs]
            fan = LY.ring_fans(rings) if rings else np.zeros((0, 2))
            self._geom_vao(tag + "_f", self._geom(fan, (255, 255, 255, 255), 0))
            self._lay.append(tag)
        self._lay_ok = True
        self._dirty_layers = False
        self._dirty_laysel = True

    def _rebuild_layer_selection(self):
        """Trilhas/áreas/vias da rede selecionada nas camadas visíveis (desenhadas por cima, em destaque)."""
        b = self.board
        o = self.origin
        net = self.sel_net
        tr, pd, rings = [], [], []
        if net is not None and not b.net_is_nc[net]:
            for i, L in enumerate(b.layers):
                if not self.layer_visible(i):
                    continue
                if len(L.seg):
                    tr.append(L.seg[L.seg_net == net])
                if len(L.pad):
                    pd.append(L.pad[L.pad_net == net])
                if not b.net_is_gnd[net]:
                    rings += [r for n_, rs in L.polys if n_ == net for r in rs]
        tr = LY.trace_instances(np.concatenate(tr) if tr else np.zeros((0, 5)), o)
        pd = LY.pad_instances(np.concatenate(pd) if pd else np.zeros((0, 5)), o)
        self._pin_vao("laysel_t", tr, np.zeros((len(tr), 4), np.uint8))
        self._pin_vao("laysel_p", pd, np.zeros((len(pd), 4), np.uint8))
        fan = LY.ring_fans(rings) if rings else np.zeros((0, 2))
        self._geom_vao("laysel_f", self._geom(fan, (255, 255, 255, 255), 0))
        self._dirty_laysel = False

    def _fill_rings(self, name, mvp, color):
        """Preenche polígonos com furos (regra par/ímpar) usando o stencil."""
        if not self.counts.get(name):
            return
        GL.glUseProgram(self.p_geom)
        GL.glUniformMatrix3fv(self._u(self.p_geom, "mvp"), 1, GL.GL_TRUE, mvp)
        GL.glUniform1f(self._u(self.p_geom, "curSide"), 0.0)
        GL.glEnable(GL.GL_STENCIL_TEST)
        GL.glStencilMask(0x01)
        GL.glColorMask(GL.GL_FALSE, GL.GL_FALSE, GL.GL_FALSE, GL.GL_FALSE)
        GL.glStencilFunc(GL.GL_ALWAYS, 0, 0x01)
        GL.glStencilOp(GL.GL_KEEP, GL.GL_KEEP, GL.GL_INVERT)
        self._draw_geom(name, GL.GL_TRIANGLES)
        GL.glColorMask(GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE, GL.GL_TRUE)
        GL.glStencilFunc(GL.GL_NOTEQUAL, 0, 0x01)
        GL.glStencilOp(GL.GL_ZERO, GL.GL_ZERO, GL.GL_ZERO)     # já limpa o stencil para a próxima
        GL.glUniform1f(self._u(self.p_geom, "useTint"), 1.0)
        GL.glUniform4f(self._u(self.p_geom, "tint"), *color)
        self._draw_geom("lay_cover", GL.GL_TRIANGLES)
        GL.glUniform1f(self._u(self.p_geom, "useTint"), 0.0)
        GL.glDisable(GL.GL_STENCIL_TEST)

    def _draw_tinted_pads(self, name, mvp, color, min_px):
        n = self.counts.get(name, 0)
        if not n:
            return
        GL.glUseProgram(self.p_pin)
        GL.glUniformMatrix3fv(self._u(self.p_pin, "mvp"), 1, GL.GL_TRUE, mvp)
        GL.glUniform1f(self._u(self.p_pin, "scale"), self.scale)
        GL.glUniform1f(self._u(self.p_pin, "curSide"), 0.0)
        GL.glUniform1f(self._u(self.p_pin, "squareAll"), 0.0)
        GL.glUniform1f(self._u(self.p_pin, "useTint"), 1.0)
        GL.glUniform4f(self._u(self.p_pin, "tint"), *color)
        self._pins(name, grow=1.0, min_px=min_px)
        GL.glUniform1f(self._u(self.p_pin, "useTint"), 0.0)

    def _draw_layers(self, mvp):
        b = self.board
        order = list(range(len(b.layers)))
        # o lado que está de frente é desenhado por último (fica por cima)
        if self.side == TOP:
            order = order[::-1]
        dim = self.net_active()
        k = 0.45 if dim else 1.0
        for i in order:
            if not self.layer_visible(i):
                continue
            r, g, bl = (c / 255.0 for c in self.layer_color(i))
            tag = self._lay[i]
            self._fill_rings(tag + "_f", mvp, (r, g, bl, 0.30 * k))
            self._draw_tinted_pads(tag + "_t", mvp, (r, g, bl, 0.90 * k), 0.7)
            self._draw_tinted_pads(tag + "_p", mvp, (min(1, r * 0.6 + 0.4), min(1, g * 0.6 + 0.4),
                                                     min(1, bl * 0.6 + 0.4), 0.85 * k), 1.0)
        if dim:
            hc = self.theme.get("NetHighlight")
            c = (hc[0] / 255.0, hc[1] / 255.0, hc[2] / 255.0, 1.0)
            self._fill_rings("laysel_f", mvp, (c[0], c[1], c[2], 0.45))
            self._draw_tinted_pads("laysel_t", mvp, c, 1.2)
            self._draw_tinted_pads("laysel_p", mvp, c, 1.5)

    def _rebuild_marks(self):
        """Componentes marcados (busca, comparação, notas) e redes do grupo ("seguir a rede")."""
        b = self.board
        lines, lcol, lside = [], [], []
        tri, tcol, tside = [], [], []
        for marks, fill_a in ((self.note_marks, 0.30), (self.part_marks, 0.22)):
            for pi, c in marks.items():
                if not (0 <= pi < len(b.parts)):
                    continue
                part = b.parts[pi]
                poly = part.outline
                if len(poly) < 3:
                    continue
                c = tuple(int(v) for v in c)
                fc = (c[0], c[1], c[2], int(c[3] * fill_a))
                for k in range(len(poly)):
                    lines += [poly[k], poly[(k + 1) % len(poly)]]
                lcol += [c] * (2 * len(poly))
                lside += [part.side] * (2 * len(poly))
                for k in range(1, len(poly) - 1):
                    tri += [poly[0], poly[k], poly[k + 1]]
                tcol += [fc] * (3 * (len(poly) - 2))
                tside += [part.side] * (3 * (len(poly) - 2))
        self._geom_vao("markline", self._geom(lines, lcol, lside))
        self._geom_vao("markfill", self._geom(tri, tcol, tside))
        # pinos soltos e componentes sem rede
        lc = self.theme.get("LoosePin") or (225, 95, 225, 255)
        lidx = np.nonzero(b.pin_loose)[0] if len(b.x) else np.zeros(0, np.int64)
        linst = self._pin_inst[lidx] if len(lidx) else np.zeros((0, self.PAD_FLOATS), np.float32)
        lcols = np.empty((len(lidx), 4), dtype=np.uint8)
        lcols[:] = lc
        self._pin_vao("loosepins", linst, lcols)
        lines, lside, tri, tside = [], [], [], []
        hide_mech = not self.opts.show_mech
        for pi, nonet in enumerate(b.part_no_net):
            if not nonet or (hide_mech and b.parts[pi].is_mech):
                continue
            poly = b.parts[pi].outline
            if len(poly) < 3:
                continue
            for k in range(len(poly)):
                lines += [poly[k], poly[(k + 1) % len(poly)]]
            lside += [b.parts[pi].side] * (2 * len(poly))
            for k in range(1, len(poly) - 1):
                tri += [poly[0], poly[k], poly[k + 1]]
            tside += [b.parts[pi].side] * (3 * (len(poly) - 2))
        self._geom_vao("looseline", self._geom(lines, [lc] * len(lines), lside))
        self._geom_vao("loosefill", self._geom(tri, [(lc[0], lc[1], lc[2], 60)] * len(tri), tside))
        idx = [i for ni in self.group_nets if ni != self.sel_net and 0 <= ni < len(b.net_pins)
               for i in b.net_pins[ni]]
        idx = np.asarray(idx, dtype=np.int64)
        inst = self._pin_inst[idx] if len(idx) else np.zeros((0, self.PAD_FLOATS), np.float32)
        gc = self.theme.get("NetwebOther") or (51, 153, 255, 255)
        cols = np.empty((len(idx), 4), dtype=np.uint8)
        cols[:] = (gc[0], gc[1], gc[2], 255)
        self._pin_vao("grouppins", inst, cols)
        self._marks_sel = self.sel_net
        self._dirty_marks = False

    def _rebuild_hover(self):
        b = self.board
        lines = []
        side = 0
        c = self.theme.get("HoverPart")
        if c is not None and self.hover_part is not None and self.hover_part != self.sel_part:
            part = b.parts[self.hover_part]
            poly = part.outline
            side = part.side
            for k in range(len(poly)):
                lines += [poly[k], poly[(k + 1) % len(poly)]]
        self._geom_vao("hover", self._geom(lines, [c or (0, 0, 0, 0)] * len(lines), [side] * len(lines)))
        self._dirty_hover = False

    def hover_updated(self):
        self._dirty_hover = True

    # ------------------------------------------------------------------ desenho
    def paintGL(self):
        if not self._ok:
            return
        try:
            self._paint()
        except Exception:
            import traceback
            traceback.print_exc()

    def _paint(self):
        th = self.theme
        bg = th.get("Background")
        GL.glClearColor(bg[0] / 255, bg[1] / 255, bg[2] / 255, 1.0)
        GL.glClearStencil(0)
        GL.glStencilMask(0xFF)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_STENCIL_BUFFER_BIT)
        if self.board is None:
            return
        if self._dirty_board:
            self._rebuild_board()
        if self._dirty_colors:
            self._rebuild_colors()
        if self._dirty_sel:
            self._rebuild_selection()
        if self._dirty_hover:
            self._rebuild_hover()
        if self._dirty_marks or (self.group_nets and getattr(self, "_marks_sel", None) != self.sel_net):
            self._rebuild_marks()
        if self.layer_mode and self.board.layers:
            if self._dirty_layers:
                self._rebuild_layers()
            if self._dirty_laysel:
                self._rebuild_layer_selection()
        self.tick()

        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height()
        GL.glViewport(0, 0, int(w * dpr), int(h * dpr))
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA, GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glDisable(GL.GL_DEPTH_TEST)

        copies = [(self.side, None)]
        if self.dual_view:
            copies.append((self.other_side(), self.mirror_R()))
            if self.netweb_cross_active():
                self._rebuild_netweb_cross(copies[1][1])
        for cur_side, R in copies:
            self._copy_R = R
            try:
                self._draw_copy(w, h, cur_side)
            finally:
                self._copy_R = None
        if self.dual_view and self.netweb_cross_active() and self.opts.show_netweb and self.counts.get("netweb_x"):
            # linhas que ligam as duas placas: por cima das duas
            M = self.matrix(self.origin)
            N = np.array([[2.0 / w, 0, -1.0], [0, -2.0 / h, 1.0], [0, 0, 1.0]])
            GL.glUseProgram(self.p_geom)
            GL.glUniformMatrix3fv(self._u(self.p_geom, "mvp"), 1, GL.GL_TRUE, (N @ M).astype(np.float32))
            GL.glUniform1f(self._u(self.p_geom, "curSide"), 0.0)
            GL.glUniform1f(self._u(self.p_geom, "useTint"), 0.0)
            self._draw_geom("netweb_x", GL.GL_LINES)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

    def _draw_copy(self, w, h, cur_side):
        """Desenha a placa uma vez (na vista dupla é chamado duas vezes: lado atual e cópia espelhada)."""
        th = self.theme
        M = self.matrix(self.origin)
        N = np.array([[2.0 / w, 0, -1.0], [0, -2.0 / h, 1.0], [0, 0, 1.0]])
        mvp = (N @ M).astype(np.float32)
        px2ndc = (2.0 / w, -2.0 / h)
        side = float(cur_side)
        o = self.opts

        # ---- geometria
        GL.glUseProgram(self.p_geom)
        GL.glUniformMatrix3fv(self._u(self.p_geom, "mvp"), 1, GL.GL_TRUE, mvp)
        GL.glUniform1f(self._u(self.p_geom, "curSide"), side)
        GL.glUniform1f(self._u(self.p_geom, "useTint"), 0.0)
        self._draw_geom("boardfill", GL.GL_TRIANGLES)
        lay_on = self.layer_mode and bool(self.board.layers) and getattr(self, "_lay_ok", False)
        if lay_on:
            self._draw_layers(mvp)
            GL.glUseProgram(self.p_geom)
            GL.glUniformMatrix3fv(self._u(self.p_geom, "mvp"), 1, GL.GL_TRUE, mvp)
            GL.glUniform1f(self._u(self.p_geom, "curSide"), side)
            GL.glUniform1f(self._u(self.p_geom, "useTint"), 0.0)
        if o.show_boxes:
            if not lay_on:
                self._draw_geom("partfill", GL.GL_TRIANGLES)
            self._draw_geom("partline", GL.GL_LINES)
        self._draw_geom("outline", GL.GL_LINES)
        if o.show_loose:
            self._draw_geom("loosefill", GL.GL_TRIANGLES)
            self._draw_geom("looseline", GL.GL_LINES)
        self._draw_geom("markfill", GL.GL_TRIANGLES)
        self._draw_geom("markline", GL.GL_LINES)
        self._draw_geom("selfill", GL.GL_TRIANGLES)
        self._draw_geom("selline", GL.GL_LINES)
        self._draw_geom("hover", GL.GL_LINES)

        # ---- pinos
        GL.glUseProgram(self.p_pin)
        GL.glUniformMatrix3fv(self._u(self.p_pin, "mvp"), 1, GL.GL_TRUE, mvp)
        GL.glUniform1f(self._u(self.p_pin, "scale"), self.scale)
        GL.glUniform1f(self._u(self.p_pin, "curSide"), side)
        GL.glUniform1f(self._u(self.p_pin, "squareAll"), 1.0 if o.square_pins else 0.0)
        GL.glUniform1f(self._u(self.p_pin, "useTint"), 0.0)
        self._pins("pins", grow=o.pin_scale, min_px=0.8, border=1.0)
        if o.show_netweb:
            GL.glUseProgram(self.p_geom)
            if not self.netweb_cross_active():
                self._draw_geom("netweb", GL.GL_LINES)
            GL.glUseProgram(self.p_pin)
        self._pins("grouppins", grow=o.pin_scale, min_px=1.6, border=1.0)
        if o.show_loose:
            self._pins("loosepins", grow=o.pin_scale, min_px=4.0, ring=1.4, extra=1.0)
        if self.sel_net is not None and self.board.net_is_gnd[self.sel_net] and o.gnd_subtle:
            self._pins("netpins", grow=o.pin_scale, min_px=1.6, ring=1.3)     # GND discreto: só o contorno
        else:
            self._pins("netpins", grow=o.pin_scale, min_px=1.6, border=1.0)
        self._pins("rings", grow=o.pin_scale, min_px=3.0, ring=1.4, extra=0.0)
        self._pins("selpin", grow=o.pin_scale, min_px=7.0, ring=2.2, extra=2.5)

        # ---- textos
        if self.counts.get("text") and (o.show_part_labels or o.show_pin_labels or o.show_net_names):
            GL.glUseProgram(self.p_text)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.tex)
            GL.glUniform1i(self._u(self.p_text, "atlas"), 0)
            GL.glActiveTexture(GL.GL_TEXTURE1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.pen_tex)
            GL.glUniform1i(self._u(self.p_text, "penTex"), 1)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glUniform1f(self._u(self.p_text, "ellEm"), getattr(self.atlas, "ell_em", 0.6))
            GL.glUniformMatrix3fv(self._u(self.p_text, "mvp"), 1, GL.GL_TRUE, mvp)
            GL.glUniformMatrix2fv(self._u(self.p_text, "lin"), 1, GL.GL_TRUE,
                                  np.ascontiguousarray(M[:2, :2], dtype=np.float32))
            GL.glUniform2f(self._u(self.p_text, "px2ndc"), *px2ndc)
            GL.glUniform1f(self._u(self.p_text, "scale"), self.scale)
            GL.glUniform1f(self._u(self.p_text, "curSide"), side)
            GL.glUniform1f(self._u(self.p_text, "pinGrow"), o.pin_scale)
            GL.glUniform3f(self._u(self.p_text, "kindOn"), float(o.show_part_labels),
                           float(o.show_pin_labels), float(o.show_net_names))
            GL.glUniform3f(self._u(self.p_text, "kindScale"), o.part_label_scale, o.pin_label_scale,
                           o.pin_label_scale)
            cols = _f(th.get("ComponentLabelText")) + _f(th.get("PinLabelText")) + \
                _f(th.get("NetNameText", "PinLabelText"))
            GL.glUniform4fv(self._u(self.p_text, "colors"), 3, np.array(cols, dtype=np.float32))
            sh = []
            for k in ("ComponentLabelText", "PinLabelText", "NetNameText"):
                sh += text_shadow(th.get(k, "PinLabelText"))
            GL.glUniform4fv(self._u(self.p_text, "shadowColors"), 3, np.array(sh, dtype=np.float32))
            GL.glBindVertexArray(self.vao["text"])
            GL.glUniform1f(self._u(self.p_text, "isShadow"), 1.0)
            GL.glUniform2f(self._u(self.p_text, "shadow"), 1.0, 1.0)
            GL.glDrawArraysInstanced(GL.GL_TRIANGLE_STRIP, 0, 4, self.counts["text"])
            GL.glUniform1f(self._u(self.p_text, "isShadow"), 0.0)
            GL.glUniform2f(self._u(self.p_text, "shadow"), 0.0, 0.0)
            GL.glDrawArraysInstanced(GL.GL_TRIANGLE_STRIP, 0, 4, self.counts["text"])
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)


    def _draw_geom(self, name, mode):
        n = self.counts.get(name, 0)
        if n:
            GL.glBindVertexArray(self.vao[name])
            GL.glDrawArrays(mode, 0, n)

    def _pins(self, name, grow, min_px, ring=0.0, border=0.0, extra=0.0):
        n = self.counts.get(name, 0)
        if not n:
            return
        GL.glUniform1f(self._u(self.p_pin, "grow"), grow)
        GL.glUniform1f(self._u(self.p_pin, "minPx"), min_px)
        GL.glUniform1f(self._u(self.p_pin, "extraPx"), extra)
        GL.glUniform1f(self._u(self.p_pin, "ring"), ring)
        GL.glUniform1f(self._u(self.p_pin, "border"), border)
        GL.glBindVertexArray(self.vao[name])
        GL.glDrawArraysInstanced(GL.GL_TRIANGLE_STRIP, 0, 4, n)


def text_shadow(c):
    """Sombra do texto: clara para texto escuro, escura para texto claro."""
    lum = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255
    return [1.0, 1.0, 1.0, 0.35] if lum < 0.45 else [0.0, 0.0, 0.0, 0.65]


def _convex_hull(pts):
    pts = np.unique(np.round(pts, 3), axis=0)
    if len(pts) < 3:
        return [tuple(p) for p in pts]
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(tuple(p))
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(tuple(p))
    return lower[:-1] + upper[:-1]
