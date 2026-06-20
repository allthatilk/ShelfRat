#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Persistent configuration for Shelf Rat.

Settings are stored via Calibre's JSONConfig under
``plugins/shelf_rat.json`` in the Calibre config directory. The server
password is stored in plain text there (same as most Calibre plugins);
this is intended for self-hosted servers on trusted machines.
"""

from qt.core import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QLabel,
    QGroupBox,
)

from calibre.gui2 import warning_dialog
from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/shelf_rat")

# Defaults
prefs.defaults["server_url"] = "https://"
prefs.defaults["username"] = ""
# Only persisted when 'remember_password' is True; otherwise kept in memory
# for the current Calibre session only (see the session cache below).
prefs.defaults["password"] = ""
prefs.defaults["remember_password"] = False
prefs.defaults["verify_ssl"] = True
# Calibre format codes, in preference order, used when picking which file to
# upload for a book. CBZ/CBR/CB7 are mapped to Grimmory's "CBX" group.
prefs.defaults["preferred_formats"] = "EPUB,PDF,AZW3,MOBI,FB2,CBZ,CBR,CB7"
prefs.defaults["replace_mode"] = "REPLACE_WHEN_PROVIDED"
# Where Calibre tags should land on Grimmory: 'categories' (= genres in the
# Grimmory UI) or 'tags'.
prefs.defaults["tags_field"] = "categories"
# Remembered last selection.
prefs.defaults["last_library_id"] = None
prefs.defaults["last_path_id"] = None
# Default metadata inclusion toggles.
prefs.defaults["include_series"] = True
prefs.defaults["include_tags"] = True
prefs.defaults["include_identifiers"] = True
prefs.defaults["include_description"] = True
prefs.defaults["apply_metadata_after_upload"] = True


REPLACE_MODES = ["REPLACE_WHEN_PROVIDED", "REPLACE_MISSING", "REPLACE_ALL"]


# --- session-only password cache -----------------------------------------
# Holds the password in memory for the life of the Calibre process when the
# user has NOT opted in to remembering it. Never written to disk.
_session_password = None


def set_session_password(pw):
    global _session_password
    _session_password = pw or None


def get_session_password():
    return _session_password


def get_effective_password():
    """The password to use right now, or None if we must prompt.

    Prefers the persisted password (only present when remembered), then the
    in-memory session value."""
    if prefs["remember_password"] and prefs["password"]:
        return prefs["password"]
    return _session_password


class ConfigWidget(QWidget):

    def __init__(self):
        QWidget.__init__(self)
        layout = QVBoxLayout(self)

        conn_box = QGroupBox("Grimmory server", self)
        layout.addWidget(conn_box)
        form = QFormLayout(conn_box)

        self.server_url = QLineEdit(self)
        self.server_url.setText(prefs["server_url"] or "")
        self.server_url.setPlaceholderText("https://")
        form.addRow("Server URL:", self.server_url)

        self.username = QLineEdit(self)
        self.username.setText(prefs["username"] or "")
        form.addRow("Username:", self.username)

        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setText(get_effective_password() or "")
        self.password.setPlaceholderText(
            "Leave blank to be asked once per Calibre session"
        )
        form.addRow("Password:", self.password)

        self.remember_password = QCheckBox(
            "Remember password (stored in plain text on disk)", self
        )
        self.remember_password.setChecked(bool(prefs["remember_password"]))
        form.addRow("", self.remember_password)

        self.verify_ssl = QCheckBox("Verify TLS/SSL certificate", self)
        self.verify_ssl.setChecked(bool(prefs["verify_ssl"]))
        self.verify_ssl.setToolTip(
            "Keep this on. Turning it off disables certificate AND hostname "
            "checking, exposing your connection (including your password) to "
            "interception."
        )
        self.verify_ssl.toggled.connect(self._on_verify_toggled)
        form.addRow("", self.verify_ssl)

        opts_box = QGroupBox("Upload options", self)
        layout.addWidget(opts_box)
        opts_form = QFormLayout(opts_box)

        self.preferred_formats = QLineEdit(self)
        self.preferred_formats.setText(prefs["preferred_formats"] or "")
        self.preferred_formats.setToolTip(
            "Comma-separated Calibre format codes, in the order Shelf Rat "
            "should try them when choosing which file to upload for a book."
        )
        opts_form.addRow("Preferred formats:", self.preferred_formats)

        self.tags_field = QComboBox(self)
        self.tags_field.addItem("Tags", "tags")
        self.tags_field.addItem("Categories / genres", "categories")
        idx = self.tags_field.findData(prefs["tags_field"])
        self.tags_field.setCurrentIndex(idx if idx >= 0 else 0)
        opts_form.addRow("Send Calibre tags as:", self.tags_field)

        self.replace_mode = QComboBox(self)
        for mode in REPLACE_MODES:
            self.replace_mode.addItem(mode, mode)
        idx = self.replace_mode.findData(prefs["replace_mode"])
        self.replace_mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.replace_mode.setToolTip(
            "How metadata updates are applied on the server.\n"
            "REPLACE_WHEN_PROVIDED only changes fields Shelf Rat sends.\n"
            "REPLACE_MISSING only fills empty fields.\n"
            "REPLACE_ALL overwrites everything."
        )
        opts_form.addRow("Metadata replace mode:", self.replace_mode)

        note = QLabel(
            "By default the password is kept only in memory and you are asked "
            'for it once per Calibre session. Tick "Remember password" to save '
            "it in plain text in the Calibre config folder (use only with "
            "self-hosted servers you trust).",
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def _on_verify_toggled(self, checked):
        if not checked:
            warning_dialog(
                self,
                "Shelf Rat",
                (
                    "Disabling TLS/SSL verification turns off certificate and "
                    "hostname checking. Your connection — including your "
                    "password — could be intercepted or tampered with. Only do "
                    "this for a server you control on a trusted network."
                ),
                show=True,
            )

    def save_settings(self):
        prefs["server_url"] = self.server_url.text().strip()
        prefs["username"] = self.username.text().strip()
        remember = self.remember_password.isChecked()
        pw = self.password.text()
        prefs["remember_password"] = remember
        # Persist only when remembering; otherwise wipe any stored copy but
        # keep it for this session so the user isn't immediately re-prompted.
        prefs["password"] = pw if remember else ""
        set_session_password(pw)
        prefs["verify_ssl"] = self.verify_ssl.isChecked()
        prefs["preferred_formats"] = self.preferred_formats.text().strip()
        prefs["tags_field"] = self.tags_field.currentData()
        prefs["replace_mode"] = self.replace_mode.currentData()
