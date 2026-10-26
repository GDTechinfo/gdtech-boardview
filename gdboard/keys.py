"""Atalhos configuráveis. Arquivo .keybindings em formato INI
([Keybindings], Ação=tecla e Ação2=tecla alternativa)."""
from __future__ import annotations

# id, rótulo, padrão principal, padrão alternativo
ACTIONS = [
    ("OpenFile", "Abrir arquivo", "Ctrl+O", ""),
    ("Find", "Buscar componente (caixa Part)", "Ctrl+F", ""),
    ("FindNet", "Buscar rede (caixa Net)", "Ctrl+Shift+F", ""),
    ("ZoomIn", "Aproximar", "+", "="),
    ("ZoomOut", "Afastar", "-", ""),
    ("PanUp", "Mover para cima", "W", "Up"),
    ("PanDown", "Mover para baixo", "S", "Down"),
    ("PanLeft", "Mover para a esquerda", "A", "Left"),
    ("PanRight", "Mover para a direita", "D", "Right"),
    ("RotateClockwise", "Girar 90° (horário)", "R", ""),
    ("RotateCounterclockwise", "Girar 90° (anti-horário)", "Shift+R", ","),
    ("FitBoardToScreen", "Ajustar placa à tela", "F", "Home"),
    ("ZoomToSelection", "Zoom na seleção", "Z", ""),
    ("FlipBoardSide", "Virar lado (Top/Bottom)", "Space", "Middle Mouse Button"),
    ("FlipBoardSideOverXAxis", "Virar lado pelo eixo X", "Ctrl+Space", ""),
    ("MirrorBoardHorizontally", "Espelhar horizontalmente", "M", ""),
    ("MirrorBoardVertically", "Espelhar verticalmente", "H", ""),
    ("ToggleNetweb", "Linhas da rede selecionada", "N", ""),
    ("TogglePinLabels", "Mostrar números dos pinos", "P", ""),
    ("TogglePartLabels", "Mostrar nomes dos componentes", "L", ""),
    ("ToggleNetNames", "Mostrar nomes das redes nos pinos", "Shift+N", ""),
    ("ToggleFPS", "Mostrar FPS", "F11", ""),
    ("ToggleHoverInfo", "Caixa de informações ao passar o mouse", "I", ""),
    ("ToggleDualView", "Vista dupla (Top + Bottom espelhado)", "F2", ""),
    ("ToggleNetwebCross", "Vista dupla: linhas da rede ligando as duas placas", "Ctrl+N", ""),
    ("ToggleLayerMode", "Modo camadas (trilhas de cobre)", "F3", ""),
    ("LayerNext", "Camadas: mostrar a próxima", "PgDown", ""),
    ("LayerPrev", "Camadas: mostrar a anterior", "PgUp", ""),
    ("OpenSchematic", "Esquema elétrico (PDF)", "F4", ""),
    ("CloseTab", "Fechar aba", "Ctrl+W", ""),
    ("NextTab", "Próxima aba", "Ctrl+Tab", ""),
    ("PrevTab", "Aba anterior", "Ctrl+Shift+Tab", ""),
    ("ShortUseNet", "Caçar curto na rede selecionada", "Ctrl+K", ""),
    ("FollowNet", "Seguir a rede selecionada", "Ctrl+L", ""),
    ("ShortNext", "Caçar curto: próximo capacitor", "]", ""),
    ("ShortPrev", "Caçar curto: capacitor anterior", "[", ""),
    ("ShortCheckNext", "Caçar curto: verificado + próximo", "V", ""),
    ("DonorSearch", "Procurar a peça selecionada em outras placas (sucata)", "Ctrl+Shift+P", ""),
    ("ToggleTools", "Mostrar painel de ferramentas", "F6", ""),
    ("NextNetPin", "Próximo pino da mesma rede", "Tab", ""),
    ("PrevNetPin", "Pino anterior da mesma rede", "Shift+Tab", ""),
    ("ToggleMinimap", "Mostrar minimapa", "F7", ""),
    ("ToggleLoose", "Destacar pinos soltos e componentes sem rede", "F8", ""),
    ("ToggleMech", "Mostrar peças mecânicas (reforço, furos de fixação, suportes)", "Shift+M", ""),
    ("ToggleGndSubtle", "GND discreto (só o contorno)", "", ""),
    ("NewTab", "Ir para a aba Início", "Ctrl+T", ""),
    ("ClearAllSelections", "Limpar seleção", "Esc", ""),
]
ACTION_IDS = [a[0] for a in ACTIONS]
LABELS = {a[0]: a[1] for a in ACTIONS}

MOUSE_NAMES = ["Left Mouse Button", "Middle Mouse Button", "Right Mouse Button",
               "Mouse Button 4", "Mouse Button 5"]

# nomes alternativos de teclas -> nomes do Qt
_ALIASES = {"Spacebar": "Space", "Escape": "Esc", "Plus": "+", "Minus": "-"}


def normalize(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if s in MOUSE_NAMES:
        return s
    parts = [p.strip() for p in s.replace(" + ", "+").split("+") if p.strip()]
    if s.endswith("+") and (not parts or parts[-1] != "+"):
        parts.append("+")
    parts = [_ALIASES.get(p, p) for p in parts]
    return "+".join(parts)


def defaults() -> dict:
    return {a[0]: [a[2], a[3]] for a in ACTIONS}


def load(path) -> dict:
    kb = defaults()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            if "=" not in line or line.strip().startswith("["):
                continue
            k, v = line.rstrip("\r\n").split("=", 1)
            k = k.strip()
            alt = k.endswith("2") and k[:-1] in kb
            base = k[:-1] if alt else k
            if base in kb:
                kb[base][1 if alt else 0] = normalize(v)
    return kb


def save(kb: dict, path):
    lines = ["[Keybindings]"]
    for aid in ACTION_IDS:
        a, b = kb.get(aid, ["", ""])
        lines.append(f"{aid}={a.replace('Space', 'Spacebar') if a == 'Space' else a}")
        lines.append(f"{aid}2={b.replace('Space', 'Spacebar') if b == 'Space' else b}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def to_settings(kb: dict) -> str:
    import json
    return json.dumps(kb)


def from_settings(s: str) -> dict:
    import json
    kb = defaults()
    try:
        data = json.loads(s or "{}")
    except ValueError:
        return kb
    for k, v in data.items():
        if k in kb and isinstance(v, list) and len(v) == 2:
            kb[k] = [str(v[0]), str(v[1])]
    return kb
