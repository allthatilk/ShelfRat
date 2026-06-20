#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for the Worker's book-matching helpers in main.py.

These resolve a Calibre book to a Grimmory book id by (normalised) title,
disambiguating by author when several share a title. Since the upload endpoint
returns no id, this matching is what lets metadata be applied afterwards."""

import pytest

from grimmory_client import GrimmoryError
from main import Worker


def book(book_id, title, authors=None):
    meta = {"title": title}
    if authors is not None:
        meta["authors"] = authors
    return {"id": book_id, "metadata": meta}


@pytest.fixture
def worker():
    # conn is unused by the helpers under test; the Qt base is stubbed.
    return Worker(None, "metadata", {})


# --- _build_title_index ---------------------------------------------------
def test_build_index_groups_by_normalised_title():
    index = Worker._build_title_index(
        [
            book(1, "The Book"),
            book(2, "the book  "),
            book(3, "Other"),
        ]
    )
    assert {b["id"] for b in index["the book"]} == {1, 2}
    assert [b["id"] for b in index["other"]] == [3]


def test_build_index_skips_titleless_entries():
    index = Worker._build_title_index(
        [
            {"id": 1, "metadata": {}},
            {"id": 2},
            book(3, "Real"),
        ]
    )
    assert list(index) == ["real"]


# --- _match_book_id -------------------------------------------------------
def test_match_single_candidate(worker):
    index = Worker._build_title_index([book(10, "Book")])
    assert worker._match_book_id(index, {"title": "book", "authors": []}) == (
        10,
        None,
    )


def test_match_no_candidate(worker):
    index = Worker._build_title_index([book(10, "Book")])
    book_id, note = worker._match_book_id(
        index, {"title": "missing", "authors": []}
    )
    assert book_id is None
    assert note == "no match on server"


def test_match_skips_untitled(worker):
    # An untitled book (empty title) must never match a real server book,
    # even one literally titled "Unknown".
    index = Worker._build_title_index([book(10, "Unknown")])
    book_id, note = worker._match_book_id(index, {"title": "", "authors": []})
    assert book_id is None
    assert "no title" in note


def test_match_disambiguates_by_author(worker):
    index = Worker._build_title_index(
        [
            book(1, "Dup", ["Alice"]),
            book(2, "Dup", ["Bob"]),
        ]
    )
    assert worker._match_book_id(
        index, {"title": "Dup", "authors": ["Bob"]}
    ) == (2, None)


def test_match_ambiguous_is_skipped(worker):
    index = Worker._build_title_index(
        [
            book(1, "Dup", ["Alice"]),
            book(2, "Dup", ["Bob"]),
        ]
    )
    book_id, note = worker._match_book_id(
        index, {"title": "Dup", "authors": ["Carol"]}
    )
    assert book_id is None
    assert "skipped" in note


# --- _apply_metadata (sends only the changed fields) ----------------------
def test_apply_metadata_sends_only_changes(worker, mocker):
    client = mocker.Mock()
    worker._apply_metadata(
        client, 7, {"seriesName": "Saga", "seriesNumber": 2}
    )
    client.update_metadata.assert_called_once_with(
        7,
        {"seriesName": "Saga", "seriesNumber": 2},
        replace_mode="REPLACE_WHEN_PROVIDED",
    )


def test_apply_metadata_does_not_fetch_book_first(worker, mocker):
    # Default mode (REPLACE_WHEN_PROVIDED) updates directly; only REPLACE_ALL
    # GETs the book first (to merge). Assert the GET never happens.
    client = mocker.Mock()
    worker._apply_metadata(client, 7, {"title": "X"})
    client.update_metadata.assert_called_once()
    client.get_book.assert_not_called()


def test_apply_metadata_replace_all_merges_full_object(mocker):
    w = Worker(None, "metadata", {}, replace_mode="REPLACE_ALL")
    client = mocker.Mock()
    client.get_book.return_value = {
        "metadata": {"title": "Old", "publisher": "P"}
    }
    w._apply_metadata(client, 7, {"title": "New"})

    args, kwargs = client.update_metadata.call_args
    assert args[1]["title"] == "New"  # our change
    assert args[1]["publisher"] == "P"  # preserved, not blanked
    assert args[1]["bookId"] == 7
    assert kwargs["replace_mode"] == "REPLACE_ALL"


def test_apply_metadata_replace_all_falls_back_when_read_fails(mocker):
    w = Worker(None, "metadata", {}, replace_mode="REPLACE_ALL")
    client = mocker.Mock()
    client.get_book.side_effect = GrimmoryError("boom")
    w._apply_metadata(client, 7, {"title": "New"})

    # Falls back to a non-destructive partial update rather than blanking.
    args, kwargs = client.update_metadata.call_args
    assert args[1] == {"title": "New"}
    assert kwargs["replace_mode"] == "REPLACE_WHEN_PROVIDED"
