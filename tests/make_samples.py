"""Gera arquivos de exemplo em todos os formatos suportados (para teste)."""
import os
import random
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gdboard import parsers  # noqa

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "samples")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- placa de referência (mils)
OUTLINE = [(0, 0), (3000, 0), (3000, 2000), (0, 2000), (0, 0)]
# nome, lado ('T','B','O'), smd, pinos [(x, y, net, nome)]
PARTS = [
    ("U1", "T", True, [(1000 + 50 * i, 1000, f"N{i}", f"P{i}") for i in range(4)] +
                       [(1150 - 50 * i, 1200, ("GND" if i == 0 else "VCC" if i == 1 else f"M{i}"), f"Q{i}")
                        for i in range(4)]),
    ("R1", "T", True, [(500, 500, "N0", "1"), (560, 500, "VCC", "2")]),
    ("C1", "B", True, [(2000, 1500, "VCC", "1"), (2000, 1560, "GND", "2")]),
    ("C2", "B", True, [(2200, 1500, "N1", "1"), (2260, 1560, "GND", "2")]),
]
NAILS = [(1, 1500, 300, "T", "VCC"), (2, 1600, 300, "B", "GND")]
NETS = sorted({p[2] for _, _, _, pins in PARTS for p in pins})


def w(name, data, mode="wb"):
    path = os.path.join(OUT, name)
    with open(path, mode) as f:
        f.write(data)
    return path


def all_pins():
    for pi, (name, side, smd, pins) in enumerate(PARTS):
        for x, y, net, pn in pins:
            yield pi, name, side, x, y, net, pn


# ---------------------------------------------------------------- BRD
def make_brd(encoded=False):
    L = ["str_length:", "1 2 3 4", "var_data:",
         f"{len(OUTLINE)} {len(PARTS)} {sum(len(p[3]) for p in PARTS)} {len(NAILS)}", "Format:"]
    L += [f"{x} {y}" for x, y in OUTLINE]
    L.append("Parts:")
    end = 0
    for name, side, smd, pins in PARTS:
        end += len(pins)
        t = (5 if smd else 1) if side == "T" else (10 if smd else 2)
        L.append(f"{name} {t} {end}")
    L.append("Pins:")
    for pi, name, side, x, y, net, pn in all_pins():
        L.append(f"{x} {y} -99 {pi + 1} {net}")
    L.append("Nails:")
    for pr, x, y, s, net in NAILS:
        L.append(f"{pr} {x} {y} {1 if s == 'T' else 2} {net}")
    data = ("\r\n".join(L) + "\r\n").encode()
    if encoded:
        # inverso de: d = ~rotl2(c)
        out = bytearray()
        for d in data:
            if d in (10, 13, 0):
                out.append(d)
                continue
            v = (~d) & 0xFF
            c = ((v >> 2) | (v << 6)) & 0xFF
            out.append(c)
        data = bytes(out)
        assert data[:4] == parsers.BRD_SIGNATURE, data[:4].hex()
        return w("encoded.brd", data)
    return w("plain.brd", data)


# ---------------------------------------------------------------- BRD2
def make_brd2():
    maxx, maxy = 3000, 2000
    netid = {n: i + 1 for i, n in enumerate(NETS)}
    L = [f"BRDOUT: {len(OUTLINE)} {maxx} {maxy}"] + [f"{x} {y}" for x, y in OUTLINE]
    L += ["", f"NETS: {len(NETS)}"] + [f"{netid[n]} {n}" for n in NETS]
    L += ["", f"PARTS: {len(PARTS)}"]
    start = 0
    for name, side, smd, pins in PARTS:
        xs = [p[0] for p in pins]; ys = [p[1] for p in pins]
        sd = 1 if side == "T" else 2
        y1, y2 = (min(ys), max(ys)) if side == "T" else (maxy - min(ys), maxy - max(ys))
        L.append(f"{name} {min(xs)} {y1} {max(xs)} {y2} {start} {sd}")
        start += len(pins)
    L += ["", f"PINS: {start}"]
    for pi, name, side, x, y, net, pn in all_pins():
        sd = 1 if side == "T" else 2
        yy = y if side == "T" else maxy - y
        L.append(f"{x} {yy} {netid[net]} {sd}")
    L += ["", f"NAILS: {len(NAILS)}"]
    for pr, x, y, s, net in NAILS:
        yy = y if s == "T" else maxy - y
        L.append(f"{pr} {x} {yy} {netid[net]} {1 if s == 'T' else 2}")
    return w("brd2.brd", ("\n".join(L) + "\n").encode())


# ---------------------------------------------------------------- texto estilo ASC/BDV
def asc_sections():
    fmt = ["<<format.asc>>"] + [f"hdr{i}" for i in range(8)] + [f"{x / 1000:.3f} {y / 1000:.3f}" for x, y in OUTLINE]
    pins = ["<<pins.asc>>"] + [f"hdr{i}" for i in range(8)]
    pid = 0
    for name, side, smd, pl in PARTS:
        pins.append(f"Part {name} ({'T' if side == 'T' else 'B'})")
        for x, y, net, pn in pl:
            pid += 1
            pins.append(f"{pid}  {pn}  {x / 1000:.3f}  {y / 1000:.3f}  1  {net}  {pid}")
    nails = ["<<nails.asc>>"] + [f"hdr{i}" for i in range(7)]
    for pr, x, y, s, net in NAILS:
        nails.append(f"${pr} {x / 1000:.3f} {y / 1000:.3f} 1 A1 ({s}) {pr} {net}")
    return fmt, pins, nails


def make_bdv():
    fmt, pins, nails = asc_sections()
    text = "\r\n".join(fmt + pins + nails) + "\r\n"
    plain = text.encode()
    # codifica: e = (count - d) & 0xFF (mesma operação da decodificação)
    out = bytearray()
    count = 0xA0
    for i, d in enumerate(plain):
        if d == 13 and i + 1 < len(plain) and plain[i + 1] == 10:
            count += 1
        if d in (10, 13, 0):
            out.append(d)
        else:
            e = (count - d) & 0xFF
            assert e not in (10, 13, 0)
            out.append(e)
        if count > 285:
            count = 159
    data = bytes(out)
    assert b"dd:1.3?,r?-=bb" in data
    w("plain.bdv", plain)
    return w("encoded.bdv", data)


def make_asc():
    d = os.path.join(OUT, "asc")
    os.makedirs(d, exist_ok=True)
    fmt, pins, nails = asc_sections()
    # formato ASC: 1ª linha + 7 ignoradas
    for nm, sec in (("format.asc", fmt), ("pins.asc", pins), ("nails.asc", nails)):
        sec = [sec[0]] + sec[2:]   # remove 1 linha de cabeçalho (ASC pula 7, não 8)
        if nm == "nails.asc":
            sec = [sec[0]] + sec[1:]
        with open(os.path.join(d, nm), "w", newline="\r\n") as f:
            f.write("\n".join(sec) + "\n")
    return os.path.join(d, "pins.asc")


# ---------------------------------------------------------------- BVR
def make_bvr():
    L = ["BVRAW_FORMAT_1", "<<Layout>>", "x,y"]
    L += [f"{x / 1000:.3f},{y / 1000:.3f}" for x, y in OUTLINE]
    L += ["<<Pin>>", "header"]
    pid = 0
    for name, side, smd, pl in PARTS:
        for x, y, net, pn in pl:
            pid += 1
            L.append(f"{name}\t({'T' if side == 'T' else 'B'})\t{pid}\t{pn}\t{x / 1000:.3f}\t{y / 1000:.3f}\t1\t{net}")
    L += ["<<Nail>>", "header"]
    for pr, x, y, s, net in NAILS:
        L.append(f"T{pr}\t{x / 1000:.3f}\t{y / 1000:.3f}\t1\tA1\t({s})\t{pr}\t{net}")
    return w("v1.bvr", ("\r\n".join(L) + "\r\n").encode())


def make_bvr3():
    L = ["BVRAW_FORMAT_3"]
    for name, side, smd, pl in PARTS:
        ox, oy = pl[0][0], pl[0][1]
        L += [f"PART_NAME {name}", f"PART_SIDE {side}", f"PART_ORIGIN {ox} {oy}",
              f"PART_MOUNT {'SMD' if smd else 'TH'}"]
        for i, (x, y, net, pn) in enumerate(pl):
            L += [f"PIN_ID {i}", f"PIN_NUMBER {i + 1}", f"PIN_NAME {pn}", f"PIN_SIDE {side}",
                  f"PIN_ORIGIN {x - ox} {y - oy}", "PIN_RADIUS 12", f"PIN_NET {net}", "PIN_END"]
        L.append("PART_END")
    segs = " ".join(f"{a[0]} {a[1]} {b[0]} {b[1]}" for a, b in zip(OUTLINE, OUTLINE[1:]))
    L.append(f"OUTLINE_SEGMENTED {segs}")
    return w("v3.bvr", ("\n".join(L) + "\n").encode())


# ---------------------------------------------------------------- CAD
def make_cad():
    L = ["###Panel Added", "header"]
    for name, side, smd, pl in PARTS:
        L.append(f"COMP {name} 1 a b 0 0 {1 if side == 'T' else 2} z")
    for name, side, smd, pl in PARTS:
        for i, (x, y, net, pn) in enumerate(pl):
            L.append(f"C_PIN {name}-{i + 1} {x / 1000:.4f} {y / 1000:.4f} 0 0 0 x /{net}")
    L.append("NET /VCC")
    L.append("N_VIA 1.5 0.3 s 1 0")
    return w("panel.cad", ("\r\n".join(L) + "\r\n").encode())


# ---------------------------------------------------------------- CST
def make_cst():
    b = bytearray()
    b += struct.pack("<h", len(PARTS))
    b += b"\x00\x00\x00\x00"
    sec = b"CDevice"
    b += struct.pack("<h", len(sec)) + sec
    for name, side, smd, pl in PARTS:
        nb = name.encode()
        b += bytes([len(nb)]) + nb
        b += b"\x00" * 4
        b += bytes([0x0C if side == "T" else 0x01])
        b += b"\x00" * 6
    # o leitor volta 2 bytes: os 2 últimos dos 6 "desconhecidos" são o nº de redes
    b[-2:] = struct.pack("<h", len(NETS))
    for n in NETS:
        nb = n.encode()
        b += bytes([len(nb)]) + nb
    b += b"\x00" * 8
    npins = sum(len(p[3]) for p in PARTS)
    # cabeçalho CPad: 8 bytes antes de 'CPad' começa o nº de pinos (short) + 10 bytes de cabeçalho
    hdr = struct.pack("<h", npins) + b"\x00\x00\x00\x00" + b"\x00\x00" + b"CPad"
    b += hdr
    for pi, name, side, x, y, net, pn in all_pins():
        b += struct.pack("<hhhhhh", pi, 0, NETS.index(net), x, y, 0) + b"\x00" * 4
    return w("board.cst", bytes(b))


# ---------------------------------------------------------------- FZ
def fz_content():
    L = ["A!REFDES!COMP_INSERTION_CODE!SYM_NAME!SYM_MIRROR!SYM_ROTATE!"]
    for name, side, smd, pl in PARTS:
        L.append(f"S!{name}!X!SYM!{'YES' if side == 'B' else 'NO'}!0!")
    L.append("A!NET_NAME!REFDES!PIN_NUMBER!PIN_NAME!PIN_X!PIN_Y!TEST_POINT!RADIUS!")
    for pi, name, side, x, y, net, pn in all_pins():
        L.append(f"S!{net}!{name}!{pn}!{pn}!{x}!{y}!!1200!")
    L.append("A!TESTVIA!NET!REFDES!PIN!NAME!X!Y!SIDE!RADIUS!")
    for pr, x, y, s, net in NAILS:
        L.append(f"S!Y!{net}!TP{pr}!0!x!{x}!{y}!{s}!10!")
    content = ("\r\n".join(L) + "\r\n").encode()
    descr = ("BOARD\r\nPARTNUMBER\tDESC\tQTY\tLOC\tPN2\r\n"
             "123\tIC CONTROLLER XYZ\t1\tU1\tABC\r\n"
             "456\tRES 10K 0402\t1\tR1\tDEF\r\n").encode()
    cz = zlib.compress(content)
    dz = zlib.compress(descr)
    return b"\x00\x00\x00\x00" + cz + dz + struct.pack("<I", len(dz) + 8)


def make_key(parity):
    rnd = random.Random(42)
    key = []
    for i in range(44):
        while True:
            v = rnd.getrandbits(32)
            t = v
            for sh in (16, 8, 4, 2, 1):
                t ^= t >> sh
            if ((~t) & 1) == parity[i]:
                key.append(v)
                break
    return key


def fz_encrypt(data: bytes, key) -> bytes:
    M = 0xFFFFFFFF

    def rotl(a, s):
        s &= 31
        return ((a << s) | (a >> ((32 - s) & 31))) & M

    out = bytearray()
    ib = [0] * 16
    for d in data:
        A = ib[0] | ib[1] << 8 | ib[2] << 16 | ib[3] << 24
        B = ib[4] | ib[5] << 8 | ib[6] << 16 | ib[7] << 24
        C = ib[8] | ib[9] << 8 | ib[10] << 16 | ib[11] << 24
        D = ib[12] | ib[13] << 8 | ib[14] << 16 | ib[15] << 24
        B = (B + key[0]) & M
        D = (D + key[1]) & M
        for i in range(1, 21):
            t = rotl((B * (2 * B + 1)) & M, 5)
            u = rotl((D * (2 * D + 1)) & M, 5)
            A = (rotl(A ^ t, u) + key[2 * i]) & M
            C = (rotl(C ^ u, t) + key[2 * i + 1]) & M
            A, B, C, D = B, C, D, A
        A = (A + key[42]) & M
        c = d ^ (A & 0xFF)
        out.append(c)
        ib = ib[1:] + [c]
    return bytes(out)


def make_fz():
    plain = fz_content()
    w("plain.fz", plain)
    key = make_key(parsers.FZ_PARITY)
    enc = fz_encrypt(plain, key)
    w("encrypted.fz", enc)
    w("fz_key.txt", ", ".join(f"0x{k:08x}" for k in key).encode())
    return key


# ---------------------------------------------------------------- XZZ
def make_xzz(xor=0):
    rnd = random.Random(7)
    while True:
        key = rnd.getrandbits(64)
        if parsers.check_xzz_key(key):
            break
    des = parsers.des_ecb(key)
    S = parsers.XZZ_SCALE
    u = lambda v: struct.pack("<I", v)

    def enc(block):
        pad = (-len(block)) % 8
        return des.encrypt(block + b"\0" * pad)[:len(block)] if pad == 0 else des.encrypt(block + b"\0" * pad)

    nets = {n: i + 1 for i, n in enumerate(NETS)}
    blocks = b""
    for a, b in zip(OUTLINE, OUTLINE[1:]):
        body = u(28) + u(a[0] * S) + u(a[1] * S) + u(b[0] * S) + u(b[1] * S) + u(S) + u(0)
        blocks += b"\x05" + u(len(body)) + body
    for name, side, smd, pl in PARTS:
        nm = name.encode()
        grp = b"GRP"
        sub06 = b"\x06" + b"\0" * 30 + u(len(nm)) + nm   # 0x06 + 30 bytes, nome em +31
        pins = b""
        for x, y, net, pn in pl:
            pnb = pn.encode()
            pb = u(0) + u(x * S) + u(y * S) + b"\0" * 8 + u(len(pnb)) + pnb + b"\0" * 32 + u(nets[net])
            pins += b"\x09" + u(len(pb)) + pb
        body = b"\0" * 18 + u(len(grp)) + grp + sub06 + pins
        plain = u(len(body)) + body
        pad = (-len(plain)) % 8
        plain += b"\0" * pad
        cipher = des.encrypt(plain)
        blocks += b"\x07" + u(len(cipher)) + cipher
    # test pad
    tp = b"TP9"
    body = u(1) + u(1500 * S) + u(300 * S) + b"\0" * 8 + u(len(tp)) + tp + b"\0" * 4 + u(nets["VCC"])
    blocks += b"\x09" + u(len(body)) + body

    netblk = b""
    for n, i in nets.items():
        nb = n.encode()
        netblk += u(8 + len(nb)) + u(i) + nb

    hdr = bytearray(b"XZZPCB V1.0" + b"\0" * (0x40 - 11))
    main_off = 0x40 - 0x20
    main = u(len(blocks)) + blocks
    net_off = 0x40 + len(main) - 0x20
    hdr[0x20:0x24] = u(main_off)
    hdr[0x28:0x2C] = u(net_off)
    data = bytearray(bytes(hdr) + main + u(len(netblk)) + netblk + b"v6v6555v6v6")
    if xor:
        for i in range(len(data) - 11):
            data[i] ^= xor
        data[0x10] = xor  # posição 0x10 guarda a chave XOR (era 0 antes do XOR)
    name = "xor.pcb" if xor else "board.pcb"
    w(name, bytes(data))
    w("xzz_key.txt", f"0x{key:016X}".encode())
    return key


if __name__ == "__main__":
    make_brd(False); make_brd(True); make_brd2(); make_bdv(); make_asc(); make_bvr(); make_bvr3()
    make_cad(); make_cst(); make_fz(); make_xzz(0); make_xzz(0x5A)
    print("ok", OUT)
