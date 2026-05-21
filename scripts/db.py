"""SQLite database access."""

import os
import sqlite3
from contextlib import contextmanager

DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flickr.db")


def db():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError("Database not found. Visit http://localhost:8000/sync to run a sync.")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db():
    if not os.path.exists(DB_FILE):
        raise FileNotFoundError("Database not found. Visit http://localhost:8000/sync to run a sync.")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
