"""The loop every simulation ends by calling.

This was written out twice - inside generate_ioc's FOOTER as a string, and
again as parsedIoc.run - and neither copy was tested, because testing either
meant starting an IOC.  As one function it can be: the tick loop is an ordinary
coroutine, and what runSimulation does to start an IOC is four calls that can
be stood in for.

The behaviour worth pinning is the one that has no symptom.  A coroutine the
dispatcher runs that raises is dropped without a word, which leaves the IOC up
and every readback frozen - so a device that raises has to be logged, and the
devices after it in the list have to keep being stepped.
"""

import asyncio
import contextlib
import inspect
import logging

import pytest

from dls_va_ioc_sim import simulation_loop
from dls_va_ioc_sim.simulation_loop import runSimulation, stepForever


class countingDevice:
    """A device that counts its ticks, and can be made to fail on every one."""

    def __init__(self, prefix, failing=False):
        self.prefix = prefix
        self.failing = failing
        self.ticks = 0

    def tick(self, delta):
        self.ticks += 1
        if self.failing:
            raise ValueError(f"{self.prefix} cannot read its volume")


def stepFor(devices, seconds=0.05, period=0.001):
    """Run the tick loop for a moment, then stop it."""

    async def main():
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(stepForever(devices, period), timeout=seconds)

    asyncio.run(main())


def test_every_device_is_stepped():
    devices = [countingDevice("A"), countingDevice("B")]

    stepFor(devices)

    assert devices[0].ticks > 0
    assert devices[1].ticks == devices[0].ticks


def test_a_device_that_raises_does_not_stop_the_ones_after_it(caplog):
    """The failure this exists for.  A gauge left off every volume raises on
    its first tick; before this, that took its whole controller's readbacks
    down with it and nothing said so."""
    good = countingDevice("SR99A-VA-IONP-01")
    bad = countingDevice("SR99A-VA-GAUGE-01", failing=True)
    devices = [bad, good]

    with caplog.at_level(logging.ERROR):
        stepFor(devices)

    assert good.ticks > 1, "a device after a failing one has to keep going"
    assert bad.ticks > 1, "and the failing one is still tried"


def test_a_failing_device_is_reported_once_and_by_name(caplog):
    """Once: it fails on every tick, and a message a second is a log nobody
    can read.  By name: the whole point is knowing which device stopped."""
    bad = countingDevice("SR99A-VA-GAUGE-01", failing=True)

    with caplog.at_level(logging.ERROR):
        stepFor([bad])

    failures = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(failures) == 1, f"{len(failures)} messages for one device"
    assert failures[0].getMessage() == "Simulation failed for SR99A-VA-GAUGE-01"
    assert failures[0].exc_info, "the traceback is what says why"


def test_each_failing_device_is_reported_separately(caplog):
    bad = [
        countingDevice("SR99A-VA-GAUGE-01", failing=True),
        countingDevice("SR99A-VA-GAUGE-02", failing=True),
    ]

    with caplog.at_level(logging.ERROR):
        stepFor(bad)

    reported = {record.getMessage() for record in caplog.records}
    assert reported == {
        "Simulation failed for SR99A-VA-GAUGE-01",
        "Simulation failed for SR99A-VA-GAUGE-02",
    }


@pytest.fixture
def startedIoc(monkeypatch):
    """runSimulation with everything that would really start an IOC stood in.

    The same four things dbdump stubs, for the same reason: they are what turn
    a recordset into a running IOC, and none of them can be undone.
    """
    calls = {}

    def dispatcher(*arguments, **keywords):
        calls["dispatched"] = arguments
        return None

    monkeypatch.setattr(
        simulation_loop.builder,
        "LoadDatabase",
        lambda *a, **k: calls.setdefault("loaded", True),
    )
    monkeypatch.setattr(
        simulation_loop.asyncio_dispatcher,
        "AsyncioDispatcher",
        lambda *a, **k: dispatcher,
    )
    monkeypatch.setattr(
        simulation_loop.softioc,
        "iocInit",
        lambda *a, **k: calls.setdefault("iocInit", (a, k)),
    )
    monkeypatch.setattr(
        simulation_loop.softioc,
        "interactive_ioc",
        lambda *a, **k: calls.setdefault("interactive", a),
    )
    monkeypatch.setattr(
        simulation_loop.softioc,
        "non_interactive_ioc",
        lambda *a, **k: calls.setdefault("served", True),
    )
    return calls


def test_the_ioc_is_started_without_pvaccess(startedIoc):
    """The one place enable_pva=False is passed now.

    pythonSoftIOC would otherwise start a PVXS server beside the Channel Access
    one, serving every record under its own name on the standard pvAccess port
    - where a client looking for the real machine would find it, whatever
    EPICS_CA_SERVER_PORT says.
    """
    runSimulation([], interactive=False)

    assert startedIoc["loaded"], "the database has to be loaded first"
    _arguments, keywords = startedIoc["iocInit"]
    assert keywords == {"enable_pva": False}


def test_the_ports_are_settled_before_the_ioc_starts(startedIoc, monkeypatch):
    """`dls-va-ioc-sim run` served a cell on whatever Channel Access
    configuration it inherited until this - which on a machine at Diamond is
    the real one."""
    monkeypatch.delenv("EPICS_CA_SERVER_PORT", raising=False)

    runSimulation([], interactive=False)

    import os

    from dls_va_ioc_sim.epics_ports import CA_SERVER_PORT

    assert os.environ["EPICS_CA_SERVER_PORT"] == str(CA_SERVER_PORT)


def test_the_environment_still_wins(startedIoc, monkeypatch):
    """setdefault, so a second simulation runs beside the first."""
    monkeypatch.setenv("EPICS_CA_SERVER_PORT", "6066")

    runSimulation([], interactive=False)

    import os

    assert os.environ["EPICS_CA_SERVER_PORT"] == "6066"


def test_interactive_and_served_are_the_two_ways_it_blocks(startedIoc):
    """Either way it blocks - non-interactive is what a container wants, and
    what stops the process exiting the moment it has come up."""
    runSimulation([], interactive=False)
    assert startedIoc["served"]
    assert "interactive" not in startedIoc


def test_the_interactive_shell_gets_the_namespace_it_was_given(startedIoc):
    """globals() from the instance, which is what puts the volumes in scope."""
    namespace = {"arc": object()}

    runSimulation([], interactive=True, namespace=namespace)

    assert startedIoc["interactive"] == (namespace,)


def test_the_hook_runs_between_loading_the_database_and_starting_the_ioc(
    monkeypatch,
):
    """`start` sizes the callback ring there and nowhere else will do.

    Before LoadDatabase there is no record count to size it from; after iocInit
    the ring already exists, and the overflow it prevents is silent - the
    records are simply not processed, so every readback in a 24 cell ring
    freezes with nothing but a line on stderr to say why.
    """
    order = []
    monkeypatch.setattr(
        simulation_loop.builder,
        "LoadDatabase",
        lambda *a, **k: order.append("LoadDatabase"),
    )
    monkeypatch.setattr(
        simulation_loop.asyncio_dispatcher,
        "AsyncioDispatcher",
        lambda *a, **k: lambda *i, **j: None,
    )
    monkeypatch.setattr(
        simulation_loop.softioc, "iocInit", lambda *a, **k: order.append("iocInit")
    )
    monkeypatch.setattr(
        simulation_loop.softioc,
        "non_interactive_ioc",
        lambda *a, **k: order.append("serving"),
    )

    runSimulation(
        [], interactive=False, beforeIocInit=lambda: order.append("sized the ring")
    )

    assert order == ["LoadDatabase", "sized the ring", "iocInit", "serving"]


def test_the_hook_is_optional(startedIoc):
    """One instance has nothing to do there, and must not have to say so."""
    runSimulation([], interactive=False)

    assert startedIoc["iocInit"]


def test_the_dispatcher_is_given_a_coroutine_function(startedIoc):
    """Not a lambda returning a coroutine: the dispatcher inspects what it is
    given, and the pythonSoftIOC versions differ over what they would pass to
    it.  A no-argument coroutine function behaves the same on all of them."""
    runSimulation([], interactive=False)

    (simulate,) = startedIoc["dispatched"]
    # inspect, not asyncio: asyncio.iscoroutinefunction is deprecated from
    # 3.12 and warnings are errors here, so the asyncio one fails the test it
    # is meant to be making.
    assert inspect.iscoroutinefunction(simulate)
    assert not simulate.__code__.co_argcount

    # ...and it really is the tick loop, not something that returns at once.
    with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
        asyncio.run(asyncio.wait_for(simulate(), timeout=0.02))
