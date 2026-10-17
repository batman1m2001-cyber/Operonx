"""What never reaches a coder session or a check command: credentials (plan §11).

The coder's shell and the repo's own tests run code the engine did not
write. Their environment is the engine's minus every variable that looks
like a credential, by name: a token, a secret, a password, an API or
access key, an auth socket; and the providers' prefixes (``ANTHROPIC_``,
``OPENROUTER``, ``OPENAI``, ``AWS_``, …). The bot token
(``OPERONX_TELEGRAM_TOKEN``) is one of them. A check that needs a
credential gets it from the policy, by name, never by inheritance.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, Mapping

__all__ = ["is_secret_name", "scrub_env"]

_NAME = re.compile(r"TOKEN|SECRET|PASSW(OR)?D|CREDENTIAL|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|_KEY$|AUTH|COOKIE|SESSION")
_PREFIX = (
    "ANTHROPIC",
    "OPENROUTER",
    "OPENAI",
    "AWS_",
    "AZURE_",
    "GOOGLE_APPLICATION",
    "GCP_",
    "HF_",
    "HUGGING",
    "GH_",
    "GITHUB_",
    "GITLAB_",
    "NPM_",
    "PYPI",
    "TWINE_",
    "DOCKER_AUTH",
    "SLACK_",
    "TELEGRAM",
    "LANGFUSE_",
)


def is_secret_name(name: str) -> bool:
    up = name.upper()
    return bool(_NAME.search(up)) or up.startswith(_PREFIX)


def scrub_env(env: Mapping[str, str], keep: Iterable[str] = ()) -> Dict[str, str]:
    """*env* without its credentials; names in *keep* stay (an API-key sign-in the coder needs)."""
    kept = set(keep)
    return {k: v for k, v in env.items() if k in kept or not is_secret_name(k)}
