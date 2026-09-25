# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

from decimal import Decimal

from openclear.web.templates import _currency, _format_metrics, _render_markdown


def test_currency_formats_decimal():
    assert _currency(Decimal("1234.5")) == "$1,234.50"
    assert _currency(Decimal("0")) == "$0.00"


def test_currency_passes_none_through():
    assert _currency(None) is None


def test_format_metrics_renders_precision_recall_as_percent():
    result = _format_metrics({"precision": 0.5, "recall": 1.0, "auc": 0.75})
    assert result == "Precision: 50%, Recall: 100%, AUC: 0.75"


def test_format_metrics_handles_error_shape():
    result = _format_metrics({"error": "insufficient training data"})
    assert result == "Error: insufficient training data"


def test_format_metrics_handles_empty_or_none():
    assert _format_metrics(None) == "—"
    assert _format_metrics({}) == "—"


def test_render_markdown_handles_empty_or_none():
    assert _render_markdown(None) == ""
    assert _render_markdown("") == ""


def test_render_markdown_renders_basic_constructs():
    result = _render_markdown("**bold** _italic_\n\n- one\n- two")
    assert "<strong>bold</strong>" in result
    assert "<em>italic</em>" in result
    assert "<li>one</li>" in result
    assert "<li>two</li>" in result


def test_render_markdown_renders_link_with_safe_rel():
    result = _render_markdown("[click here](https://example.com)")
    assert '<a href="https://example.com"' in result
    assert 'rel="noopener noreferrer nofollow"' in result


def test_render_markdown_strips_script_tags():
    result = _render_markdown("Notice <script>alert('xss')</script> here")
    assert "<script" not in result
    assert "alert" not in result


def test_render_markdown_strips_javascript_urls():
    result = _render_markdown("[click me](javascript:alert(1))")
    assert "javascript:" not in result
    assert "href" not in result


def test_render_markdown_strips_external_image_urls():
    # img is allowed (services/message_content.py validates/re-encodes an embedded
    # data: image at save time), but an external image URL is still rejected here at
    # render time too -- src is stripped, the src-less <img> tag itself remains (nh3's
    # attribute_filter only strips the disallowed attribute, not the whole element).
    result = _render_markdown("![alt text](https://evil.example/track.png)")
    assert "evil.example" not in result
    assert "src=" not in result


def test_render_markdown_allows_valid_data_uri_image():
    result = _render_markdown("![alt text](data:image/png;base64,iVBORw0KGgo=)")
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in result


def test_render_markdown_strips_svg_data_uri_image():
    # SVG can carry a <script> tag -- confirmed directly during this feature's planning
    # that nh3's url_schemes alone doesn't inspect the MIME subtype inside a data: URI.
    result = _render_markdown("![alt text](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)")
    assert "src=" not in result
    assert "svg" not in result


def test_render_markdown_strips_disallowed_attributes():
    result = _render_markdown('<p onclick="alert(1)" style="color:red">text</p>')
    assert "onclick" not in result
    assert "style" not in result


def test_render_markdown_returns_markup_instance():
    from markupsafe import Markup

    result = _render_markdown("**bold**")
    assert isinstance(result, Markup)
