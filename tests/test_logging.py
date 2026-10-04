import logging

from app.logging_config import configure_logging


def test_http_client_access_logs_are_suppressed() -> None:
    configure_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
