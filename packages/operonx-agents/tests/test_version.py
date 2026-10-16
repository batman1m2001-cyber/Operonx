"""`__version__` is the version in pyproject.toml (kb said 0.2.3 through 0.2.6)."""

import re
from pathlib import Path

import operonx_agents


def test_version_is_the_one_in_pyproject():
    toml = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert operonx_agents.__version__ == re.search(r'^version = "([^"]+)"', toml, re.M).group(1)
