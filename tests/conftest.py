"""Shared pytest setup for the VaultSync server test suite.

Puts the repo root on sys.path so tests can `import app`, and provides safe
defaults for the env vars that `app.config` validates at import time.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("VAULTSYNC_SECRET", "testkey_for_tests")
os.environ.setdefault("DB_PASS", "testpass")
