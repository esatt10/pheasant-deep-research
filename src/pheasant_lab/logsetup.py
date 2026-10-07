"""Apply ``logging.yaml``'s ``logging`` section to the process that runs a stage.

``level``, ``format`` and ``file`` were declared from the start and read by
nothing: every stage logged at INFO, as text, to stderr, whatever the file
said - a setting with no reader, which is worse than no setting because it
stops anyone looking for the missing behaviour. This is the reader.

``file`` is relative to the run directory (``logs/lab.log``), so a run's own
diagnostics travel with it, are listed by the console's Logs page, and go
when the run is deleted. An absolute path is honoured as given. ``-v`` still
wins: a person who asked for debug output on the command line gets it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

#: Marks the handlers this module installed, so a second stage in one process
#: (the demo runs six) replaces them rather than logging every line twice.
_OWNED = "_pheasant_lab_owned"


class JsonFormatter(logging.Formatter):
    """One JSON object per line: what a log shipper or ``jq`` wants."""

    def format(self, record: logging.LogRecord) -> str:
        row: dict[str, Any] = {
            "at": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            row["exception"] = self.formatException(record.exc_info)
        return json.dumps(row, default=str)


TEXT_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(section: Any, run_root: Path | None, *, verbose: bool = False) -> Path | None:
    """Configure the root logger from ``section``; returns the log file, if any."""

    level_name = "DEBUG" if verbose else str(getattr(section, "level", "INFO") or "INFO").upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        raise ValueError(f"logging.level {level_name!r} is not a level")
    formatter: logging.Formatter = (
        JsonFormatter()
        if getattr(section, "format", "text") == "json"
        else logging.Formatter(TEXT_FORMAT)
    )
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()
        else:
            # basicConfig's stderr handler: keep it, in the configured shape.
            handler.setFormatter(formatter)
            handler.setLevel(level)
    target: Path | None = None
    configured = getattr(section, "file", None)
    if configured:
        target = Path(configured)
        if not target.is_absolute():
            if run_root is None:
                return None
            target = run_root / target
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(target, encoding="utf-8")
        handler.setFormatter(formatter)
        handler.setLevel(level)
        setattr(handler, _OWNED, True)
        root.addHandler(handler)
    return target
