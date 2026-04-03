"""Tests for WebSocket handler: WS /path/ws."""

import pytest
from fastapi.testclient import TestClient

from hush.serve import HushApp


def _recv_until_result(ws):
    """Read messages until the final 'result' message arrives."""
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == "result":
            return messages


class TestWebSocketHandler:
    def test_ws_basic_request(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, websocket=True)
        client = TestClient(app.fastapi)
        with client.websocket_connect("/double/ws") as ws:
            ws.send_json({"inputs": {"x": 5}})
            messages = _recv_until_result(ws)
            result_msg = messages[-1]
            assert result_msg["type"] == "result"
            assert result_msg["data"]["result"] == 10

    def test_ws_multiple_messages(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, websocket=True)
        client = TestClient(app.fastapi)
        with client.websocket_connect("/double/ws") as ws:
            for x in [1, 2, 3]:
                ws.send_json({"inputs": {"x": x}})
                messages = _recv_until_result(ws)
                result_msg = messages[-1]
                assert result_msg["type"] == "result"
                assert result_msg["data"]["result"] == x * 2

    def test_ws_close_message(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, websocket=True)
        client = TestClient(app.fastapi)
        with client.websocket_connect("/double/ws") as ws:
            ws.send_json({"inputs": {"x": 5}})
            _recv_until_result(ws)
            ws.send_json({"type": "close"})

    def test_ws_not_enabled(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, websocket=False)
        client = TestClient(app.fastapi)
        # WebSocket should not be available
        with pytest.raises(Exception):
            with client.websocket_connect("/double/ws"):
                pass

    def test_ws_greet(self, greet_graph):
        app = HushApp()
        app.endpoint("/greet", graph=greet_graph, websocket=True)
        client = TestClient(app.fastapi)
        with client.websocket_connect("/greet/ws") as ws:
            ws.send_json({"inputs": {"name": "Hush"}})
            messages = _recv_until_result(ws)
            result_msg = messages[-1]
            assert result_msg["type"] == "result"
            assert result_msg["data"]["greeting"] == "Hello, Hush!"

    def test_ws_invalid_input(self, double_graph):
        app = HushApp()
        app.endpoint("/double", graph=double_graph, websocket=True)
        client = TestClient(app.fastapi)
        with client.websocket_connect("/double/ws") as ws:
            # Send invalid input (missing required field)
            ws.send_json({"inputs": {}})
            resp = ws.receive_json()
            assert resp["type"] == "error"
