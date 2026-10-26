#!/usr/bin/env bash
# GDTech BoardView — instalação no Linux (Linux Mint / Ubuntu / Debian e parecidos).
# Copia o programa para ~/.local/share/gdtech-boardview, cria um Python próprio (venv) com as bibliotecas,
# põe o atalho no menu de aplicativos e abre o programa. Rodar de novo atualiza e conserta.
set -u

APP="GDTech BoardView"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/gdtech-boardview"
VENV="$DEST/venv"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"      # pasta "programa"

ok()   { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
info() { printf '\033[1;36m» %s\033[0m\n' "$*"; }
erro() { printf '\033[1;31m✖ %s\033[0m\n' "$*"; }
fim_erro() {
    erro "$*"
    echo
    read -r -p "Aperte Enter para fechar..." _ || true
    exit 1
}

echo
echo "=============================================="
echo "   $APP — instalação no Linux"
echo "   criado por GDTech Informática"
echo "=============================================="
echo

[ -f "$SRC/gdtech_boardview.py" ] || fim_erro "Não achei o programa em $SRC. Extraia o .zip inteiro antes."

# ---------------------------------------------------------------- pacotes do sistema
# python3-venv: para o Python próprio do programa; libxcb-cursor0 e cia.: o Qt 6 precisa para abrir janelas.
info "Conferindo o que o sistema precisa..."
PKGS=(python3 python3-venv python3-pip libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1
      libxcb-shape0 libxcb-xinerama0 libxcb-randr0 libxcb-render-util0 libxcb-image0 libegl1 libgl1
      libfontconfig1 libdbus-1-3)
FALTA=()
if command -v dpkg-query >/dev/null 2>&1; then
    for p in "${PKGS[@]}"; do
        dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || FALTA+=("$p")
    done
    # libgl1 / libegl1 podem ter outro nome em versões mais novas: não trava se só eles "faltarem"
    if [ ${#FALTA[@]} -gt 0 ]; then
        info "Instalando: ${FALTA[*]}"
        echo "   (vai pedir a sua senha — é a mesma de entrar no Linux)"
        sudo apt-get update -qq || true
        if ! sudo apt-get install -y "${FALTA[@]}"; then
            # tenta um por um, para não perder tudo por causa de um nome que não existe nesta versão
            for p in "${FALTA[@]}"; do sudo apt-get install -y "$p" >/dev/null 2>&1 || erro "não deu para instalar $p (seguindo assim mesmo)"; done
        fi
    fi
    ok "Pacotes do sistema"
else
    erro "Este Linux não usa apt: instale python3, python3-venv e libxcb-cursor0 pelo gerenciador dele."
fi

command -v python3 >/dev/null 2>&1 || fim_erro "Python 3 não encontrado."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || fim_erro "Precisa do Python 3.9 ou mais novo (este é $(python3 -V))."

# ---------------------------------------------------------------- copia o programa
info "Copiando o programa para $DEST ..."
mkdir -p "$DEST/programa" || fim_erro "Não deu para criar $DEST"
if [ "$SRC" != "$DEST/programa" ]; then
    rm -rf "$DEST/programa/gdboard"
    cp -r "$SRC/gdboard" "$SRC/gdtech_boardview.py" "$SRC/gdtech_boardview.png" "$DEST/programa/" \
        || fim_erro "Falha ao copiar os arquivos."
    cp "$SRC/instalador/instalar_linux.sh" "$SRC/instalador/requisitos.txt" "$DEST/" 2>/dev/null || true
    [ -f "$SRC/../Manual do GDTech BoardView.pdf" ] && cp "$SRC/../Manual do GDTech BoardView.pdf" "$DEST/"
fi
find "$DEST/programa" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null
ok "Programa copiado"

# ---------------------------------------------------------------- Python próprio + bibliotecas
info "Preparando o Python do programa (na primeira vez baixa ~150 MB, pode demorar)..."
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV" || fim_erro "Não deu para criar o ambiente Python (falta o pacote python3-venv?)."
fi
"$VENV/bin/python" -m pip install --upgrade pip -q --disable-pip-version-check || true
"$VENV/bin/python" -m pip install --upgrade -q --disable-pip-version-check -r "$SRC/instalador/requisitos.txt" \
    || fim_erro "Falha ao baixar as bibliotecas. Confira a internet e rode de novo."
"$VENV/bin/python" - <<'PY' || fim_erro "As bibliotecas não carregaram direito."
import PySide6.QtWidgets, numpy, pypdfium2, OpenGL, Crypto  # noqa
print("  bibliotecas ok")
PY
ok "Python e bibliotecas"

# ---------------------------------------------------------------- atalhos
info "Criando os atalhos..."
BIN="$HOME/.local/bin"
mkdir -p "$BIN" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
cat > "$BIN/gdtech-boardview" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python" "$DEST/programa/gdtech_boardview.py" "\$@"
EOF
chmod +x "$BIN/gdtech-boardview"
cp "$DEST/programa/gdtech_boardview.png" "$HOME/.local/share/icons/hicolor/256x256/apps/gdtech-boardview.png"

DESKTOP="$HOME/.local/share/applications/gdtech-boardview.desktop"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=$APP
Comment=Visualizador de boardview — GDTech Informática
Exec="$BIN/gdtech-boardview" %F
Icon=gdtech-boardview
Terminal=false
Categories=Electronics;Engineering;Graphics;
StartupWMClass=gdtech-boardview
EOF
chmod +x "$DESKTOP"
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true

# atalho na Área de Trabalho (a pasta muda de nome conforme o idioma)
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
    cp "$DESKTOP" "$DESK/gdtech-boardview.desktop"
    chmod +x "$DESK/gdtech-boardview.desktop"
    # o Mint (Nemo) só abre atalho da Área de Trabalho marcado como confiável
    gio set "$DESK/gdtech-boardview.desktop" metadata::trusted true >/dev/null 2>&1 || true
fi

# desinstalador
cat > "$DEST/desinstalar.sh" <<EOF
#!/usr/bin/env bash
rm -f "$BIN/gdtech-boardview" "$DESKTOP" "$DESK/gdtech-boardview.desktop" \\
      "$HOME/.local/share/icons/hicolor/256x256/apps/gdtech-boardview.png"
rm -rf "$DEST"
echo "GDTech BoardView removido (suas configurações e notas continuam guardadas, caso instale de novo)."
EOF
chmod +x "$DEST/desinstalar.sh"
ok "Atalho no menu de aplicativos e na Área de Trabalho"

echo
ok "Pronto! Abrindo o $APP..."
echo "   Da próxima vez, abra pelo menu (procure \"GDTech\") ou pelo atalho na Área de Trabalho."
echo "   Para desinstalar: $DEST/desinstalar.sh"
echo
nohup "$BIN/gdtech-boardview" >/dev/null 2>&1 &
sleep 2
