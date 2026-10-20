"""An LLMOp sends the token provider's current token on every request.

``LLMOp`` takes its model from the ResourceHub once and keeps it, and the hub
put a renewed ``oauth2:`` / ``keycloak:`` token on an instance only on a later
``hub.get()`` — which a running op never makes. So the client kept the token
it was built with: the provider renewed in the background, nobody handed the
new token over, and an hour into a run every request failed with
``AuthenticationError: Invalid Token`` (a 7,179-call batch failed from call
4,439 on).

The test runs the real stack against one local server that is both the token
endpoint and the chat endpoint, and accepts only the token it issued last.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from operonx.core import END, START, Operon, graph
from operonx.core.registry import ResourceHub
from operonx.providers.ops import LLMOp

pytestmark = pytest.mark.unit


class _Gateway:
    """Issues tok1, tok2, … at /token; /v1/chat/completions takes only the last."""

    def __init__(self):
        self.issued = 0
        self.revoked = False
        self.seen: list[str] = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path.endswith("/token"):
                    gateway.issued += 1
                    gateway.revoked = False
                    return self._send(
                        200, {"access_token": f"tok{gateway.issued}", "expires_in": 3600}
                    )
                auth = self.headers.get("Authorization", "")
                gateway.seen.append(auth)
                if gateway.revoked or auth != f"Bearer tok{gateway.issued}":
                    return self._send(401, {"error": {"message": "Invalid Token"}})
                return self._send(
                    200,
                    {
                        "id": "c",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "m",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "ok"},
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@graph
def ask(text: str):
    answer = LLMOp.of(resource="gw", prompt={"user": "{text}"}, text=text)
    START >> answer >> END


@pytest.fixture
def gateway(tmp_path, request):
    gw = _Gateway()
    client_id = f"token-refresh-{request.node.name}"
    (tmp_path / "resources.yaml").write_text(
        f"oauth2:srv:\n"
        f"  token_url: http://127.0.0.1:{gw.port}/token\n"
        f"  client_id: {client_id}\n"
        f"  client_secret: s\n"
        f"  scope: all-apis\n"
        f"llm:gw:\n"
        f"  api_type: openai\n"
        f"  api_key: oauth2:srv\n"
        f"  base_url: http://127.0.0.1:{gw.port}/v1\n"
        f"  model: m\n",
        encoding="utf-8",
    )
    hub = ResourceHub.from_yaml(tmp_path / "resources.yaml")
    ResourceHub.set_instance(hub)
    gw.provider = hub.get("oauth2:srv")
    yield gw
    gw.provider.shutdown()
    gw.server.shutdown()
    ResourceHub.reset_instance()


def test_a_token_renewed_mid_run_reaches_the_request(gateway):
    engine = Operon(ask(text="hi"))
    assert asyncio.run(engine.run(inputs={})).get("content") == "ok"

    with gateway.provider._lock:  # what the provider's background thread does near expiry
        gateway.provider._fetch_token()

    assert asyncio.run(engine.run(inputs={})).get("content") == "ok"
    assert gateway.seen == ["Bearer tok1", "Bearer tok2"]


def test_a_401_retries_once_with_a_token_fetched_now(gateway):
    engine = Operon(ask(text="hi"))
    assert asyncio.run(engine.run(inputs={})).get("content") == "ok"

    gateway.revoked = True  # the endpoint stops taking a token before its advertised expiry

    assert asyncio.run(engine.run(inputs={})).get("content") == "ok"
    assert gateway.seen == ["Bearer tok1", "Bearer tok1", "Bearer tok2"]
