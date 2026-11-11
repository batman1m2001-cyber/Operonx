"""Credentials stay out of the coder's environment. The coder's own denials
(incl. ``git push``) and the scrubbed Claude variables are in test_coder."""

from __future__ import annotations

from operonx_project.coder.claude import base_env
from operonx_project.safety import is_secret_name, scrub_env

SECRETS = {
    "OPERONX_TELEGRAM_TOKEN": "123456:abc",
    "OPENROUTER_API_KEY": "sk-or-1",
    "ANTHROPIC_API_KEY": "sk-ant-1",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "GH_TOKEN": "ghp_1",
    "DATABASE_PASSWORD": "pw",
    "SSH_AUTH_SOCK": "/tmp/agent.sock",
    "LANGFUSE_SECRET_KEY": "lf",
}


def test_secret_names():
    for name in SECRETS:
        assert is_secret_name(name), name
    for name in ("PATH", "HOME", "LANG", "PYTHONPATH", "COLUMNS", "TERM", "UV_CACHE_DIR"):
        assert not is_secret_name(name), name
    assert scrub_env({"A_TOKEN": "x", "PATH": "/bin"}, keep=["A_TOKEN"]) == {"A_TOKEN": "x", "PATH": "/bin"}


def test_credentials_do_not_reach_the_coder(monkeypatch):
    for k, v in {**SECRETS, "PROJECT_SETTING": "kept"}.items():
        monkeypatch.setenv(k, v)
    coder_env = base_env()
    assert not set(SECRETS) & set(coder_env) and coder_env["PROJECT_SETTING"] == "kept"
    assert "ANTHROPIC_API_KEY" in base_env(keep_anthropic=True)  # an API-key sign-in, only when asked
