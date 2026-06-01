"""Shared pytest fixtures."""

import sys
from pathlib import Path

# Make the package importable without an editable install.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
