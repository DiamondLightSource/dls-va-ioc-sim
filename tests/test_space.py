"""What a space publishes on :STA, and the alarm severity that goes with it.

A screen colours a space's lamp on the severity of :STA rather than on its
value, so the severity is as much of the interface as the number is - and it is
the half of it a database diff cannot see, because it only exists at run time.
This is the net for it.

Building records is global state - a name can be created once per process, as
test_instance.py explains - so each case runs in a subprocess of its own, over
stub groups rather than a whole simulated cell.  Stubs are what makes a case
readable: a space is pure aggregation, so "a valve group reading Closed" is the
entire input, and there is no pumping down to wait for.
"""

import subprocess
import sys
import textwrap

import pytest

# The stub groups a space is built over.  Only the records a space actually
# reads are here; anything it merely writes through to is a method that does
# nothing, which is enough because nothing reads it back.
STUBS = """
    from dls_va_ioc_sim.gauge_records import GaugeStatus
    from dls_va_ioc_sim.ion_pump_records import SupplyStatus
    from dls_va_ioc_sim.vacuum_space_records import spaceRecord

    class reading:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    class interlock:
        def __init__(self):
            self.setpointPV = reading(1.0e-6)
            self.enablePV = reading(1)

        def setSetpoint(self, value):
            pass

        def setEnable(self, value):
            pass

    class group:
        '''Every group a space takes, in one object - it reads them by name.

        A space asks each of its five groups for whichever records that kind of
        group has, so one class covers all five; which of them is passed as
        which decides what the space sees.  The two that differ are the status
        records: :STA means a supply state on an ion pump group and a gauge
        state on an IMG group, and they are different numbers.
        '''

        def __init__(self, pressure, status, valve):
            self.pressurePV = reading(pressure)
            self.pressureLogPV = reading(math.log10(pressure))
            self.statusPV = reading(status)
            self.staPV = reading(valve)
            self.startingPV = reading(0)
            self.openingPV = reading(0)
            self.switchingPV = reading(0)
            self.cchvPV = reading(1)
            self.ctlSetpointPV = reading(1.0e-2)
            self.ctlEnablePV = reading(1)
            self.proSetpointPV = reading(5.0e-4)
            self.valveInterlock = interlock()
            self.mpsInterlock = interlock()
            self.ionPumpInterlock = interlock()

        def setStart(self, value):
            pass

        def setCon(self, value):
            pass

        def setCchv(self, value):
            pass

        def setPro(self, value):
            pass

        def setCtlSetpoint(self, value):
            pass

        def setCtlEnable(self, value):
            pass

    built = []

    def space(pressure=1.0e-9, supply=SupplyStatus.RUNNING, valve=1,
              gauge=GaugeStatus.OK):
        # Numbered, because a record name can only be created once in a
        # process and a case may want to compare two spaces.
        built.append(len(built) + 1)
        gauges = group(pressure, gauge, valve)
        supplies = group(pressure, supply, valve)
        valves = group(pressure, gauge, valve)
        return spaceRecord(f"FE99B-VA-SPACE-{built[-1]:02d}",
                           ionp=supplies, gauge=gauges,
                           img=gauges, pirg=gauges, valve=valves)

    def published(space):
        '''(value, severity) as the record itself would put them on the wire.

        pythonSoftIOC keeps a record's value, severity, alarm status and
        timestamp in the one tuple and offers no public getter for the
        severity, so this is the only way to see what a space has actually
        published without starting an IOC and reading it back over Channel
        Access.  It is checked that way as well - see CLAUDE.md.
        '''
        value, severity, status, _ = space.statusPV._value
        return int(value.value), severity, status
"""


def inSubprocess(body):
    """Run a case against a freshly built space, and give back what it printed."""
    source = textwrap.dedent(
        "import math\nfrom softioc import alarm\n"
        + textwrap.dedent(STUBS)
        + textwrap.dedent(body)
    )
    built = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True
    )

    # Its traceback, rather than the whole source echoed back by check=True.
    assert built.returncode == 0, built.stderr
    return built.stdout.strip()


@pytest.mark.parametrize(
    "state, expected",
    [
        # Nothing wrong: no bits, no alarm, and a screen shows the space green.
        ("", "0 NO_ALARM"),
        # The one this exists for.  A valve that is not open is bit 2, which is
        # over :VALVESTA's HIHI of 3.5, so a space with a shut valve under it
        # is a MAJOR alarm - Closed, Closing, Opening and Fault alike.
        ("valve=3", "4 MAJOR"),
        ("valve=4", "4 MAJOR"),
        ("valve=2", "4 MAJOR"),
        ("valve=0", "4 MAJOR"),
        # A pump that is not running is MINOR, over :IONPSTA's HIGH of 0.5.
        ("supply=SupplyStatus.STANDBY", "2 MINOR"),
        ("supply=SupplyStatus.INTERLOCK", "2 MINOR"),
        # Pressure over 1e-7 is MINOR, from :PSTA's HIGH and from :P's own.
        ("pressure=1.0e-6", "8 MINOR"),
        ("pressure=1.0e-7", "8 MINOR"),
        ("pressure=9.9e-8", "0 NO_ALARM"),
        # A gauge that is not reading sets its bit and raises nothing, which is
        # the template: :IMGSTA's HIGH is above anything it can reach and the
        # MAJOR version of it is commented out.  A space does not go red
        # because a cold cathode is off.
        ("gauge=GaugeStatus.ABOVE_RANGE", "1 NO_ALARM"),
        ("gauge=GaugeStatus.BELOW_RANGE", "0 NO_ALARM"),
        # The worst wins, not the last one worked out: a shut valve on a space
        # that is also above 1e-7 is MAJOR, and both bits are set.
        ("valve=3, pressure=1.0e-6", "12 MAJOR"),
        ("valve=3, supply=SupplyStatus.STANDBY, pressure=1.0e-6", "14 MAJOR"),
    ],
)
def test_a_space_publishes_the_worst_alarm_underneath_it(state, expected):
    names = {0: "NO_ALARM", 1: "MINOR", 2: "MAJOR", 3: "INVALID"}
    printed = inSubprocess(f"""
        value, severity, _ = published(space({state}))
        print(value, {names!r}[severity])
    """)

    assert printed == expected


def test_a_space_in_alarm_says_the_alarm_came_from_below_it():
    """STAT is LINK, which is what an MS link raises on the real IOC - the
    space has no limit of its own for :STA to have tripped."""
    printed = inSubprocess("""
        print(*[published(space(valve=3))[2] == alarm.LINK_ALARM,
                published(space())[2] == alarm.NO_ALARM])
    """)

    assert printed == "True True"


def test_a_space_is_in_alarm_from_the_moment_it_is_built():
    """Not from its first tick.  A cell comes up with every valve Closed, and
    a synoptic that was green for a second before turning red is a synoptic
    nobody trusts."""
    printed = inSubprocess("""
        built = space(valve=3)
        print(published(built))
    """)

    assert printed.startswith("(4, 2,")
