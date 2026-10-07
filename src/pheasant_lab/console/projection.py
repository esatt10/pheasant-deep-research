"""The live view of a run, folded from its raw files and nothing else.

The console draws what a run *is doing*: which agent is searching, what it
handed Pheasant, whether Pheasant has indexed it yet, what it has cost. Every
one of those is already in the append-only trace (rule 5), so this module is a
fold over ``raw/events.jsonl``, ``raw/spans.jsonl`` and
``raw/ingest-receipts.jsonl`` - it never asks a running process, never writes,
and two consoles watching one run draw the same picture.

It is a projection in the same sense as the DuckDB one: derived, rebuildable,
and never a source of a number a report states. Two things it deliberately
does not do:

* **It does not infer indexing from silence.** A document is ``indexed`` when
  its receipt says so or the barrier crossed, ``awaiting_claim`` only when the
  region said the sync was queued and its queue listing says unclaimed, and
  otherwise stays ``accepted``. A console that guessed would show a run that
  looks healthy while its barrier is waiting on an indexer that is not there.
* **It does not score.** Arm progress is counted (answered, abstained); no
  metric is computed here, because a live metric over a partial question set
  is a number with the wrong denominator (rule 1).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

#: The run's phases, in order, with the stage key ``state.json`` records.
PHASES: tuple[tuple[str, str], ...] = (
    ("plan", "Plan"),
    ("collect", "Collect"),
    ("freeze-benchmark", "Freeze"),
    ("evaluate", "Evaluate"),
    ("report", "Report"),
)

#: Custody stages, in the order a document moves through them.
CUSTODY = (
    "discovered",
    "acquired",
    "submitted",
    "accepted",
    "awaiting_claim",
    "claimed",
    "indexed",
    "rejected",
)

_STAGE_RANK = {name: index for index, name in enumerate(CUSTODY)}

ARM_LABELS = {
    "S0": "Source-aware specialist",
    "C0": "Prior-only control",
    "P0": "Pheasant corpus",
    "P1": "Pheasant + memory",
    "P2": "Tuned replay",
}


def parse_time(value: Any) -> float | None:
    """Seconds since the epoch for an ISO timestamp, or ``None``."""

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class _Agent:
    agent_id: str
    role: str
    subtopic_id: str | None = None
    searches: int = 0
    discovered: int = 0
    acquired: int = 0
    submitted: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    last_event: str | None = None
    last_at: float | None = None
    last_query: str | None = None
    open_search: bool = False

    def as_dict(self, subtopics: dict[str, dict[str, Any]]) -> dict[str, Any]:
        subtopic = subtopics.get(self.subtopic_id or "", {})
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "short_id": _short_agent(self.agent_id),
            "subtopic_id": self.subtopic_id,
            "subtopic_label": subtopic.get("label"),
            "searches": self.searches,
            "discovered": self.discovered,
            "acquired": self.acquired,
            "submitted": self.submitted,
            "tool_calls": self.tool_calls,
            "cost_usd": round(self.cost_usd, 6),
            "last_event": self.last_event,
            "last_at": self.last_at,
            "last_query": self.last_query,
            "status": "searching" if self.open_search else "idle",
        }


@dataclass
class LiveModel:
    """Folds events, spans and receipts into the console's view model."""

    run_id: str
    started_at: float | None = None
    last_at: float | None = None
    sequence: int = 0
    manifest: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, _Agent] = field(default_factory=dict)
    subtopics: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    ticks: list[dict[str, Any]] = field(default_factory=list)
    bars: list[dict[str, Any]] = field(default_factory=list)
    spend: dict[str, dict[str, float]] = field(default_factory=dict)
    facets: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)
    rounds: list[dict[str, Any]] = field(default_factory=list)
    arms: dict[str, dict[str, Any]] = field(default_factory=dict)
    questions_total: int | None = None
    region: dict[str, Any] = field(default_factory=dict)
    notices: list[dict[str, Any]] = field(default_factory=list)
    barrier: dict[str, Any] = field(default_factory=dict)
    sync: dict[str, Any] = field(default_factory=dict)
    queue: list[dict[str, Any]] | None = None
    #: What the region said it holds for the lab's source after the barrier
    #: (``ingest.inventory``, pheasant >= 0.13.3); ``None`` until it says.
    inventory: dict[str, Any] | None = None
    #: Where the region's latest sync stands *between* acceptance and
    #: indexing: ``awaiting_claim``, ``claimed`` or ``None``. Held on the
    #: model, not stamped on documents, because the events that say so and
    #: the receipts that say which documents were accepted arrive in
    #: different files and either can be read first.
    claim_state: str | None = None
    claim_waiting: float | None = None
    tools: Counter = field(default_factory=Counter)
    phase_events: dict[str, float] = field(default_factory=dict)
    finished: bool = False
    _receipt_keys: dict[str, str] = field(default_factory=dict)

    # -- folding -----------------------------------------------------------
    def apply_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event_type") or "")
        status = str(event.get("status") or "")
        payload = event.get("payload") or {}
        at = parse_time(event.get("occurred_at"))
        self.sequence = max(self.sequence, int(event.get("sequence") or 0))
        if at is not None:
            if self.started_at is None:
                self.started_at = at
            self.last_at = max(self.last_at or at, at)
        agent = self._agent(event)
        if agent is not None:
            agent.last_event = kind
            agent.last_at = at

        if kind == "run.started" and self.started_at is None:
            self.started_at = at
        elif kind == "collection.plan":
            self.phase_events.setdefault("plan", at or 0.0)
            for row in payload.get("subtopics") or []:
                subtopic_id = str(row.get("subtopic_id") or row.get("id") or "")
                if subtopic_id:
                    self.subtopics[subtopic_id] = {
                        "subtopic_id": subtopic_id,
                        "question": row.get("question"),
                        "facet_ids": list(row.get("facet_ids") or []),
                        "label": _subtopic_label(subtopic_id),
                    }
            self._tick("planner", at, "plan", f"{len(self.subtopics)} subtopics")
        elif kind == "collection.search":
            if agent is not None:
                agent.subtopic_id = payload.get("subtopic_id") or agent.subtopic_id
                if status == "started":
                    agent.searches += 1
                    agent.open_search = True
                    agent.last_query = payload.get("query") or agent.last_query
                else:
                    agent.open_search = False
            self.phase_events.setdefault("collect", at or 0.0)
        elif kind == "collection.discovered":
            source_id = str(payload.get("source_id") or "")
            if agent is not None:
                agent.discovered += 1
                agent.subtopic_id = payload.get("subtopic_id") or agent.subtopic_id
            if source_id:
                self.sources.setdefault(
                    source_id,
                    {
                        "source_id": source_id,
                        "title": payload.get("title"),
                        "subtopic_id": payload.get("subtopic_id"),
                        "agent_id": event.get("agent_id"),
                        "provider": payload.get("provider"),
                        "identifier": payload.get("stable_identifier"),
                        "stage": "discovered",
                        "waiting_seconds": None,
                    },
                )
            self._tick(_lane_for(event), at, "discovered", payload.get("title"))
        elif kind == "collection.acquired":
            source_id = str(payload.get("source_id") or "")
            if agent is not None:
                agent.acquired += 1
            if source_id in self.sources:
                self._advance(source_id, "acquired")
                self.sources[source_id]["mode"] = payload.get("mode")
            self._tick(_lane_for(event), at, "acquired", self._title(source_id))
        elif kind == "collection.round":
            self.rounds.append(
                {
                    "round": payload.get("round"),
                    "at": self._offset(at),
                    "decision": payload.get("decision"),
                    "acquired": payload.get("acquired"),
                    "new_eligible_claims": payload.get("new_eligible_claims"),
                }
            )
            budget = payload.get("budget") or {}
            for bucket, row in (budget.get("by_bucket") or {}).items():
                self.spend.setdefault(bucket, {}).update(
                    budget_usd=float(row.get("budget_usd") or 0.0)
                )
            self._tick("orchestrator", at, "round", f"round {payload.get('round')} closed")
        elif kind == "collection.audit":
            self.audit = {k: v for k, v in payload.items() if k != "facet_coverage"}
            self.facets = list(payload.get("facet_coverage") or [])
            self._tick("auditor", at, "audit", f"coverage {payload.get('coverage_fraction')}")
        elif kind == "collection.finished":
            audit = payload.get("audit") or {}
            if audit.get("facet_coverage"):
                self.facets = list(audit["facet_coverage"])
                self.audit = {k: v for k, v in audit.items() if k != "facet_coverage"}
            self._tick("orchestrator", at, "finished", "collection finished")
        elif kind == "benchmark.built":
            self.phase_events.setdefault("freeze-benchmark", at or 0.0)
            cohorts = payload.get("cohorts") or {}
            self.questions_total = sum(int(v) for v in cohorts.values()) or self.questions_total
            self._tick("orchestrator", at, "freeze", "benchmark frozen")
        elif kind == "arm.answered":
            self.phase_events.setdefault("evaluate", at or 0.0)
            arm_id = str(payload.get("arm_id") or event.get("arm_id") or "")
            row = self.arms.setdefault(
                arm_id,
                {"arm_id": arm_id, "answered": 0, "abstained": 0, "latency_ms": 0.0, "cost": 0.0},
            )
            row["answered"] += 1
            row["abstained"] += 1 if payload.get("abstained") else 0
            row["latency_ms"] += float(payload.get("latency_ms") or 0.0)
            row["cost"] += float(payload.get("cost_usd") or 0.0)
            self._tick(f"arm:{arm_id}", at, "answer", payload.get("question_id"))
        elif kind == "cost.model_call":
            bucket = str(payload.get("bucket") or "reserve")
            row = self.spend.setdefault(bucket, {})
            row["committed_usd"] = row.get("committed_usd", 0.0) + float(
                payload.get("actual_usd") or 0.0
            )
            if agent is not None:
                agent.cost_usd += float(payload.get("actual_usd") or 0.0)
        elif kind == "mcp.session.initialized":
            self.region.update(
                server_name=payload.get("server_name"),
                server_version=payload.get("server_version"),
                protocol_version=payload.get("protocol_version"),
            )
        elif kind == "mcp.tool.call":
            self._tool_call(event, payload, status, at, agent)
        elif kind == "ingest.sync":
            self._sync(payload, at)
        elif kind == "ingest.barrier":
            self._barrier(payload, at)
        elif kind == "ingest.inventory":
            self.inventory = {
                key: payload.get(key)
                for key in (
                    "disposition",
                    "region_documents",
                    "region_bytes",
                    "receipts_indexed",
                    "receipts",
                )
            }
            self._tick("pheasant", at, "inventory", payload.get("disposition"))
            if payload.get("disposition") == "mismatch":
                self._notice(
                    "warn",
                    "Region holds a different count",
                    f"The region lists {payload.get('region_documents')} document(s) in "
                    f"{payload.get('source_name')}; {payload.get('receipts_indexed')} receipt(s) "
                    "say indexed. A duplicate, a stray file or a loss - reconcile says which.",
                    at,
                    code="inventory",
                )
        elif kind in {"memory.seeded", "memory.indexed", "tuning.strategy"}:
            self._tick("orchestrator", at, kind.split(".")[0], kind)

    def apply_span(self, span: dict[str, Any]) -> None:
        name = str(span.get("name") or "")
        attributes = span.get("attributes") or {}
        start = self._offset(parse_time(span.get("started_at")))
        end = self._offset(parse_time(span.get("ended_at")))
        if start is None or end is None:
            return
        if name.startswith("research."):
            lane = f"agent:{attributes.get('agent_id')}"
            label = name.split(".", 1)[1]
            agent = self._agent(
                {"agent_id": attributes.get("agent_id"), "agent_role": attributes.get("agent_role")}
            )
            if agent is not None and attributes.get("subtopic_id"):
                agent.subtopic_id = agent.subtopic_id or attributes.get("subtopic_id")
        elif name == "arm.answer":
            lane = f"arm:{attributes.get('arm_id')}"
            label = "answer"
        elif name in {"collection", "evaluation"}:
            lane = "orchestrator"
            label = name
        else:
            return
        self.bars.append(
            {
                "lane": lane,
                "start": start,
                "end": max(end, start),
                "label": label,
                "status": span.get("status"),
            }
        )

    def apply_receipt(self, receipt: dict[str, Any]) -> None:
        source_id = str(receipt.get("source_id") or "")
        status = str(receipt.get("status") or "")
        key = str(receipt.get("idempotency_key") or "")
        if key:
            self._receipt_keys[key] = source_id
        if source_id not in self.sources:
            return
        if status == "accepted":
            self._advance(source_id, "accepted")
        elif status == "indexed":
            self._advance(source_id, "indexed")
        elif status in {"rejected", "failed"}:
            self.sources[source_id]["stage"] = "rejected"
            self.sources[source_id]["error"] = receipt.get("error_code")
        elif status == "no_receipt":
            self._advance(source_id, "submitted")

    # -- region ------------------------------------------------------------
    def _tool_call(
        self,
        event: dict[str, Any],
        payload: dict[str, Any],
        status: str,
        at: float | None,
        agent: _Agent | None,
    ) -> None:
        tool = str(payload.get("tool") or "")
        self.tools[tool] += 1
        if agent is not None:
            agent.tool_calls += 1
            if tool.startswith("submit"):
                agent.submitted += 1
        refusal = payload.get("refusal_code")
        self._tick(
            "pheasant",
            at,
            "tool_failed" if status == "failed" else "tool",
            tool,
            arm=event.get("arm_id") or payload.get("arm_id"),
        )
        if refusal == "REGION_BUSY":
            self._notice(
                "warn",
                "Region busy, retrying",
                f"{tool} was refused while another holder owned the lease "
                f"(attempt {payload.get('attempt')}).",
                at,
                code="REGION_BUSY",
            )
        elif status == "failed" and refusal:
            self._notice("danger", f"{tool} refused", str(refusal), at, code=str(refusal))

    def _sync(self, payload: dict[str, Any], at: float | None) -> None:
        disposition = str(payload.get("disposition") or "")
        region = payload.get("region") or {}
        self.sync = {
            "disposition": disposition,
            "at": self._offset(at),
            "task_id": region.get("task_id") or next(iter(region.get("queued_tasks") or []), None),
            "mode": payload.get("mode"),
        }
        if disposition == "queued":
            self.claim_state = "awaiting_claim"
            self.queue = None
            self._notice(
                "warn",
                "Sync queued, not yet claimed",
                "Pheasant published the sync"
                + (f" as task {self.sync['task_id']}" if self.sync["task_id"] else "")
                + " instead of running it. Documents are accepted and become searchable "
                "once an indexer claims and finishes it.",
                at,
                code="queued",
            )
        elif disposition == "already_syncing":
            self._notice(
                "info",
                "Already syncing",
                "Another job holds the source; these documents are picked up by it, and the "
                "barrier waits for that job.",
                at,
                code="already_syncing",
            )
        self._tick("indexer", at, f"sync_{disposition}", disposition)

    def _barrier(self, payload: dict[str, Any], at: float | None) -> None:
        disposition = str(payload.get("disposition") or "")
        queue = payload.get("queue")
        self.barrier = {
            "state": disposition,
            "poll": payload.get("poll"),
            "waited_seconds": payload.get("waited_seconds"),
            "still_accepted": payload.get("still_accepted"),
            "acknowledged": payload.get("acknowledged"),
            "at": self._offset(at),
        }
        if isinstance(queue, list):
            self.queue = queue
            states = {task.get("state") for task in queue}
            if "claimed" in states:
                self.claim_state = "claimed"
            elif "awaiting_claim" in states:
                self.claim_state = "awaiting_claim"
            self.claim_waiting = max(
                (task.get("waiting_seconds") or 0.0 for task in queue), default=None
            )
            claimed = [task for task in queue if task.get("state") == "claimed"]
            if claimed:
                self._notice(
                    "info",
                    "Indexer claimed the sync",
                    f"Claimed by {claimed[0].get('claimed_by') or 'an indexer'}; "
                    f"{payload.get('still_accepted')} document(s) still indexing.",
                    at,
                    code="claimed",
                )
        if disposition in {"crossed", "timed_out"}:
            self.claim_state = None
        if disposition == "crossed":
            for source in self.sources.values():
                if source["stage"] in {"accepted", "submitted"}:
                    source["stage"] = "indexed"
            self.queue = []
            self._notice(
                "ok",
                "Barrier crossed",
                f"Indexed and acknowledged after {payload.get('waited_seconds')}s "
                f"({payload.get('poll')} poll(s)).",
                at,
                code="crossed",
            )
        elif disposition == "timed_out":
            self._notice(
                "danger",
                "Barrier timed out",
                f"{payload.get('still_accepted')} document(s) still accepted, not indexed. "
                "They are not lost: the region holds them and a later sync can index them.",
                at,
                code="timed_out",
            )
        self._tick(
            "indexer",
            at,
            f"barrier_{disposition}",
            payload.get("still_accepted"),
            claimed=isinstance(queue, list)
            and any(task.get("state") == "claimed" for task in queue),
        )

    def _notice(self, tone: str, title: str, detail: str, at: float | None, *, code: str) -> None:
        # One live notice per code: a barrier polling twenty times is one
        # wait, updated, not twenty toasts.
        for notice in self.notices:
            if notice["code"] == code and not notice.get("closed"):
                notice.update(title=title, detail=detail, at=self._offset(at), tone=tone)
                break
        else:
            self.notices.append(
                {
                    "tone": tone,
                    "title": title,
                    "detail": detail,
                    "at": self._offset(at),
                    "code": code,
                }
            )
        # A later state closes the earlier ones it supersedes.
        supersedes = {
            "claimed": {"queued"},
            "crossed": {"queued", "claimed", "already_syncing", "timed_out"},
            "timed_out": {"claimed"},
        }.get(code, set())
        for notice in self.notices:
            if notice["code"] in supersedes:
                notice["closed"] = True

    # -- helpers -----------------------------------------------------------
    def _agent(self, event: dict[str, Any]) -> _Agent | None:
        agent_id = event.get("agent_id")
        if not agent_id:
            return None
        agent = self.agents.get(agent_id)
        if agent is None:
            agent = self.agents[agent_id] = _Agent(
                agent_id=str(agent_id), role=str(event.get("agent_role") or "agent")
            )
        return agent

    def _advance(self, source_id: str, stage: str) -> None:
        source = self.sources[source_id]
        if source["stage"] == "rejected":
            return
        if _STAGE_RANK.get(stage, 0) > _STAGE_RANK.get(source["stage"], 0):
            source["stage"] = stage

    def _title(self, source_id: str) -> str | None:
        return (self.sources.get(source_id) or {}).get("title")

    def _offset(self, at: float | None) -> float | None:
        if at is None or self.started_at is None:
            return None
        return round(at - self.started_at, 3)

    def _tick(self, lane: str, at: float | None, kind: str, label: Any, **extra: Any) -> None:
        offset = self._offset(at)
        if offset is None:
            return
        self.ticks.append({"lane": lane, "t": offset, "kind": kind, "label": label, **extra})

    # -- the view ----------------------------------------------------------
    def _display(self, source: dict[str, Any]) -> dict[str, Any]:
        if source["stage"] == "accepted" and self.claim_state:
            return {**source, "stage": self.claim_state, "waiting_seconds": self.claim_waiting}
        return source

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        sources = [self._display(source) for source in self.sources.values()]
        custody = Counter(source["stage"] for source in sources)
        # The ladder is cumulative - an indexed document was also discovered,
        # acquired, submitted and accepted - but the two fleet states are
        # not rungs every document climbs: a standalone region indexes inside
        # the sync call and nothing is ever awaiting a claim. They are counted
        # as they stand now, so a funnel never claims a wait that did not
        # happen.
        ladder = ("discovered", "acquired", "submitted", "accepted", "indexed")
        funnel: dict[str, int] = {}
        for stage in ladder:
            rank = _STAGE_RANK[stage]
            funnel[stage] = sum(
                count
                for name, count in custody.items()
                if name != "rejected" and _STAGE_RANK.get(name, 0) >= rank
            )
        funnel["awaiting_claim"] = custody.get("awaiting_claim", 0)
        funnel["claimed"] = custody.get("claimed", 0)
        funnel["rejected"] = custody.get("rejected", 0)

        budget_total = float(self.manifest.get("cost_budget_usd") or 0.0)
        allocation = (self.manifest.get("resolved_config") or {}).get("budget", {}).get(
            "allocation"
        ) or {}
        buckets = []
        for bucket in sorted(set(allocation) | set(self.spend)):
            row = self.spend.get(bucket, {})
            buckets.append(
                {
                    "bucket": bucket,
                    "budget_usd": row.get("budget_usd")
                    or round(budget_total * float(allocation.get(bucket, 0.0)), 4),
                    "committed_usd": round(row.get("committed_usd", 0.0), 6),
                }
            )

        stages = self.state.get("stages") or {}
        # A phase is reached when one of its events was seen or its stage was
        # recorded; the last phase reached is the active one, and every phase
        # before it is done - the CLI runs them in this order and no other.
        reached = [
            index
            for index, (key, _) in enumerate(PHASES)
            if key in self.phase_events or key in stages or (key == "report" and self.finished)
        ]
        last = max(reached) if reached else 0
        phases = []
        for index, (key, label) in enumerate(PHASES):
            recorded = (stages.get(key) or {}).get("status")
            if index < last or recorded == "completed" or (key == "report" and self.finished):
                state = "done"
            elif index == last:
                state = "active"
            else:
                state = "pending"
            phases.append({"key": key, "label": label, "state": state})

        lanes = self._lanes()
        horizon = max(
            [bar["end"] for bar in self.bars] + [tick["t"] for tick in self.ticks] + [0.0]
        )
        clock = now if now is not None else self.last_at
        elapsed = (clock - self.started_at) if clock and self.started_at else horizon
        topic = self._topic()
        arms = []
        for arm_id in sorted(self.arms):
            row = self.arms[arm_id]
            arms.append(
                {
                    "arm_id": arm_id,
                    "label": ARM_LABELS.get(arm_id, arm_id),
                    "answered": row["answered"],
                    "abstained": row["abstained"],
                    "mean_latency_ms": round(row["latency_ms"] / row["answered"], 1)
                    if row["answered"]
                    else None,
                    "cost_usd": round(row["cost"], 6),
                }
            )
        return {
            "run": {
                "run_id": self.run_id,
                "experiment": self.manifest.get("experiment_name"),
                "topic_id": topic.get("id"),
                "topic_title": topic.get("title"),
                "command": self.manifest.get("command"),
                "config_digest": self.manifest.get("config_digest"),
                "created_at": self.manifest.get("created_at"),
                "sequence": self.sequence,
                "elapsed_seconds": round(max(elapsed, horizon), 3),
                "horizon_seconds": round(horizon, 3),
                "finished": self.finished,
                "phases": phases,
                "arms_configured": list(self.manifest.get("arms") or []),
            },
            "agents": [
                agent.as_dict(self._labelled_subtopics())
                for agent in sorted(self.agents.values(), key=lambda a: a.agent_id)
            ],
            "subtopics": list(self._labelled_subtopics().values()),
            "sources": sorted(sources, key=lambda s: (s.get("subtopic_id") or "", s["source_id"])),
            "custody": funnel,
            "lanes": lanes,
            "rounds": self.rounds,
            "budget": {
                "total_usd": budget_total,
                "committed_usd": round(sum(b["committed_usd"] for b in buckets), 6),
                "buckets": buckets,
            },
            "facets": self.facets,
            "audit": self.audit,
            "arms": arms,
            "questions_total": self.questions_total,
            "region": {
                **self.region,
                "tool_calls": dict(self.tools),
                "sync": self.sync,
                "barrier": self.barrier,
                "queue": self.queue,
                "inventory": self.inventory,
            },
            "notices": [n for n in self.notices if not n.get("closed")][-6:],
            "notice_history": self.notices[-20:],
        }

    def _topic(self) -> dict[str, Any]:
        wanted = self.state.get("topic_id") or next(iter(self.manifest.get("topics") or []), None)
        for topic in (self.manifest.get("resolved_config") or {}).get("topics") or []:
            if isinstance(topic, dict) and (wanted is None or topic.get("id") == wanted):
                return topic
        return {"id": wanted} if wanted else {}

    def _labelled_subtopics(self) -> dict[str, dict[str, Any]]:
        """Subtopics named by the facet they cover, which is what a person reads."""

        facets = {
            str(facet.get("id")): facet.get("label")
            for facet in self._topic().get("facets") or []
            if isinstance(facet, dict)
        }
        labelled = {}
        for subtopic_id, row in self.subtopics.items():
            names = [facets[f] for f in row.get("facet_ids") or [] if facets.get(f)]
            labelled[subtopic_id] = {**row, "label": names[0] if names else row.get("label")}
        return labelled

    def _lanes(self) -> list[dict[str, Any]]:
        # Pheasant's two lanes sit directly under the orchestrator: what the
        # region is doing with the swarm's work - above all a sync waiting for
        # an indexer to claim it - is the thing this console exists to show,
        # and must not be scrolled below a column of research branches.
        lanes: list[dict[str, Any]] = [
            {"id": "orchestrator", "label": "Orchestrator", "kind": "orchestrator"},
            {"id": "pheasant", "label": "Pheasant · MCP", "kind": "pheasant"},
            {"id": "indexer", "label": "Indexer", "kind": "indexer"},
            {"id": "planner", "label": "Planner", "kind": "planner"},
        ]
        for agent in sorted(self.agents.values(), key=lambda a: a.agent_id):
            if agent.role != "researcher":
                continue
            label = _short_agent(agent.agent_id)
            sub = self._labelled_subtopics().get(agent.subtopic_id or "", {}).get("label")
            lanes.append(
                {
                    "id": f"agent:{agent.agent_id}",
                    "label": f"{label} · {sub}" if sub else label,
                    "kind": "researcher",
                }
            )
        lanes.append({"id": "auditor", "label": "Auditor", "kind": "auditor"})
        for arm_id in sorted(
            set(self.arms) | {b["lane"][4:] for b in self.bars if b["lane"].startswith("arm:")}
        ):
            lanes.append({"id": f"arm:{arm_id}", "label": arm_id, "kind": "arm"})
        for lane in lanes:
            lane["bars"] = [bar for bar in self.bars if bar["lane"] == lane["id"]]
            lane["ticks"] = [tick for tick in self.ticks if tick["lane"] == lane["id"]][-400:]
        indexer = next(lane for lane in lanes if lane["id"] == "indexer")
        indexer["bars"] = self._indexer_bars()
        return lanes

    def _indexer_bars(self) -> list[dict[str, Any]]:
        """Pre-claim and claimed intervals, read off the sync and barrier ticks."""

        bars: list[dict[str, Any]] = []
        open_kind: str | None = None
        open_at = 0.0
        for tick in (t for t in self.ticks if t["lane"] == "indexer"):
            kind = tick["kind"]
            if kind == "sync_queued":
                open_kind, open_at = "awaiting_claim", tick["t"]
            elif (
                kind == "barrier_waiting" and open_kind == "awaiting_claim" and tick.get("claimed")
            ):
                bars.append(
                    {
                        "lane": "indexer",
                        "start": open_at,
                        "end": tick["t"],
                        "label": "awaiting claim",
                        "status": "pre_claim",
                    }
                )
                open_kind, open_at = "claimed", tick["t"]
            if kind in {"barrier_crossed", "barrier_timed_out"} and open_kind:
                # Crossing straight from "awaiting claim" means an indexer
                # claimed and finished between two polls. Say so, rather than
                # drawing the whole interval as a wait nobody serviced.
                unobserved = open_kind == "awaiting_claim" and kind == "barrier_crossed"
                bars.append(
                    {
                        "lane": "indexer",
                        "start": open_at,
                        "end": tick["t"],
                        "label": "queued · claim between polls"
                        if unobserved
                        else "awaiting claim"
                        if open_kind == "awaiting_claim"
                        else "indexing",
                        "status": "pre_claim" if open_kind == "awaiting_claim" else "ok",
                    }
                )
                open_kind = None
            if kind == "sync_completed":
                bars.append(
                    {
                        "lane": "indexer",
                        "start": tick["t"],
                        "end": tick["t"] + 0.05,
                        "label": "indexed in call",
                        "status": "ok",
                    }
                )
        if open_kind:
            bars.append(
                {
                    "lane": "indexer",
                    "start": open_at,
                    "end": None,
                    "label": "awaiting claim" if open_kind == "awaiting_claim" else "indexing",
                    "status": "pre_claim" if open_kind == "awaiting_claim" else "ok",
                }
            )
        return bars


def _lane_for(event: dict[str, Any]) -> str:
    agent_id = event.get("agent_id")
    return f"agent:{agent_id}" if agent_id else "orchestrator"


def _short_agent(agent_id: str) -> str:
    # agent-researcher-003-0f44e883 -> researcher 003
    parts = agent_id.split("-")
    if len(parts) >= 3 and parts[0] == "agent":
        return f"{parts[1]} {parts[2]}"
    return agent_id


def _subtopic_label(subtopic_id: str) -> str:
    return subtopic_id.removeprefix("sub-").replace("-", " ")


# ---------------------------------------------------------------------------
# reading a run directory, incrementally
# ---------------------------------------------------------------------------


@dataclass
class _Tail:
    """A byte offset into one append-only file; only whole lines are read."""

    path: Path
    offset: int = 0

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
        if not chunk:
            return []
        end = chunk.rfind(b"\n")
        if end < 0:
            return []  # a line still being written
        self.offset += end + 1
        rows = []
        for line in chunk[: end + 1].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


class RunWatcher:
    """Folds one run directory, reading only what was appended since last time."""

    def __init__(self, root: Path) -> None:
        self.root = root
        raw = root / "raw"
        self.events = _Tail(raw / "events.jsonl")
        self.spans = _Tail(raw / "spans.jsonl")
        self.receipts = _Tail(raw / "ingest-receipts.jsonl")
        self.model = LiveModel(run_id=root.name)
        self.event_log: list[dict[str, Any]] = []

    def refresh(self) -> list[dict[str, Any]]:
        """Fold what is new; return the new events (for a stream to forward)."""

        self.model.manifest = _read_json(self.root / "run-manifest.json")
        self.model.state = _read_json(self.root / "state.json")
        self.model.finished = (self.root / "reports" / "summary.md").is_file()
        events = self.events.read()
        for event in events:
            self.model.apply_event(event)
        self.event_log.extend(events)
        for span in self.spans.read():
            self.model.apply_span(span)
        for receipt in self.receipts.read():
            self.model.apply_receipt(receipt)
        return events


def fold_until(root: Path, sequence: int) -> LiveModel:
    """The view as it stood when event ``sequence`` was written.

    What the replay scrubber shows. Spans and receipts carry their own times,
    so each is included only if it had been written by then - a span that
    ended later is not drawn, exactly as the live view would not have had it.
    """

    watcher = RunWatcher(root)
    watcher.model.manifest = _read_json(root / "run-manifest.json")
    watcher.model.state = {}
    cutoff: float | None = None
    for event in watcher.events.read():
        if int(event.get("sequence") or 0) > sequence:
            break
        watcher.model.apply_event(event)
        cutoff = parse_time(event.get("occurred_at")) or cutoff
    if cutoff is None:
        return watcher.model
    for span in watcher.spans.read():
        ended = parse_time(span.get("ended_at"))
        if ended is not None and ended <= cutoff:
            watcher.model.apply_span(span)
    # An `indexed` receipt is re-appended carrying the time it was first
    # *accepted*, so its timestamp cannot place it. What can: the lab learns
    # a receipt is indexed only by crossing the barrier, so before a crossed
    # barrier has been folded, no receipt may say indexed.
    crossed = watcher.model.barrier.get("state") == "crossed"
    for receipt in watcher.receipts.read():
        recorded = parse_time(receipt.get("recorded_at"))
        if recorded is None or recorded > cutoff:
            continue
        if receipt.get("status") == "indexed" and not crossed:
            continue
        watcher.model.apply_receipt(receipt)
    return watcher.model


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _config_of(manifest: dict[str, Any]) -> str | None:
    words = str(manifest.get("command") or "").split()
    for index, word in enumerate(words[:-1]):
        if word == "--config":
            return words[index + 1]
    return None


def list_runs(output_root: Path) -> list[dict[str, Any]]:
    """Every run under ``output_root``, newest first, with what a list needs."""

    rows = []
    if not output_root.is_dir():
        return rows
    for root in output_root.glob("run-*"):
        manifest = _read_json(root / "run-manifest.json")
        if not manifest:
            continue
        state = _read_json(root / "state.json")
        events = root / "raw" / "events.jsonl"
        stages = state.get("stages") or {}
        wanted = state.get("topic_id") or next(iter(manifest.get("topics") or []), None)
        topic = next(
            (
                t
                for t in (manifest.get("resolved_config") or {}).get("topics") or []
                if isinstance(t, dict) and t.get("id") == wanted
            ),
            {"id": wanted},
        )
        rows.append(
            {
                "run_id": root.name,
                "experiment": manifest.get("experiment_name"),
                "topic_title": topic.get("title"),
                "command": manifest.get("command"),
                "created_at": manifest.get("created_at"),
                "updated_at": events.stat().st_mtime if events.is_file() else None,
                "stages": {k: (v or {}).get("status") for k, v in stages.items()},
                "reported": (root / "reports" / "summary.md").is_file(),
                "arms": manifest.get("arms"),
                "cost_budget_usd": manifest.get("cost_budget_usd"),
                "mock": "mock" in str((manifest.get("pheasant") or {}).get("transport", ""))
                or (root / "mock-region.json").is_file(),
                # What a resume needs to reproduce the run's own configuration:
                # the config file it was started from and the overrides it ran
                # under. A different digest would be refused, correctly.
                "config": _config_of(manifest),
                "overrides": [f"{k}={v}" for k, v in (manifest.get("overrides") or {}).items()],
                "complete": all(
                    (stages.get(stage) or {}).get("status") == "completed"
                    for stage in ("collect", "freeze-benchmark", "evaluate")
                )
                and (root / "reports" / "summary.md").is_file(),
                "interrupted": any(
                    (entry or {}).get("status") == "running" for entry in stages.values()
                ),
            }
        )
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows
