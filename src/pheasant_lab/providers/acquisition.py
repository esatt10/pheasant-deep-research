"""Fetch original research files without reading or parsing their contents."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..lifecycle import durable_write_bytes

MAX_FILE_BYTES = 40 * 1024 * 1024


def download_pdf(url: str, destination: Path, *, timeout: float = 30.0) -> int:
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("research file URL must use HTTP or HTTPS")
    with (
        httpx.Client(timeout=timeout, follow_redirects=True) as client,
        client.stream("GET", url, headers={"User-Agent": "pheasant-swarm-lab/0.1"}) as response,
    ):
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("research file exceeds the 40 MiB download limit")
    if not bytes(content[:1024]).lstrip().startswith(b"%PDF-"):
        raise ValueError("download did not return a PDF (possibly a publisher landing page)")
    durable_write_bytes(destination, bytes(content))
    return len(content)
