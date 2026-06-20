#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for preferences defaults and the session-password logic in
config.py."""

import pytest

import config


# --- defaults -------------------------------------------------------------
@pytest.mark.parametrize(
    "key,expected",
    [
        ("server_url", "https://"),
        ("remember_password", False),
        ("password", ""),
        ("verify_ssl", True),
        ("tags_field", "categories"),
        ("replace_mode", "REPLACE_WHEN_PROVIDED"),
    ],
)
def test_defaults(key, expected):
    assert config.prefs[key] == expected


def test_replace_modes_contents():
    assert config.REPLACE_MODES == [
        "REPLACE_WHEN_PROVIDED",
        "REPLACE_MISSING",
        "REPLACE_ALL",
    ]


# --- session password cache ----------------------------------------------
def test_session_password_roundtrip():
    assert config.get_session_password() is None
    config.set_session_password("secret")
    assert config.get_session_password() == "secret"


def test_set_session_password_blank_becomes_none():
    config.set_session_password("")
    assert config.get_session_password() is None


# --- get_effective_password ----------------------------------------------
@pytest.mark.parametrize(
    "remember,stored,session,expected",
    [
        (True, "stored", None, "stored"),  # remembered password wins
        (True, "stored", "sess", "stored"),  # ... even over a session value
        (True, "", "sess", "sess"),  # empty stored -> fall back to session
        (False, "stored", None, None),  # not remembered -> ignore stored
        (False, "stored", "sess", "sess"),  # not remembered -> use session
        (False, "", None, None),  # nothing available
    ],
)
def test_get_effective_password(remember, stored, session, expected):
    config.prefs["remember_password"] = remember
    config.prefs["password"] = stored
    config.set_session_password(session)
    assert config.get_effective_password() == expected
