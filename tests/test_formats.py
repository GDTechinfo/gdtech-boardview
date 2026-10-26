"""Testes dos leitores de formato (rodam no GitHub a cada envio: python -m pytest tests).

Usam os arquivos de exemplo de tests/samples (gerados por tests/make_samples.py — não são de nenhum
fabricante). Não precisam de interface gráfica."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from gdboard import parsers  # noqa: E402
from gdboard.board import Board  # noqa: E402

SAMPLES = os.path.join(ROOT, "tests", "samples")
FILES = ["plain.brd", "encoded.brd", "brd2.brd", "plain.bdv", "encoded.bdv", "v1.bvr", "panel.cad",
         "board.cst", "board.pcb", "plain.fz", os.path.join("asc", "format.asc")]


@pytest.mark.parametrize("name", FILES)
def test_sample_opens(name):
    path = os.path.join(SAMPLES, name)
    raw = parsers.load_file(path)
    assert raw.valid
    assert raw.parts, "nenhum componente"
    assert raw.pins, "nenhum pino"
    b = Board(raw, path)
    assert len(b.x) == len(raw.pins)
    x0, y0, x1, y1 = b.bounds
    assert x1 > x0 and y1 > y0


def _sample_key():
    txt = open(os.path.join(SAMPLES, "fz_key.txt"), encoding="utf-8").read()
    return [int(t, 16) for t in txt.replace(",", " ").split() if t.startswith("0x")]


def test_encrypted_fz_needs_its_key():
    path = os.path.join(SAMPLES, "encrypted.fz")
    with pytest.raises(parsers.FormatError):
        parsers.load_file(path)                       # chave embutida não serve para este arquivo
    raw = parsers.load_file(path, {"fz": _sample_key()})
    assert raw.parts and raw.pins


def test_builtin_keys_decode():
    for k in (parsers.BUILTIN_FZ_KEY, parsers.BUILTIN_CAE_KEY):
        assert len(k) == 44 and all(0 <= w <= 0xFFFFFFFF for w in k)
    assert parsers.check_fz_key(parsers.BUILTIN_CAE_KEY, parsers.CAE_PARITY)
    assert parsers.check_xzz_key(parsers.BUILTIN_XZZ_KEY)


def test_detects_by_content_not_extension(tmp_path):
    src = os.path.join(SAMPLES, "plain.brd")
    dst = tmp_path / "placa.bv"
    dst.write_bytes(open(src, "rb").read())
    raw = parsers.load_file(str(dst))
    assert raw.parts
