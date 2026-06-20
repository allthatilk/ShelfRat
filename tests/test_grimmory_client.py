#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for the dependency-free Grimmory API client.

Network is mocked by patching ``grimmory_client.urlopen`` (via pytest-mock).
The fixture records every ``urllib.request.Request`` passed to it so tests can
assert URL, method, headers and body.
"""

import json
from urllib.error import URLError

import pytest

import grimmory_client
from grimmory_client import GrimmoryClient, GrimmoryError


# --- helpers --------------------------------------------------------------
class FakeResponse:
    def __init__(self, body=b"", status=200):
        self._body = body
        self.status = status
        self.headers = {}

    def read(self):
        return self._body


def json_response(obj, status=200):
    return FakeResponse(json.dumps(obj).encode("utf-8"), status=status)


@pytest.fixture
def urlopen(mocker):
    """Patches grimmory_client.urlopen. Set ``state.response`` to a
    FakeResponse (returned) or an Exception (raised). Inspect ``state.calls``
    for (request, timeout, context) tuples."""

    class State:
        response = FakeResponse()
        calls = []

        @property
        def last_request(self):
            return self.calls[-1][0]

        @property
        def last_context(self):
            return self.calls[-1][2]

    state = State()
    state.calls = []

    def fake(req, timeout=None, context=None):
        state.calls.append((req, timeout, context))
        if isinstance(state.response, Exception):
            raise state.response
        return state.response

    mocker.patch("grimmory_client.urlopen", side_effect=fake)
    return state


@pytest.fixture
def client():
    c = GrimmoryClient("https://grim.test")
    c.access_token = "tok123"
    return c


# --- construction & validation -------------------------------------------
def test_strips_trailing_slash():
    assert GrimmoryClient("https://grim.test/").base_url == "https://grim.test"


@pytest.mark.parametrize("bad", ["", None, "ftp://x", "grim.test", "ws://h"])
def test_invalid_base_url_raises(bad):
    with pytest.raises(GrimmoryError):
        GrimmoryClient(bad)


@pytest.mark.parametrize("ok", ["http://h:6060", "https://grim.test"])
def test_valid_base_url_accepted(ok):
    assert GrimmoryClient(ok).base_url == ok


# --- headers & url --------------------------------------------------------
def test_headers_without_token():
    h = GrimmoryClient("https://grim.test")._headers()
    assert h["Accept"] == "application/json"
    assert "Authorization" not in h


def test_headers_with_token(client):
    assert client._headers()["Authorization"] == "Bearer tok123"


def test_url_with_query(client):
    assert (
        client._url("/p", {"a": 1, "b": "x"}) == "https://grim.test/p?a=1&b=x"
    )


# --- error extraction -----------------------------------------------------
@pytest.mark.parametrize(
    "body,expected",
    [
        ('{"message":"bad thing"}', "bad thing"),
        ('{"error":"e1"}', "e1"),
        ('{"detail":"d1"}', "d1"),
        ('{"errorMessage":"em"}', "em"),
        ("", None),
        ("not json at all", "not json at all"),
        ('{"unrelated":"x"}', '{"unrelated":"x"}'),
    ],
)
def test_extract_error(body, expected):
    assert GrimmoryClient._extract_error(body) == expected


def test_extract_error_truncates_long_text():
    out = GrimmoryClient._extract_error("z" * 1000)
    assert len(out) == 300


# --- multipart encoding ---------------------------------------------------
def test_encode_multipart_structure():
    body = GrimmoryClient._encode_multipart("BOUND", "book.epub", b"DATA")
    text = body.decode("latin-1")
    assert text.startswith("--BOUND\r\n")
    assert (
        'Content-Disposition: form-data; name="file"; '
        'filename="book.epub"' in text
    )
    assert "Content-Type: application/octet-stream" in text
    assert "DATA" in text
    assert text.endswith("--BOUND--\r\n")


@pytest.mark.parametrize(
    "raw,clean",
    [
        ('a"b.epub', "ab.epub"),
        ("line\r\nbreak.pdf", "linebreak.pdf"),
    ],
)
def test_encode_multipart_sanitizes_filename(raw, clean):
    body = GrimmoryClient._encode_multipart("B", raw, b"x").decode("latin-1")
    assert f'filename="{clean}"' in body


# --- login ----------------------------------------------------------------
def test_login_sets_token(client, urlopen):
    urlopen.response = json_response({"accessToken": "newtok"})
    assert client.login("user", "pw") == "newtok"
    assert client.access_token == "newtok"
    req = urlopen.last_request
    assert req.full_url.endswith("/api/v1/auth/login")
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {"username": "user", "password": "pw"}


@pytest.mark.parametrize("resp", [{}, {"refreshToken": "r"}, None])
def test_login_without_token_raises(client, urlopen, resp):
    urlopen.response = (
        FakeResponse(b"") if resp is None else json_response(resp)
    )
    with pytest.raises(GrimmoryError):
        client.login("u", "p")


# --- list endpoints -------------------------------------------------------
def test_get_libraries_returns_list(client, urlopen):
    urlopen.response = json_response([{"id": 1, "name": "Main"}])
    libs = client.get_libraries()
    assert libs == [{"id": 1, "name": "Main"}]
    assert urlopen.last_request.full_url.endswith("/api/v1/libraries")


def test_get_libraries_empty_on_no_body(client, urlopen):
    urlopen.response = FakeResponse(b"")
    assert client.get_libraries() == []


def test_get_book_by_id(client, urlopen):
    urlopen.response = json_response({"id": 9, "metadata": {"title": "X"}})
    book = client.get_book(9)
    assert book["metadata"]["title"] == "X"
    assert "/api/v1/books/9" in urlopen.last_request.full_url


def test_get_books_uses_full_metadata_query(client, urlopen):
    urlopen.response = json_response([])
    client.get_books()
    url = urlopen.last_request.full_url
    assert "/api/v1/books?" in url
    assert "stripForListView=false" in url


# --- metadata update ------------------------------------------------------
def test_update_metadata_builds_request(client, urlopen):
    urlopen.response = json_response({"bookId": 7})
    client.update_metadata(
        7,
        {"title": "t", "seriesName": "S"},
        clear_flags={"tags": True},
        replace_mode="REPLACE_ALL",
    )
    req = urlopen.last_request
    assert req.get_method() == "PUT"
    assert "/api/v1/books/7/metadata" in req.full_url
    assert "replaceMode=REPLACE_ALL" in req.full_url
    body = json.loads(req.data)
    assert body["metadata"]["seriesName"] == "S"
    # clearFlags is always complete; our explicit flag is merged in.
    flags = body["clearFlags"]
    assert flags["tags"] is True
    assert flags["title"] is False
    assert set(flags) == set(grimmory_client.CLEAR_FLAG_FIELDS)


def test_update_metadata_sends_full_clearflags_by_default(client, urlopen):
    urlopen.response = json_response({})
    client.update_metadata(1, {"title": "t"})
    body = json.loads(urlopen.last_request.data)
    assert set(body["clearFlags"]) == set(grimmory_client.CLEAR_FLAG_FIELDS)
    assert all(v is False for v in body["clearFlags"].values())
    assert "replaceMode=REPLACE_WHEN_PROVIDED" in urlopen.last_request.full_url


# --- upload ---------------------------------------------------------------
def test_upload_file_request(client, urlopen):
    urlopen.response = FakeResponse(b"", status=204)
    assert client.upload_file(3, 5, "book.epub", b"BYTES") is True
    req = urlopen.last_request
    assert req.get_method() == "POST"
    assert "/api/v1/files/upload?" in req.full_url
    assert "libraryId=3" in req.full_url and "pathId=5" in req.full_url
    assert req.get_header("Content-type").startswith(
        "multipart/form-data; boundary="
    )
    assert req.get_header("Authorization") == "Bearer tok123"
    assert b"BYTES" in req.data


# --- error mapping --------------------------------------------------------
@pytest.mark.parametrize(
    "code,body,needle",
    [
        (401, "", "Authentication failed"),
        (403, "", "Authentication failed"),
        (401, '{"message":"nope"}', "nope"),
        (500, '{"message":"boom"}', "boom"),
        (404, "plain", "plain"),
    ],
)
def test_http_error_maps_to_grimmory_error(
    client, urlopen, http_error, code, body, needle
):
    urlopen.response = http_error(code, body)
    with pytest.raises(GrimmoryError) as exc:
        client.get_libraries()
    assert needle in str(exc.value)
    assert str(code) in str(exc.value)


def test_url_error_maps_to_unreachable(client, urlopen):
    urlopen.response = URLError("connection refused")
    with pytest.raises(GrimmoryError) as exc:
        client.get_libraries()
    assert "Could not reach" in str(exc.value)


# --- TLS verification toggle ---------------------------------------------
def test_verify_ssl_true_uses_no_custom_context(urlopen):
    c = GrimmoryClient("https://grim.test", verify_ssl=True)
    assert c._ctx is None
    urlopen.response = json_response([])
    c.get_libraries()
    assert urlopen.last_context is None


def test_verify_ssl_false_passes_unverified_context(urlopen):
    import ssl

    c = GrimmoryClient("https://grim.test", verify_ssl=False)
    assert isinstance(c._ctx, ssl.SSLContext)
    assert c._ctx.verify_mode == ssl.CERT_NONE
    urlopen.response = json_response([])
    c.get_libraries()
    assert urlopen.last_context is c._ctx
