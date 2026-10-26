"""Compara a saída dos leitores Python com os leitores C++ originais do OpenBoardView."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gdboard import parsers  # noqa

HARNESS = sys.argv[1]
S = sys.argv[2]
SIDE = {parsers.BOTH: 0, parsers.BOTTOM: 1, parsers.TOP: 2}   # enum do OBV: Both, Bottom, Top


def py_dump(raw):
    out = [f"valid {int(raw.valid)}"]
    out += [f"F {int(x)} {int(y)}" for x, y in raw.format]
    out += [f"S {int(a[0])} {int(a[1])} {int(b[0])} {int(b[1])}" for a, b in raw.outline_segments]
    out += [f"P {p.name} {SIDE[p.side]} {0 if p.smd else 1} {p.end_of_pins}" for p in raw.parts]
    for p in raw.pins:
        out.append(f"N {int(p.x)} {int(p.y)} {p.probe} {p.part} {SIDE[p.side]} {p.net} "
                   f"{p.snum or '-'} {p.name or '-'}")
    return out


def c_dump(kind, path, *extra):
    r = subprocess.run([HARNESS, kind, path, *extra], capture_output=True, text=True, errors="replace")
    return r.stdout.strip().splitlines()


def check(kind, fname, pyfunc, *extra, ignore_names=False, ignore_end=False):
    path = os.path.join(S, fname)
    a = c_dump(kind, path, *extra)
    b = py_dump(pyfunc(path))
    def norm(lines):
        res = []
        for l in lines:
            t = l.split(" ")
            if t[0] == "N" and ignore_names:
                t = t[:7]
            if t[0] == "P" and ignore_end:
                t = t[:4]
            res.append(" ".join(t))
        return res
    a, b = norm(a), norm(b)
    ok = a == b
    print(f"{'OK ' if ok else 'DIF'} {kind:5s} {fname}  ({len(a)} linhas)")
    if not ok:
        import difflib
        for l in list(difflib.unified_diff(a, b, "obv", "py", lineterm=""))[:30]:
            print("    " + l)
    return ok


rb = lambda p: open(p, "rb").read()
fzkey = open(os.path.join(S, "fz_key.txt")).read().replace(",", " ").split()
xzzkey = open(os.path.join(S, "xzz_key.txt")).read().strip()
res = [
    check("brd", "plain.brd", lambda p: parsers.parse_brd(rb(p))),
    check("brd", "encoded.brd", lambda p: parsers.parse_brd(rb(p))),
    check("brd2", "brd2.brd", lambda p: parsers.parse_brd2(rb(p))),
    # o OBV não guarda o nome do pino no BDV (nós também não)
    check("bdv", "encoded.bdv", lambda p: parsers.parse_bdv(rb(p))),
    check("asc", "asc/pins.asc", parsers.parse_asc, ignore_names=True),
    check("bvr", "v1.bvr", lambda p: parsers.parse_bvr(rb(p))),
    check("bvr3", "v3.bvr", lambda p: parsers.parse_bvr3(rb(p))),
    check("cad", "panel.cad", lambda p: parsers.parse_cad(rb(p))),
    check("cst", "board.cst", lambda p: parsers.parse_cst(rb(p))),
    check("fz", "plain.fz", lambda p: parsers.parse_fz(rb(p)), ignore_end=True),
    check("fz", "encrypted.fz", lambda p: parsers.parse_fz(rb(p), [int(k, 16) for k in fzkey]), *fzkey,
          ignore_end=True),
    check("xzz", "board.pcb", lambda p: parsers.parse_xzz(rb(p), int(xzzkey, 16)), xzzkey),
    check("xzz", "xor.pcb", lambda p: parsers.parse_xzz(rb(p), int(xzzkey, 16)), xzzkey),
]
print("TUDO OK" if all(res) else "HÁ DIFERENÇAS")
