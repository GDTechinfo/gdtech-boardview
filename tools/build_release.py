"""Gera o .zip com o instalador (INSTALAR.bat para Windows, INSTALAR_LINUX.sh para Linux) e o manual.

Uso:  python tools/build_release.py [pasta-de-saída]       (padrão: builds/)

Conteúdo do zip:
  GDTech_BoardView/
    INSTALAR.bat                 Windows: instala com um Python próprio (não precisa de administrador)
    INSTALAR_LINUX.sh            Linux (Mint, Ubuntu, Debian...)
    Manual do GDTech BoardView.pdf
    programa/                    o código do programa
"""
import os
import re
import stat
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VER = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                open(os.path.join(ROOT, "gdboard", "main.py"), encoding="utf-8").read()).group(1)
OUT_DIR = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "builds"))
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, f"GDTech_BoardView_{VER}.zip")

PROGRAM = ["gdboard", "gdtech_boardview.py", "gdtech_boardview.ico", "gdtech_boardview.png", "requirements.txt",
           "run.bat", "run.sh", "README.md", "LICENSE", "THIRD_PARTY.md", "instalador", "icon"]
SKIP_DIRS = {"__pycache__"}
LINUX_LAUNCHER = """#!/usr/bin/env bash
# GDTech BoardView - instalar no Linux (Mint, Ubuntu...). Dois cliques -> "Executar no terminal".
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" || exit 1
exec bash "programa/instalador/instalar_linux.sh"
"""


def add(z, src, arc):
    info = zipfile.ZipInfo.from_file(src, arc)
    mode = 0o755 if src.endswith(".sh") else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with open(src, "rb") as f:
        z.writestr(info, f.read())


with zipfile.ZipFile(OUT, "w") as z:
    base = "GDTech_BoardView"
    add(z, os.path.join(ROOT, "instalador", "INSTALAR.bat"), f"{base}/INSTALAR.bat")
    # o atalho do Linux é gerado aqui (um arquivo "INSTALAR_LINUX.sh" ao lado de "instalar_linux.sh" na mesma
    # pasta seria o mesmo arquivo no Windows, que não diferencia maiúsculas de minúsculas)
    info = zipfile.ZipInfo(f"{base}/INSTALAR_LINUX.sh", date_time=zipfile.ZipInfo.from_file(
        os.path.join(ROOT, "instalador", "instalar_linux.sh")).date_time)
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    z.writestr(info, LINUX_LAUNCHER)
    add(z, os.path.join(ROOT, "docs", "Manual do GDTech BoardView.pdf"), f"{base}/Manual do GDTech BoardView.pdf")
    for item in PROGRAM:
        p = os.path.join(ROOT, item)
        if os.path.isfile(p):
            add(z, p, f"{base}/programa/{item}")
            continue
        for d, dirs, files in os.walk(p):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for fn in files:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                src = os.path.join(d, fn)
                add(z, src, f"{base}/programa/{os.path.relpath(src, ROOT).replace(os.sep, '/')}")
print(OUT)
