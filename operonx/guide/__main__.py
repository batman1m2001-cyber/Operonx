"""``python -m operonx.guide``: where the guide is, and what it holds."""

from operonx import __version__
from operonx.guide import pages, path

print(f"operonx {__version__} guide for coding assistants: {path()}")
for page in pages():
    title = next(
        (
            line.lstrip("# ").strip()
            for line in page.read_text(encoding="utf-8").splitlines()
            if line.startswith("# ")
        ),
        page.stem,
    )
    print(f"  {page}  —  {title}")
