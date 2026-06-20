#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Test bootstrap.

The Shelf Rat modules are written to run *inside* Calibre, so at import time
they pull in ``qt.core``, ``calibre.*`` and ``calibre_plugins.shelf_rat.*`` —
none of which exist in a plain virtualenv. This conftest installs lightweight
stand-ins into ``sys.modules`` *before* the plugin modules are imported, and
aliases the real repo modules under the ``calibre_plugins.shelf_rat`` package
name the code imports itself with.

``grimmory_client`` is pure standard library and needs none of this, but the
stubs are harmless to it.
"""

import io
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _register(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# --- fake qt.core ---------------------------------------------------------
class _QtObject:
    """Permissive stand-in: usable as a base class and instantiable."""

    def __init__(self, *args, **kwargs):
        pass


def _pyqtSignal(*args, **kwargs):
    class _Signal:
        def connect(self, *a, **k):
            pass

        def emit(self, *a, **k):
            pass

    return _Signal()


_QT_NAMES = [
    "Qt",
    "QThread",
    "QDialog",
    "QWidget",
    "QVBoxLayout",
    "QHBoxLayout",
    "QFormLayout",
    "QGroupBox",
    "QRadioButton",
    "QComboBox",
    "QCheckBox",
    "QLabel",
    "QLineEdit",
    "QPushButton",
    "QProgressDialog",
    "QDialogButtonBox",
    "QIcon",
]
_qt_attrs = {name: type(name, (_QtObject,), {}) for name in _QT_NAMES}
_qt_attrs["pyqtSignal"] = _pyqtSignal
_register("qt")
_register("qt.core", **_qt_attrs)


# --- fake calibre.* -------------------------------------------------------
class _FakeJSONConfig(dict):
    """Mimics calibre.utils.config.JSONConfig: a dict with a ``defaults``
    mapping that backs missing keys."""

    def __init__(self, name="", *args, **kwargs):
        super().__init__()
        self.defaults = {}

    def __getitem__(self, key):
        if key in self:
            return dict.__getitem__(self, key)
        return self.defaults.get(key)

    def get(self, key, default=None):
        if key in self:
            return dict.__getitem__(self, key)
        return self.defaults.get(key, default)


def _ascii_filename(s, *args, **kwargs):
    return "".join(c for c in (s or "") if ord(c) < 128)


_register("calibre")
_register(
    "calibre.customize",
    InterfaceActionBase=type("InterfaceActionBase", (object,), {}),
)
_register(
    "calibre.gui2",
    error_dialog=lambda *a, **k: None,
    info_dialog=lambda *a, **k: None,
    warning_dialog=lambda *a, **k: None,
    question_dialog=lambda *a, **k: True,
)
_register(
    "calibre.gui2.actions",
    InterfaceAction=type("InterfaceAction", (object,), {}),
)
_register("calibre.utils")
_register("calibre.utils.config", JSONConfig=_FakeJSONConfig)
_register("calibre.utils.filenames", ascii_filename=_ascii_filename)


# --- alias the real repo modules under calibre_plugins.shelf_rat ----------
_register("calibre_plugins")
shelf_pkg = _register("calibre_plugins.shelf_rat")

import config as _config  # noqa: E402  (needs stubs above)
import grimmory_client as _gc  # noqa: E402

sys.modules["calibre_plugins.shelf_rat.config"] = _config
sys.modules["calibre_plugins.shelf_rat.grimmory_client"] = _gc
shelf_pkg.config = _config
shelf_pkg.grimmory_client = _gc

import main as _main  # noqa: E402  (imports the two aliases above)

sys.modules["calibre_plugins.shelf_rat.main"] = _main
shelf_pkg.main = _main


@pytest.fixture(autouse=True)
def reset_plugin_state():
    """Reset persisted prefs and the in-memory password between tests so they
    don't leak state into each other."""
    _config.prefs.clear()
    _config.set_session_password(None)
    yield
    _config.prefs.clear()
    _config.set_session_password(None)


@pytest.fixture
def http_error():
    """Factory for a urllib HTTPError with a readable body."""
    from urllib.error import HTTPError

    def _make(code, body=b"", url="https://grim.test/api", msg="err"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        return HTTPError(url, code, msg, {}, io.BytesIO(body))

    return _make
