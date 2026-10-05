"""The real CLI, made to die once, the way a process really dies.

``os._exit`` skips every ``finally``, flush and ``atexit`` - it is what an
out-of-memory kill or a ``SIGKILL`` looks like from the inside. Before dying it
leaves half a JSON line at the end of ``events.jsonl``, which is what an
append interrupted mid-write leaves. ``CRASH_MARKER`` names a file whose
existence means "already crashed once", so the retry runs clean.

``CRASH_AT`` picks the moment:

* ``collect.persist1`` - mid-round: once the first research branch has
  extracted and submitted its sources, while the others may still be working,
  and before the round is checkpointed;
* ``evaluate.answer5`` - after the fifth recorded answer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MARKER = Path(os.environ["CRASH_MARKER"])
WHERE = os.environ.get("CRASH_AT", "collect.persist1")


def die(raw: Path) -> None:
    MARKER.write_text("crashed\n", encoding="utf-8")
    with (raw / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"event_id": "event-torn", "sequence": 999')
    os._exit(137)


if not MARKER.exists():
    if WHERE == "collect.persist1":
        from pheasant_lab.orchestration import researcher

        original = researcher.Researcher._persist

        def persist(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            out = original(self, *args, **kwargs)
            die(self.tracer.paths.raw)
            return out

        researcher.Researcher._persist = persist  # type: ignore[method-assign]
    elif WHERE == "evaluate.answer5":
        from pheasant_lab.arms import base

        original_record = base.Arm._record
        count = {"n": 0}

        def record(self, answer):  # type: ignore[no-untyped-def]
            out = original_record(self, answer)
            count["n"] += 1
            if count["n"] == 5:
                die(self.tracer.paths.raw)
            return out

        base.Arm._record = record  # type: ignore[method-assign]

from pheasant_lab.cli import main  # noqa: E402

sys.exit(main())
