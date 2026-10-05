"""Research topics, read and added from the console.

A topic is *content*, not a setting: a title, seed terms, a date window, the
facets coverage is measured against and what counts as an authoritative
source. It does not fit in a ``--set``, so this is the one place the console
writes a file - and it writes exactly one, ``configs/topics.local.yaml``
(ignored by git, like every other local config), never the file a config
names. The new file carries the topics the current file already holds plus
the new one, and the console points the run at it with an ordinary override,
``experiment.topics_file=configs/topics.local.yaml``. The argv a run records
is therefore still the whole story, and the shipped topics files are never
rewritten under anyone.

A topic is validated by the same :class:`~pheasant_lab.settings.Topic` model
``load_config`` uses, and then the whole config is resolved again against the
written file, so the console cannot save a topic the CLI would refuse.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from ..settings import Topic, TopicsFile, load_config

LOCAL_TOPICS = "configs/topics.local.yaml"
OVERRIDE_KEY = "experiment.topics_file"

#: Topic and facet ids land in record ids, file names and report headings.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")

HEADER = """\
# Research topics added from the pheasant-lab console.
#
# The console writes this file and nothing else: the topics of the file the
# config named, plus the ones added. A run uses it through
#   --set experiment.topics_file=configs/topics.local.yaml
# which is recorded in the run's argv like any other override. Edit it by
# hand freely; the console re-reads it every time.
"""


def topic_rows(project_root: Path, config: Path, overrides: dict[str, str]) -> dict[str, Any]:
    """Every topic the resolved config sees, whole, and the file it came from."""

    resolved = load_config(config, overrides=overrides, project_root=project_root, env_file=".env")
    source = Path(resolved.source_files["topics"])
    try:
        shown = str(source.resolve().relative_to(project_root.resolve()))
    except ValueError:
        shown = str(source)
    return {
        "topics_file": shown,
        "local_file": LOCAL_TOPICS,
        "override": f"{OVERRIDE_KEY}={LOCAL_TOPICS}",
        "topics": [_dump(topic) for topic in resolved.topics],
    }


def add_topic(
    project_root: Path,
    config: Path,
    overrides: dict[str, str],
    payload: dict[str, Any],
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Validate ``payload`` as a topic and write it beside the current ones."""

    topic = Topic.model_validate(_clean(payload))
    # Enforced for topics added here, not at load: a topics file with neither
    # loaded before intents existed, and must keep loading.
    if not topic.seed_terms and not topic.intent:
        raise ValueError(
            f"topic {topic.id} has neither seed terms nor an intent; the planner would have "
            "nothing to search from"
        )
    if not SLUG.match(topic.id):
        raise ValueError(
            f"topic id {topic.id!r} must be lowercase letters, digits and hyphens (2-80)"
        )
    for facet in topic.facets:
        if not SLUG.match(facet.id):
            raise ValueError(f"facet id {facet.id!r} must be lowercase letters, digits and hyphens")
        if facet.weight <= 0:
            raise ValueError(f"facet {facet.id} has weight {facet.weight}; weights are positive")
    if len({facet.id for facet in topic.facets}) != len(topic.facets):
        raise ValueError(f"topic {topic.id} repeats a facet id")

    current = load_config(config, overrides=overrides, project_root=project_root, env_file=".env")
    topics = list(current.topics)
    existing = [index for index, row in enumerate(topics) if row.id == topic.id]
    if existing and not replace:
        raise ValueError(f"a topic {topic.id!r} already exists; save it as a replacement")
    if existing:
        topics[existing[0]] = topic
    else:
        topics.append(topic)

    target = (project_root / LOCAL_TOPICS).resolve()
    document = {"topics": [_dump(row) for row in TopicsFile(topics=topics).topics]}
    _write(target, HEADER + "\n" + yaml.safe_dump(document, sort_keys=False, allow_unicode=True))

    # Resolve again against what was written: the CLI is the judge.
    pointed = {**overrides, OVERRIDE_KEY: LOCAL_TOPICS}
    load_config(config, overrides=pointed, project_root=project_root, env_file=".env")
    return {
        "topic": _dump(topic),
        "replaced": bool(existing),
        "topics_file": LOCAL_TOPICS,
        "override": f"{OVERRIDE_KEY}={LOCAL_TOPICS}",
        "count": len(topics),
    }


def _dump(topic: Topic) -> dict[str, Any]:
    row = topic.model_dump(mode="json", by_alias=True)
    if row.get("intent") is None:
        row.pop("intent", None)
    return row


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the empty strings a form sends for a field left blank."""

    data = dict(payload)
    data["intent"] = str(data.get("intent") or "").strip() or None
    data["seed_terms"] = [str(t).strip() for t in data.get("seed_terms") or [] if str(t).strip()]
    window = dict(data.get("date_range") or {})
    data["date_range"] = {key: (window.get(key) or None) for key in ("from", "to")}
    authority = dict(data.get("source_authority") or {})
    for key in ("family_key", "preferred_types"):
        if key in authority:
            authority[key] = [str(v).strip() for v in authority[key] or [] if str(v).strip()]
    if not authority.get("family_key"):
        authority.pop("family_key", None)
    data["source_authority"] = authority
    return data


def _write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
