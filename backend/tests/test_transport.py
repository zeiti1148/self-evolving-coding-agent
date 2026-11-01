from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socketserver
import ssl
import threading
import time

import httpx
import pytest

from backend.agent.llm import ChatClient
from backend.agent.transport import TransportTrace, exception_chain
from backend.config import Settings
from backend.shared import ApplicationError


@contextmanager
def server(instance):
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=2)


def model_handler(label, received):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": label}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


def test_direct_mode_ignores_ambient_proxy_and_explicit_mode_uses_it(monkeypatch):
    direct_hits, proxy_hits = [], []
    with server(ThreadingHTTPServer(("127.0.0.1", 0), model_handler("direct", direct_hits))) as target:
        with server(ThreadingHTTPServer(("127.0.0.1", 0), model_handler("proxy", proxy_hits))) as proxy:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                monkeypatch.setenv(key, f"http://127.0.0.1:{proxy}")
            monkeypatch.setenv("NO_PROXY", "")
            config = replace(Settings(), llm_base_url=f"http://127.0.0.1:{target}", llm_api_key="private-key", llm_model="test")
            for enabled, label in ((False, "direct"), (True, "proxy")):
                client = ChatClient(replace(config, llm_trust_env=enabled))
                try:
                    assert client.complete([])["content"] == label
                    assert client.audit[0]["transport_policy"] == ("environment" if enabled else "direct")
                    assert client.audit[0]["transport_events"]
                finally:
                    client.close()
    assert direct_hits == ["/chat/completions"]
    assert len(proxy_hits) == 1 and proxy_hits[0].endswith("/chat/completions")


def test_real_tls_eof_is_classified_before_any_http_body_is_sent(monkeypatch):
    class CloseTLS(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)  # Receive ClientHello, then close without a TLS reply.
    monkeypatch.setattr("backend.agent.llm.time.sleep", lambda _: None)
    with server(socketserver.ThreadingTCPServer(("127.0.0.1", 0), CloseTLS)) as port:
        settings = replace(Settings(), llm_base_url=f"https://127.0.0.1:{port}", llm_api_key="private-key", llm_model="test")
        client = ChatClient(settings)
        try:
            with pytest.raises(ApplicationError, match="三次"):
                client.complete([])
            assert len(client.audit) == 3
            for event in client.audit:
                assert event["failure_stage"] == "connection.start_tls"
                assert event["error_type"] == "ConnectError"
                assert any(x["type"] == "SSLEOFError" for x in event["error_chain"])
                assert all("send_request" not in x["event"] for x in event["transport_events"])
                assert "http_status" not in event
        finally:
            client.close()


def test_diagnostics_do_not_copy_exception_messages_headers_or_trace_info(monkeypatch):
    secret = "private-key://credentials-and-body"
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key=secret, llm_model="test")
    def handler(request):
        trace = request.extensions["trace"]
        trace("socks.start_tls.started", {"headers": {"Authorization": secret}, "body": secret})
        cause = ssl.SSLEOFError(8, secret)
        cause.reason = "UNEXPECTED_EOF_WHILE_READING"
        try:
            raise cause
        except ssl.SSLError as exc:
            error = httpx.ConnectError(secret, request=request)
            trace("socks.start_tls.failed", {"exception": error, "password": secret})
            raise error from exc
    monkeypatch.setattr("backend.agent.llm.time.sleep", lambda _: None)
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ApplicationError) as caught:
        client.complete([])
    assert secret not in json.dumps(client.audit) + str(caught.value)
    assert client.audit[0]["failure_stage"] == "socks.start_tls"
    assert client.audit[0]["error_chain"][-1]["tls_reason"] == "UNEXPECTED_EOF_WHILE_READING"
    client.close()


def test_trace_and_cyclic_exception_chains_are_bounded():
    exc = ValueError("private message")
    exc.__cause__ = exc
    assert exception_chain(exc) == [{"type": "ValueError"}]
    trace = TransportTrace(time.monotonic())
    for _ in range(100):
        trace("http11.receive_response_body.started", {"body": "private"})
    trace("unknown.private.started", {})
    trace("connection.start_tls.failed", {})
    assert len(trace.events) == 48 and trace.failed_stage == "connection.start_tls"
