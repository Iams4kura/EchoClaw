"""Shared test fixtures."""

import sys
import os

# Ensure the project root is on sys.path so `from src.*` imports work
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
