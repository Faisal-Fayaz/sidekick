"""TUI theme system: sidekick dark/light + opencode/dracula/tokyonight, picker.

Single source of truth for every color in the TUI. No bare hex lives
anywhere else (CSS uses $variables, roles resolve via active_roles()).
"""

from __future__ import annotations

DARK_NAME = "sidekick"
LIGHT_NAME = "sidekick-light"
OPENCODE_NAME = "opencode"
DRACULA_NAME = "dracula"
TOKYONIGHT_NAME = "tokyonight"

THEME_NAMES = (DARK_NAME, LIGHT_NAME, OPENCODE_NAME, DRACULA_NAME, TOKYONIGHT_NAME)

DARK = {
    "primary": "#34f5a2",  # mint — focus, accents, you>
    "secondary": "#9d7bff",  # soft violet — sidekick>, help panel
    "accent": "#ffb000",  # amber — highlights, mic idle
    "background": "#0d1512",  # green-tinted black
    "surface": "#14201a",
    "panel": "#14201a",
    "border": "#244034",  # muted green-gray borders
    "muted": "#8fa698",  # dim secondary text
    "error": "#ff6b6b",
    "warning": "#ffd166",
}

LIGHT = {
    "primary": "#0a7d4f",  # deep green
    "secondary": "#6a3df0",  # violet
    "accent": "#b26a00",  # dark amber
    "background": "#f4f6f3",
    "surface": "#ffffff",
    "panel": "#ffffff",
    "border": "#c9d6cd",
    "muted": "#5f7166",
    "error": "#c62f2f",
    "warning": "#8a5a00",
}

DARK_ROLES = {
    "you": "bold #34f5a2",
    "sidekick": "bold #9d7bff",
    "tool": "#8fa698",
    "sys": "#8fa698 italic",
    "warn": "bold #ffd166",
    "error": "bold #ff6b6b",
}

LIGHT_ROLES = {
    "you": "bold #0a7d4f",
    "sidekick": "bold #6a3df0",
    "tool": "#5f7166",
    "sys": "#5f7166 italic",
    "warn": "bold #8a5a00",
    "error": "bold #c62f2f",
}

# opencode default TUI palette (opencode.ai/docs/themes + theme JSON).
OPENCODE = {
    "primary": "#fab283",  # peach — focus, accents
    "secondary": "#5c9cf5",  # blue
    "accent": "#9d7cd8",  # purple — highlights
    "background": "#0a0a0a",
    "surface": "#141414",
    "panel": "#141414",
    "border": "#484848",
    "muted": "#808080",  # dim secondary text
    "error": "#e06c75",
    "warning": "#f5a742",
}

OPENCODE_ROLES = {
    "you": "bold #fab283",
    "sidekick": "bold #5c9cf5",
    "tool": "#808080",
    "sys": "#808080 italic",
    "warn": "bold #e5c07b",
    "error": "bold #e06c75",
}

DRACULA = {
    "primary": "#bd93f9",  # purple
    "secondary": "#8be9fd",  # cyan
    "accent": "#ff79c6",  # pink — highlights
    "background": "#282a36",
    "surface": "#44475a",
    "panel": "#44475a",
    "border": "#6272a4",
    "muted": "#6272a4",  # comment gray — dim secondary text
    "error": "#ff5555",
    "warning": "#f1fa8c",
}

DRACULA_ROLES = {
    "you": "bold #bd93f9",
    "sidekick": "bold #8be9fd",
    "tool": "#6272a4",
    "sys": "#6272a4 italic",
    "warn": "bold #f1fa8c",
    "error": "bold #ff5555",
}

TOKYONIGHT = {
    "primary": "#7aa2f7",  # blue
    "secondary": "#bb9af7",  # magenta
    "accent": "#7dcfff",  # cyan — highlights
    "background": "#1a1b26",
    "surface": "#292e42",
    "panel": "#292e42",
    "border": "#3b4261",
    "muted": "#545c7e",  # dim secondary text
    "error": "#f7768e",
    "warning": "#e0af68",
}

TOKYONIGHT_ROLES = {
    "you": "bold #7aa2f7",
    "sidekick": "bold #bb9af7",
    "tool": "#545c7e",
    "sys": "#545c7e italic",
    "warn": "bold #e0af68",
    "error": "bold #f7768e",
}

_THEMES = {
    DARK_NAME: (DARK, DARK_ROLES),
    LIGHT_NAME: (LIGHT, LIGHT_ROLES),
    OPENCODE_NAME: (OPENCODE, OPENCODE_ROLES),
    DRACULA_NAME: (DRACULA, DRACULA_ROLES),
    TOKYONIGHT_NAME: (TOKYONIGHT, TOKYONIGHT_ROLES),
}

_current = DARK_NAME


def current_name() -> str:
    return _current


def name_for_mode(mode: str) -> str:
    """Config value ('dark'/'light') -> theme name."""
    return LIGHT_NAME if (mode or "").strip().lower() == "light" else DARK_NAME


def mode_for_name(name: str) -> str:
    """Theme name -> config value."""
    return "light" if name == LIGHT_NAME else "dark"


def normalize_theme_name(name: object) -> str:
    """Config value -> registered theme name. Legacy dark/light map to the
    sidekick themes; unknown values fall back to dark. Never raises."""
    try:
        key = str(name or "").strip().lower()
    except Exception:
        return DARK_NAME
    if key in _THEMES:
        return key
    if key == "dark":
        return DARK_NAME
    if key == "light":
        return LIGHT_NAME
    return DARK_NAME


def active_roles() -> dict[str, str]:
    return _THEMES[_current][1]


def install_sidekick_theme(app, name: str = DARK_NAME) -> None:
    """Register all themes, activate name. Never raises (falls back to default)."""
    global _current
    try:
        from textual.theme import Theme

        for theme_name, (palette, _) in _THEMES.items():
            app.register_theme(
                Theme(
                    name=theme_name,
                    primary=palette["primary"],
                    secondary=palette["secondary"],
                    accent=palette["accent"],
                    background=palette["background"],
                    surface=palette["surface"],
                    panel=palette["panel"],
                    warning=palette["warning"],
                    error=palette["error"],
                    dark=theme_name != LIGHT_NAME,
                )
            )
        if name in _THEMES:
            app.theme = name
            _current = name
    except Exception:
        pass


def set_theme(app, name: str) -> str:
    """Switch active theme + role set. Returns the effective name."""
    global _current
    if name in _THEMES:
        try:
            app.theme = name
        except Exception:
            pass
        _current = name
    return _current


def toggle_theme(app) -> str:
    """Flip dark <-> light. Returns the new name."""
    return set_theme(app, LIGHT_NAME if _current == DARK_NAME else DARK_NAME)
