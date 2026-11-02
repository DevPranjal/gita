import copy
import json
import re

import pytest

from gita.eval.reviewbench import campaign
from gita.eval.reviewbench.common import ProtocolError, digest, write_json
from gita.eval.reviewbench.initial_context import render_context, serialize_context


def packet():
    return {
        "version": 1, "base": "b" * 40, "head": "h" * 40,
        "inventory": [{"path": "module.py", "head": ["100644", "a" * 40]}],
        "budget": {"max_characters": 100000}, "coverage": {"absence_claim": False},
        "raw_patch": {"text": "-before\r\n+after\r\n", "truncated": False},
        "current_source": {"coverage": {"absence_claim": False}, "blocks": [{
            "path": "module.py", "start_line": 1, "end_line": 2,
            "text": "1: def method():\r\n2:     return '``` and `````'\r",
            "entities": [{"name": "method"}]}]},
        "related": {"coverage": {"absence_claim": False}, "sources": [{
            "path": "helper.py", "text": "1: return '\u03bb'\n", "source_kind": "indexed_definition"}]},
    }


def extract_bodies(rendered):
    values = []
    for match in re.finditer(r"(?m)^PRESENTATION ([^\n]+)\n(`{3,})\n", rendered):
        info = json.loads(match.group(1))
        start = match.end()
        displayed = rendered[start:start + info["display_characters"]]
        assert rendered[start + len(displayed):].startswith("\n" + match.group(2) + "\n")
        text = json.loads(displayed) if info["encoding"] == "json_escaped_non_unicode_text" else displayed
        assert len(text) == info["text_characters"]
        values.append(text)
    return values


def test_readable_source_roundtrips_crlf_unicode_and_embedded_fences_without_mutation():
    original = packet()
    before = copy.deepcopy(original)
    rendered = render_context(original)
    assert extract_bodies(rendered) == [
        original["raw_patch"]["text"],
        original["current_source"]["blocks"][0]["text"],
        original["related"]["sources"][0]["text"],
    ]
    assert original == before
    assert json.dumps(original["inventory"], ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")) in rendered
    assert "CURRENT HEAD SOURCE 0" in rendered and "before/after Git lines" in rendered
    assert "1: def method():\r\n2:" in rendered


def test_non_unicode_git_text_is_explicitly_escaped_losslessly():
    original = packet()
    original["raw_patch"]["text"] = "bad byte " + chr(0xDCFF)
    rendered = render_context(original)
    rendered.encode("utf8", "strict")
    assert extract_bodies(rendered)[0] == original["raw_patch"]["text"]
    assert "json_escaped_non_unicode_text" in rendered


def test_readable_budget_refuses_instead_of_clipping():
    value = packet()
    canonical = serialize_context(value)
    assert campaign.present_initial_evidence(canonical, {}) == canonical
    value["budget"]["max_characters"] = 100
    with pytest.raises(ProtocolError, match="exceeds"):
        campaign.present_initial_evidence(serialize_context(value), {"initial_context_format": "readable-v1"})


def test_readable_prompt_attestation_preserves_actual_crlf_bytes(tmp_path):
    value = packet()
    frozen = {"initial_context": True, "initial_context_format": "readable-v1",
              "reviewer_input_diagnostics": True,
              "initial_context_digests": {"task": {"H": digest(value)}}}
    slot = {"task": "task", "arm": "H"}
    write_json(tmp_path / "initial-context" / "task" / "H.json", value)
    directory = tmp_path / "attempts" / "one"
    directory.mkdir(parents=True)
    original, canonical = campaign.initial_packet(directory, slot, frozen)
    presented = campaign.present_initial_evidence(canonical, frozen)
    write_json(directory / "initial_context.json", original)
    (directory / "initial_context.txt").write_bytes(canonical.encode("utf8"))
    (directory / "initial_rendered.txt").write_bytes(presented.encode("utf8"))
    prompt = "Read untrusted evidence\n" + presented + "\nEND INITIAL EVIDENCE"
    (directory / "prompt.txt").write_bytes(prompt.encode("utf8"))
    write_json(directory / "invocation.json", {
        "prompt_digest": digest(prompt), "input_diagnostics": True})
    assert campaign.validate_initial_delivery(directory, slot, frozen)
    (directory / "initial_rendered.txt").write_bytes(presented.replace("return", "altered").encode("utf8"))
    with pytest.raises(ProtocolError, match="Readable"):
        campaign.validate_initial_delivery(directory, slot, frozen)
