"""Abre todos os boardviews de uma pasta e mostra o resultado (teste em lote)."""
import os, sys, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gdboard import parsers
from gdboard.board import Board

root = sys.argv[1]
exts = tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else tuple(parsers.SUPPORTED_EXTS)
ok = fail = 0
for dp, _, fs in os.walk(root):
    for f in sorted(fs):
        if not f.lower().endswith(exts):
            continue
        p = os.path.join(dp, f)
        t = time.time()
        try:
            raw = parsers.load_file(p)
            b = Board(raw, p)
            ok += 1
            print(f"OK   {raw.format_name:7s} {time.time()-t:5.1f}s  {b.stats():48s} {os.path.relpath(p, root)[:70]}")
        except Exception as e:
            fail += 1
            print(f"FAIL {type(e).__name__}: {str(e)[:90]}  {os.path.relpath(p, root)[:70]}")
print(f"\n{ok} OK, {fail} falhas")
