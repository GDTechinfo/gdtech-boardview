#!/usr/bin/env bash
# Gera o .AppImage (Linux x86_64). Rode depois de "python tools/build_exe.py" no Linux.
# Uso: tools/build_appimage.sh [versão]
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:-$(python3 tools/get_version.py)}"
[ -d dist/gdtech-boardview ] || { echo "Falta dist/gdtech-boardview (rode python tools/build_exe.py)"; exit 1; }

rm -rf AppDir
mkdir -p AppDir/usr/bin AppDir/usr/share/applications AppDir/usr/share/icons/hicolor/256x256/apps
cp -r dist/gdtech-boardview AppDir/usr/bin/
cp packaging/linux/AppRun AppDir/AppRun
chmod +x AppDir/AppRun
cp packaging/linux/gdtech-boardview.desktop AppDir/
cp packaging/linux/gdtech-boardview.desktop AppDir/usr/share/applications/
cp icon/gdtech_boardview_256.png AppDir/gdtech-boardview.png
cp icon/gdtech_boardview_256.png AppDir/usr/share/icons/hicolor/256x256/apps/gdtech-boardview.png
ln -sf gdtech-boardview.png AppDir/.DirIcon

TOOL=tools/appimagetool-x86_64.AppImage
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi
mkdir -p builds
OUT="builds/GDTech_BoardView-${VER}-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream AppDir "$OUT"
echo "Pronto: $OUT"
