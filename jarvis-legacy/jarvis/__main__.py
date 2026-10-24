"""``python -m jarvis`` — то же, что команда ``jarvis``."""

from __future__ import annotations

import sys

from .interfaces.cli import main

if __name__ == "__main__":
    sys.exit(main())
