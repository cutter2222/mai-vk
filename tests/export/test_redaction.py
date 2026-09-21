import logging

from presentation_designer.shared.log_redaction import SignedURLFilter


def test_signed_urls_redacted_without_dropping_context():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        "",
        1,
        'GET %s "%s"',
        ("http://api/source?token=secret&key=id&md5=private", "200 OK"),
        None,
    )
    assert SignedURLFilter().filter(record)
    assert (
        record.getMessage()
        == 'GET http://api/source?token=[redacted]&key=id&md5=[redacted] "200 OK"'
    )


def test_uvicorn_access_formatter_keeps_structured_arguments():
    from uvicorn.logging import AccessFormatter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/source?token=secret&md5=private", "1.1", 200),
        None,
    )
    assert SignedURLFilter().filter(record)
    text = AccessFormatter("%(request_line)s %(status_code)s").format(record)
    assert "secret" not in text and "private" not in text
    assert "token=[redacted]" in text and "200" in text
