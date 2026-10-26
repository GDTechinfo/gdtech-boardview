"""Imprime as notas do release do GitHub (a versão e a tabela de downloads)."""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ver = re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                open(os.path.join(ROOT, "gdboard", "main.py"), encoding="utf-8").read()).group(1)
print(f"""GDTech BoardView {ver}

### Downloads

| Arquivo | Para quem |
|---|---|
| `GDTech_BoardView_Setup_*.exe` | **Windows** — instalador (atalhos, "Abrir com" dos boardviews) |
| `GDTech_BoardView_*_portatil.exe` | Windows — roda direto, sem instalar |
| `GDTech_BoardView-*-x86_64.AppImage` | **Linux** — dê permissão de execução e abra (Mint, Ubuntu, Debian, Fedora...) |
| `GDTech_BoardView_*.zip` | Windows e Linux — instala com um Python próprio (INSTALAR.bat / INSTALAR_LINUX.sh) e traz o manual em PDF |
""")
