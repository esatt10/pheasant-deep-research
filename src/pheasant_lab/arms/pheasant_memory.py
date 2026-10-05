"""``P1`` - the same snapshot, with memory and steering on.

The paired difference ``P1 - P0`` is the memory-attributable effect, and it is
only that if two things hold:

* the **corpus is identical**. P1 runs against the snapshot P0 ran against;
  anything that changed the corpus between them would be attributed to memory.
* the memory was written from the **learned** cohort only. A record derived
  from a holdout question makes ``learned - holdout`` a restatement instead of
  a memorisation detector, and the leakage checker refuses that run.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..pheasant.capabilities import CapabilityMap
from ..pheasant.client import PheasantClient
from ..pheasant.ingestion import read_index_queue
from ..pheasant.retrieval import MemoryOptions
from ..settings import PheasantFile
from ..textkit import content_terms, truncate
from .pheasant_corpus import PheasantCorpusArm


class PheasantMemoryArm(PheasantCorpusArm):
    arm_id = "P1"
    memory_enabled = True

    def memory_options(self) -> MemoryOptions:
        return MemoryOptions(
            enabled=True,
            current_only=True,
            # Steering records change ranking; they are not passages. Asking
            # for them in the result list would hand an answerer a line of
            # rule syntax dressed as retrieved knowledge.
            include_rules=False,
            as_of=None,
        )


#: pheasant's memory source. A fleet publishes its sync like any other.
MEMORY_SOURCE = "agent-memory"


class MemorySeeder:
    """Writes the memory ``P1`` is measured with.

    Every record names the question that produced it. That field is what the
    leakage checker reads to prove no holdout or control question created the
    treatment it is being tested against - so it is written even though
    nothing in the region requires it.

    On a role-split region a memory write answers ``sync: {status: queued}``
    (pheasant >= 0.13.2): the record is stored and is not yet searchable.
    ``P1`` starting then would be measured partly *without* the treatment it
    is named for, so :meth:`seed` waits for the queued tasks to leave the
    region's index queue and records how that went (``memory.indexed``).
    """

    def __init__(
        self,
        client: PheasantClient,
        capabilities: CapabilityMap,
        config: PheasantFile,
        *,
        tracer: Any = None,
        principal: str = "pheasant-swarm-lab",
        clock: Any = time,
    ) -> None:
        self.client = client
        self.capabilities = capabilities
        self.config = config
        self.tracer = tracer
        self.principal = principal
        self._kb_field = str(config.argument_map.get("knowledge_base_field", "knowledge_base"))
        self.written: list[dict[str, Any]] = []
        self._clock = clock
        #: Task ids of memory syncs the region published rather than ran.
        self.queued_tasks: set[str] = set()
        #: How the wait for those tasks ended; ``None`` when nothing was queued.
        self.index_outcome: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        return self.capabilities.has("write_memory")

    def seed(
        self,
        first_pass: Sequence[Mapping[str, Any]],
        *,
        learned_question_ids: Iterable[str],
        forbidden_question_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Derive records from the first pass over the learned cohort.

        Two rules, both enforced here rather than downstream:

        * only ``learned`` questions may produce a record;
        * a record is derived from what the *arm* did - the queries it ran and
          what came back - never from the expected answers, which this class
          is never given.
        """

        if not self.available:
            return []
        learned = set(learned_question_ids)
        forbidden = set(forbidden_question_ids)
        records: list[dict[str, Any]] = []

        for entry in first_pass:
            question_id = str(entry.get("question_id") or "")
            if question_id not in learned or question_id in forbidden:
                continue
            for record in self._candidates(entry):
                written = self._write(record, question_id)
                if written is not None:
                    records.append(written)
        self.written.extend(records)
        if self.tracer is not None:
            self.tracer.emit(
                "memory.seeded",
                payload={"records": len(records), "learned_questions": len(learned)},
            )
        if self.queued_tasks:
            self.index_outcome = self.wait_until_indexed()
        return records

    def wait_until_indexed(self, wait_seconds: float | None = None) -> dict[str, Any]:
        """Wait for every queued memory sync to leave the index queue.

        A task is done when the region's complete listing no longer holds it.
        A ``dead`` task never will, so it ends the wait as ``failed``. Without
        the ``index_queue`` capability, or with a listing the backend cannot
        give, completion is unknowable: the outcome says so rather than
        guessing, and the engine turns it into a limitation.
        """

        pending = set(self.queued_tasks)
        deadline = self._clock.monotonic() + (
            self.config.timeout_seconds if wait_seconds is None else wait_seconds
        )
        backoff = max(0.05, self.config.retry_backoff_seconds)
        started = self._clock.monotonic()
        polls = 0
        outcome = "unknown"
        dead: list[str] = []
        while True:
            polls += 1
            tasks = read_index_queue(
                self.client,
                self.capabilities,
                {self._kb_field: self.config.knowledge_base},
                source=MEMORY_SOURCE,
            )
            if tasks is None:
                outcome = "unknown"
                break
            listed = {str(task.get("task_id")): task for task in tasks}
            dead = sorted(t for t in pending if (listed.get(t) or {}).get("state") == "dead")
            if dead:
                outcome = "failed"
                break
            pending = {t for t in pending if t in listed}
            if not pending:
                outcome = "indexed"
                break
            if self._clock.monotonic() >= deadline:
                outcome = "timed_out"
                break
            self._clock.sleep(min(backoff, self.config.retry_backoff_max_seconds))
            backoff *= 2
        result = {
            "outcome": outcome,
            "tasks": sorted(self.queued_tasks),
            "outstanding": sorted(pending) if outcome != "indexed" else [],
            "dead": dead,
            "polls": polls,
            "waited_seconds": round(self._clock.monotonic() - started, 3),
        }
        if self.tracer is not None:
            self.tracer.emit(
                "memory.indexed",
                status={"indexed": "succeeded", "failed": "failed"}.get(outcome, "partial"),
                payload=result,
            )
        return result

    @property
    def limitation(self) -> str | None:
        """What ``P1``'s numbers cannot claim, when the wait did not confirm."""

        if self.index_outcome is None or self.index_outcome["outcome"] == "indexed":
            return None
        reason = {
            "unknown": "the region offers no complete index-queue listing to confirm it",
            "timed_out": f"the wait ended after {self.index_outcome['waited_seconds']}s",
            "failed": "the region dead-lettered the task",
        }[self.index_outcome["outcome"]]
        return (
            f"P1's memory syncs were published to the region's index queue, and their indexing "
            f"was not confirmed before P1 ran ({reason}); P1 may have been measured partly "
            "without its memory"
        )

    def _candidates(self, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Three rule shapes, each derived from the arm's own trace."""

        out: list[dict[str, Any]] = []
        queries = [str(q) for q in entry.get("queries_used") or []]
        artifacts = [str(a) for a in entry.get("retrieved_artifact_ids") or []]
        question = str(entry.get("question_text") or "")

        # 1. A fact record: what this session actually established, in words.
        if entry.get("answer_text") and not entry.get("abstained"):
            out.append(
                {
                    "kind": "fact",
                    "scope": "org",
                    "subject": _subject(question),
                    "text": truncate(str(entry["answer_text"]), 600),
                }
            )
        # 2. An alias: a query term that retrieved nothing, mapped to one that
        #    did. This is the shape a region's own formation rules propose,
        #    and the one most likely to be a false positive, so it is only
        #    emitted when the two rounds actually differed.
        if len(queries) >= 2 and artifacts:
            first, later = content_terms(queries[0]), content_terms(queries[-1])
            fresh = [term for term in later if term not in first]
            if fresh and first:
                out.append(
                    {
                        "kind": "alias",
                        "scope": "org",
                        "subject": _subject(question),
                        "text": f"{first[0]} -> {fresh[0]}",
                    }
                )
        # 3. A preference: the source the session found useful, in the
        #    region's own rule grammar (`when: <terms> -> prefer: <path>`). A
        #    record the region cannot parse is ignored there without a word,
        #    so a free-text preference would be a P1 treatment that never
        #    took effect while the run reported it as seeded.
        triggers = content_terms(question)[:3]
        path = _locator(entry, artifacts[0]) if artifacts else None
        if triggers and path:
            out.append(
                {
                    "kind": "preference",
                    "scope": "org",
                    "subject": _subject(question),
                    "text": f"when: {', '.join(triggers)} -> prefer: {path}",
                }
            )
        return out

    def _write(self, record: Mapping[str, Any], question_id: str) -> dict[str, Any] | None:
        arguments = {
            self._kb_field: self.config.knowledge_base,
            "text": record["text"],
            "scope": record.get("scope", "org"),
            "kind": record.get("kind", "fact"),
            "subject": record.get("subject"),
            "principal": self.principal,
            "sync": True,
            "tags": ["pheasant-swarm-lab", f"origin:{question_id}"],
        }
        try:
            outcome = self.client.call(
                self.capabilities.tool("write_memory"), arguments, idempotent=False, stage="answer"
            )
        except Exception as exc:
            if self.tracer is not None:
                self.tracer.errors.record(
                    exc,
                    stage="answer",
                    component="arms.MemorySeeder",
                    operation="memory_write",
                    resolution="skipped",
                )
            return None
        payload = outcome.result.payload() if outcome.result else {}
        body = payload if isinstance(payload, Mapping) else {}
        sync = body.get("sync")
        if isinstance(sync, Mapping) and sync.get("status") == "queued" and sync.get("task_id"):
            self.queued_tasks.add(str(sync["task_id"]))
        # Pheasant nests the stored record under `record`.
        stored = body.get("record") if isinstance(body.get("record"), Mapping) else body
        written = {
            "record_id": str(stored.get("record_id") or body.get("record_id") or ""),
            "kind": record.get("kind"),
            "scope": record.get("scope"),
            "subject": record.get("subject"),
            "text": record["text"],
            "originating_question_id": question_id,
            "outcome": body.get("outcome"),
            "created": body.get("created"),
        }
        if self.tracer is not None:
            self.tracer.append("memory-records.jsonl", written)
        return written


def _locator(entry: Mapping[str, Any], artifact_id: str) -> str | None:
    """The region's path for ``artifact_id``, from the session's own searches."""

    for call in entry.get("search_calls") or []:
        for result in call.get("results") or []:
            if str(result.get("artifact_id") or "") == artifact_id and result.get("locator"):
                return str(result["locator"])
    return None


def _subject(question: str) -> str:
    terms = content_terms(question)[:3]
    return " ".join(terms) or "general"
