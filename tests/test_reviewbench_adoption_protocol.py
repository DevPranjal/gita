"""Declare small workflow experiments without weakening fixed25 confirmation."""
from types import SimpleNamespace

import pytest

from gita.eval.reviewbench import campaign
from gita.eval.reviewbench.common import ProtocolError, digest


@pytest.fixture
def frozen_inputs(monkeypatch):
    monkeypatch.setattr(campaign, "verify", lambda root: {
        "digest": "data", "pin": "a" * 40, "starter_ids": [str(i) for i in range(25)]})
    monkeypatch.setattr(campaign, "policy_digest", lambda: "policy")
    monkeypatch.setattr(campaign, "git", lambda *args: b"b" * 40)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(stdout="1.0.97"))
    monkeypatch.setattr("shutil.which", lambda name: "copilot.exe")
    return [{"task": {"id": "0"}}, {"task": {"id": "6"}}], {"policy_digest": "policy"}


def test_pilot_has_explicit_subset_workflow_and_smaller_caps(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    p = campaign.protocol(tmp_path, scopes, parsers, development_pilot=True,
                          indexed_workflow=True, reviewer_credits=80, judge_credits=60)
    assert len(p["schedule"]) == 4
    assert p["development_pilot"] and p["targeted_judging"]
    assert p["budgets"]["reviewer_credits"] == 80
    assert p["budgets"]["judge_credits"] == 60
    assert p["workflow_guidance"]["H"] != p["workflow_guidance"]["B"]
    assert "irrelevant calls" in p["workflow_guidance"]["H"]


def test_small_subset_cannot_masquerade_as_fixed25(tmp_path, frozen_inputs):
    scopes, parsers = frozen_inputs
    with pytest.raises(ProtocolError, match="25"):
        campaign.protocol(tmp_path, scopes, parsers)
    for ids in (["0", "0"], ["outside"], [str(i) for i in range(4)]):
        with pytest.raises(ProtocolError, match="Pilot"):
            campaign.protocol(tmp_path, [{"task": {"id": i}} for i in ids],
                              parsers, development_pilot=True)


def test_default_prompts_remain_identical_and_workflow_is_explicit():
    task = {"repo": "https://github.com/example/repo", "pr_number": 1,
            "base": "a" * 40, "head": "b" * 40}
    assert campaign.review_prompt(task, arm="B") == campaign.review_prompt(task, arm="H")
    b = campaign.review_prompt(task, arm="B", indexed_workflow=True)
    h = campaign.review_prompt(task, arm="H", indexed_workflow=True)
    assert "entity_context(path,line)" in h and "entity_context(path,line)" not in b
    assert "entity_diff(token,path)" in h and "start with change_context" in h
    assert "All evidence tools are optional" not in h


def test_review_profile_matches_capabilities_without_hiding_raw_fallback():
    from gita.eval.reviewbench.evidence import COMMON_TOOLS, inventory
    ready = {t["name"] for t in inventory("H", workflow=True, history_ready=True)}
    gaps = {t["name"] for t in inventory("H", workflow=True, history_ready=False)}
    assert set(COMMON_TOOLS) <= ready and set(COMMON_TOOLS) <= gaps
    assert {"entity_diff", "history"} <= ready
    assert not {"entity_diff", "history"} & gaps
    assert not {"entities", "source", "state", "events"} & ready
    assert {"entity_context", "change_context", "id", "index_status"} <= gaps


def test_workflow_metadata_is_bound_separately_from_immutable_source_scope():
    scope = {"repo": "source", "history_ready": True}
    slot = {"task": "task", "arm": "H"}
    frozen = {"indexed_workflow": True, "task_tool_schemas": {},
              "scope_digests": {"task": digest(scope)}}
    config = {**scope, "arm": "H", "cursor_secret": "secret", "tool_log": "calls",
              "indexed_workflow": True}
    campaign.validate_broker_config(config, slot, frozen)
    for changed in ({**config, "indexed_workflow": False},
                    {**config, "arm": "B"}, {**config, "history_ready": False}):
        with pytest.raises(ProtocolError):
            campaign.validate_broker_config(changed, slot, frozen)
