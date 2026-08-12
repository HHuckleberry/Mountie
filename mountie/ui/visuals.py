"""Palette tokens, status colors, and application stylesheet."""

COSMIC_TOKENS = {
    "dark": {
        "positive": (94, 219, 140),
        "negative": (255, 160, 154),
        "neutral": (255, 163, 125),
        "muted": (211, 211, 211),
        "secondary": (185, 188, 192),
    },
    "light": {
        "positive": (0, 87, 44),
        "negative": (137, 4, 24),
        "neutral": (121, 44, 0),
        "muted": (95, 99, 104),
        "secondary": (95, 99, 104),
    },
}


PALETTE_COLORS = {
    "dark": {
        "window": "#1c1c1c", "base": "#252525", "alt_base": "#2e2e2e",
        "text": "#e8e8e8", "mid": "#4a4a4a", "button": "#2e2e2e",
        "disabled": "#7a7a7a", "highlight": "#63d0df",
    },
    "light": {
        "window": "#f5f5f5", "base": "#ffffff", "alt_base": "#ededed",
        "text": "#1b1b1b", "mid": "#c6c6c6", "button": "#ededed",
        "disabled": "#9a9a9a", "highlight": "#2a7fb8",
    },
}


STATUS_TOKEN_KEY = {
    "connected": "positive",
    "disconnected": "muted",
    "connecting...": "neutral",
    "disconnecting...": "neutral",
    "checking...": "muted",
    "unknown": "muted",
    "error": "negative",
    "keyring error": "negative",
    "authentication failed": "negative",
    "share not found": "negative",
    "host not found": "negative",
    "connection refused": "negative",
    "network unreachable": "negative",
    "host unreachable": "negative",
    "connection timed out": "negative",
    "unmount failed": "negative",
    "invalid share": "negative",
}


APP_STYLESHEET = """
QMainWindow, QDialog {
    background: palette(window);
}
QListWidget {
    border: none;
    background: transparent;
}
QListWidget::item {
    border: none;
}
QListWidget::item:selected {
    background: transparent;
}
#shareCard {
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 10px;
}
#shareLabel {
    font-size: 13px;
    font-weight: 600;
}
/* Color is set per-card from the "secondary" token, not here: palette(mid)
   is a border/separator color and lands around 1.7:1 against the card
   background, which is unreadable for body text in both themes. */
#shareTarget {
    font-size: 12px;
}
#headerTitle {
    font-size: 18px;
    font-weight: 700;
}
QPushButton#primaryButton {
    background: palette(highlight);
    color: palette(highlighted-text);
    border: none;
    border-radius: 8px;
    padding: 7px 16px;
    font-weight: 600;
}
QPushButton#primaryButton:hover {
    background: palette(highlight);
}
QToolButton[class="iconButton"] {
    border: none;
    border-radius: 6px;
    padding: 5px;
}
QToolButton[class="iconButton"]:hover {
    background: palette(mid);
}
"""
