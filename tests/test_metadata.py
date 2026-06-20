#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for the pure Calibre->Grimmory mapping helpers in main.py:
``calibre_to_grimmory_metadata``, ``choose_upload_format`` and
``_normalize_title``."""

import datetime
import types

import pytest

import sys

from main import (
    calibre_to_grimmory_metadata,
    choose_upload_format,
    _normalize_title,
    embed_metadata,
)


@pytest.fixture
def make_mi():
    """Factory for a fake Calibre Metadata object."""

    def _make(**overrides):
        fields = dict(
            title="A Title",
            authors=["Jane Doe"],
            publisher=None,
            languages=[],
            pubdate=None,
            comments=None,
            series=None,
            series_index=None,
            tags=[],
            identifiers={},
        )
        fields.update(overrides)
        return types.SimpleNamespace(**fields)

    return _make


# --- basic fields ---------------------------------------------------------
def test_basic_fields(make_mi):
    mi = make_mi(
        title="T",
        authors=["A", "B"],
        publisher="Pub",
        languages=["eng", "fra"],
        pubdate=datetime.date(2020, 3, 4),
    )
    md = calibre_to_grimmory_metadata(mi)
    assert md["title"] == "T"
    assert md["authors"] == ["A", "B"]
    assert md["publisher"] == "Pub"
    # All languages, comma-joined; left as 3-letter codes here because the
    # calibre converter isn't importable in the test env (see tests below).
    assert md["language"] == "eng,fra"
    assert md["publishedDate"] == "2020-03-04"


def test_language_fallback_when_converter_unavailable(make_mi):
    # No calibre.utils.localization in the test env -> keep the original code.
    md = calibre_to_grimmory_metadata(make_mi(languages=["eng"]))
    assert md["language"] == "eng"


def test_language_converted_to_iso639_1(make_mi, monkeypatch):
    import types

    fake = types.ModuleType("calibre.utils.localization")
    fake.lang_as_iso639_1 = lambda code: {"eng": "en", "fra": "fr"}.get(code)
    monkeypatch.setitem(sys.modules, "calibre.utils.localization", fake)
    md = calibre_to_grimmory_metadata(make_mi(languages=["eng", "fra"]))
    assert md["language"] == "en,fr"


def test_unknown_author_filtered(make_mi):
    md = calibre_to_grimmory_metadata(make_mi(authors=["Unknown"]))
    assert "authors" not in md


def test_undefined_pubdate_skipped(make_mi):
    # Calibre uses year 101 as the "undefined" sentinel.
    md = calibre_to_grimmory_metadata(
        make_mi(pubdate=datetime.date(101, 1, 1))
    )
    assert "publishedDate" not in md


# --- series ---------------------------------------------------------------
@pytest.mark.parametrize("index,expected", [(1, 1.0), (1.5, 1.5), (3, 3.0)])
def test_series_included(make_mi, index, expected):
    md = calibre_to_grimmory_metadata(
        make_mi(series="Saga", series_index=index)
    )
    assert md["seriesName"] == "Saga"
    assert md["seriesNumber"] == expected
    assert isinstance(md["seriesNumber"], float)


def test_series_excluded_when_flag_off(make_mi):
    md = calibre_to_grimmory_metadata(
        make_mi(series="Saga", series_index=2), include_series=False
    )
    assert "seriesName" not in md and "seriesNumber" not in md


def test_no_series_means_no_series_fields(make_mi):
    md = calibre_to_grimmory_metadata(make_mi(series=None))
    assert "seriesName" not in md


# --- tags -----------------------------------------------------------------
@pytest.mark.parametrize("field", ["tags", "categories"])
def test_tags_go_to_configured_field(make_mi, field):
    md = calibre_to_grimmory_metadata(
        make_mi(tags=["sci-fi", "fav"]), tags_field=field
    )
    assert md[field] == ["sci-fi", "fav"]


def test_tags_excluded_when_flag_off(make_mi):
    md = calibre_to_grimmory_metadata(make_mi(tags=["x"]), include_tags=False)
    assert "tags" not in md and "categories" not in md


# --- identifiers ----------------------------------------------------------
@pytest.mark.parametrize(
    "identifiers,expected",
    [
        ({"isbn": "9781234567897"}, {"isbn13": "9781234567897"}),
        ({"isbn": "978-1-234-56789-7"}, {"isbn13": "9781234567897"}),
        ({"isbn": "0306406152"}, {"isbn10": "0306406152"}),
        ({"amazon": "B00ABC"}, {"asin": "B00ABC"}),
        ({"asin": "B00XYZ"}, {"asin": "B00XYZ"}),
        ({"goodreads": "gr1"}, {"goodreadsId": "gr1"}),
        ({"google": "gg1"}, {"googleId": "gg1"}),
    ],
)
def test_identifiers_mapped(make_mi, identifiers, expected):
    md = calibre_to_grimmory_metadata(make_mi(identifiers=identifiers))
    for key, value in expected.items():
        assert md[key] == value


def test_identifiers_excluded_when_flag_off(make_mi):
    md = calibre_to_grimmory_metadata(
        make_mi(identifiers={"isbn": "9781234567897"}),
        include_identifiers=False,
    )
    assert "isbn13" not in md


def test_invalid_isbn_length_ignored(make_mi):
    md = calibre_to_grimmory_metadata(make_mi(identifiers={"isbn": "123"}))
    assert "isbn13" not in md and "isbn10" not in md


# --- description ----------------------------------------------------------
def test_description_included_by_default(make_mi):
    md = calibre_to_grimmory_metadata(make_mi(comments="<p>hi</p>"))
    assert md["description"] == "<p>hi</p>"


def test_description_excluded_when_disabled(make_mi):
    md = calibre_to_grimmory_metadata(
        make_mi(comments="<p>hi</p>"), include_description=False
    )
    assert "description" not in md


# --- format selection -----------------------------------------------------
@pytest.mark.parametrize(
    "available,preferred,allowed,expected",
    [
        # respects preference order
        (["EPUB", "PDF"], ["EPUB", "PDF"], {"EPUB", "PDF"}, "EPUB"),
        (["EPUB", "PDF"], ["PDF", "EPUB"], {"EPUB", "PDF"}, "PDF"),
        # filters by the library's allowed groups
        (["PDF"], ["EPUB", "PDF"], {"EPUB"}, None),
        # comic formats map to the CBX group
        (["CBZ"], ["CBZ"], {"CBX"}, "CBZ"),
        (["CBR"], ["CBR"], {"CBX"}, "CBR"),
        (["CBZ"], ["CBZ"], {"EPUB"}, None),
        # empty/None allowed means "anything goes"
        (["MOBI"], ["EPUB", "MOBI"], set(), "MOBI"),
        (["MOBI"], ["EPUB", "MOBI"], None, "MOBI"),
        # case-insensitive on the available set
        (["epub"], ["EPUB"], {"EPUB"}, "EPUB"),
        # case-insensitive on the library's allowed groups too
        (["PDF"], ["PDF"], {"pdf"}, "PDF"),
        (["CBZ"], ["CBZ"], {"cbx"}, "CBZ"),
        # nothing available
        ([], ["EPUB"], None, None),
        # available but none preferred
        (["TXT"], ["EPUB", "PDF"], None, None),
    ],
)
def test_choose_upload_format(available, preferred, allowed, expected):
    assert choose_upload_format(available, preferred, allowed) == expected


# --- embed_metadata -------------------------------------------------------
def test_embed_metadata_falls_back_without_calibre(make_mi):
    # calibre.ebooks.metadata.meta isn't importable in the test env, so the
    # function returns the raw bytes unchanged.
    db = types.SimpleNamespace(format=lambda bid, fmt: b"RAWBYTES")
    assert embed_metadata(db, 1, "EPUB", make_mi()) == b"RAWBYTES"


def test_embed_metadata_none_when_no_file(make_mi):
    db = types.SimpleNamespace(format=lambda bid, fmt: None)
    assert embed_metadata(db, 1, "EPUB", make_mi()) is None


def test_embed_metadata_invokes_set_metadata(make_mi, monkeypatch):
    fake = types.ModuleType("calibre.ebooks.metadata.meta")
    calls = {}

    def set_metadata(stream, mi, fmt):
        calls["fmt"] = fmt
        data = stream.read()
        stream.seek(0)
        stream.truncate()
        stream.write(b"EMBEDDED:" + data)

    fake.set_metadata = set_metadata
    monkeypatch.setitem(sys.modules, "calibre.ebooks.metadata.meta", fake)
    db = types.SimpleNamespace(format=lambda bid, fmt: b"RAW")

    out = embed_metadata(db, 1, "EPUB", make_mi())
    assert calls["fmt"] == "epub"  # format passed lower-cased
    assert out == b"EMBEDDED:RAW"


# --- title normalisation --------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  The Book ", "the book"),
        ("UPPER", "upper"),
        (None, ""),
        ("", ""),
    ],
)
def test_normalize_title(raw, expected):
    assert _normalize_title(raw) == expected
