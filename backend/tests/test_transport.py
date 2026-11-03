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
from backend.shared import ApplicationError, ErrorCode


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


@pytest.mark.parametrize("task_expires_first", [False, True])
def test_real_http_keepalive_body_respects_request_and_task_deadlines(task_expires_first):
    received, stop = [], threading.Event()
    body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "late result"}}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", str(40 + len(body)))
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    if stop.wait(.04):
                        return
                self.wfile.write(body)
            except OSError:
                pass  # The client closes its response when the deadline is observed.

        def log_message(self, *args):
            pass

    with server(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as port:
        settings = replace(Settings(), llm_base_url=f"http://127.0.0.1:{port}",
                           llm_api_key="private-key", llm_model="test", llm_timeout=.6)
        # Prepare TLS context before the short fixture budget so this tests response I/O.
        client = ChatClient(settings, transport=httpx.HTTPTransport())
        client.set_deadline(time.monotonic() + (.3 if task_expires_first else 5))
        try:
            with pytest.raises(ApplicationError) as caught:
                client.complete([])
            assert caught.value.code == (ErrorCode.AGENT_STOPPED if task_expires_first else ErrorCode.LLM_ERROR)
            assert len(received) == 1 and len(client.audit) == 1
            assert client.audit[0]["success"] is False
            # A socket timeout can win the race with the next chunk's deadline check.
            assert client.audit[0]["error_type"] in {"ApplicationError", "ReadTimeout"}
            assert client.audit[0]["error_code"] == caught.value.code.value
            assert "private-key" not in json.dumps(client.audit)
        finally:
            stop.set()
            client.close()


def test_real_http_json_arriving_after_slow_headers_is_not_accepted():
    received = []
    body = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "late result"}}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Progress: ")
                self.wfile.flush()
                # Each read is timely, but a complete valid response arrives too late.
                for _ in range(20):
                    self.wfile.write(b".")
                    self.wfile.flush()
                    time.sleep(.04)
                self.wfile.write(f"\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                self.wfile.flush()
            except OSError:
                pass

        def log_message(self, *args):
            pass

    with server(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as port:
        settings = replace(Settings(), llm_base_url=f"http://127.0.0.1:{port}",
                           llm_api_key="private-key", llm_model="test", llm_timeout=.4)
        client = ChatClient(settings, transport=httpx.HTTPTransport())
        client.set_deadline(time.monotonic() + 5)
        try:
            with pytest.raises(ApplicationError) as caught:
                client.complete([])
            assert caught.value.code == ErrorCode.LLM_ERROR
            assert len(received) == 1 and len(client.audit) == 1
            assert client.audit[0]["success"] is False
            assert client.audit[0]["error_type"] == "ApplicationError"
            assert client.audit[0]["error_code"] == "LLM_ERROR"
        finally:
            client.close()


@pytest.mark.parametrize("late_stage", ["json", "message"])
def test_response_processing_cannot_return_success_after_request_deadline(monkeypatch, late_stage):
    from backend.agent import llm
    clock = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    original_loads, original_validate = llm.json.loads, llm.validate_message

    def late_json(value, *args, **kwargs):
        result = original_loads(value, *args, **kwargs)
        if isinstance(value, bytearray):
            clock[0] += 2
        return result

    def late_message(value):
        result = original_validate(value)
        clock[0] += 2
        return result

    if late_stage == "json":
        monkeypatch.setattr(llm.json, "loads", late_json)
    else:
        monkeypatch.setattr(llm, "validate_message", late_message)
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="private-key",
                       llm_model="test", llm_timeout=1)
    response = {"choices": [{"finish_reason": "stop", "message": {"content": "late result"}}]}
    client = ChatClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    client.set_deadline(clock[0] + 10)
    try:
        with pytest.raises(ApplicationError) as caught:
            client.complete([])
        assert caught.value.code == ErrorCode.LLM_ERROR
        assert len(client.audit) == 1 and client.audit[0]["success"] is False
        assert client.audit[0]["error_type"] == "ApplicationError"
        assert client.audit[0]["error_code"] == "LLM_ERROR"
    finally:
        client.close()


def test_connection_retries_share_one_request_deadline(monkeypatch):
    clock, received = [100.0], []
    monkeypatch.setattr("backend.agent.llm.time.monotonic", lambda: clock[0])

    def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr("backend.agent.llm.time.sleep", advance)

    def handler(request):
        received.append(request)
        advance(.4)
        raise httpx.ConnectError("local failure", request=request)

    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="private-key",
                       llm_model="test", llm_timeout=1)
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ApplicationError, match="单次") as caught:
            client.complete([])
        assert caught.value.code == ErrorCode.LLM_ERROR
        assert len(received) == 2
        assert all(event["success"] is False for event in client.audit)
        assert client.audit[-1]["error_type"] == "ConnectError"
        assert client.audit[-1]["error_code"] == "LLM_ERROR"
    finally:
        client.close()


def test_reused_client_gets_a_fresh_budget_for_each_completion(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("backend.agent.llm.time.monotonic", lambda: clock[0])

    def handler(request):
        clock[0] += .6
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "on time"}}]})

    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="private-key",
                       llm_model="test", llm_timeout=1)
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    client.set_deadline(clock[0] + 10)
    try:
        assert client.complete([])["content"] == "on time"
        assert client.complete([])["content"] == "on time"
        assert len(client.audit) == 2 and all(event["success"] for event in client.audit)
    finally:
        client.close()


def test_application_error_audit_keeps_only_type_and_code(monkeypatch):
    secret = "private-validation-message"

    def reject(_message):
        raise ApplicationError(ErrorCode.LLM_ERROR, secret, details={"private": secret})

    monkeypatch.setattr("backend.agent.llm.validate_message", reject)
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="private-key", llm_model="test")
    response = {"choices": [{"finish_reason": "stop", "message": {"content": "private-body"}}]}
    client = ChatClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    try:
        with pytest.raises(ApplicationError):
            client.complete([])
        assert len(client.audit) == 1
        assert client.audit[0]["success"] is False
        assert client.audit[0]["error_type"] == "ApplicationError"
        assert client.audit[0]["error_code"] == "LLM_ERROR"
        serialized = json.dumps(client.audit)
        assert all(value not in serialized for value in [secret, "private-body", "private-key"])
    finally:
        client.close()


def test_real_http_503_is_not_retried_and_does_not_read_private_error_body():
    received, stop, body_sent = [], threading.Event(), threading.Event()
    secret = "private-provider-error-body"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(503)
            self.send_header("Content-Length", str(len(secret)))
            self.send_header("Retry-After", "120")
            self.end_headers()
            if not stop.wait(2):
                body_sent.set()
                try:
                    self.wfile.write(secret.encode())
                except OSError:
                    pass

        def log_message(self, *args):
            pass

    with server(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as port:
        settings = replace(Settings(), llm_base_url=f"http://127.0.0.1:{port}",
                           llm_api_key="private-key", llm_model="test", llm_timeout=.5)
        client = ChatClient(settings, transport=httpx.HTTPTransport())
        try:
            with pytest.raises(ApplicationError, match="HTTP 503") as caught:
                client.complete([])
            assert caught.value.code == ErrorCode.LLM_ERROR
            assert received == ["/chat/completions"]
            assert not body_sent.is_set()
            assert len(client.audit) == 1
            assert client.audit[0]["http_status"] == 503
            assert client.audit[0]["error_type"] == "HTTPStatusError"
            assert client.audit[0]["error_code"] == "LLM_ERROR"
            assert secret not in json.dumps(client.audit) + str(caught.value)
        finally:
            stop.set()
            client.close()


def test_real_http_stalled_body_times_out_without_resending_request():
    received, stop = [], threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b" ")
            self.wfile.flush()
            stop.wait(2)

        def log_message(self, *args):
            pass

    with server(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as port:
        settings = replace(Settings(), llm_base_url=f"http://127.0.0.1:{port}",
                           llm_api_key="private-key", llm_model="test", llm_timeout=.2)
        client = ChatClient(settings, transport=httpx.HTTPTransport())
        client.set_deadline(time.monotonic() + 5)
        try:
            with pytest.raises(ApplicationError) as caught:
                client.complete([])
            assert caught.value.code == ErrorCode.LLM_ERROR
            assert received == ["/chat/completions"]
            assert len(client.audit) == 1
            assert client.audit[0]["error_type"] == "ReadTimeout"
            assert client.audit[0]["error_code"] == "LLM_ERROR"
            assert client.audit[0]["success"] is False
        finally:
            stop.set()
            client.close()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interruption_propagates_closes_body_and_never_retries(interruption):
    received, closed = [], []
    secret = "private-interruption-detail"

    class InterruptedStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b" "
            raise interruption(secret)

        def close(self):
            closed.append(True)

    def handler(request):
        received.append(request)
        return httpx.Response(200, stream=InterruptedStream())

    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="private-key", llm_model="test")
    client = ChatClient(settings, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(interruption):
            client.complete([])
        assert len(received) == 1 and closed == [True]
        assert len(client.audit) == 1 and client.audit[0]["success"] is False
        assert client.audit[0]["error_type"] == interruption.__name__
        assert secret not in json.dumps(client.audit)
    finally:
        client.close()


@pytest.mark.parametrize("task_expires_first", [False, True])
def test_deadline_takes_precedence_over_late_validation_error(monkeypatch, task_expires_first):
    clock = [100.0]
    monkeypatch.setattr("backend.agent.llm.time.monotonic", lambda: clock[0])

    def late_rejection(_message):
        clock[0] += 2
        raise ApplicationError(ErrorCode.LLM_ERROR, "invalid response")

    monkeypatch.setattr("backend.agent.llm.validate_message", late_rejection)
    settings = replace(Settings(), llm_base_url="https://model.invalid/v1", llm_api_key="private-key",
                       llm_model="test", llm_timeout=1)
    response = {"choices": [{"finish_reason": "stop", "message": {"content": "late result"}}]}
    client = ChatClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)))
    client.set_deadline(clock[0] + (.5 if task_expires_first else 10))
    try:
        with pytest.raises(ApplicationError, match="时限") as caught:
            client.complete([])
        expected = ErrorCode.AGENT_STOPPED if task_expires_first else ErrorCode.LLM_ERROR
        assert caught.value.code == expected
        assert len(client.audit) == 1 and client.audit[0]["success"] is False
        assert client.audit[0]["error_code"] == expected.value
    finally:
        client.close()
