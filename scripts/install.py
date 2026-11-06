"""Build the add-on and open it with Anki, which installs it.

    uv run python scripts/install.py

Anki asks to confirm, then needs a restart. Replacing an earlier install keeps your config
(`meta.json`) and `user_files/`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from build import build


def open_with_anki(path: Path) -> None:
    """Hand the file to Anki through the OS file association."""
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    elif sys.platform.startswith("win"):
        os.startfile(path)
    else:
        subprocess.run(["xdg-open", path], check=True)


def main() -> int:
    package = build()
    print(f"built {package}")
    open_with_anki(package)
    print("Confirm the install in Anki, then restart Anki.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
