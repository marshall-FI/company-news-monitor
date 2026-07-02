from __future__ import annotations

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
PAGES_DIR = REPO_ROOT / "pages-dist"
GENERATED_DIR = REPO_ROOT / "public" / "generated"
STATIC_READER = REPO_ROOT / "static-reader" / "index.html"


def main() -> int:
    if not GENERATED_DIR.exists():
        raise SystemExit("Run feed-generator/generate.py before building the Pages artifact.")
    if not STATIC_READER.exists():
        raise SystemExit(f"Missing static reader at {STATIC_READER}")

    if PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)
    PAGES_DIR.mkdir(parents=True)

    shutil.copy2(STATIC_READER, PAGES_DIR / "index.html")
    shutil.copytree(GENERATED_DIR, PAGES_DIR / "generated")
    (PAGES_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Built GitHub Pages artifact at {PAGES_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
