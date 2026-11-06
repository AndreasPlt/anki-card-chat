"""Package the add-on as an `.ankiaddon` file.

    uv run python scripts/build.py      # writes dist/anki_card_chat.ankiaddon

The archive holds the contents of `src/anki_card_chat` with `manifest.json` at its root, the
layout Anki installs from. Anki takes the add-on's display name from that manifest.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ADDON_NAME = "anki_card_chat"
ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "src" / ADDON_NAME


def build() -> Path:
    """Write `dist/<ADDON_NAME>.ankiaddon` and return its path."""
    if not PACKAGE_DIR.is_dir():
        raise SystemExit(f"add-on package not found: {PACKAGE_DIR}")
    target = ROOT / "dist" / f"{ADDON_NAME}.ankiaddon"
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            relative = path.relative_to(PACKAGE_DIR)
            if path.is_file() and "__pycache__" not in relative.parts:
                archive.write(path, relative.as_posix())
    return target


def main() -> int:
    print(f"built {build()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
