# Shelf Rat — a Calibre plugin for Grimmory

Upload books from your Calibre library **directly** to a self-hosted
[Grimmory](https://grimmory.org) server (a BookLore fork) — no bookdrop
required. Select one or many books, pick a destination library and path, and
Shelf Rat uploads the files and (optionally) pushes their metadata, including
**series** information. It can also run in a **metadata-only** mode to update
tags/series on books that already live on the server.

## Features

- Toolbar button in the main Calibre window ("Shelf Rat").
- Multi-select: upload all selected books in one go.
- Direct upload to a chosen **library** and **path** (uses
  `POST /api/v1/files/upload`, *not* bookdrop).
- Series-aware: sends `seriesName` / `seriesNumber` from Calibre's series
  field.
- Optional metadata push after upload: tags, authors, publisher, language,
  identifiers (ISBN/ASIN/Goodreads/Google), description.
- **Metadata-only** mode: match selected Calibre books to books already on the
  server (by title, disambiguated by author) and update their metadata without
  re-uploading.
- Picks the best file format per book based on a configurable preference list
  and the target library's `allowedFormats` (Calibre `CBZ/CBR/CB7` map to
  Grimmory's `CBX`).
- Runs network work off the UI thread with a progress dialog.

## Requirements

- Calibre **6.0** or newer (uses PyQt6).
- A reachable Grimmory server and an account with upload / edit-metadata
  permission.

## Install

From this directory:

```sh
# macOS app-bundle install of Calibre:
/Applications/calibre.app/Contents/MacOS/calibre-customize -b .

# or, if the Calibre CLI tools are on your PATH:
calibre-customize -b .
```

Then **restart Calibre**. The "Shelf Rat" button appears on the main toolbar
(if not, add it via *Preferences → Toolbars & menus → The main toolbar*).

Rapid iterate-and-test loop:

```sh
CALIBRE=/Applications/calibre.app/Contents/MacOS
"$CALIBRE/calibre-debug" -s            # shut down running Calibre
"$CALIBRE/calibre-customize" -b .      # rebuild + install
"$CALIBRE/calibre"                     # relaunch
```

Uninstall: *Preferences → Plugins → Shelf Rat → Remove plugin*, or
`calibre-customize -r "Shelf Rat"`.

## Configure

*Preferences → Plugins → Shelf Rat → Customize plugin*:

- **Server URL** — e.g. `https://grimmory.example.com` (include the port if
  needed, e.g. `http://localhost:6060`).
- **Username** — your Grimmory login name.
- **Password** — by default kept **in memory only**: Shelf Rat asks for it once
  per Calibre session (the first time it needs to connect). Tick **Remember
  password** to persist it in plain text in the Calibre config folder (use only
  with self-hosted servers you trust). Unticking it wipes any stored copy.
- **Verify TLS/SSL certificate** — uncheck for self-signed certs.
- **Preferred formats** — ordered Calibre format codes to choose from when a
  book has several files.
- **Send Calibre tags as** — `tags` or `categories` on Grimmory.
- **Metadata replace mode** — `REPLACE_WHEN_PROVIDED` (default; only touches
  fields Shelf Rat sends), `REPLACE_MISSING`, or `REPLACE_ALL`.

## Use

1. Select one or more books in Calibre.
2. Click **Shelf Rat**.
3. Choose an action:
   - **Upload books to Grimmory** — pick a Library and Path (click
     *Connect / refresh libraries* if the lists are empty), choose which
     metadata to include, and whether to apply it after upload.
   - **Update metadata only** — update series/tags/etc. on books already on the
     server (no upload).
4. Click **Start**. A progress dialog runs the job; a summary lists what was
   uploaded / updated / skipped.

## How matching works (and its limits)

The direct-upload endpoint returns `204 No Content` with **no book ID** — the
server ingests the file asynchronously via its library scan. So to apply
metadata after an upload, Shelf Rat polls `GET /api/v1/books` and matches the
new book by **title** (disambiguating by author when several share a title).

Implications:
- After upload, there can be a short delay before the book is scanned in; Shelf
  Rat retries for a while. If a match still isn't found, the file is uploaded
  but its metadata isn't applied (reported in the summary) — you can re-run in
  *metadata-only* mode later.
- Title matching is case-insensitive and trimmed. Very generic or duplicate
  titles may match ambiguously (the summary flags these).

## Project layout

| File | Purpose |
|---|---|
| `__init__.py` | Plugin declaration (`InterfaceActionBase`). |
| `plugin-import-name-shelf_rat.txt` | Enables `calibre_plugins.shelf_rat.*` imports. |
| `ui.py` | Toolbar button (`InterfaceAction`). |
| `main.py` | Dialog, background worker, Calibre→Grimmory metadata mapping. |
| `config.py` | Preferences + config widget (`JSONConfig`). |
| `grimmory_client.py` | Dependency-free Grimmory API client (stdlib `urllib`). |

## Notes

- No third-party Python dependencies — Calibre doesn't bundle `requests`, so
  the client uses the standard library only.
- Audiobook formats are recognized for the `AUDIOBOOK` library type but are not
  the primary focus.
- Uploads are buffered **in memory**: each book's file is read in full (with
  current metadata embedded) and sent as a single request, one book at a time.
  This is fine for typical ebooks, but a single very large file (e.g. a
  multi-hundred-MB audiobook or comic) briefly needs roughly its own size in
  RAM. Memory is released before the next book, so it doesn't accumulate across
  a batch.

## License

Copyright (C) 2026 allthatilk.

Shelf Rat is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License v3.0 or later** (GPL-3.0-or-later) as
published by the Free Software Foundation. It is distributed in the hope that it
will be useful, but **without any warranty**. See the [LICENSE](LICENSE) file
for the full text.

GPLv3 is also the license of Calibre itself, which this plugin builds on.
