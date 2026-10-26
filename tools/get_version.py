"""Imprime a versão do programa (lida do código, sem abrir a interface)."""
import os
import re

src = open(os.path.join(os.path.dirname(__file__), "..", "gdboard", "main.py"), encoding="utf-8").read()
print(re.search(r'APP_VERSION\s*=\s*"([^"]+)"', src).group(1))
