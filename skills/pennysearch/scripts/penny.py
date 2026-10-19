#!/usr/bin/env python3
"""PennySearch command line. Run `python penny.py --help`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pennysearch.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
