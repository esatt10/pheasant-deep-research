"""Every agent's full trace, read back from the run's raw files.

The live view summarises; this is the record. For one *actor* - the
orchestration (planner, auditor, benchmark builder and the lab itself), one
research branch, or one evaluation arm - it returns the span tree with every
event in its span, the model calls with their tokens and spend, every MCP
call with its request and response, and for an arm each question it answered
with what it read and claimed.

Nothing is recomputed. Spans, events, the MCP log, answers and questions are
joined on keys they already carry: ``span_id`` for events, ``arm_id`` and
``question_id`` for answers, and - because the MCP log predates span ids on
its rows - the exact ``(tool, attempt, duration_ms)`` an MCP call shares with
the ``mcp.tool.call`` event that recorded it. A call that cannot be joined is
listed under the actor it names (its ``arm_id``) or under orchestration, never
guessed onto a branch.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .projection import ARM_LABELS, _short_agent, parse_time

ORCHESTRATION = "orchestration"


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _actor_of(event_or_span: dict[str, Any]) -> str:
    attributes = event_or_span.get("attributes") or {}
    agent = event_or_span.get("agent_id") or attributes.get("agent_id")
    arm = event_or_span.get("arm_id") or attributes.get("arm_id")
    if agent:
        return f"agent:{agent}"
    if arm:
        return f"arm:{arm}"
    return ORCHESTRATION


def _mcp_key(tool: Any, attempt: Any, duration: Any) -> tuple[str, int, float] | None:
    try:
        return (str(tool), int(attempt or 1), round(float(duration), 6))
    except (TypeError, ValueError):
        return None


class RunTraces:
    """One run's raw files, indexed by actor. Built once per request."""

    def __init__(self, root: Path) -> None:
        raw = root / "raw"
        self.root = root
        self.events = _rows(raw / "events.jsonl")
        self.spans = _rows(raw / "spans.jsonl")
        self.mcp = _rows(raw / "mcp-calls.redacted.jsonl")
        self.answers = _rows(raw / "answers.jsonl")
        self.claims = _rows(raw / "claims.jsonl")
        self.errors = _rows(raw / "errors.jsonl")
        self.questions = {row.get("question_id"): row for row in _rows(raw / "questions.jsonl")}
        self.started = min(
            (parse_time(e.get("occurred_at")) for e in self.events if e.get("occurred_at")),
            default=None,
        )
        # The MCP log joined to the event that recorded each call.
        by_key: dict[tuple[str, int, float], list[int]] = defaultdict(list)
        for index, call in enumerate(self.mcp):
            key = _mcp_key(call.get("tool"), call.get("attempt"), call.get("duration_ms"))
            if key:
                by_key[key].append(index)
        self.mcp_of_event: dict[str, int] = {}
        self.mcp_actor: dict[int, str] = {}
        for event in self.events:
            if event.get("event_type") != "mcp.tool.call":
                continue
            payload = event.get("payload") or {}
            key = _mcp_key(payload.get("tool"), payload.get("attempt"), payload.get("duration_ms"))
            if key and by_key.get(key):
                index = by_key[key].pop(0)
                self.mcp_of_event[str(event.get("event_id"))] = index
                self.mcp_actor[index] = _actor_of(event)
        for index, call in enumerate(self.mcp):
            if index not in self.mcp_actor:
                self.mcp_actor[index] = (
                    f"arm:{call['arm_id']}" if call.get("arm_id") else ORCHESTRATION
                )

    # -- actors ------------------------------------------------------------
    def actors(self) -> list[dict[str, Any]]:
        counts: dict[str, dict[str, Any]] = {}

        def row(actor: str) -> dict[str, Any]:
            if actor not in counts:
                counts[actor] = {
                    "actor": actor,
                    "kind": actor.split(":", 1)[0] if ":" in actor else ORCHESTRATION,
                    "label": self._label(actor),
                    "role": None,
                    "events": 0,
                    "spans": 0,
                    "mcp_calls": 0,
                    "model_calls": 0,
                    "cost_usd": 0.0,
                    "tokens": 0,
                    "failures": 0,
                }
            return counts[actor]

        for event in self.events:
            item = row(_actor_of(event))
            item["events"] += 1
            item["role"] = item["role"] or event.get("agent_role")
            if event.get("status") == "failed":
                item["failures"] += 1
            if event.get("event_type") == "cost.model_call":
                payload = event.get("payload") or {}
                item["model_calls"] += 1
                item["cost_usd"] += float(payload.get("actual_usd") or 0.0)
                item["tokens"] += int(payload.get("input_tokens") or 0) + int(
                    payload.get("output_tokens") or 0
                )
        for span in self.spans:
            row(_actor_of(span))["spans"] += 1
        for actor in self.mcp_actor.values():
            row(actor)["mcp_calls"] += 1
        order = {"orchestration": 0, "agent": 1, "arm": 2}
        return sorted(
            (dict(item, cost_usd=round(item["cost_usd"], 6)) for item in counts.values()),
            key=lambda item: (order.get(item["kind"], 3), item["actor"]),
        )

    def _label(self, actor: str) -> str:
        if actor == ORCHESTRATION:
            return "Orchestration · planner, auditor, benchmark, lab"
        kind, _, ident = actor.partition(":")
        if kind == "agent":
            return _short_agent(ident)
        if kind == "arm":
            return f"{ident} · {ARM_LABELS.get(ident, ident)}"
        return actor

    # -- one actor's trace -------------------------------------------------
    def trace(self, actor: str) -> dict[str, Any]:
        spans = [s for s in self.spans if _actor_of(s) == actor]
        events = [e for e in self.events if _actor_of(e) == actor]
        if not spans and not events and not any(a == actor for a in self.mcp_actor.values()):
            raise KeyError(actor)

        nodes: dict[str, dict[str, Any]] = {}
        for span in spans:
            start = self._offset(span.get("started_at"))
            end = self._offset(span.get("ended_at"))
            nodes[str(span.get("span_id"))] = {
                "span_id": span.get("span_id"),
                "parent_span_id": span.get("parent_span_id"),
                "name": span.get("name"),
                "status": span.get("status"),
                "error": span.get("error"),
                "start": start,
                "end": end,
                "duration_ms": span.get("duration_ms"),
                "attributes": span.get("attributes") or {},
                "children": [],
                "events": [],
            }
        loose: list[dict[str, Any]] = []
        for event in events:
            item = self._event(event)
            holder = nodes.get(str(event.get("span_id")))
            if holder is None:
                holder = self._enclosing(nodes, item)
                if holder is not None:
                    item["placed"] = "by_time"
            if holder is None and item.get("question_id"):
                # An arm reports a question answered after its span closes.
                owners = [
                    node
                    for node in nodes.values()
                    if node["attributes"].get("question_id") == item["question_id"]
                ]
                if len(owners) == 1:
                    holder = owners[0]
                    item["placed"] = "by_question"
            (holder["events"] if holder else loose).append(item)
        roots: list[dict[str, Any]] = []
        for node in nodes.values():
            parent = nodes.get(str(node["parent_span_id"]))
            (parent["children"] if parent else roots).append(node)
        for node in nodes.values():
            node["children"].sort(key=lambda n: (n["start"] is None, n["start"] or 0.0))
        roots.sort(key=lambda n: (n["start"] is None, n["start"] or 0.0))

        unjoined = [
            self._mcp_summary(index)
            for index, owner in self.mcp_actor.items()
            if owner == actor and index not in set(self.mcp_of_event.values())
        ]
        times = [n["start"] for n in nodes.values() if n["start"] is not None] + [
            e["t"] for e in loose if e["t"] is not None
        ]
        ends = [n["end"] for n in nodes.values() if n["end"] is not None] + times
        result: dict[str, Any] = {
            "actor": actor,
            "label": self._label(actor),
            "window": {"start": min(times, default=0.0), "end": max(ends, default=0.0)},
            "spans": roots,
            "events": loose,
            "unjoined_mcp_calls": unjoined,
            "errors": [row for row in self.errors if _actor_of(row) == actor],
        }
        if actor.startswith("agent:"):
            agent = actor.split(":", 1)[1]
            result["claims"] = [
                {
                    key: claim.get(key)
                    for key in (
                        "claim_id",
                        "claim_text",
                        "claim_type",
                        "support",
                        "source_id",
                        "subtopic_id",
                        "round",
                        "facet_ids",
                        "locator",
                        "eligible",
                    )
                    if key in claim
                }
                for claim in self.claims
                if claim.get("researcher_agent_id") == agent
            ]
        if actor.startswith("arm:"):
            arm = actor.split(":", 1)[1]
            result["answers"] = [self._answer(a) for a in self.answers if a.get("arm_id") == arm]
        return result

    @staticmethod
    def _enclosing(nodes: dict[str, dict[str, Any]], item: dict[str, Any]) -> dict[str, Any] | None:
        """The innermost span of the same actor open when the event happened.

        An event emitted on a worker thread carries a span id the tracer never
        exported, so its recorded span is not in ``spans.jsonl``. It is placed
        in the narrowest span of its own actor - and of its own question, when
        both carry one - whose window contains it, and marked ``by_time`` so
        the view can say the placement was inferred rather than recorded. An
        event no window contains goes to the one span of its question, if
        exactly one exists (``by_question``), and otherwise stays loose.
        """

        moment = item.get("t")
        if moment is None:
            return None
        question = item.get("question_id")
        best: dict[str, Any] | None = None
        for node in nodes.values():
            start, end = node["start"], node["end"]
            if start is None or end is None or not start - 0.0005 <= moment <= end + 0.0005:
                continue
            owner = node["attributes"].get("question_id")
            if question and owner and owner != question:
                continue
            if best is None or (end - start) < (best["end"] - best["start"]):
                best = node
        return best

    def mcp_call(self, index: int) -> dict[str, Any]:
        if not 0 <= index < len(self.mcp):
            raise KeyError(index)
        call = self.mcp[index]
        return {
            "index": index,
            "tool": call.get("tool"),
            "status": call.get("status"),
            "attempt": call.get("attempt"),
            "duration_ms": call.get("duration_ms"),
            "arm_id": call.get("arm_id"),
            "question_id": call.get("question_id"),
            "recorded_at": call.get("recorded_at"),
            "request": call.get("request"),
            "response": call.get("response"),
        }

    # -- shaping -----------------------------------------------------------
    def _offset(self, value: Any) -> float | None:
        moment = parse_time(value)
        if moment is None or self.started is None:
            return None
        return round(moment - self.started, 4)

    def _event(self, event: dict[str, Any]) -> dict[str, Any]:
        item = {
            "event_id": event.get("event_id"),
            "sequence": event.get("sequence"),
            "t": self._offset(event.get("occurred_at")),
            "event_type": event.get("event_type"),
            "status": event.get("status"),
            "question_id": event.get("question_id"),
            "payload": event.get("payload") or {},
        }
        index = self.mcp_of_event.get(str(event.get("event_id")))
        if index is not None:
            item["mcp_call"] = index
        return item

    def _mcp_summary(self, index: int) -> dict[str, Any]:
        call = self.mcp[index]
        return {
            "mcp_call": index,
            "tool": call.get("tool"),
            "status": call.get("status"),
            "duration_ms": call.get("duration_ms"),
            "question_id": call.get("question_id"),
            "t": self._offset(call.get("recorded_at")),
        }

    def _answer(self, answer: dict[str, Any]) -> dict[str, Any]:
        question = self.questions.get(answer.get("question_id")) or {}
        return {
            "question_id": answer.get("question_id"),
            "question": question.get("text"),
            "question_type": question.get("type"),
            "cohorts": question.get("cohorts"),
            "repetition": answer.get("repetition"),
            "status": answer.get("status"),
            "abstained": answer.get("abstained"),
            "abstention_reason": answer.get("abstention_reason"),
            "answer_text": answer.get("answer_text"),
            "claims": answer.get("claims") or [],
            "queries_used": answer.get("queries_used") or [],
            "read_passages": answer.get("read_passages") or [],
            "search_calls": answer.get("search_calls"),
            "latency_ms": answer.get("latency_ms"),
            "cost_usd": answer.get("cost_usd"),
            "model": answer.get("model"),
            "snapshot_id": answer.get("snapshot_id"),
            "error": answer.get("error"),
        }
