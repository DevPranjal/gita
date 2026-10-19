"""Exercise actual SQLite name predicates against canonical stored name tuples."""
import sqlite3

import pytest

from gita.index.query import IndexSession
from gita.index.store import _dump


def matches(token, rows, *, locator=False):
    predicate, parameters = IndexSession._name_filter(token, locator=locator)
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(
            "CREATE TABLE definition(id INTEGER, names_json TEXT, terminal_name TEXT);"
            "CREATE TABLE file_state(id INTEGER, path BLOB);")
        for index, (path, names) in enumerate(rows):
            connection.execute("INSERT INTO definition VALUES(?,?,?)",
                               (index, _dump(tuple(names)), names[-1]))
            connection.execute("INSERT INTO file_state VALUES(?,?)", (index, path))
        return [row[0] for row in connection.execute(
            "SELECT definition.id FROM definition JOIN file_state USING(id) WHERE " + predicate
            + " ORDER BY definition.id", parameters)]
    finally:
        connection.close()


@pytest.mark.parametrize("token", [
    "API.Services.DeviceMonitoringService.PingDeviceAsync",
    "API.Services::DeviceMonitoringService::PingDeviceAsync",
    "PingDeviceAsync",
])
def test_compound_namespace_names_are_not_incorrectly_split(token):
    assert matches(token, [(b"src/API/Service.cs",
                            ("API.Services", "DeviceMonitoringService", "PingDeviceAsync"))]) == [0]


def test_same_display_name_in_different_structural_scopes_remains_ambiguous():
    rows = [(b"a.cs", ("API.Services", "Service", "Run")),
            (b"b.cs", ("API", "Services", "Service", "Run"))]
    assert matches("API.Services.Service.Run", rows) == [0, 1]


def test_directory_locator_obeys_path_component_boundaries():
    rows = [(b"src/API/AppDbService.cs", ("API.Services", "AppDbService")),
            (b"src/API/Services/AppDbService.cs", ("API.Services", "AppDbService")),
            (b"src/API2/AppDbService.cs", ("API.Services", "AppDbService")),
            (b"src/API-extra/AppDbService.cs", ("API.Services", "AppDbService"))]
    assert matches("src/API::AppDbService", rows, locator=True) == [0, 1]
    assert matches("src/API/::AppDbService", rows, locator=True) == [0, 1]
    assert matches("src/API/Services/AppDbService.cs::AppDbService", rows, locator=True) == [1]


def test_root_namespace_is_not_silently_reinterpreted_as_a_directory():
    rows = [(b"API/Other.cs", ("Other", "Service")), (b"elsewhere.cs", ("API", "Service"))]
    assert matches("API::Service", rows, locator=True) == []
    assert matches("API::Service", rows) == [1]
    assert matches("API/::Service", rows, locator=True) == [0]


def test_explicit_directory_selector_never_selects_a_file_with_the_same_name():
    assert matches("root/::Service", [(b"root", ("Service",))], locator=True) == []


@pytest.mark.parametrize("names,token", [
    (("naive", "naïve"), "naive.naïve"),
    (("Component", "['a.b', 'x'].map(fn)"), "Component::['a.b', 'x'].map(fn)"),
    (("odd", "x' OR 1=1--"), "absent' OR 1=1--"),
])
def test_literal_names_remain_parameterized_and_lossless(names, token):
    expected = [] if token.startswith("absent") else [0]
    assert matches(token, [(b"file", names)]) == expected
