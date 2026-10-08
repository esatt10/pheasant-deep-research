"""The settings catalog: complete, derived, and readable by every surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from pheasant_lab.catalog import (
    DOCS,
    REASONING_EFFORTS,
    ROLE_ADVICE,
    ROLES,
    advise,
    catalog,
    configurable_leaves,
    unsupported_effort,
)
from pheasant_lab.settings import ConfigError, load_config

REPO = Path(__file__).resolve().parents[2]


def _demo(**overrides: str):
    return load_config(
        REPO / "configs/demo.yaml", overrides=overrides, project_root=REPO, env_file=None
    )


def test_every_configurable_field_is_explained_and_no_entry_is_stale() -> None:
    leaves = {leaf.key for leaf in configurable_leaves()}
    assert sorted(leaves - set(DOCS)) == [], "a field with no explanation"
    assert sorted(set(DOCS) - leaves) == [], "an explanation for a field that is gone"
    for key, doc in DOCS.items():
        assert doc.help.strip(), key


def test_every_role_has_a_recommended_model_and_reasoning_level() -> None:
    config = _demo()
    assert set(config.models) <= set(ROLES)
    for role in ROLES:
        advice = ROLE_ADVICE[role]
        assert advice.model and advice.reasoning_effort in REASONING_EFFORTS
        assert unsupported_effort(advice.model, advice.reasoning_effort) is None
    rows = {row["key"]: row for row in catalog(config)["fields"]}
    assert rows["models.researcher.model"]["recommended"] == ROLE_ADVICE["researcher"].model
    assert rows["models.planner.reasoning_effort"]["recommended"] == "high"


def test_current_values_are_what_the_override_would_change() -> None:
    config = _demo(
        **{
            "budget.allocation.planning": "0.2",
            "budget.allocation.collection": "0.3",
            "replay.search_mode": "text",
            "metrics.k": "5",
            "logging.level": "DEBUG",
        }
    )
    rows = {row["key"]: row for row in catalog(config)["fields"]}
    assert rows["budget.allocation.planning"]["current"] == 0.2
    assert rows["replay.search_mode"]["current"] == "text"
    assert rows["metrics.k"]["current"] == 5
    assert rows["logging.level"]["current"] == "DEBUG"
    assert rows["transport"]["current"] == "mock"
    assert rows["replay.search_mode"]["section"] == "search"
    assert rows["experiment.cost_budget_usd"]["section"] == "budget"


def test_budget_and_connection_overrides_reach_the_resolved_config() -> None:
    # These heads used to be dropped on the floor: no file claimed them.
    config = _demo(
        **{
            "budget.allocation.planning": "0.2",
            "budget.allocation.collection": "0.3",
            "source_name": "elsewhere",
            "token_env": "OTHER_TOKEN",
        }
    )
    assert config.budget.allocation.planning == 0.2
    assert config.pheasant.source_name == "elsewhere"
    assert config.pheasant.token_env == "OTHER_TOKEN"


def test_an_override_no_file_owns_is_refused() -> None:
    with pytest.raises(ConfigError, match="names no configuration section"):
        _demo(**{"bugdet.allocation.planning": "0.2"})


def test_new_search_knobs_stay_out_of_the_digest_until_set() -> None:
    plain = _demo()
    assert plain.digest() == _demo(**{"replay.search_mode": "null"}).digest()
    assert plain.digest() != _demo(**{"replay.search_mode": "text"}).digest()
    assert plain.replay.wire_search_mode == "hybrid"


def test_advice_warns_when_hosted_answerers_differ() -> None:
    assert advise(_demo()) == []
    differing = _demo(
        **{
            "models.specialist.provider": "openai",
            "models.specialist.model": "replay:specialist",
        }
    )
    assert any("not compared on one model" in row["message"] for row in advise(differing))


def test_the_recommended_models_are_priced_in_the_shipped_list() -> None:
    config = _demo()
    for model in {advice.model for advice in ROLE_ADVICE.values()}:
        assert model in config.pricing.models, model


def test_a_price_for_a_model_no_role_uses_does_not_move_the_digest() -> None:
    from pheasant_lab.settings import ModelPrice

    config = _demo()
    added = config.model_copy(deep=True)
    added.pricing.models["some-future-model"] = ModelPrice(input=1.0, output=2.0)
    assert added.digest() == config.digest()
    changed = config.model_copy(deep=True)
    changed.pricing.models["replay:researcher"] = ModelPrice(input=1.0, output=1.0)
    assert changed.digest() != config.digest()


def test_a_reasoning_level_the_model_refuses_is_caught_before_spend() -> None:
    assert unsupported_effort("gpt-6.1-sol", "none")
    assert unsupported_effort("gpt-6-luna", "minimal")
    assert unsupported_effort("gpt-6-luna", "none") is None
    assert unsupported_effort("some-other-model", "minimal") is None
    config = _demo(
        **{
            "models.planner.provider": "openai",
            "models.planner.model": "gpt-6.1-sol",
            "models.planner.reasoning_effort": "none",
        }
    )
    assert any("does not accept" in row["message"] for row in advise(config))
    rows = {row["key"]: row for row in catalog(config)["fields"]}
    assert rows["models.planner.reasoning_effort"]["choices"] == [
        None,
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_the_claude_5_5_family_is_priced_and_its_effort_floor_is_checked() -> None:
    config = _demo()
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        assert model in config.pricing.models, model
    # Opus 5.5 cannot turn thinking off; Sonnet and Haiku 5.5 can.
    assert unsupported_effort("claude-opus-5-5", "none")
    assert unsupported_effort("claude-sonnet-5-5", "none") is None
    assert unsupported_effort("claude-haiku-5-5", "none") is None
    for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
        assert unsupported_effort(model, "minimal")
        assert unsupported_effort(model, "xhigh") is None
