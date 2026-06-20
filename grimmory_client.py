#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
A small, dependency-free Grimmory API client.

Calibre does not bundle ``requests``, so this uses the Python standard
library (``urllib``) only. All methods raise :class:`GrimmoryError` with a
human-readable message on failure.

Endpoints used (Grimmory / BookLore):
  * POST /api/v1/auth/login            -> {accessToken, ...}
  * GET  /api/v1/libraries             -> [Library{id,name,paths,allowedFormats}]
  * GET  /api/v1/books                 -> [Book{id, metadata{title,...}}]
  * POST /api/v1/files/upload          -> 204 (multipart, ?libraryId=&pathId=)
  * PUT  /api/v1/books/{id}/metadata   -> updates series/tags/etc.
"""

from __future__ import annotations

import json
import ssl
import uuid
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode


class GrimmoryError(Exception):
    pass


# Every boolean field of Grimmory's MetadataClearFlags. The server's
# MetadataChangeDetector reads each of these for every update and throws a
# NullPointerException if clearFlags (or any field in it) is missing, so we
# always send a complete object (all False = "don't clear anything").
CLEAR_FLAG_FIELDS: tuple[str, ...] = (
    "title",
    "subtitle",
    "publisher",
    "publishedDate",
    "description",
    "seriesName",
    "seriesNumber",
    "seriesTotal",
    "isbn13",
    "isbn10",
    "asin",
    "goodreadsId",
    "comicvineId",
    "hardcoverId",
    "hardcoverBookId",
    "googleId",
    "pageCount",
    "language",
    "amazonRating",
    "amazonReviewCount",
    "goodreadsRating",
    "goodreadsReviewCount",
    "hardcoverRating",
    "hardcoverReviewCount",
    "lubimyczytacId",
    "lubimyczytacRating",
    "ranobedbId",
    "ranobedbRating",
    "audibleId",
    "audibleRating",
    "audibleReviewCount",
    "authors",
    "categories",
    "moods",
    "tags",
    "cover",
    "audiobookCover",
    "reviews",
    "narrator",
    "abridged",
    "ageRating",
    "contentRating",
)


class GrimmoryClient:

    def __init__(
        self, base_url: str, verify_ssl: bool = True, timeout: int = 300
    ) -> None:
        if not base_url:
            raise GrimmoryError(
                "No Grimmory server URL configured. "
                "Set one in the Shelf Rat preferences."
            )
        self.base_url = base_url.rstrip("/")
        if not (
            self.base_url.startswith("http://")
            or self.base_url.startswith("https://")
        ):
            raise GrimmoryError(
                f"Server URL must start with http:// or https:// (got {base_url!r})."
            )
        self.timeout = timeout
        self.access_token: str | None = None
        self._ctx: ssl.SSLContext | None
        if verify_ssl:
            self._ctx = None  # use urllib's default (verified) context
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ctx = ctx

    # -- low level ---------------------------------------------------------
    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = self.base_url + path
        if query:
            url += "?" + urlencode(query)
        return url

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = "Bearer " + self.access_token
        if extra:
            headers.update(extra)
        return headers

    def _open(self, req: Request):
        # self._ctx is None for the default (verified) context, so passing it
        # through unconditionally is equivalent to omitting the argument.
        try:
            return urlopen(req, timeout=self.timeout, context=self._ctx)
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code in (401, 403):
                hint = (
                    "Authentication failed or you lack permission. "
                    "Check your username/password and upload rights."
                )
                msg = self._extract_error(body) or hint
            else:
                msg = self._extract_error(body) or e.reason
            raise GrimmoryError(f"Server returned HTTP {e.code}: {msg}")
        except URLError as e:
            raise GrimmoryError(
                f"Could not reach the Grimmory server: {e.reason}"
            )
        except ssl.SSLError as e:
            raise GrimmoryError(
                f"TLS/SSL error: {e}. If you use a self-signed certificate, "
                'turn off "Verify TLS/SSL certificate" in preferences.'
            )

    @staticmethod
    def _extract_error(body: str | None) -> str | None:
        if not body:
            return None
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                for key in ("message", "error", "detail", "errorMessage"):
                    if data.get(key):
                        return str(data[key])
        except Exception:
            pass
        body = body.strip()
        return body[:300] if body else None

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(
            self._url(path, query), data=data, headers=headers, method=method
        )
        resp = self._open(req)
        raw = resp.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    # -- API ---------------------------------------------------------------
    def login(self, username: str, password: str) -> str:
        data = self._request_json(
            "POST",
            "/api/v1/auth/login",
            body={"username": username, "password": password},
        )
        if not data or not data.get("accessToken"):
            raise GrimmoryError(
                "Login failed: the server did not return an access token."
            )
        token = data["accessToken"]
        self.access_token = token
        return token

    def get_libraries(self) -> list:
        return self._request_json("GET", "/api/v1/libraries") or []

    def get_books(self) -> list:
        """Return all books with full metadata (used to resolve titles to
        Grimmory book IDs)."""
        return (
            self._request_json(
                "GET",
                "/api/v1/books",
                query={
                    "stripForListView": "false",
                    "withDescription": "false",
                },
            )
            or []
        )

    def get_book(self, book_id: int) -> dict | None:
        """Fetch a single book with its full metadata (used to merge changes
        for a REPLACE_ALL update so existing fields aren't blanked)."""
        return self._request_json(
            "GET",
            f"/api/v1/books/{int(book_id)}",
            query={"withDescription": "true"},
        )

    def update_metadata(
        self,
        book_id: int,
        metadata: dict,
        clear_flags: dict | None = None,
        replace_mode: str = "REPLACE_WHEN_PROVIDED",
    ) -> Any:
        # Always send a complete clearFlags; the server NPEs on a missing one.
        flags = {field: False for field in CLEAR_FLAG_FIELDS}
        if clear_flags:
            flags.update(clear_flags)
        body = {"metadata": metadata, "clearFlags": flags}
        return self._request_json(
            "PUT",
            f"/api/v1/books/{int(book_id)}/metadata",
            query={"replaceMode": replace_mode},
            body=body,
        )

    def upload_file(
        self, library_id: int, path_id: int, filename: str, file_bytes: bytes
    ) -> bool:
        """Upload a single file directly into a library path (not bookdrop).
        Returns True on success (server responds 204 with no body)."""
        boundary = "----ShelfRat" + uuid.uuid4().hex
        data = self._encode_multipart(boundary, filename, file_bytes)
        headers = self._headers(
            {"Content-Type": "multipart/form-data; boundary=" + boundary}
        )
        req = Request(
            self._url(
                "/api/v1/files/upload",
                {"libraryId": library_id, "pathId": path_id},
            ),
            data=data,
            headers=headers,
            method="POST",
        )
        self._open(req)
        return True

    @staticmethod
    def _encode_multipart(
        boundary: str, filename: str, file_bytes: bytes
    ) -> bytes:
        safe_name = (
            (filename or "book")
            .replace('"', "")
            .replace("\r", "")
            .replace("\n", "")
        )
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        footer = (f"\r\n--{boundary}--\r\n").encode("utf-8")
        # b''.join allocates the result once and avoids an intermediate copy of
        # the (potentially large) file payload that chained + would create. The
        # whole body is still held in memory; stream the upload if that becomes
        # a problem for very large files.
        return b"".join((header, file_bytes, footer))
