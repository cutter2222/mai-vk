"""Keep signed office download/source URLs out of HTTP access logs."""

import logging
import re


class SignedURLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn's AccessFormatter unpacks these five arguments even after getMessage().
        # Preserve their shape, redacting the URL before both plain and access formatting.
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.args = tuple(
                re.sub(r"([?&](?:token|md5)=)[^&\s\"']+", r"\1[redacted]", arg)
                if isinstance(arg, str) else arg for arg in record.args
            )
            return True
        record.msg = re.sub(r"([?&](?:token|md5)=)[^&\s\"']+", r"\1[redacted]", record.getMessage())
        record.args = ()
        return True


def configure_url_redaction() -> None:
    for name in ("httpx", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, SignedURLFilter) for item in logger.filters):
            logger.addFilter(SignedURLFilter())
