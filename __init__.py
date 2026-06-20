#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Shelf Rat - a Calibre plugin that uploads books from your Calibre library
directly to a Grimmory server (a BookLore fork), with series/metadata support.

This module only declares the plugin to Calibre. The actual GUI lives in
``ui.py`` and the logic in ``main.py`` / ``grimmory_client.py``. Keep the
imports here light: Calibre imports this file in non-GUI contexts too.
"""

from calibre.customize import InterfaceActionBase


class ShelfRat(InterfaceActionBase):

    name = "Shelf Rat"
    description = (
        "Upload books from your Calibre library directly to a "
        "Grimmory server, with series and metadata support."
    )
    supported_platforms = ["windows", "osx", "linux"]
    author = "allthatilk"
    version = (0, 1, 0)
    minimum_calibre_version = (6, 0, 0)

    # Points Calibre at the InterfaceAction subclass that creates the toolbar
    # button. Format is '<import-package>.<module>:<class>'. The import package
    # name comes from the empty 'plugin-import-name-shelf_rat.txt' file.
    actual_plugin = "calibre_plugins.shelf_rat.ui:ShelfRatAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.shelf_rat.config import ConfigWidget

        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
        ac = self.actual_plugin_
        if ac is not None:
            ac.apply_settings()
