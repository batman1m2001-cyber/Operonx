"""The tests run on a scripted model — `api_type: fake` — so they need no
key and no network: it answers every request with "Echo: <the last user
message>". The session fixture installs it as `llm:assistant` before any
graph reads the resources.
"""

import operonx
import pytest

FAKE = """
llm:assistant:
  api_type: fake
  script:
    - echo: "Echo: "
"""


@pytest.fixture(scope="session")
def fake_model(tmp_path_factory):
    """`llm:assistant`, answered by the fake model."""
    path = tmp_path_factory.mktemp("resources") / "resources.yaml"
    path.write_text(FAKE)
    operonx.bootstrap(resources=path, env=False)
    yield path
