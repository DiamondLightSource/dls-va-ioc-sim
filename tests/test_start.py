"""Running more than one instance as a single IOC.

Everything here runs in a subprocess, for the reason test_instance.py gives:
softioc's record builder is global state, a name can only be created once in a
process, and LoadDatabase consumes the recordset. Merging instances is exactly
an exercise of that global state, so it cannot be done twice in one process.

The check that matters is the record count. Two instances loaded together must
build the sum of what each builds alone - no record dropped by a name
collision, none built twice - which is the same "diff the database" argument
the rest of the framework rests on, counted rather than diffed because the two
databases here are of different cells.
"""

import subprocess
import sys
import textwrap

import pytest


def python(script, cwd):
    """Run a script in a subprocess of its own, with the package importable."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def cli(*arguments, cwd, check=True):
    return subprocess.run(
        [sys.executable, "-m", "dls_va_ioc_sim", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def twoCells(cellXml, tmp_path):
    """The same XML generated as two different cells, so the names differ."""
    cli("generate", str(cellXml), "98", "-o", "sr98c.py", cwd=tmp_path)
    cli("generate", str(cellXml), "99", "-o", "sr99c.py", cwd=tmp_path)
    return "sr98c.py", "sr99c.py"


def recordsIn(names, cwd):
    """Load these instances in one process and count what they built."""
    result = python(
        f"""
        from dls_va_ioc_sim.start_ioc import loadInstances, tickList
        import epicsdbbuilder
        instances = loadInstances({list(names)!r})
        print(epicsdbbuilder.CountRecords(), len(tickList(instances)),
              len(instances))
        """,
        cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    counts = result.stdout.split()[-3:]
    return tuple(int(count) for count in counts)


def test_two_instances_build_the_sum_of_their_databases(twoCells, tmp_path):
    first, second = twoCells

    oneRecords, oneDevices, _ = recordsIn([first], cwd=tmp_path)
    otherRecords, otherDevices, _ = recordsIn([second], cwd=tmp_path)
    bothRecords, bothDevices, loaded = recordsIn([first, second], cwd=tmp_path)

    assert bothRecords == oneRecords + otherRecords
    assert bothDevices == oneDevices + otherDevices
    assert loaded == 2


def test_one_instance_is_just_that_instance(twoCells, tmp_path):
    """`start` on a single file has to be the same thing as running the file,
    or the k8s deployment and the desk are running different code."""
    first, _ = twoCells

    records, _, _ = recordsIn([first], cwd=tmp_path)
    dumped = cli("dbdump", first, "out.db", cwd=tmp_path)

    assert f"{records} records" in dumped.stdout


def test_the_same_instance_twice_is_refused(twoCells, tmp_path):
    """Two of one cell would build the same record names, and the error from
    the builder is about a duplicate record rather than about the mistake."""
    first, _ = twoCells

    result = python(
        f"""
        from dls_va_ioc_sim.start_ioc import loadInstances
        loadInstances([{first!r}, {first!r}])
        """,
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "was given twice" in result.stderr


def test_a_file_that_is_not_an_instance_says_so(tmp_path):
    (tmp_path / "notaninstance.py").write_text("answer = 42\n")

    result = python(
        """
        from dls_va_ioc_sim.start_ioc import loadInstances
        loadInstances(["notaninstance.py"])
        """,
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert "defines no simulatedDevices" in result.stderr


def test_the_callback_queue_is_sized_from_the_database(tmp_path):
    """The cbLow ring defaults to 2000 entries and every readback update
    queues onto it. One cell never comes near that; a ring of cells overflows
    it in one pass, and the overflow is silently dropped record processing."""
    result = python(
        """
        from dls_va_ioc_sim.start_ioc import callbackQueueSize
        print(callbackQueueSize(2080), callbackQueueSize(49920))
        """,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    oneCell, wholeRing = (int(size) for size in result.stdout.split()[-2:])

    assert oneCell >= 20000
    assert wholeRing > 4 * 49920 - 1, "four entries a record, at least"
    assert wholeRing > 2000, "2000 is the EPICS default this exists to raise"
