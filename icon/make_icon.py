"""Ícone do GDTech BoardView: pixel art 32x32 no mesmo estilo dos ícones da fonte/multímetro
(contorno escuro, corpo com brilho e sombra, detalhes em âmbar/ciano/verde). Gera .ico (16-256), PNGs e prévias."""
import io
import struct
import base64
from PIL import Image, ImageDraw

T = (0, 0, 0, 0)
INK, BODY, HI, SH = "#0e1013", "#2c3138", "#4a525c", "#1a1d22"
AMB, YEL, CYA, RED, GRN = "#ffb300", "#ffd400", "#00e5ff", "#e53935", "#7CFF5B"
SCR, GRAY, LGRAY = "#05080b", "#8b929b", "#c9ced4"
PCB, PCBHI, PCBSH, TRC = "#1f6b45", "#2f8f5d", "#14472e", "#3fb877"     # placa verde e trilhas
CAP, RES = "#8a5a44", "#15171a"


def art():
    im = Image.new("RGBA", (32, 32), T)
    d = ImageDraw.Draw(im)
    R = lambda x0, y0, x1, y1, c: d.rectangle([x0, y0, x1, y1], fill=c)
    P = lambda x, y, c: d.point((x, y), fill=c)

    # ---------- placa ----------
    R(1, 3, 29, 27, INK); R(2, 2, 28, 28, INK)
    R(2, 3, 28, 27, PCB)
    R(2, 3, 28, 3, PCBHI); R(2, 27, 28, 27, PCBSH)                  # brilho e sombra
    for cx, cy in ((4, 5), (26, 5), (4, 25)):                        # furos de fixação
        P(cx, cy, SCR); P(cx - 1, cy, LGRAY); P(cx + 1, cy, LGRAY); P(cx, cy - 1, LGRAY); P(cx, cy + 1, LGRAY)
    # conector PCIe (dedos dourados) na borda de baixo
    R(5, 28, 19, 29, INK)
    for x in range(6, 19, 2):
        R(x, 28, x, 29, AMB)
    P(12, 28, INK); P(12, 29, INK)

    # ---------- trilhas ----------
    for y in (8, 10):
        R(4, y, 9, y, TRC)
    R(20, 9, 26, 9, TRC); R(26, 9, 26, 12, TRC)
    R(14, 19, 14, 24, TRC); R(16, 19, 16, 22, TRC); R(16, 22, 20, 22, TRC)
    # rede selecionada (amarela) saindo da GPU até um capacitor
    R(6, 15, 9, 15, YEL); R(6, 15, 6, 20, YEL); R(6, 20, 8, 20, YEL)

    # ---------- GPU no centro ----------
    R(9, 6, 20, 18, INK)
    R(10, 7, 19, 17, BODY)
    R(10, 7, 19, 7, HI); R(10, 17, 19, 17, SH)
    R(12, 9, 17, 15, "#3a4049"); R(12, 9, 17, 9, "#5b646f")           # die
    P(13, 11, CYA); P(14, 11, CYA); P(13, 12, CYA)                    # reflexo do die
    for x in range(11, 19, 2):                                        # bolinhas (BGA) na borda do chip
        P(x, 8, AMB); P(x, 16, AMB)

    # ---------- componentes ----------
    # capacitor ligado à rede amarela
    R(8, 20, 9, 22, YEL); R(8, 21, 9, 21, CAP)
    # capacitores e resistores espalhados
    for (x, y) in ((22, 13), (24, 13)):
        R(x, y, x, y + 2, GRAY); P(x, y + 1, CAP)
    for (x, y) in ((3, 12), (3, 17)):
        R(x, y, x + 2, y, GRAY); P(x + 1, y, RES)
    R(21, 4, 23, 4, GRAY); P(22, 4, RES)
    R(10, 22, 12, 22, GRAY); P(11, 22, CAP)
    R(10, 24, 12, 24, GRAY); P(11, 24, RES)

    # ---------- lupa (canto de baixo/direita) ----------
    # cabo
    for i in range(4):
        R(26 + i, 27 + i, 27 + i, 27 + i, INK)
        P(27 + i, 27 + i, AMB)
    P(30, 31, INK); P(31, 30, INK)
    # aro
    d.ellipse([16, 16, 28, 28], fill=INK)
    d.ellipse([17, 17, 27, 27], fill=LGRAY)
    d.ellipse([18, 18, 26, 26], fill=GRAY)
    # lente (vê um pedaço da placa ampliado: pino em âmbar e trilha amarela)
    d.ellipse([19, 19, 25, 25], fill="#0d5f73")
    R(20, 21, 21, 23, AMB); R(23, 21, 24, 23, AMB)                    # dois pads...
    R(21, 22, 23, 22, YEL)                                            # ...ligados pela rede selecionada
    P(20, 20, "#bff6ff"); P(21, 19, "#bff6ff")                        # brilho da lente
    return im


base = art()
base.save("base32.png")


def scaled(n):
    if n % 32 == 0:
        return base.resize((n, n), Image.NEAREST)
    k = 12 if n in (24, 48) else 8
    return base.resize((32 * k, 32 * k), Image.NEAREST).resize((n, n), Image.BOX)


sizes = [16, 24, 32, 48, 64, 128, 256]
pngs = []
for n in sizes:
    b = io.BytesIO(); scaled(n).save(b, format="PNG", optimize=True); pngs.append(b.getvalue())
head = struct.pack("<HHH", 0, 1, len(sizes)); off = 6 + 16 * len(sizes); dirs, blob = b"", b""
for n, data in zip(sizes, pngs):
    dirs += struct.pack("<BBBBHHII", 0 if n >= 256 else n, 0 if n >= 256 else n, 0, 0, 1, 32, len(data), off + len(blob))
    blob += data
open("gdtech_boardview.ico", "wb").write(head + dirs + blob)
scaled(256).save("gdtech_boardview_256.png")
b = io.BytesIO(); scaled(64).save(b, format="PNG", optimize=True)
open("icon64.b64", "w").write(base64.b64encode(b.getvalue()).decode())
for name, bg in (("prev_dark", "#1b1b1b"), ("prev_light", "#e8e4d8")):
    big = Image.new("RGBA", (32 * 12, 32 * 12), bg); big.alpha_composite(base.resize((32 * 12, 32 * 12), Image.NEAREST)); big.save(name + ".png")
    W = 256 + 128 + 64 + 48 + 32 + 24 + 16 + 7 * 14
    sheet = Image.new("RGBA", (W, 280), bg); x0 = 10
    for n in (256, 128, 64, 48, 32, 24, 16):
        sheet.alpha_composite(scaled(n), (x0, 10)); x0 += n + 14
    sheet.save("sizes_" + name + ".png")
print("ok")
