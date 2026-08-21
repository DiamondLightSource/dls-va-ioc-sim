"""What happens to a demand record when a group writes it, and to the echo.

The one thing here that cannot be seen anywhere else in these tests: **on a
running IOC, writing an output record calls that record's own callback back**.
Nothing in an unstarted process does that, so every other test in this suite -
and `interactive_ioc`, and a database diff - is blind to it.  It is also where
the worst bug this framework has had lived: a valve that opened and closed once
a second, for ever, because two demands were in flight at once and each one's
echo kept re-writing the other's record.  See device_groups.acceptDemand.

So there are two halves here.  The first drives acceptDemand over a stub record
that echoes the way softioc does, which is quick and says exactly what the rule
is.  The second starts a real IOC in a subprocess and does to it what the
operator did: open everything from the space, then close one group underneath
while the first demand is still being fanned out.
"""

import os
import subprocess
import sys

import pytest
from softioc import device

from dls_va_ioc_sim.device_groups import acceptDemand


class echoingRecord:
    """An output record as a running IOC has one: .set() calls back.

    softioc dispatches that callback rather than running it inline, so the
    echoes queue up here and are delivered by hand, which is what lets a test
    put two demands in flight and decide the order they land in.
    """

    def __init__(self, value):
        self.value = value
        self.echoes = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.echoes.append(value)

    def deliver(self):
        """Hand back the oldest echo, as the dispatcher would."""
        return self.echoes.pop(0)


@pytest.fixture
def running(monkeypatch):
    """An IOC that is running, as far as acceptDemand can tell.

    softioc's dispatcher is set by iocInit and is what turns a record
    processing into an on_update call - see device_groups.expectsEcho.
    """
    monkeypatch.setattr(device, "dispatcher", object())


def test_a_demand_is_shown_on_its_own_record(running):
    con = echoingRecord(0)

    assert acceptDemand(con, 1) is True
    assert con.get() == 1


def test_the_echo_of_our_own_write_is_not_a_demand(running):
    """The whole point: the callback our own write causes must do nothing."""
    con = echoingRecord(0)
    acceptDemand(con, 1)

    assert acceptDemand(con, con.deliver()) is False


def test_a_demand_already_on_the_record_is_still_a_demand(running):
    """:CON is a command, not a setpoint.

    Opening a valve that is already showing Open has to open it - which is why
    these records are always_update in the first place - so nothing may be
    dropped for agreeing with the record.  Only an echo we are waiting for is.
    """
    con = echoingRecord(1)

    assert acceptDemand(con, 1) is True
    assert con.echoes == [], "there was nothing to write"


def test_two_demands_in_flight_settle(running):
    """The bug, at the smallest size it happens.

    A group is told to close and then to open before the first echo has come
    back.  Each demand writes, so each has an echo behind it, and each echo
    finds the *other* demand's value on the record.  Before acceptDemand knew
    its own writes, that was a loop with no way out: every echo wrote, and
    every write made another echo.
    """
    con = echoingRecord(0)
    demands, dropped = [1, 0], 0

    for _ in range(100):
        if demands:
            value = demands.pop(0)
        elif con.echoes:
            value = con.deliver()
        else:
            break
        if not acceptDemand(con, value):
            dropped += 1
    else:
        pytest.fail(f"the demands never settled - {con.echoes} still in flight")

    assert dropped == 2, "one echo per write, and nothing else"
    assert con.get() == 0, "the demand that arrived last is the one that stands"


def test_the_echoes_are_matched_by_value_and_not_just_counted(running):
    """Which is what keeps two demands the right way round.

    Counting would drop whichever callback arrived first.  When two demands are
    in flight that is the operator's second demand, not an echo at all - so the
    valve would take the older of the two and stay shut.
    """
    con = echoingRecord(0)
    acceptDemand(con, 1)  # closed, and an echo of 1 behind it
    acceptDemand(con, 0)  # opened, and an echo of 0 behind that

    assert acceptDemand(con, con.deliver()) is False
    assert acceptDemand(con, con.deliver()) is False
    assert con.get() == 0


def test_nothing_is_expected_of_a_process_that_is_not_running_an_ioc():
    """No dispatcher means no callback, so there is no echo to wait for.

    A unit test or `dbdump` writes these records and nothing comes back.  An
    echo expected there would never be consumed, and would be sat in front of
    the next real demand - which would then be dropped.
    """
    assert device.dispatcher is None, "no IOC has been started in this process"
    con = echoingRecord(0)

    assert acceptDemand(con, 1) is True
    assert acceptDemand(con, 1) is True, "nothing was expecting to see 1 again"


# The other half: a real IOC, with the demands going through real records.
#
# The dispatcher is held up on purpose for a moment before the two demands are
# written, so that both are waiting when it comes free and the second lands in
# the middle of the first's fan-out however fast the machine is.  On a real one
# that window is wide open anyway - a fan-out sleeps through every valve's
# OPEN_DELAY, several seconds for a cell, and whatever the operator does next
# lands inside it.
TWO_DEMANDS = """
import os
import sys
import time

from softioc import asyncio_dispatcher, builder, softioc

from dls_va_ioc_sim.parsed_ioc import iocFromXml
from dls_va_ioc_sim.vacuum_model import gate, vacuumLayout, vacuumVolume

OPEN, CLOSE = 0, 1

ioc = iocFromXml(sys.argv[1], cell="99")
namespace = {"gate": gate, "vacuumLayout": vacuumLayout, "vacuumVolume": vacuumVolume}
exec(compile(ioc.layoutTemplate(), "<layout>", "exec"), namespace)
ioc.attach(namespace["vacuum"])

space = next(s for s in ioc.spaces if s.prefix == "SR99C-VA-SPACE-01")
group = ioc.groups["SR99MS-VA-GVALV-01"]
valve = group.members[0]

for eachValve in ioc.valves.values():
    eachValve.ilkStaPV.set(2)                 # interlocks OK, so a valve moves
    eachValve.openingDelayPV.set(0.01)        # the test does not need the wait
    eachValve.closingDelayPV.set(0.01)

builder.LoadDatabase()
dispatcher = asyncio_dispatcher.AsyncioDispatcher()
softioc.iocInit(dispatcher, enable_pva=False)

dispatcher(lambda: time.sleep(1.0))           # hold the dispatcher up
time.sleep(0.2)
space.conPV.set(CLOSE)                        # close the whole cell
group.conPV.set(OPEN)                         # and open one group of it

time.sleep(2)

samples = set()
for _ in range(20):
    time.sleep(0.1)
    samples.add((group.conPV.get(), valve.conPV.get(), valve.staPV.get()))

print("states", sorted(samples))
print("settled", len(samples) == 1)
print("final", valve.staVals[valve.staPV.get()])

# _exit, because the dispatcher runs its loop in a thread of its own and there
# is no interactive_ioc here to shut it down; flush first, or a pipe eats all
# of the above.
sys.stdout.flush()
os._exit(0)
"""


def test_a_demand_landing_during_a_fan_out_settles(d2CellXml, tmp_path):
    """The bug as it was reported: everything opened from the space, then one
    group closed underneath it, and a valve left opening and closing once a
    second until the IOC was killed.

    Here it is the other way up - closed from the space, then one group opened
    - because a demand only echoes if it writes, and these records come up
    reading Open.  It is the same two demands in flight.
    """
    script = tmp_path / "two_demands.py"
    script.write_text(TWO_DEMANDS)

    result = subprocess.run(
        [sys.executable, str(script), str(d2CellXml)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "EPICS_CA_SERVER_PORT": "6098",
            "EPICS_CA_REPEATER_PORT": "6099",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "settled True" in result.stdout, (
        "the valve never stopped moving:\n" + result.stdout
    )
    # The group's demand is the one that arrived last, so it stands.
    assert "final Open" in result.stdout, result.stdout
