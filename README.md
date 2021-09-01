# FNYAA

A fork of an anime tracker that is a file CMS. In early dev.

See CHANGELOG.md for all the changes.

## Setting up for development

This project uses the latest version of Python (development started on 3.9). Development mainly occurs on Windows, but other OSes should be supported.

### Code Quality

- Before we get any deeper, remember to follow PEP8 style guidelines and run `./dev.py lint` before committing to see a list of warnings/problems.
  - You may also use `./dev.py fix && ./dev.py isort` to automatically fix some of the issues reported by the previous command.
- Other than PEP8, try to keep your code clean and easy to understand, as well. It's only polite!

### Install dependencies

- Set up a virtualenv: `python -mvenv venv` and activate it (`source venv/bin/activate` on linux, `venv/Scripts/Activate.ps1` on Windows)
- Make sure `wheel` is installed (saves you all the compiletime for deps): `pip install -U wheel`
- Install the deps: `python install -Ur requirements.txt`

### Finishing up

The default storage backend is SQLite.

- Run `python db_create.py` to create the database and import categories
  - Follow the advice of `db_create.py` and run `./db_migrate.py stamp head` to mark the database version for Alembic
- Start the dev server with `python run.py`
- When you are finished developing, deactivate your virtualenv with `pyenv deactivate` or `source deactivate` (or just close your shell session)

## Database migrations

- Database migrations are done with [flask-Migrate](https://flask-migrate.readthedocs.io/), a wrapper around [Alembic](http://alembic.zzzcomputing.com/en/latest/).
- If someone has made changes in the database schema and included a new migration script:
  - If your database has never been marked by Alembic (you're on a database from before the migrations), run `./db_migrate.py stamp head` before pulling the new migration script(s).
    - If you already have the new scripts, check the output of `./db_migrate.py history` instead and choose a hash that matches your current database state, then run `./db_migrate.py stamp <hash>`.
  - Update your branch (eg. `git fetch && git rebase origin/master`)
  - Run `./db_migrate.py upgrade head` to run the migration. Done!
- If *you* have made a change in the database schema:
  - Save your changes in `models.py` and ensure the database schema matches the previous version (ie. your new tables/columns are not added to the live database)
  - Run `./db_migrate.py migrate -m "Short description of changes"` to automatically generate a migration script for the changes
    - Check the script (`migrations/versions/...`) and make sure it works! Alembic may not able to notice all changes.
  - Run `./db_migrate.py upgrade` to run the migration and verify the upgrade works.
    - (Run `./db_migrate.py downgrade` to verify the downgrade works as well, then upgrade again)
