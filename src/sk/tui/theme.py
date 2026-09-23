"""TUI theme system: dark + light identities, role styles, toggle.

Single source of truth for every color in the TUI. No bare hex lives
anywhere else (CSS uses $variables, roles resolve via active_roles()).
"""

from __future__ import annotations

DARK_NAME = "sidekick"
LIGHT_NAME = "sidekick-light"

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

_THEMES = {DARK_NAME: (DARK, DARK_ROLES), LIGHT_NAME: (LIGHT, LIGHT_ROLES)}

_current = DARK_NAME


def current_name() -> str:
    return _current


def name_for_mode(mode: str) -> str:
    """Config value ('dark'/'light') -> theme name."""
    return LIGHT_NAME if (mode or "").strip().lower() == "light" else DARK_NAME


def mode_for_name(name: str) -> str:
    """Theme name -> config value."""
    return "light" if name == LIGHT_NAME else "dark"


def active_roles() -> dict[str, str]:
    return _THEMES[_current][1]


def install_sidekick_theme(app, name: str = DARK_NAME) -> None:
    """Register both themes, activate name. Never raises (falls back to default)."""
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
                    dark=theme_name == DARK_NAME,
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
