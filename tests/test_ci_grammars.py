"""CI must materialize grammars before indexed tests or timed qualification."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import ModuleType

import pytest
import tree_sitter_language_pack as language_pack
import yaml


@pytest.mark.parametrize("job", ("test", "index-qualification"))
def test_ci_provisions_native_grammars_not_just_download_requests(job, monkeypatch):
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text()
    )
    step = next(step for step in workflow["jobs"][job]["steps"]
                if step.get("name", "").startswith("Provision"))
    command = shlex.split(step["run"])
    assert command[:2] == ["python", "-c"]
    installed = set()
    pack = ModuleType("tree_sitter_language_pack")
    # 1.14.3 download() returns the requested count without creating native files.
    pack.download = lambda names: len(names)
    pack.get_parser = lambda name: installed.add(name)
    pack.downloaded_languages = lambda: sorted(installed)
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", pack)
    exec(command[2], {})
    expected = {"python"} if job == "index-qualification" else {
        "python", "javascript", "typescript", "tsx", "csharp", "cpp",
        "go", "rust", "json", "yaml", "toml", "markdown",
    }
    assert installed == expected


def test_index_evidence_requires_persisted_grammar_in_a_fresh_process(tmp_path):
    cache = tmp_path / "grammars"
    cache.mkdir()
    script = """
import sys
import tree_sitter_language_pack as pack
pack.configure(pack.PackConfig(cache_dir=sys.argv[1]))
from gita.index import evidence
from gita.index.model import IndexContractError, ParseStatus
source = b"def f():\\n    return 1\\n"
before = evidence.parse_fingerprint()
if sys.argv[2] == "cold":
    assert pack.downloaded_languages() == []
    def forbidden(name):
        raise AssertionError("cold evidence must not download a grammar")
    evidence.get_parser = forbidden
    try:
        evidence.parse_evidence(source, b'app.py')
    except IndexContractError as error:
        assert error.code == "parser_unavailable"
    else:
        raise AssertionError("cold evidence unexpectedly parsed")
else:
    assert pack.downloaded_languages() == ['python']
    assert evidence.parse_evidence(source, b'app.py').result.status is ParseStatus.COMPLETE
assert evidence.parse_fingerprint() == before
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    def check(state):
        result = subprocess.run(
            [sys.executable, "-c", script, str(cache), state],
            env=environment, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr + result.stdout

    check("cold")
    originals = list(Path(language_pack.cache_dir()).glob("tree_sitter_python.*"))
    assert originals, "Provision the Python grammar before running indexed tests"
    for original in originals:
        shutil.copyfile(original, cache / original.name)
    check("warm")


def test_qualification_artifacts_use_same_absolute_runner_temp_directory():
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml").read_text()
    )
    steps = workflow["jobs"]["index-qualification"]["steps"]
    test_step = next(step for step in steps if step.get("name") == "Qualification contracts and publication recovery")
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact"))
    path = upload["with"]["path"]
    assert path == "${{ runner.temp }}/index-qualification-tests"
    assert f'--basetemp "{path}"' in test_step["run"]
