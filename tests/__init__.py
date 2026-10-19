"""TaskFlow automated test suite.

Run from the project folder with:  python -m unittest -v

Python runs this file before any test module, and therefore before app.py is
imported. It points the app at a new temporary database outside the project,
checks that location is safe, and deletes it when the tests finish. The real
task_manager.db and .env are never opened.
"""
import atexit
import os
import shutil
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DATABASE = os.path.join(PROJECT_ROOT, "task_manager.db")

_test_folder = tempfile.mkdtemp(prefix="taskflow-tests-")
TEST_DATABASE = os.path.join(_test_folder, "test.db")


def _is_inside(path, folder):
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(folder)]) == os.path.realpath(folder)


# Refuse to run unless the test database is a brand-new file outside the project.
if _is_inside(TEST_DATABASE, PROJECT_ROOT):
    raise RuntimeError(f"Test database {TEST_DATABASE} must be outside the project folder.")

if os.path.realpath(TEST_DATABASE) == os.path.realpath(REAL_DATABASE):
    raise RuntimeError("Test database must not be the real task_manager.db.")

if os.path.exists(TEST_DATABASE):
    raise RuntimeError(f"Test database {TEST_DATABASE} already exists.")

# Set for this test process only: nothing is written to .env.
os.environ["TASKFLOW_DATABASE"] = TEST_DATABASE
os.environ["FLASK_SECRET_KEY"] = "test-only-secret-key-not-used-anywhere-else"

atexit.register(shutil.rmtree, _test_folder, ignore_errors=True)
