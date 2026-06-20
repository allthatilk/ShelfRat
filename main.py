#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
The Shelf Rat dialog and its background worker.

Two modes:
  * Upload  - upload the selected books' files to a chosen library + path on
              the Grimmory server (directly, NOT via bookdrop), optionally
              applying Calibre metadata (series/tags/...) afterwards.
  * Metadata only - match the selected books to books already on the server
              (by title) and update their metadata only.

Network work runs in a QThread so the GUI stays responsive.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from html import escape

from qt.core import (
    Qt,
    QThread,
    pyqtSignal,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QRadioButton,
    QComboBox,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressDialog,
    QDialogButtonBox,
)

from calibre.gui2 import error_dialog, info_dialog, warning_dialog

from calibre_plugins.shelf_rat.config import (
    prefs,
    REPLACE_MODES,
    get_effective_password,
    set_session_password,
)
from calibre_plugins.shelf_rat.grimmory_client import (
    GrimmoryClient,
    GrimmoryError,
)

# Calibre format code -> Grimmory allowedFormats group.
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

# How long to keep polling the server for an uploaded book to appear before
# applying metadata (the library scan that ingests an upload is asynchronous).
MATCH_POLL_TRIES = 8
MATCH_POLL_DELAY = 4  # seconds


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


class Worker(QThread):

    progress = pyqtSignal(int, int, str)  # done, total, message
    libraries_ready = pyqtSignal(object)  # list of Library dicts
    results_ready = pyqtSignal(object)  # list of (job, status, detail)
    failed = pyqtSignal(str)

    def __init__(
        self,
        parent,
        kind,
        conn,
        jobs=None,
        library_id=None,
        path_id=None,
        replace_mode="REPLACE_WHEN_PROVIDED",
        apply_metadata=False,
    ):
        QThread.__init__(self, parent)
        self.kind = kind  # 'libraries' | 'upload' | 'metadata'
        self.conn = conn  # dict: url, username, password, verify_ssl
        self.jobs = jobs or []
        self.library_id = library_id
        self.path_id = path_id
        self.replace_mode = replace_mode
        self.apply_metadata = apply_metadata
        self._cancel = False

    def cancel(self):
        self._cancel = True

    # -- helpers -----------------------------------------------------------
    def _client(self):
        client = GrimmoryClient(
            self.conn["url"], verify_ssl=self.conn["verify_ssl"]
        )
        client.login(self.conn["username"], self.conn["password"])
        return client

    @staticmethod
    def _build_title_index(books):
        index = {}
        for book in books:
            book_meta = book.get("metadata") or {}
            title = _normalize_title(book_meta.get("title"))
            if not title:
                continue
            index.setdefault(title, []).append(book)
        return index

    def _match_book_id(self, index, job):
        title = _normalize_title(job["title"])
        if not title:
            # An untitled book has nothing reliable to match on; never fall
            # back to a placeholder title (it could hit an unrelated book).
            return None, "the book has no title to match against the server"
        candidates = index.get(title)
        if not candidates:
            return None, "no match on server"
        if len(candidates) == 1:
            return candidates[0].get("id"), None
        # Disambiguate multiple same-title books by author.
        wanted_authors = {author.lower() for author in job.get("authors", [])}
        for book in candidates:
            book_meta = book.get("metadata") or {}
            book_authors = {
                author.lower() for author in (book_meta.get("authors") or [])
            }
            if wanted_authors & book_authors:
                return book.get("id"), None
        # Several books share this title and the author doesn't disambiguate.
        # Skip rather than risk editing the wrong book.
        return None, (
            "multiple books on the server share this title; "
            "skipped to avoid editing the wrong one"
        )

    def _apply_metadata(self, client, book_id, changes):
        """Update a book's metadata.

        For REPLACE_WHEN_PROVIDED / REPLACE_MISSING we send only the fields we
        changed; the server leaves the rest alone. REPLACE_ALL overwrites every
        field, so we first read the book's current metadata and merge our
        changes onto it -- otherwise fields we don't send would be blanked. If
        that read fails we fall back to a non-destructive partial update rather
        than risk wiping data."""
        if self.replace_mode != "REPLACE_ALL":
            client.update_metadata(
                book_id, changes, replace_mode=self.replace_mode
            )
            return

        base = None
        try:
            book = client.get_book(book_id)
            if isinstance(book, dict):
                base = book.get("metadata")
        except GrimmoryError:
            base = None
        if base is None:
            client.update_metadata(
                book_id, changes, replace_mode="REPLACE_WHEN_PROVIDED"
            )
            return
        merged = dict(base)
        merged.update(changes)
        merged["bookId"] = book_id
        client.update_metadata(book_id, merged, replace_mode="REPLACE_ALL")

    def _metadata_failure_detail(self, book_id, error, metadata):
        return f"{error}\nsent to book {book_id}: {json.dumps(metadata, ensure_ascii=False)}"

    # -- run ---------------------------------------------------------------
    def run(self):
        try:
            if self.kind == "libraries":
                client = self._client()
                self.libraries_ready.emit(client.get_libraries())
                return
            if self.kind == "upload":
                self._run_upload()
            else:
                self._run_metadata_only()
        except GrimmoryError as e:
            self.failed.emit(str(e))
        except Exception as e:  # pragma: no cover - defensive
            import traceback

            self.failed.emit(
                f"Unexpected error: {e}\n{traceback.format_exc()}"
            )

    def _run_upload(self):
        client = self._client()
        total = len(self.jobs)
        results = []
        uploaded = []
        for i, job in enumerate(self.jobs):
            if self._cancel:
                break
            self.progress.emit(i, total, f"Uploading: {job['title']}")
            data = job["read_bytes"]()
            if not data:
                results.append((job, "skipped", "no uploadable file found"))
                continue
            try:
                client.upload_file(
                    self.library_id, self.path_id, job["filename"], data
                )
                results.append((job, "uploaded", ""))
                uploaded.append(job)
            except GrimmoryError as e:
                results.append((job, "failed", str(e)))

        if self.apply_metadata and uploaded and not self._cancel:
            self._apply_after_upload(client, uploaded, results)

        self.results_ready.emit(results)

    def _apply_after_upload(self, client, uploaded, results):
        """Poll the server until uploaded books are scanned in, then set
        their metadata. Updates the matching entries in ``results``."""
        pending = list(uploaded)
        matched = {}  # calibre id -> grimmory book id
        for attempt in range(MATCH_POLL_TRIES):
            if self._cancel or not pending:
                break
            self.progress.emit(
                len(uploaded) - len(pending),
                len(uploaded),
                f"Waiting for server to scan uploads "
                f"(try {attempt + 1}/{MATCH_POLL_TRIES})...",
            )
            index = self._build_title_index(client.get_books())
            still = []
            for job in pending:
                book_id, _note = self._match_book_id(index, job)
                if book_id is not None:
                    matched[job["calibre_id"]] = book_id
                else:
                    still.append(job)
            pending = still
            if pending and attempt < MATCH_POLL_TRIES - 1:
                time.sleep(MATCH_POLL_DELAY)

        for idx, (job, status, detail) in enumerate(results):
            if status != "uploaded":
                continue
            book_id = matched.get(job["calibre_id"])
            if book_id is None:
                results[idx] = (
                    job,
                    "uploaded",
                    (
                        "uploaded, but could not match it on the "
                        "server to set metadata"
                    ),
                )
                continue
            try:
                self._apply_metadata(client, book_id, job["metadata"])
                results[idx] = (job, "uploaded+metadata", "")
            except GrimmoryError as e:
                results[idx] = (
                    job,
                    "uploaded",
                    "uploaded, but metadata update failed: "
                    + self._metadata_failure_detail(
                        book_id, e, job["metadata"]
                    ),
                )

    def _run_metadata_only(self):
        client = self._client()
        total = len(self.jobs)
        self.progress.emit(0, total, "Fetching books from server...")
        index = self._build_title_index(client.get_books())
        results = []
        for i, job in enumerate(self.jobs):
            if self._cancel:
                break
            self.progress.emit(i, total, f"Updating: {job['title']}")
            book_id, note = self._match_book_id(index, job)
            if book_id is None:
                results.append((job, "skipped", note))
                continue
            try:
                self._apply_metadata(client, book_id, job["metadata"])
                results.append((job, "metadata", note or ""))
            except GrimmoryError as e:
                results.append(
                    (
                        job,
                        "failed",
                        self._metadata_failure_detail(
                            book_id, e, job["metadata"]
                        ),
                    )
                )
        self.results_ready.emit(results)


class PasswordDialog(QDialog):
    """One-shot prompt for the Grimmory password, with an opt-in to remember
    it. Returns (password, remember) or (None, False) if cancelled."""

    @classmethod
    def ask(cls, parent, username):
        dialog = cls(parent, username)
        if (
            dialog.exec() == QDialog.DialogCode.Accepted
            and dialog.password.text()
        ):
            return dialog.password.text(), dialog.remember.isChecked()
        return None, False

    def __init__(self, parent, username):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Shelf Rat")
        layout = QVBoxLayout(self)
        # The label renders as rich text (it contains <b>), so escape the
        # interpolated username to prevent HTML injection from a crafted value.
        layout.addWidget(
            QLabel(
                f"Enter the Grimmory password for "
                f"<b>{escape(username or '(no username set)')}</b>:",
                self,
            )
        )
        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password)
        self.remember = QCheckBox(
            "Remember password (stored in plain text on disk)", self
        )
        self.remember.setChecked(bool(prefs["remember_password"]))
        layout.addWidget(self.remember)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.password.setFocus()


class ShelfRatDialog(QDialog):

    def __init__(self, gui, book_ids):
        QDialog.__init__(self, gui)
        self.gui = gui
        self.db = gui.current_db.new_api
        self.book_ids = book_ids
        self.libraries = []  # cached Library dicts from the server
        self.worker = None
        self.progress = None

        self.setWindowTitle("Shelf Rat")
        self.setMinimumWidth(540)
        self._build_ui()
        # Load libraries up front only if we already have a password (remembered
        # or entered earlier this session) so opening the dialog never triggers
        # an unprompted password dialog.
        if (
            prefs["username"]
            and prefs["server_url"]
            and get_effective_password()
        ):
            self._fetch_libraries()

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(
            QLabel(f"<b>{len(self.book_ids)}</b> book(s) selected.", self)
        )

        mode_box = QGroupBox("Action", self)
        layout.addWidget(mode_box)
        mode_l = QVBoxLayout(mode_box)
        self.rb_upload = QRadioButton("Upload books to Grimmory", self)
        self.rb_metadata = QRadioButton(
            "Update metadata only (for books already on the server)", self
        )
        self.rb_upload.setChecked(True)
        mode_l.addWidget(self.rb_upload)
        mode_l.addWidget(self.rb_metadata)

        # Destination
        self.dest_box = QGroupBox("Destination", self)
        layout.addWidget(self.dest_box)
        dest_l = QFormLayout(self.dest_box)
        self.library_combo = QComboBox(self)
        self.library_combo.currentIndexChanged.connect(self._update_paths)
        dest_l.addRow("Library:", self.library_combo)
        self.path_combo = QComboBox(self)
        dest_l.addRow("Path:", self.path_combo)
        self.refresh_btn = QPushButton("Connect / refresh libraries", self)
        self.refresh_btn.clicked.connect(self._fetch_libraries)
        dest_l.addRow("", self.refresh_btn)

        # Metadata options
        meta_box = QGroupBox("Metadata", self)
        layout.addWidget(meta_box)
        meta_l = QVBoxLayout(meta_box)
        self.cb_series = QCheckBox("Include series (name & number)", self)
        self.cb_series.setChecked(bool(prefs["include_series"]))
        self.cb_tags = QCheckBox("Include tags", self)
        self.cb_tags.setChecked(bool(prefs["include_tags"]))
        self.cb_identifiers = QCheckBox(
            "Include identifiers (ISBN/ASIN/...)", self
        )
        self.cb_identifiers.setChecked(bool(prefs["include_identifiers"]))
        self.cb_desc = QCheckBox("Include description/comments", self)
        self.cb_desc.setChecked(bool(prefs["include_description"]))
        self.cb_apply = QCheckBox("Apply this metadata after uploading", self)
        self.cb_apply.setChecked(bool(prefs["apply_metadata_after_upload"]))
        for checkbox in (
            self.cb_series,
            self.cb_tags,
            self.cb_identifiers,
            self.cb_desc,
            self.cb_apply,
        ):
            meta_l.addWidget(checkbox)

        rm_l = QHBoxLayout()
        rm_l.addWidget(QLabel("Replace mode:", self))
        self.replace_combo = QComboBox(self)
        for mode in REPLACE_MODES:
            self.replace_combo.addItem(mode, mode)
        idx = self.replace_combo.findData(prefs["replace_mode"])
        self.replace_combo.setCurrentIndex(idx if idx >= 0 else 0)
        rm_l.addWidget(self.replace_combo)
        rm_l.addStretch(1)
        meta_l.addLayout(rm_l)

        # Buttons
        btn_l = QHBoxLayout()
        btn_l.addStretch(1)
        self.start_btn = QPushButton("Start", self)
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._start)
        self.close_btn = QPushButton("Close", self)
        self.close_btn.clicked.connect(self.reject)
        btn_l.addWidget(self.start_btn)
        btn_l.addWidget(self.close_btn)
        layout.addLayout(btn_l)

    # -- libraries ---------------------------------------------------------
    def _ensure_password(self):
        """Return the password to use, prompting once if needed. Returns None
        if the user cancels the prompt."""
        pw = get_effective_password()
        if pw:
            return pw
        pw, remember = PasswordDialog.ask(self, prefs["username"].strip())
        if pw is None:
            return None
        set_session_password(pw)  # in-memory for the rest of the session
        if remember:
            prefs["remember_password"] = True
            prefs["password"] = pw
        return pw

    def _conn(self):
        """Build connection settings, resolving the password (may prompt).
        Returns None if the user cancels the password prompt."""
        pw = self._ensure_password()
        if pw is None:
            return None
        return {
            "url": prefs["server_url"].strip(),
            "username": prefs["username"].strip(),
            "password": pw,
            "verify_ssl": bool(prefs["verify_ssl"]),
        }

    def _require_config(self):
        if not prefs["server_url"].strip() or not prefs["username"].strip():
            error_dialog(
                self,
                "Shelf Rat",
                (
                    "Configure your Grimmory server URL and credentials first "
                    "(Preferences → Plugins → Shelf Rat → "
                    "Customize plugin)."
                ),
                show=True,
            )
            return False
        return True

    def _busy(self):
        return self.worker is not None and self.worker.isRunning()

    def _fetch_libraries(self):
        if not self._require_config():
            return
        if self._busy():
            return warning_dialog(
                self,
                "Shelf Rat",
                (
                    "An operation is already running; "
                    "please wait for it to finish."
                ),
                show=True,
            )
        conn = self._conn()
        if conn is None:
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Connecting...")
        self.worker = Worker(self, "libraries", conn)
        self.worker.libraries_ready.connect(self._on_libraries)
        self.worker.failed.connect(self._on_lib_error)
        self.worker.start()

    def _on_libraries(self, libraries):
        self.worker = None
        self.libraries = libraries or []
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("Connect / refresh libraries")
        self.library_combo.blockSignals(True)
        self.library_combo.clear()
        for lib in self.libraries:
            self.library_combo.addItem(lib.get("name", "Library"), lib)
        self.library_combo.blockSignals(False)
        # Restore last selection.
        last = prefs["last_library_id"]
        if last is not None:
            for i, lib in enumerate(self.libraries):
                if lib.get("id") == last:
                    self.library_combo.setCurrentIndex(i)
                    break
        self._update_paths()
        if not self.libraries:
            warning_dialog(
                self,
                "Shelf Rat",
                "The server returned no libraries.",
                show=True,
            )

    def _on_lib_error(self, msg):
        self.worker = None
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("Connect / refresh libraries")
        error_dialog(
            self,
            "Shelf Rat",
            "Could not load libraries.",
            det_msg=msg,
            show=True,
        )

    def _update_paths(self, *args):
        lib = self.library_combo.currentData()
        self.path_combo.clear()
        if not lib:
            return
        for path in lib.get("paths") or []:
            self.path_combo.addItem(
                path.get("path", str(path.get("id"))), path
            )
        last = prefs["last_path_id"]
        if last is not None:
            for i, path in enumerate(lib.get("paths") or []):
                if path.get("id") == last:
                    self.path_combo.setCurrentIndex(i)
                    break

    # -- start -------------------------------------------------------------
    def _meta_opts(self):
        return dict(
            include_series=self.cb_series.isChecked(),
            include_tags=self.cb_tags.isChecked(),
            tags_field=prefs["tags_field"],
            include_identifiers=self.cb_identifiers.isChecked(),
            include_description=self.cb_desc.isChecked(),
        )

    def _build_jobs(self, upload, allowed_groups):
        """Build the per-book work items on the GUI thread (DB reads here are
        cheap; the big format bytes are read lazily inside the worker)."""
        opts = self._meta_opts()
        preferred = [
            fmt.strip().upper()
            for fmt in (prefs["preferred_formats"] or "").split(",")
            if fmt.strip()
        ]
        accepts = (
            ", ".join(sorted(allowed_groups))
            if allowed_groups
            else "any format"
        )
        jobs = []
        skipped = []  # list of (label, reason) for books we won't process
        for cid in self.book_ids:
            mi = self.db.get_metadata(cid)
            label = (
                mi.title or ", ".join(mi.authors or []) or f"calibre id {cid}"
            )
            # A book with no title can't be matched on the server (for
            # post-upload metadata or in metadata-only mode), so refuse it.
            if not (mi.title or "").strip():
                reason = "no title set, so it can't be matched on the server"
                skipped.append((label, reason))
                continue
            job = {
                "calibre_id": cid,
                "title": mi.title,
                "authors": list(mi.authors or []),
                "metadata": calibre_to_grimmory_metadata(mi, **opts),
            }
            if upload:
                have = self.db.formats(cid)
                fmt = choose_upload_format(have, preferred, allowed_groups)
                if not fmt:
                    reason = (
                        f"no format this library accepts "
                        f"(has: {', '.join(have) or 'no files'}; "
                        f"accepts: {accepts})"
                    )
                    skipped.append((label, reason))
                    continue
                ext = fmt.lower()
                job["fmt"] = fmt
                job["filename"] = self._safe_filename(mi, ext)
                job["read_bytes"] = (
                    lambda db=self.db, cid=cid, fmt=fmt, mi=mi: embed_metadata(
                        db, cid, fmt, mi
                    )
                )
            jobs.append(job)
        return jobs, skipped

    @staticmethod
    def _safe_filename(mi, ext):
        from calibre.utils.filenames import ascii_filename

        title = mi.title or "book"
        author = (mi.authors or ["Unknown"])[0]
        base = ascii_filename(f"{title} - {author}").strip() or "book"
        return f"{base}.{ext}"

    def _start(self):
        if not self._require_config():
            return
        if self._busy():
            return warning_dialog(
                self,
                "Shelf Rat",
                (
                    "An operation is already running; "
                    "please wait for it to finish."
                ),
                show=True,
            )
        upload = self.rb_upload.isChecked()

        library_id = path_id = None
        allowed_groups = None
        if upload:
            lib = self.library_combo.currentData()
            path = self.path_combo.currentData()
            if not lib or not path:
                return error_dialog(
                    self,
                    "Shelf Rat",
                    (
                        "Choose a destination library and "
                        'path. Click "Connect / refresh libraries" if the lists '
                        "are empty."
                    ),
                    show=True,
                )
            library_id = lib.get("id")
            path_id = path.get("id")
            allowed_groups = set(lib.get("allowedFormats") or [])
            prefs["last_library_id"] = library_id
            prefs["last_path_id"] = path_id

        jobs, skipped = self._build_jobs(upload, allowed_groups)
        detail = "\n".join(f"- {label}: {reason}" for label, reason in skipped)
        if not jobs:
            return error_dialog(
                self,
                "Shelf Rat",
                "Nothing to do.",
                det_msg=detail or None,
                show=True,
            )
        if skipped:
            warning_dialog(
                self,
                "Shelf Rat",
                f"{len(skipped)} book(s) will be skipped.",
                det_msg=detail,
                show=True,
            )

        # Persist metadata toggles.
        prefs["include_series"] = self.cb_series.isChecked()
        prefs["include_tags"] = self.cb_tags.isChecked()
        prefs["include_identifiers"] = self.cb_identifiers.isChecked()
        prefs["include_description"] = self.cb_desc.isChecked()
        prefs["apply_metadata_after_upload"] = self.cb_apply.isChecked()
        prefs["replace_mode"] = self.replace_combo.currentData()

        self._run_worker(upload, jobs, library_id, path_id)

    def _run_worker(self, upload, jobs, library_id, path_id):
        conn = self._conn()
        if conn is None:
            return
        kind = "upload" if upload else "metadata"
        self.worker = Worker(
            self,
            kind,
            conn,
            jobs=jobs,
            library_id=library_id,
            path_id=path_id,
            replace_mode=self.replace_combo.currentData(),
            apply_metadata=self.cb_apply.isChecked(),
        )

        self.progress = QProgressDialog(
            "Starting...", "Cancel", 0, max(1, len(jobs)), self
        )
        self.progress.setWindowTitle("Shelf Rat")
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.setMinimumDuration(0)
        self.progress.canceled.connect(self.worker.cancel)

        self.worker.progress.connect(self._on_progress)
        self.worker.results_ready.connect(self._on_results)
        self.worker.failed.connect(self._on_failed)
        self.start_btn.setEnabled(False)
        self.worker.start()

    # -- worker callbacks --------------------------------------------------
    def _on_progress(self, done, total, message):
        if self.progress is None:
            return
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        self.progress.setLabelText(message)

    def _finish(self):
        self.worker = None
        if self.progress is not None:
            self.progress.reset()
            self.progress = None
        self.start_btn.setEnabled(True)

    def _on_failed(self, msg):
        self._finish()
        error_dialog(
            self, "Shelf Rat", "The operation failed.", det_msg=msg, show=True
        )

    def _on_results(self, results):
        self._finish()
        counts = {}
        lines = []
        for job, status, detail in results:
            counts[status] = counts.get(status, 0) + 1
            line = f"[{status}] {job['title']}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
        summary = ", ".join(
            f"{count} {status}" for status, count in sorted(counts.items())
        )
        # 'skipped' is an expected outcome (e.g. no match on the server), not a
        # problem. An 'uploaded' row with a detail message is a partial failure
        # (uploaded, but metadata couldn't be matched/applied).
        bad = any(
            status == "failed" or (status == "uploaded" and detail)
            for _job, status, detail in results
        )
        det = "\n".join(lines)
        if bad:
            warning_dialog(
                self,
                "Shelf Rat",
                f"Done with some issues: {summary}",
                det_msg=det,
                show=True,
            )
        else:
            info_dialog(
                self, "Shelf Rat", f"Done: {summary}", det_msg=det, show=True
            )
