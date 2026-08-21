"""Generating the instance.

One file is the whole deliverable: the instance is its own launcher, so the
header that makes it runnable - the shebang, the PEP 723 dependency, the
Channel Access ports - is as much a part of the output as the devices are, and
is checked here.  The instance is only parsed, not run: building its records
needs the whole of softioc and is done once, in test_instance.py, in a
subprocess.
"""

import ast
import builtins
import os
import re
import stat
import tomllib

import pytest

from dls_va_ioc_sim.builder_xml import parseXml
from dls_va_ioc_sim.epics_ports import (
    CA_REPEATER_PORT,
    CA_SERVER_PORT,
    PVA_BROADCAST_PORT,
    PVA_SERVER_PORT,
)
from dls_va_ioc_sim.generate_ioc import (
    generate,
    instancePath,
    requirement,
)


@pytest.fixture
def declarations(cellXml):
    return parseXml(cellXml, cell="99")


def scriptMetadata(source):
    """The instance's PEP 723 block, parsed the way uv parses it.

    Which is as TOML, after stripping the leading "# " off every line - so a
    line of prose in there that is not *also* a TOML comment is a syntax error
    that takes the whole header out and stops the file running at all.  Reading
    it back through a real TOML parser is what catches that; eyeballing the
    lines for a leading "#" does not, because they all have one.
    """
    lines = source.splitlines()
    start = lines.index("# /// script")
    end = lines.index("# ///", start)
    body = "\n".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in lines[start + 1 : end]
    )
    return tomllib.loads(body)


def test_the_instance_is_valid_python(declarations):
    ast.parse(generate(declarations))


def test_generating_twice_gives_the_same_file(declarations):
    """Nothing about the output may depend on dictionary or set ordering: the
    verification this framework relies on is a byte-for-byte diff."""
    assert generate(declarations) == generate(declarations)


def calledButNotDefined(source):
    """Names the instance calls at module level without importing or making.

    This is the failure the import block used to be able to have: it was a
    hand-kept list beside a generator that emitted class names from a
    different hand-kept list, so a template added to one and not the other
    wrote a file that died on the import - and only a subprocess test that
    happened to build that device would notice.  Both lists come off the
    registry now, and this is what says so.
    """
    tree = ast.parse(source)
    imported, assigned, called = set(), set(), set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                assigned.update(
                    name.id for name in ast.walk(target) if isinstance(name, ast.Name)
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)

    return called - imported - assigned - set(dir(builtins))


def test_the_instance_imports_every_class_it_calls(declarations):
    assert calledButNotDefined(generate(declarations)) == set()


def test_a_diamond_two_instance_imports_every_class_it_calls(d2CellXml):
    """The other rack file, which is the template most recently added and so
    the one most likely to have been left out of the import block."""
    assert calledButNotDefined(generate(parseXml(d2CellXml, cell="99"))) == set()


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
    for valve in declarations.valves:
        assert f'#     gate("{valve.prefix}"),' in source


def test_every_valve_is_built_even_though_none_is_a_gate(declarations):
    source = generate(declarations)
    for valve in declarations.valves:
        assert f'valveRecord("{valve.prefix}")' in source


def test_the_rack_and_the_plc_are_written_into_the_instance(declarations):
    """A cell overview shows the rack beside the vacuum, so the fans, the 24 V
    supplies and the PLC's own health are built even though none of them is a
    vacuum device and none of them ticks."""
    source = generate(declarations)

    assert 'plcInfoRecord("SR99C-VA-VLVCC-01")' in source
    assert 'commonRecord("SR99C")' in source


def test_a_diamond_two_cell_gets_the_diamond_two_rack(d2CellXml):
    """The instance says which of SR-VA's two rack files the cell was built
    from, because the class it calls is the one that names the right heads.

    No macros: the cell quotes none of `$(straight1)`, `$(girder1)` or
    `$(girder2)`, so the file says only what the XML said and the three heads
    come from `commonD2Record`'s own defaults."""
    source = generate(parseXml(d2CellXml, cell="99"))

    assert 'commonD2Record("SR99C")\n' in source


def test_a_macro_the_cell_quotes_is_written_into_the_instance(d2CellXml, tmp_path):
    """`commonD2.xml` names its RGA power cycle devices through macros, and a
    cell may name one itself. The generated file has to carry it, or the
    instance would quietly build a power cycle line for the wrong head."""
    quoted = tmp_path / "quoted.xml"
    quoted.write_text(
        d2CellXml.read_text().replace(
            '<SR-VA.commonD2 dom="SR03C"',
            '<SR-VA.commonD2 dom="SR03C" straight1="X" girder2="ZZ"',
        )
    )

    source = generate(parseXml(quoted, cell="99"))

    assert 'commonD2Record("SR99C", girder2="ZZ", straight1="X")' in source
    # ...and the one it did not quote is left to the default, not written out
    # as an empty string.
    assert "girder1=" not in source


def test_the_instance_keeps_the_non_standard_ports(declarations):
    """The ports are in the instance, not in a wrapper around it: there is
    then no way to start one on the real machine's port by forgetting a
    step."""
    source = generate(declarations)

    assert (
        f'os.environ.setdefault("EPICS_CA_SERVER_PORT", "{CA_SERVER_PORT}")' in source
    )
    assert (
        f'os.environ.setdefault("EPICS_CA_REPEATER_PORT", "{CA_REPEATER_PORT}")'
        in source
    )
    assert CA_SERVER_PORT != 5064, "5064 is the real machine's port"


def test_the_instance_serves_no_pvaccess(declarations):
    """pythonSoftIOC starts a PVXS server beside the Channel Access one, which
    offers the same records under the same names and has never heard of
    EPICS_CA_SERVER_PORT. An instance safely on 6064 still answered
    `pvget SR06A-VA-IONP-01:P` on the standard pvAccess port until this.

    The instance no longer calls iocInit itself - runSimulation does, and it is
    the one place enable_pva=False is passed now, which is checked in
    test_simulation_loop.  What the instance has to do is go through it."""
    source = generate(declarations)

    assert "runSimulation(" in source
    assert "softioc.iocInit" not in source, (
        "an instance that starts its own IOC is a second place for "
        "enable_pva to be forgotten"
    )


def test_the_pvaccess_ports_are_moved_too(declarations):
    """The second layer, for if pvAccess is ever switched back on: it should
    come up somewhere harmless rather than on the real machine's port."""
    source = generate(declarations)

    assert (
        f'os.environ.setdefault("EPICS_PVAS_SERVER_PORT", "{PVA_SERVER_PORT}")'
        in source
    )
    assert (
        f'os.environ.setdefault("EPICS_PVAS_BROADCAST_PORT", "{PVA_BROADCAST_PORT}")'
        in source
    )
    assert PVA_SERVER_PORT != 5075, "5075 is the real machine's pvAccess port"


def test_nothing_in_the_instance_was_eaten_by_the_format_template(declarations):
    """HEADER and FOOTER are %-format templates, so a literal % in one has to
    be doubled.  The one that was not took the tick loop's own message out:

        logging.exception("Simulation failed for {'volume': 'sr99s'}", ...)

    because a bare %s takes the whole mapping.  That line lives in
    simulation_loop now rather than in a template, but the hazard belongs to
    the templates, so this checks the substitution left nothing behind - a
    stray conversion shows up as a dict repr or an unfilled %(name)s."""
    source = generate(declarations)

    assert not re.search(r"%\(\w+\)s", source), "an unsubstituted conversion"
    assert "{'volume'" not in source, "a bare %s took the whole mapping"
    for line in source.splitlines():
        # A doubled %% in a template comes out as a single % in the file, and
        # every one left should be inside a string or a comment, not a
        # conversion that got away.
        assert "%s" not in line or line.lstrip().startswith("#"), line


def test_the_ports_are_set_before_anything_that_imports_softioc(declarations):
    """Whatever libca reads them at, the ports have to be in the environment
    before anything of EPICS starts up, and this is the order that was tested.

    The instance imports no softioc name of its own any more, so the thing to
    be after is the *first* import of this package - every one of which reaches
    softioc through the record classes."""
    source = generate(declarations)

    assert source.index("EPICS_CA_SERVER_PORT") < source.index("from dls_va_ioc_sim")

    above = source[: source.index("EPICS_CA_SERVER_PORT")].splitlines()
    imported = [
        line for line in above if line.startswith("import ") or line.startswith("from ")
    ]
    assert imported == ["import os"], (
        "nothing but os may be imported above the ports: " + repr(imported)
    )


def test_the_instance_is_its_own_launcher(declarations):
    source = generate(declarations, instance="sr99c-va-ioc-01.py")

    # uv reads the header, builds the environment and runs the file: the
    # shebang is what makes ./sr99c-va-ioc-01.py all there is to it.
    assert source.startswith("#!/usr/bin/env -S uv run --script\n")
    assert "# /// script\n" in source
    assert f'# dependencies = ["{requirement()}"]\n' in source
    assert "dls-va-ioc-sim" in requirement()
    # And it names itself, so the file says how to run the file.
    assert "./sr99c-va-ioc-01.py" in source


def test_an_unreleased_generator_does_not_pin_a_version_that_is_on_no_index(
    monkeypatch,
):
    """setuptools_scm gives a checkout something like 0.1.dev0+d20260811.
    Pinning that writes an instance that cannot resolve and so cannot start."""
    import dls_va_ioc_sim.generate_ioc as generator

    monkeypatch.setattr(generator, "__version__", "0.1.dev0+d20260811")
    assert generator.requirement() == "dls-va-ioc-sim"

    monkeypatch.setattr(generator, "__version__", "1.0.0b1")
    assert generator.requirement() == "dls-va-ioc-sim==1.0.0b1"


def test_an_unreleased_generator_points_the_instance_at_its_own_checkout(
    declarations, monkeypatch
):
    """Unpinned is not enough on its own: uv would fetch the last *release*,
    which is exactly the copy without the unreleased work in it.  An instance
    generated from a checkout named a class that release had never heard of and
    died on the import - so the header has to say where the package came
    from."""
    import dls_va_ioc_sim.generate_ioc as generator

    monkeypatch.setattr(generator, "__version__", "0.1.dev0+d20260811")
    root = generator.projectRoot()
    assert root is not None, "the tests run from a checkout"

    source = generator.generate(declarations)

    assert "# [tool.uv.sources]\n" in source
    assert f'# dls-va-ioc-sim = {{ path = "{root}", editable = true }}\n' in source

    metadata = scriptMetadata(source)
    assert metadata["tool"]["uv"]["sources"]["dls-va-ioc-sim"] == {
        "path": str(root),
        "editable": True,
    }
    assert metadata["dependencies"] == ["dls-va-ioc-sim"]


def test_a_released_generator_leaves_no_local_path_in_the_instance(
    declarations, monkeypatch
):
    """The other side of it: an instance to deploy has to run on a machine
    that has never seen the one it was generated on."""
    import dls_va_ioc_sim.generate_ioc as generator

    monkeypatch.setattr(generator, "__version__", "1.0.0b1")
    source = generator.generate(declarations)

    assert "[tool.uv.sources]" not in source
    assert '# dependencies = ["dls-va-ioc-sim==1.0.0b1"]\n' in source

    metadata = scriptMetadata(source)
    assert metadata["dependencies"] == ["dls-va-ioc-sim==1.0.0b1"]
    assert "tool" not in metadata
    # The metadata block is the part that decides what gets installed, so it
    # is the part that has to be free of this machine.  (The path of the XML
    # it was generated from is quoted further down, and is only a comment.)
    assert str(generator.projectRoot()) not in str(metadata)


def test_the_instance_is_named_after_the_ioc_in_lower_case(declarations, tmp_path):
    os.chdir(tmp_path)

    assert os.path.basename(instancePath(declarations)) == "sr99c-va-ioc-01.py"


def test_an_explicit_output_is_taken_as_it_is(declarations):
    assert instancePath(declarations, "/tmp/scratch.py") == "/tmp/scratch.py"


def test_the_cli_writes_the_instance_and_marks_it_executable(
    cellXml, tmp_path, monkeypatch
):
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99"])
    assert exit.value.code == 0

    instance = tmp_path / "sr99c-va-ioc-01.py"
    assert instance.exists()
    assert instance.stat().st_mode & stat.S_IXUSR, "an instance has to run"
    ast.parse(instance.read_text())
    # It is written under the name it was saved as, whatever -o said.
    assert "./sr99c-va-ioc-01.py" in instance.read_text()


def test_it_will_not_overwrite_an_instance_that_may_have_been_edited(
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


def test_an_instance_written_elsewhere_names_itself(cellXml, tmp_path, monkeypatch):
    """The header tells you how to run the file, so it has to be the name the
    file was actually saved as and not the one the IOC would have had."""
    from dls_va_ioc_sim.__main__ import main

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit:
        main(["generate", str(cellXml), "99", "-o", "scratch.py"])
    assert exit.value.code == 0

    source = (tmp_path / "scratch.py").read_text()
    assert "./scratch.py" in source
    assert "./sr99c-va-ioc-01.py" not in source


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
