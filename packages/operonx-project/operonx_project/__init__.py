"""operonx-project: a project's requirements and board, read and kept.

A **project** is the product repo. It keeps a living BRD and a checklist in ``docs/brd/``, and its board in
``docs/brd/board.md``: cycles (a plan's round of work, its phases as cards) and a Backlog, each card moved by
the assistant that works it (:mod:`operonx_project.board`). A plan doc (``docs/*_PLAN.md``) says why and how;
where it stands is its cycle. The plan of record is ``docs/LEAN_PROJECT_PLAN.md``
in the operonx repo.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
