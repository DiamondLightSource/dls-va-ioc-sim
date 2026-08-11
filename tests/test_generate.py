"""Generating the instance and its launcher.

The generator writes a pair of files - the Python instance and the shell
script that starts it - and both are the deliverable, so both are checked
here.  The instance is only parsed, not run: building its records needs the
whole of softioc and is done once, in test_instance.py, in a subprocess.
"""

import ast
import os
import stat

import pytest

from dls_va_ioc_sim.builder_xml import parseXml
from dls_va_ioc_sim.generate_ioc import (
    CA_REPEATER_PORT,
    CA_SERVER_PORT,
    generate,
    instancePaths,
    launcherSource,
)


@pytest.fixture
def declarations(cellXml):
    return parseXml(cellXml, cell="99")


def test_the_instance_is_valid_python(declarations):
    ast.parse(generate(declarations))


def test_generating_twice_gives_the_same_file(declarations):
    """Nothing about the output may depend on dictionary or set ordering: the
    verification this framework relies on is a byte-for-byte diff."""
    assert generate(declarations) == generate(declarations)


def test_the_instance_imports_the_package_not_a_path_hack(declarations):
    source = generate(declarations)

    assert "sys.path.insert" not in source
    assert "from dls_va_ioc_sim.vacuum_model import" in source


def test_the_report_is_written_into_the_instance(declarations):
    """What the parse made of the XML has to be readable without re-running
    anything, so it goes in as a comment."""
    source = generate(declarations)

    for line in declarations.report().splitlines():
        assert f"# {line}".rstrip() in source


def test_the_layout_is_marked_as_a_guess(declarations):
    source = generate(declarations)

    assert "*** EDIT ME ***" in source
    # One volume per domain and no gate joining any of them: the XML cannot
    # say which valve stands between which two lengths of pipe.
    assert "a gate() goes here if a valve joins these two" in source
    for prefix in declarations.valves:
        assert f'#     gate("{prefix}"),' in source


def test_every_valve_is_built_even_though_none_is_a_gate(declarations):
    source = generate(declarations)
    for prefix in declarations.valves:
        assert f'valveRecord("{prefix}")' in source


def test_the_launcher_keeps_the_non_standard_ports():
    launcher = launcherSource("sr99c-va-ioc-01.py")

    assert f"export EPICS_CA_SERVER_PORT={CA_SERVER_PORT}" in launcher
    assert f"export EPICS_CA_REPEATER_PORT={CA_REPEATER_PORT}" in launcher
    assert CA_SERVER_PORT != 5064, "5064 is the real machine's port"


def test_the_launcher_starts_its_own_instance():
    launcher = launcherSource("sr99c-va-ioc-01.py")

    assert launcher.startswith("#!/bin/sh")
    # cd first, so it works however it was invoked.
    assert 'cd "$(dirname "$0")"' in launcher
    assert launcher.rstrip().endswith('exec $PYIOC sr99c-va-ioc-01.py "$@"')


def test_the_pair_is_named_after_the_ioc_in_lower_case(declarations, tmp_path):
    os.chdir(tmp_path)
    instance, launcher = instancePaths(declarations)

    assert os.path.basename(instance) == "sr99c-va-ioc-01.py"
    assert os.path.basename(launcher) == "sr99c-va-ioc-01.sh"


def test_an_explicit_output_names_the_launcher_too(declarations):
    instance, launcher = instancePaths(declarations, "/tmp/scratch.py")

    assert instance == "/tmp/scratch.py"
    assert launcher == "/tmp/scratch.sh"


def test_the_cli_writes_both_files_and_marks_the_launcher_executable(
    cellXml, tmp_path, monkeypatch
):
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99"])
    assert exit.value.code == 0

    instance = tmp_path / "sr99c-va-ioc-01.py"
    launcher = tmp_path / "sr99c-va-ioc-01.sh"
    assert instance.exists() and launcher.exists()
    assert launcher.stat().st_mode & stat.S_IXUSR, "a launcher has to run"
    ast.parse(instance.read_text())


def test_it_will_not_overwrite_a_pair_that_may_have_been_edited(
    cellXml, tmp_path, monkeypatch
):
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["generate", str(cellXml), "99"])

    instance = tmp_path / "sr99c-va-ioc-01.py"
    edited = instance.read_text() + "\n# the layout, thought about\n"
    instance.write_text(edited)

    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99"])
    assert exit.value.code == 1
    assert instance.read_text() == edited, "the hand edit must survive"

    # ...and --force is the way past it.
    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99", "--force"])
    assert exit.value.code == 0
    assert instance.read_text() != edited


def test_half_a_pair_is_never_written(cellXml, tmp_path, monkeypatch):
    """If either file exists, neither is written: the instance is the half
    that gets edited, and a launcher without one is no use."""
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    launcher = tmp_path / "sr99c-va-ioc-01.sh"
    launcher.write_text("# mine\n")

    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99"])
    assert exit.value.code == 1
    assert not (tmp_path / "sr99c-va-ioc-01.py").exists()
    assert launcher.read_text() == "# mine\n"


def test_a_dry_run_writes_nothing(cellXml, tmp_path, monkeypatch):
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99", "--dry-run"])

    assert exit.value.code == 0
    assert list(tmp_path.iterdir()) == []


def test_a_bad_cell_is_a_usage_error(cellXml, tmp_path, monkeypatch):
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "3"])
    assert exit.value.code == 2
