"""Shared helpers for the TaskFlow tests.

app.py opens its database as soon as it is imported, so the database setting
is checked here BEFORE app.py is imported, and the app is checked again after.
"""
import os
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta

from tests import REAL_DATABASE, TEST_DATABASE


def _same_file(first, second):
    return os.path.realpath(first) == os.path.realpath(second)


# Check 1, before app.py is imported: the setting must still be the test database.
_configured = os.environ.get("TASKFLOW_DATABASE", "")

if not _configured or not _same_file(_configured, TEST_DATABASE) or _same_file(_configured, REAL_DATABASE):
    raise RuntimeError(f"TASKFLOW_DATABASE is {_configured!r}, not the test database. "
                       "app.py was not imported.")

import app as taskflow  # noqa: E402  (must come after the check above)

# Check 2, after import: the app really is using the test database.
if not _same_file(taskflow.DATABASE, TEST_DATABASE):
    raise RuntimeError(f"app.py is using {taskflow.DATABASE}, not the test database.")

app = taskflow.app

# TESTING only makes Flask report errors directly. It does not turn off CSRF:
# Flask-WTF would need WTF_CSRF_ENABLED = False for that, which is never set.
app.config["TESTING"] = True

TODAY = date.today()


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def csrf_from(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def db():
    connection = sqlite3.connect(TEST_DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def query_one(sql, params):
    with closing(db()) as connection:
        return connection.execute(sql, params).fetchone()
