#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
The Calibre InterfaceAction that adds the Shelf Rat toolbar button.
"""

from qt.core import QIcon

from calibre.gui2 import error_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.shelf_rat.main import ShelfRatDialog


class ShelfRatAction(InterfaceAction):

    name = "Shelf Rat"
    # (text, icon, tooltip, keyboard shortcut). Icon is set in genesis().
    action_spec = (
        "Shelf Rat",
        None,
        "Upload selected books to your Grimmory server",
        None,
    )
    action_type = "current"

    def genesis(self):
        icon = self._load_icon()
        self.qaction.setIcon(icon)
        self.qaction.triggered.connect(self.show_dialog)

    def _load_icon(self):
        # Prefer a bundled icon; fall back to a built-in Calibre icon so the
        # button always has a sensible image.
        try:
            icon = get_icons("images/icon.png", "Shelf Rat")  # noqa: F821
            if icon is not None and not icon.isNull():
                return icon
        except Exception:
            pass
        if hasattr(QIcon, "ic"):
            try:
                return QIcon.ic("sync.png")
            except Exception:
                pass
        try:
            return QIcon(I("sync.png"))  # noqa: F821
        except Exception:
            return QIcon()

    def show_dialog(self):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            return error_dialog(
                self.gui,
                "Shelf Rat",
                (
                    "No books selected. Select one or "
                    "more books in your library first."
                ),
                show=True,
            )
        book_ids = list(map(self.gui.library_view.model().id, rows))
        d = ShelfRatDialog(self.gui, book_ids)
        d.exec()

    def apply_settings(self):
        # Settings are read fresh from prefs each time the dialog opens, so
        # there is nothing to cache here.
        pass
