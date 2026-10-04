#!/usr/bin/env python3
"""Simple Notes - a minimalist markdown note-taker.

Run with:  python3 noteapp.py
"""
import sys


def main() -> int:
    if sys.version_info < (3, 9):
        print("Simple Notes requires Python 3.9 or newer.")
        return 1

    try:
        import PyQt6.QtWidgets  # noqa: F401  (the only dependency that isn't bundled)
    except ImportError:
        print("Simple Notes needs PyQt6. Install it with:\n  python3 -m pip install PyQt6")
        return 1

    from simplenotes.app import run

    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
