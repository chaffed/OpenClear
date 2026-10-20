# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
"""A bank's accent as text stays readable in both themes (UI_PLAN.md UI-D2): colors.accent_text
gives the shade each theme uses, and the page's stylesheet defaults are that shade of the
default accent."""

import re
from pathlib import Path

import pytest

from openclear import colors
from openclear.web.templates import _accent_style

CSS = (Path(__file__).resolve().parents[2] / "src" / "openclear" / "static" / "css" / "app.css").read_text()


@pytest.mark.parametrize("accent", ["#2563eb", "#0f5b8a", "#757575", "#000000", "#6d28d9", "#b45309"])
def test_each_themes_shade_reads_on_its_backgrounds(accent):
    light, dark = colors.accent_text(accent)
    assert all(colors.contrast_ratio(light, bg) >= colors.AA_TEXT for bg in colors.LIGHT_BACKGROUNDS)
    assert all(colors.contrast_ratio(dark, bg) >= colors.AA_TEXT for bg in colors.DARK_BACKGROUNDS)


def test_a_readable_accent_is_used_as_it_is():
    assert colors.accent_text("#0f5b8a")[0] == "#0f5b8a"
    assert colors.accent_text("#0F5B8A")[0] == "#0f5b8a"


def test_an_unreadable_one_moves_only_as_far_as_it_must():
    dark = colors.accent_text("#0f5b8a")[1]
    assert dark != "#0f5b8a"
    assert min(colors.contrast_ratio(dark, bg) for bg in colors.DARK_BACKGROUNDS) < colors.AA_TEXT + 0.2


def test_the_stylesheet_defaults_are_the_default_accents_shades():
    light, dark = colors.accent_text(colors.DEFAULT_ACCENT)
    assert re.search(rf"--accent: {colors.DEFAULT_ACCENT};", CSS)
    assert re.search(rf"--accent-text: {light};", CSS)
    assert len(re.findall(rf"--accent-text: {dark};", CSS)) == 2  # system dark, and dark chosen


def test_the_style_carries_both_themes():
    style = str(_accent_style("#0f5b8a"))
    light, dark = colors.accent_text("#0f5b8a")
    assert f"--accent:#0f5b8a;--accent-text:{light}" in style
    assert style.count(f"--accent-text:{dark}") == 2


@pytest.mark.parametrize("stored", [None, "", "red", "#12345", "#123456;}body{display:none", "</style><script>"])
def test_only_a_hex_colour_reaches_the_stylesheet(stored):
    assert str(_accent_style(stored)) == ""
