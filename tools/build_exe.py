"""Gera o executável com o PyInstaller (o mesmo script no Windows e no Linux).

Uso:  python tools/build_exe.py            -> Windows: dist/GDTech_BoardView.exe (um arquivo só)
                                              Linux:   dist/gdtech-boardview/ (pasta, usada pelo AppImage)
Precisa:  pip install -r requirements.txt pyinstaller
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WIN = sys.platform == "win32"

hidden = ["OpenGL.arrays.numpymodule", "OpenGL.arrays.ctypesarrays", "OpenGL.arrays.numbers",
          "OpenGL.arrays.strings", "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"]
hidden += ["OpenGL.platform.win32"] if WIN else ["OpenGL.platform.glx", "OpenGL.platform.egl"]

cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
       "--collect-all", "pypdfium2", "--collect-all", "pypdfium2_raw",
       # partes do Qt que o programa não usa: executável bem menor
       "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
       "--exclude-module", "PySide6.Qt3DCore", "--exclude-module", "PySide6.QtQuick",
       "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtMultimedia",
       "--exclude-module", "tkinter"]
for h in hidden:
    cmd += ["--hidden-import", h]
if WIN:
    cmd += ["--onefile", "--name", "GDTech_BoardView", "--icon", os.path.join(ROOT, "gdtech_boardview.ico")]
else:
    cmd += ["--onedir", "--name", "gdtech-boardview"]
cmd += ["--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"), os.path.join(ROOT, "gdtech_boardview.py")]
print(" ".join(cmd))
sys.exit(subprocess.call(cmd, cwd=ROOT))
