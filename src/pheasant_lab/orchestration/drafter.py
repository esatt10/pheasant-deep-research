"""Drafting a research topic from a person's intent.

A topic is title, seed terms, facets and a window - a shape that is easy to
fill in once you know the field's vocabulary and hard before. So the console
lets a person start from what they actually want to know, in their own words,
and asks the planner's model for a *draft*: proposed seed terms and facets the
person then reads, edits and saves. Nothing is saved from here, and the intent
itself is kept on the topic, where the planner reads it on every run - the
draft is a starting point, the intent is the brief.

The call goes through the same budget guard as everything else (reserve the
worst case, run, reconcile) against a cap of its own, because it happens
before any run exists to charge it to. Under the ``replay`` provider the draft
is rule-based and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..budget import BUCKETS, CostLedger
from ..models import ModelProvider, ModelRequest
from ..promptlib import load as load_prompt
from ..settings import LabConfig

DEFAULT_DRAFT_BUDGET_USD = 0.25
SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str, prefix: str = "") -> str:
    body = SLUG.sub("-", text.lower()).strip("-")[:60].strip("-")
    return f"{prefix}{body}" if body else ""


@dataclass
class TopicDraft:
    intent: str
    title: str
    seed_terms: list[str]
    facets: list[dict[str, Any]]
    date_range: dict[str, str | None]
    notes: str
    model: str
    provider: str
    deterministic: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": slug(" ".join(self.title.split()[:6]), "topic-"),
            "intent": self.intent,
            "title": self.title,
            "seed_terms": list(self.seed_terms),
            "facets": [dict(f) for f in self.facets],
            "date_range": dict(self.date_range),
            "notes": self.notes,
            "drafted_by": {
                "provider": self.provider,
                "model": self.model,
                "deterministic": self.deterministic,
                "cost_usd": self.cost_usd,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
        }


def draft_topic(
    config: LabConfig,
    model: ModelProvider,
    *,
    intent: str,
    seed_terms: list[str] | None = None,
    max_cost_usd: float = DEFAULT_DRAFT_BUDGET_USD,
    max_facets: int = 5,
) -> TopicDraft:
    intent = intent.strip()
    if len(intent) < 12:
        raise ValueError("describe the intent in a sentence or two; there is nothing to draft from")
    spec = config.role("planner")
    ledger = CostLedger(
        total_budget_usd=max_cost_usd,
        allocation={bucket: (1.0 if bucket == "planning" else 0.0) for bucket in BUCKETS},
        pricing=config.pricing,
        fail_when_price_missing=config.pricing_ref.fail_when_model_price_missing,
        budget_section=config.budget,
    )
    given = [t.strip() for t in seed_terms or [] if t.strip()]
    request = ModelRequest(
        role="planner",
        schema="topic_draft",
        system=load_prompt("topic-drafter"),
        user=(
            f"Intent, in the person's own words:\n{intent}\n\n"
            f"Seed terms they already have: {', '.join(given) or '(none)'}\n"
            f"Collection profile: {config.collection.profile}\n"
            f"Produce at most {max_facets} facets. Return the JSON object described above "
            "and nothing else."
        ),
        context={"intent": intent, "seed_terms": given, "max_facets": max_facets},
        max_output_tokens=min(spec.max_output_tokens, 2000),
        temperature=spec.temperature,
        seed=config.experiment.seed,
    )
    with ledger.spend(
        bucket="planning",
        role="planner",
        model=spec.model,
        prompt=request.prompt_text,
        max_output_tokens=request.max_output_tokens,
    ) as cost:
        response = model.complete(request)
        cost.input_tokens = response.input_tokens
        cost.output_tokens = response.output_tokens
    data = response.data

    seeds = list(dict.fromkeys([*given, *(str(t).strip() for t in data.get("seed_terms") or [])]))
    facets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data.get("facets") or []:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        facet_id = slug(str(row.get("id") or "")) or slug(label)
        if not facet_id or facet_id in seen:
            continue
        seen.add(facet_id)
        try:
            weight = float(row.get("weight") or 1)
        except (TypeError, ValueError):
            weight = 1.0
        facets.append(
            {
                "id": facet_id,
                "label": label,
                "weight": min(3.0, max(0.5, weight)),
                "rationale": str(row.get("rationale") or ""),
            }
        )
        if len(facets) >= max_facets:
            break
    if not facets:
        raise ValueError("the model proposed no facets; edit the intent or add facets by hand")
    window = data.get("date_range") or {}
    return TopicDraft(
        intent=intent,
        title=str(data.get("title") or "").strip()[:120] or intent[:90],
        seed_terms=[s for s in seeds if s][:12],
        facets=facets,
        date_range={"from": window.get("from") or None, "to": window.get("to") or None},
        notes=str(data.get("notes") or ""),
        model=response.model,
        provider=response.provider,
        deterministic=response.deterministic,
        cost_usd=round(cost.actual_usd or 0.0, 6),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
