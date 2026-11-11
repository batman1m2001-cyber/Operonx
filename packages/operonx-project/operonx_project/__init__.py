"""operonx-project: a project's requirements, plans and board, read and kept.

A **project** is the product repo. It keeps a living BRD and a checklist in ``docs/brd/``, its plans as
``docs/*_PLAN.md`` with a status block, and its board in ``docs/brd/board.md``: one card per task, moved by
the assistant that works it (:mod:`operonx_project.board`). The plan of record is ``docs/LEAN_PROJECT_PLAN.md``
in the operonx repo.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
