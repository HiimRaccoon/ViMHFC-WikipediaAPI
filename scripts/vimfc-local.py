"""Invoke this checkout using its Python dependencies, never a stale installed vimfc."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend' / 'src'))

from vimfc.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
