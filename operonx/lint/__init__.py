"""``operonx lint``: an operonx project's conventions, checked.

The static rules (C3, C5–C12, :mod:`operonx.lint.rules`) read source only;
``--build`` (C1, C2, :mod:`operonx.lint.build`) loads the application and
builds every graph with the network blocked. The rules are the ones the
project's ``AGENTS.md`` states, so a coding agent's work can be checked
rather than trusted.
"""

from operonx.lint.rules import SKIP_DIRS, Finding, lint_env, lint_path, lint_source

__all__ = ["Finding", "SKIP_DIRS", "lint_env", "lint_path", "lint_source"]
