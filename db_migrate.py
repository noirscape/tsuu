#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys

from flask_script import Manager
from flask_migrate import Migrate, MigrateCommand

from tsuu import create_app
from tsuu.extensions import db

app = create_app('config')
migrate = Migrate(app, db)

manager = Manager(app)
manager.add_command("db", MigrateCommand)

if __name__ == "__main__":
    # Patch sys.argv to default to 'db'
    sys.argv.insert(1, 'db')

    manager.run()
