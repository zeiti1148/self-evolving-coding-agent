"""Bounded transport diagnostics: never serialize exception messages or trace info."""
from __future__ import annotations

import time


TLS_REASONS = frozenset({
    "UNEXPECTED_EOF_WHILE_READING", "CERTIFICATE_VERIFY_FAILED", "WRONG_VERSION_NUMBER",
    "TLSV1_ALERT_INTERNAL_ERROR", "TLSV1_ALERT_PROTOCOL_VERSION", "SSLV3_ALERT_HANDSHAKE_FAILURE",
})
PROTOCOLS = frozenset({"connection", "socks", "proxy", "http11", "http2"})
OPERATIONS = frozenset({
    "connect_tcp", "connect_unix_socket", "setup_socks5_connection", "start_tls",
    "send_request_headers", "send_request_body", "receive_response_headers", "receive_response_body",
})


def exception_chain(exc: BaseException) -> list[dict]:
    """Keep machine codes only; messages may embed credentials, URLs or request data."""
    result, seen = [], set()
    while exc is not None and id(exc) not in seen and len(result) < 6:
        seen.add(id(exc))
        row = {"type": type(exc).__name__[:64]}
        for name in ("errno", "winerror", "verify_code"):
            value = getattr(exc, name, None)
            if type(value) is int:
                row[name] = value
        reason = getattr(exc, "reason", None)
        if isinstance(reason, str) and reason in TLS_REASONS:
            row["tls_reason"] = reason
        result.append(row)
        exc = exc.__cause__ or exc.__context__
    return result


class TransportTrace:
    def __init__(self, started: float):
        self.started = started
        self.events: list[dict] = []
        self.failed_stage: str | None = None

    def __call__(self, name: str, info: dict) -> None:
        # info includes headers, hostnames and SSL objects. Deliberately never read it.
        parts = name.split(".")
        if (len(parts) != 3 or parts[0] not in PROTOCOLS or parts[1] not in OPERATIONS or
                parts[2] not in {"started", "complete", "failed"}):
            return
        if parts[2] == "failed":
            self.failed_stage = ".".join(parts[:2])
        if len(self.events) < 48:
            self.events.append({"event": name, "elapsed_ms": round((time.monotonic() - self.started) * 1000)})
