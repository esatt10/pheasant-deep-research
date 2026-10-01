"""The event stream under concurrency.

Research branches emit into one stream from several threads, and ``verify``
reads file order as sequence order. A number taken under the lock and written
after it is a stream that reorders itself without losing anything.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from pheasant_lab.lifecycle import RunPaths
from pheasant_lab.redaction import Redactor
from pheasant_lab.tracing.duckdb_projection import verify_raw
from pheasant_lab.tracing.events import EventLog


def test_concurrent_emits_are_written_in_sequence_order(tmp_path: Path):
    paths = RunPaths(tmp_path)
    log = EventLog(
        paths.raw / "events.jsonl",
        run_id="run-1",
        config_digest="sha256:0",
        redactor=Redactor(enabled=False),
    )
    # Yield between numbering and writing, which is where the race lived: the
    # old code took the number under the lock and appended after releasing it.
    original = log._writer.append

    def slow_append(record):
        time.sleep(0)
        original(record)

    log._writer.append = slow_append

    def branch(index: int) -> None:
        for step in range(150):
            log.emit(
                "test.event", payload={"branch": index, "step": step}, trace_id="t", span_id="s"
            )

    threads = [threading.Thread(target=branch, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    log.close()

    assert verify_raw(paths, check_digests=True) == []
