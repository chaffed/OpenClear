# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""Colour contrast (WCAG 2.1), and the colours derived from a bank's accent (UI_PLAN.md UI-D2).

A bank's accent fills buttons, the navigation count and the selected tab, always with white
text: `tenant_service` refuses an accent darker text can't sit on. As text, on the page's own
backgrounds, the accent isn't always readable: it's a dark colour by that rule, so on the dark
theme's background it's often under 4.5:1, and a borderline one is under it on the light grey
of the selected navigation item too. `accent_text` gives the shade of the accent to use as
text, links and focus rings on each theme's backgrounds."""

from functools import lru_cache

AA_TEXT = 4.5

DEFAULT_ACCENT = "#2563eb"
# The backgrounds accent-coloured text sits on in each theme (static/css/app.css --bg and
# --bg-muted): the derived shade must clear 4.5:1 on both.
LIGHT_BACKGROUNDS = ("#ffffff", "#f3f4f6")
DARK_BACKGROUNDS = ("#0f172a", "#1e293b")


def _channels(hex_color: str) -> tuple[float, float, float]:
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.1's relative luminance of a `#rrggbb` colour."""
    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in _channels(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _mix(hex_color: str, toward: str, amount: float) -> str:
    """`hex_color` moved `amount` (0 to 1) of the way to `toward`, in sRGB."""
    mixed = (a + (b - a) * amount for a, b in zip(_channels(hex_color), _channels(toward)))
    return "#" + "".join(f"{round(c * 255):02x}" for c in mixed)


def _readable_on(hex_color: str, backgrounds: tuple[str, ...], toward: str) -> str:
    """The accent itself if it reaches 4.5:1 on every background, otherwise the least change
    toward white (dark theme) or black (light theme) that does."""
    if all(contrast_ratio(hex_color, bg) >= AA_TEXT for bg in backgrounds):
        return hex_color
    for step in range(1, 101):
        shade = _mix(hex_color, toward, step / 100)
        if all(contrast_ratio(shade, bg) >= AA_TEXT for bg in backgrounds):
            return shade
    return toward


@lru_cache(maxsize=256)
def accent_text(accent: str) -> tuple[str, str]:
    """(light theme, dark theme): the shade of `accent` to use as text on each."""
    accent = accent.lower()
    return _readable_on(accent, LIGHT_BACKGROUNDS, "#000000"), _readable_on(accent, DARK_BACKGROUNDS, "#ffffff")
