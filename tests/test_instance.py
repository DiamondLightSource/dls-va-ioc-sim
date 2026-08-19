"""A generated instance really does build its records.

Everything here runs in a subprocess.  softioc's record builder is global
state - a name can only be created once in a process, and LoadDatabase
consumes the recordset - so an in-process test could only ever build one
instance, and would poison anything that ran after it.

The check that matters is the one this framework has always relied on: build
the database, and diff it.  For a change to how an instance is *assembled* the
diff has to be empty, which is a far stronger statement than "it starts".
There is deliberately no assertion on the number of records - that only
tripwires whichever instance happened to be built, and goes stale the moment
anyone adds a device.
"""

import os
import subprocess
import sys

import pytest


def cli(*arguments, cwd):
    return subprocess.run(
        [sys.executable, "-m", "dls_va_ioc_sim", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def generated(cellXml, tmp_path):
    """One generated instance, in a directory of its own."""
    cli("generate", str(cellXml), "99", cwd=tmp_path)
    return tmp_path / "sr99c-va-ioc-01.py"


def test_the_instance_builds_its_records(generated, tmp_path):
    database = tmp_path / "out.db"
    result = cli("dbdump", generated.name, str(database), cwd=tmp_path)

    assert database.exists()
    assert "records ->" in result.stdout
    text = database.read_text()
    # The three layers all reached the database.
    assert "SR99A-VA-IONP-01" in text
    assert "SR99C-VA-GIONP-01" in text
    assert "SR99C-VA-SPACE-01" in text


def test_the_rack_and_the_plc_reach_the_database(generated, tmp_path):
    """None of these is a vacuum device and none of them ticks, so the only
    thing that can go wrong is that they are not built at all - which is
    exactly what a screen full of white boxes is."""
    database = tmp_path / "rack.db"
    cli("dbdump", generated.name, str(database), cwd=tmp_path)
    text = database.read_text()

    for name in (
        'record(bi, "SR99C-VA-VLVCC-01:PLCHEALTHY")',
        'record(bi, "SR99C-VA-FANC-03:STA")',
        'record(bi, "SR99C-VA-PSU-02:STA")',
        'record(ai, "SR99C-VA-PSU-01:2:VOLTAGE")',
        'record(ao, "SR99A-VA-RGA-02:PWRC")',
    ):
        assert name in text


def test_a_diamond_two_cell_builds_its_own_rack_and_its_rga_heads(d2CellXml, tmp_path):
    """The other rack file, end to end.  D2 moves the power cycle lines onto
    RGA-01 in a straight and two girder domains, and declares its heads as
    rgamv2 rather than rga - which is the only fixture that builds an
    rgaRecord at all."""
    cli("generate", str(d2CellXml), "99", cwd=tmp_path)
    database = tmp_path / "d2.db"
    cli("dbdump", "sr99c-va-ioc-01-d2.py", str(database), cwd=tmp_path)
    text = database.read_text()

    for name in (
        'record(ao, "SR99S-VA-RGA-01:PWRC")',
        'record(ao, "SR99SM-VA-RGA-01:PWRC")',
        'record(ao, "SR99MS-VA-RGA-01:PWRC")',
        'record(longin, "SR99MS-VA-RGA-01:STA")',
        'record(bi, "SR99C-VA-FANC-01:STA")',
        'record(ai, "SR99C-VA-PSU-02:2:VOLTAGE")',
    ):
        assert name in text

    # The pre-D2 heads are not built for a D2 cell.
    assert "SR99A-VA-RGA-02:PWRC" not in text


def test_the_database_is_the_same_every_time(generated, tmp_path):
    """The check the framework is verified with: assembly must be
    deterministic, byte for byte, or a before/after diff means nothing."""
    first, second = tmp_path / "first.db", tmp_path / "second.db"
    cli("dbdump", generated.name, str(first), cwd=tmp_path)
    cli("dbdump", generated.name, str(second), cwd=tmp_path)

    # The first line carries the time the file was written.
    assert first.read_text().splitlines()[1:] == second.read_text().splitlines()[1:]


def test_a_regenerated_instance_builds_the_same_database(cellXml, generated, tmp_path):
    """Regenerating from the same XML must not move a record."""
    before = tmp_path / "before.db"
    cli("dbdump", generated.name, str(before), cwd=tmp_path)

    cli("generate", str(cellXml), "99", "--force", cwd=tmp_path)
    after = tmp_path / "after.db"
    cli("dbdump", generated.name, str(after), cwd=tmp_path)

    assert before.read_text().splitlines()[1:] == after.read_text().splitlines()[1:]


def test_a_layout_leaving_a_device_off_a_volume_is_refused(generated):
    """The bug that bit twice before attachLayout checked for it: a gauge on
    no volume keeps volume = None, raises on its first tick, the dispatcher
    swallows it, and the rest of that controller stops updating in silence."""
    source = generated.read_text()
    marker = "    gauges=["
    assert marker in source, "the generated layout should place gauges"

    # Drop the first gauge from the volume it was placed on.
    head, _, tail = source.partition(marker)
    firstGauge, _, rest = tail.partition(", ")
    generated.write_text(head + marker + rest)

    result = subprocess.run(
        [sys.executable, "-m", "dls_va_ioc_sim", "dbdump", generated.name, "out.db"],
        cwd=generated.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "on no volume" in result.stderr
    assert firstGauge.strip('"') in result.stderr


def test_the_instance_is_the_only_file_written_and_it_can_be_run(generated):
    """There is no launcher beside it any more: the shebang and the PEP 723
    header are what start it, so the file has to be executable and has to name
    the package it needs."""
    assert [path.name for path in generated.parent.iterdir()] == [generated.name]

    assert os.access(generated, os.X_OK), "an instance has to run"
    lines = generated.read_text().splitlines()
    assert lines[0] == "#!/usr/bin/env -S uv run --script"
    assert lines[1] == "# /// script"
    assert any(line.startswith('# dependencies = ["dls-va-ioc-sim') for line in lines)


# The other way in: parsed_ioc builds the same devices at start up rather than
# writing a file, which is what `dls-va-ioc-sim run` is.  It goes through the
# same parseXml as the generator, so the two cannot drift - this is the check
# that it still builds.
PARSED = """
import sys
from dls_va_ioc_sim.parsed_ioc import iocFromXml
from dls_va_ioc_sim.vacuum_model import gate, vacuumLayout, vacuumVolume

ioc = iocFromXml(sys.argv[1], cell="99")
namespace = {"gate": gate, "vacuumLayout": vacuumLayout,
             "vacuumVolume": vacuumVolume}
exec(compile(ioc.layoutTemplate(), "<layout>", "exec"), namespace)
ioc.attach(namespace["vacuum"])

devices = ioc.tickList()
print("devices", len(devices))
print("layout first", devices[0] is ioc.layout)
print("every device has a prefix", all(hasattr(d, "prefix") for d in devices))
# One tick, to prove every device can actually read its volume.
for device in devices:
    device.tick(1.0)
print("ticked")
"""


def test_the_start_up_parse_builds_and_ticks(cellXml, tmp_path):
    script = tmp_path / "parsed.py"
    script.write_text(PARSED)

    result = subprocess.run(
        [sys.executable, str(script), str(cellXml)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "layout first True" in result.stdout
    assert "every device has a prefix True" in result.stdout
    assert "ticked" in result.stdout


def test_the_start_up_parse_refuses_a_layout_with_a_device_left_off(cellXml, tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("""
import sys
from dls_va_ioc_sim.parsed_ioc import iocFromXml
from dls_va_ioc_sim.vacuum_model import vacuumLayout, vacuumVolume

ioc = iocFromXml(sys.argv[1], cell="99")
# A layout with one empty volume: every pump and gauge is left off it.
ioc.attach(vacuumLayout(vacuumVolume("everything", litres=10.0, gasLoad=1e-7)))
""")

    result = subprocess.run(
        [sys.executable, str(script), str(cellXml)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "on no volume" in result.stderr
