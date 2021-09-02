# [Unreleased-InDev]

## Feature changes

- Implemented file serving functionality.
- Allowed uploading any file/items functionality.
- Renamed to tsuu

## Steps taken to allow easier development

Most of these pertain to making tools cross-platform.

- Added `promote_user.py` so you can set ranks without modding the db.
- uWSGI dropped from requirements (it is a deploy mechanism).
- Updated references to the wrong types in the models/genericized types based on storage model.
- Dropped elasticsearch.
- Unlinked **many** dependencies from their hardcoded versions because they just don't work due to Python changes.
- Switched default backend to SQLite.
- Forked from nyaadevs [on commit 4fe0ff](https://github.com/noirscape/nyaa/commit/4fe0ff5b1aa7ec7c9bb2667d97e10ce2a318c676)