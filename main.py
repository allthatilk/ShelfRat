#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Calibre -> Grimmory metadata mapping and format-selection helpers.

Pure functions used by the Shelf Rat dialog and worker: map a Calibre
Metadata object to a Grimmory BookMetadata dict, choose which file format to
upload, and embed metadata into a book file before upload.
"""

from __future__ import annotations

from collections.abc import Iterable


FORMAT_GROUP = {
    "EPUB": "EPUB",
    "PDF": "PDF",
    "FB2": "FB2",
    "MOBI": "MOBI",
    "AZW3": "AZW3",
    "CBZ": "CBX",
    "CBR": "CBX",
    "CB7": "CBX",
    "M4B": "AUDIOBOOK",
    "MP3": "AUDIOBOOK",
    "M4A": "AUDIOBOOK",
}


def _normalize_title(s: str | None) -> str:
    return (s or "").strip().lower()


def _to_iso639_1(code: str | None) -> str | None:
    """Best-effort convert a Calibre language code (often 3-letter ISO 639-2,
    e.g. "eng") to 2-letter ISO 639-1 (e.g. "en"), which is what Grimmory
    expects. Falls back to the original code if conversion isn't available."""
    if not code:
        return code
    try:
        from calibre.utils.localization import lang_as_iso639_1

        two = lang_as_iso639_1(code)
        if two:
            return two
    except Exception:
        pass
    return code


def calibre_to_grimmory_metadata(
    mi,
    include_series: bool = True,
    include_tags: bool = True,
    tags_field: str = "tags",
    include_identifiers: bool = True,
    include_description: bool = True,
) -> dict:
    """Map a Calibre Metadata object to a Grimmory BookMetadata dict."""
    meta = {}
    if mi.title:
        meta["title"] = mi.title
    authors = [
        author
        for author in (mi.authors or [])
        if author and author != "Unknown"
    ]
    if authors:
        meta["authors"] = authors
    if getattr(mi, "publisher", None):
        meta["publisher"] = mi.publisher
    # Calibre books can be multi-lingual (languages is a list, primary first).
    # Grimmory's language field is singular but accepts a comma-separated list,
    # so send them all, each converted to ISO 639-1.
    languages = [
        iso
        for code in (getattr(mi, "languages", []) or [])
        if (iso := _to_iso639_1(code))
    ]
    if languages:
        meta["language"] = ",".join(languages)
    pubdate = getattr(mi, "pubdate", None)
    # Calibre uses year 101 (UNDEFINED_DATE) as "no date".
    if pubdate is not None and getattr(pubdate, "year", 0) > 101:
        meta["publishedDate"] = (
            f"{pubdate.year:04d}-{pubdate.month:02d}-{pubdate.day:02d}"
        )
    if include_description and getattr(mi, "comments", None):
        meta["description"] = mi.comments
    if include_series and getattr(mi, "series", None):
        meta["seriesName"] = mi.series
        if mi.series_index is not None:
            meta["seriesNumber"] = float(mi.series_index)
    if include_tags and getattr(mi, "tags", None):
        meta[tags_field] = list(mi.tags)
    if include_identifiers:
        identifiers = getattr(mi, "identifiers", {}) or {}
        isbn = (identifiers.get("isbn") or "").replace("-", "").strip()
        if len(isbn) == 13:
            meta["isbn13"] = isbn
        elif len(isbn) == 10:
            meta["isbn10"] = isbn
        asin = identifiers.get("amazon") or identifiers.get("asin")
        if asin:
            meta["asin"] = asin
        if identifiers.get("goodreads"):
            meta["goodreadsId"] = identifiers["goodreads"]
        if identifiers.get("google"):
            meta["googleId"] = identifiers["google"]
    return meta


def choose_upload_format(
    available_fmts: Iterable[str],
    preferred: list[str],
    allowed_groups: set[str] | None,
) -> str | None:
    """Pick the best Calibre format to upload.

    available_fmts: iterable of upper-case Calibre format codes the book has.
    preferred: ordered list of Calibre format codes from preferences.
    allowed_groups: set of Grimmory allowedFormats for the target library
                    (empty/None means "allow anything").
    """
    available = {fmt.upper() for fmt in available_fmts}
    # Grimmory's allowedFormats casing isn't guaranteed to match ours, so
    # compare case-insensitively. None/empty means "allow anything".
    allowed = (
        {group.upper() for group in allowed_groups} if allowed_groups else None
    )
    for fmt in preferred:
        fmt = fmt.upper()
        if fmt not in available:
            continue
        if allowed is not None:
            group = FORMAT_GROUP.get(fmt)
            if group is None or group not in allowed:
                continue
        return fmt
    return None


def embed_metadata(db, book_id: int, fmt: str, mi) -> bytes | None:
    """Return the book file's bytes with Calibre's current metadata embedded.

    Calibre keeps edited metadata (title, series, tags, ...) in its database
    but does not rewrite the on-disk file, so the raw format can carry a stale
    embedded title. Grimmory reads the embedded metadata on ingest, so we embed
    ``mi`` first (the same thing Calibre does on "Save to disk"). Falls back to
    the raw bytes if embedding isn't supported/fails for the format."""
    raw = db.format(book_id, fmt)
    if not raw:
        return raw
    try:
        from io import BytesIO
        from calibre.ebooks.metadata.meta import set_metadata

        stream = BytesIO(raw)
        set_metadata(stream, mi, fmt.lower())
        stream.seek(0)
        return stream.read()
    except Exception:
        return raw
