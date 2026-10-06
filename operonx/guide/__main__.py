"""``python -m operonx.guide``: every installed operonx guide, and its pages."""

from operonx.guide import _title, installed

for g in installed():
    print(f"{g.name} — {g.dist} {g.version}: {g.dir}")
    for page in g.pages():
        print(f"  {page.name}  —  {_title(page)}")
