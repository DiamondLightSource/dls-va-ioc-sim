"""Simulated Digitel MPC ion pump controllers and the ion pumps on them.

The PV interface mirrors the real EPICS templates in the digitelMpc support
module, so that a client (screen, archiver, another IOC) cannot tell this
simulation apart from a real controller:

    digitelMpc.template          -> mpcRecord           -- the controller
    digitelMpcIonp.template      -> ionPumpRecord       -- one pump on one
    digitelMpcIonpGroup.template -> ionPumpGroupRecord  -- up to 8 of anything

Pumps are attached to a controller on construction, the same way the
digitelMpc builder module takes an MPC object as the first argument to
digitelMpcIonp.  That gives the controller somewhere to send controller wide
actions (:RESET) and somewhere for the pumps to get :MPC_MODEL from.

Which length of pipe a pump is on is not settled here.  A pump has no pressure
of its own - whether it reads a good one is a matter of whether its supply is
running, and how low the one it reads gets is a matter of how much else is
pumping the same gas - and it gets a `volume` to read from the vacuum layout,
which is the one place that says which devices share a length of pipe.  See
vacuum_model.

Records that exist only to glue the StreamDevice protocol onto the hardware
are deliberately not created - nothing outside the IOC reads them and there is
no serial link here for them to drive.  Those are, for the controller,
:COMMSMATCH, and for a pump :ADEL, :ERRSEQ, :ERRSEL, :ERRGET, :RQSTSP1ON,
:RQSTSP2ON, :SETSPS1, :SETSPS2, :SETTEXT_PROC, :COMMSMATCH and the
:INIT:SETSP* startup sequence.  The group skips the same sort of thing:
:CALSTA, :MAXSTA, :MINSTA, :SELERR and the :SEQ* fan-out sequences are
internal to the template and are computed in Python instead.

Anything named SIM:* is a knob for this simulation only and has no equivalent
on real hardware.
"""

import enum
import math
from typing import Any

from softioc import builder

from .device_groups import acceptDemand, deviceGroup, highest, lowest, median
from .vacuum_sim import PRESSURE_MAX, PRESSURE_MIN, clampPressure, droop, jitter

# What a pump's readbacks are seeded with before the first tick.  A pump is
# built before the vacuum layout has told it which length of pipe it is on, so
# it has nothing real to quote until the simulation has stepped once.
NOMINAL_PRESSURE = 1.0e-9

# Model numbers reported by :MODEL, :MODELENUM and a pump's :MPC_MODEL.
MODELS = ("MPC2", "MPCe", "MPCq", "QPC")

# :COMMS.  The real templates start at TIMEOUT and are driven to OK once the
# serial link answers; a simulated link is always up.
COMMS_STATES = ("TIMEOUT", "OK")
COMMS_TIMEOUT, COMMS_OK = range(len(COMMS_STATES))


class SupplyStatus(enum.IntEnum):
    """Values of an ion pump's :STA record."""

    UNKNOWN = 0
    WAITING = 1
    STANDBY = 2
    SAFE_CONN = 3
    RUNNING = 4
    COOL_DOWN = 5
    PUMP_ERROR = 6
    HV_SWITCHED_OFF = 7
    INTERLOCK = 8
    SHUT_DOWN = 9
    CALIBRATION = 10


# String and alarm severity for every :STA value, as digitelMpcIonp.template
# defines them.  Running is the only state that is not an alarm: an ion pump
# that has stopped pumping is something an operator needs to see.
SUPPLY_STATES = (
    ("Unknown", "MAJOR"),
    ("Waiting", "MAJOR"),
    ("Standby", "MAJOR"),
    ("Safe-Conn", "MAJOR"),
    ("Running", "NO_ALARM"),
    ("Cool Down", "MINOR"),
    ("Pump Error", "MAJOR"),
    ("HV Switched Off", "MAJOR"),
    ("Interlock", "MAJOR"),
    ("Shut Down", "MAJOR"),
    ("Calibration", "MAJOR"),
    ("Invalid", "MAJOR"),
    ("Invalid", "MAJOR"),
    ("Invalid", "MAJOR"),
    ("Invalid", "MAJOR"),
    ("Invalid", "MAJOR"),
)


class PumpError(enum.IntEnum):
    """Values of an ion pump's :ERR record."""

    OK = 0
    TOO_MANY_CYCLES = 1
    HIGH_PRESSURE = 2
    HIGH_CURRENT = 3
    HIGH_PRESSURE_2 = 4
    PUMP_POWER = 5
    HIGH_CURRENT_2 = 6
    SHORT_CIRCUIT = 7
    MALFUNCTION = 8
    LOW_VOLTAGE = 9
    ARC_DETECT = 10


# String and alarm severity for every :ERR value.  The duplicated strings and
# the bare numbers at the top end are what the real template publishes.
ERROR_STATES = (
    ("OK", "NO_ALARM"),
    ("Too many cycles", "MINOR"),
    ("High pressure", "MINOR"),
    ("High current", "MINOR"),
    ("High pressure", "MINOR"),
    ("Pump power", "MINOR"),
    ("High current", "MINOR"),
    ("Short circuit", "MINOR"),
    ("Malfunction", "MINOR"),
    ("Low voltage", "MINOR"),
    ("Arc detect", "MINOR"),
    ("11", "MINOR"),
    ("12", "MINOR"),
    ("13", "MINOR"),
    ("14", "MINOR"),
    ("15", "MINOR"),
)

# An ion pump's discharge current is proportional to the pressure it sees and
# to how big the pump is.  This constant puts a 500 l/s pump at 10 uA when it
# is sat at 1e-6 mbar, which is the right ballpark for the real thing.
AMPS_PER_MBAR_PER_LITRE = 1.0 / 50.0


def modelValue(model):
    """Turn a model name into its :MODEL record value."""
    try:
        return MODELS.index(model)
    except ValueError:
        raise ValueError(
            f"Unknown MPC model {model!r}, expected one of "
            + ", ".join(MODELS)
        ) from None


class mpcRecord:
    """A Digitel MPC ion pump controller - digitelMpc.template."""

    def __init__(self, prefix, model="MPCe", version="5.03"):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.model = model
        self.pumpList = []

        self.modelPV = builder.mbbIn("MODEL", *MODELS,
                                     DESC="Model Number",
                                     initial_value=modelValue(model))

        self.modelEnumPV = builder.mbbIn("MODELENUM", *MODELS,
                                         DESC="Enumerated Model",
                                         initial_value=modelValue(model))

        self.versionPV = builder.stringIn("VERSION",
                                          DESC="Firmware Revision",
                                          initial_value=version)

        # A passthrough for arbitrary controller commands on real hardware.
        # There is nothing to pass them through to here.
        self.debugPV = builder.stringIn("DEBUG",
                                        DESC="Generic command",
                                        initial_value="")

        self.commsPV = builder.mbbIn("COMMS", *COMMS_STATES,
                                     DESC="Communication Status",
                                     initial_value=COMMS_OK)

        self.resetPV = builder.Action("RESET",
                                      DESC="Reset Software",
                                      ZNAM="Reset", ONAM="Reset",
                                      on_update=lambda v: self.reset())

    def addPump(self, pump):
        """Register a pump as being driven by this controller."""
        self.pumpList.append(pump)

    def reset(self):
        """:RESET - clear the latched error on every pump on this controller."""
        for pump in self.pumpList:
            pump.reset()


class ionPumpRecord:
    """One ion pump on a Digitel MPC - digitelMpcIonp.template.

    The pressure comes from the volume the pump is on, and the current,
    voltage, status and setpoint relays are derived from it.  What the pump
    contributes back is its pumping speed, which the volume only counts while
    the supply is actually running.

    A pump that has just been started goes through Waiting, and one that is
    running on a volume too high to pump trips on high pressure - which is what
    happens if you try to start ion pumps on a section that has been let up.
    """

    def __init__(self, controller, prefix, pump, size=500,
                 strapping=7000, cal=1.0, sp1on=1.0e-7, sp1off=2.0e-7,
                 sp2on=1.0e-7, sp2off=2.0e-7, startDelay=3.0,
                 tripPressure=1.0e-5, running=True):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.controller = controller
        self.pump = pump
        controller.addPump(self)

        # The length of pipe this pump reads and pumps on, filled in by
        # vacuumLayout.attach.  A pump that never gets one cannot be ticked,
        # which is the layout failing to name it.
        # The volume this sits on, filled in by vacuumLayout.attach.
        # Typed loosely because what guarantees it is set is
        # attachLayout's check, which no type checker can see.
        self.volume: Any = None

        # The real template comes up in Standby and waits to be told to start.
        # A simulation of a *running* front end is more use with the pumps
        # pumping - and with them stopped the spaces would all leak up to their
        # vent pressure while the IOC was left alone.  Pass running=False for a
        # front end that has to be started by hand.
        self.state = SupplyStatus.RUNNING if running else SupplyStatus.STANDBY
        self.stateElapsed = 0.0

        # A pump that comes up running is already reading a pressure and
        # drawing a current off it, rather than sitting at zero until the first
        # tick.  What it actually reads arrives with the first tick, once the
        # layout has said which length of pipe it is on.
        initialPressure = NOMINAL_PRESSURE if running else 0.0
        initialCurrent = (initialPressure * size * cal
                          * AMPS_PER_MBAR_PER_LITRE)

        # --- readbacks -----------------------------------------------------
        self.currentPV = builder.aIn("I", LOPR=0.0, HOPR=10.0, EGU="A", PREC=1,
                                     DESC="Pump Current",
                                     initial_value=initialCurrent)

        self.pressurePV = builder.aIn("P", LOPR=1.0e-12, HOPR=1000.0,
                                      EGU="mbar", PREC=11,
                                      DESC="Pump Pressure",
                                      initial_value=initialPressure)

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-12.0, HOPR=3.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=pressureLog(initialPressure))

        # LOPR/HOPR here look wrong for a supply that runs at kilovolts, but
        # they are what the real template publishes, so keep them.
        self.voltagePV = builder.longIn("V", LOPR=0, HOPR=10, EGU="V",
                                        DESC="Pump Voltage",
                                        initial_value=strapping if running else 0)

        self.strappingPV = builder.longIn("HV", LOPR=5600, HOPR=7000, EGU="V",
                                          DESC="HV Strapping",
                                          initial_value=strapping)

        self.statusPV = builder.mbbIn("STA", *SUPPLY_STATES,
                                      DESC="Supply Status",
                                      UNSV="MAJOR",
                                      initial_value=self.state)

        self.errorPV = builder.mbbIn("ERR", *ERROR_STATES,
                                     DESC="Error Code",
                                     UNSV="MINOR",
                                     initial_value=PumpError.OK)

        self.errorMessagePV = builder.stringIn("ERROR",
                                               DESC="Last error message",
                                               initial_value="")

        self.commsPV = builder.mbbIn("COMMS", *COMMS_STATES,
                                     DESC="Communication Status",
                                     initial_value=COMMS_OK)

        self.modelPV = builder.mbbIn("MPC_MODEL", *MODELS,
                                     DESC="Model Number",
                                     initial_value=modelValue(controller.model))

        # --- pump configuration, demand and readback ------------------------
        self.sizePV = builder.longIn("SIZE", LOPR=0, HOPR=1200, EGU="l/s",
                                     DESC="Pump Size",
                                     initial_value=size)

        # An ao in the template, even though the size it feeds back is a
        # longin, so keep it analogue here too.
        self.setSizePV = builder.aOut("SETSIZE", DRVL=0, DRVH=1200,
                                      LOPR=0, HOPR=1200, EGU="l/s", PREC=0,
                                      DESC="Pump Size",
                                      initial_value=size,
                                      on_update=lambda v: self.sizePV.set(int(v)))

        self.calPV = builder.aIn("CAL", LOPR=0.0, HOPR=9.99, PREC=2,
                                 DESC="Calibration Factor",
                                 initial_value=cal)

        self.setCalPV = builder.aOut("SETCAL", DRVL=0.0, DRVH=9.99,
                                     LOPR=0.0, HOPR=9.99, PREC=2,
                                     DESC="Calibration Factor",
                                     initial_value=cal,
                                     on_update=self.calPV.set)

        # --- HV on/off ------------------------------------------------------
        self.startPV = builder.boolOut("START", ZNAM="Stop", ONAM="Start",
                                       DESC="HV on/off",
                                       always_update=True,
                                       initial_value=1 if running else 0,
                                       on_update=lambda v: self.setStart(v))

        self.startingPV = builder.boolIn("STARTING", ZNAM="", ONAM="Starting",
                                         DESC="Starting Pumps",
                                         initial_value=0)

        # --- setpoint relays ------------------------------------------------
        # Each pump owns two of the controller's four setpoints; which two
        # depends on the pump number, exactly as :SPNUM1 and :SPNUM2 calculate.
        self.setpointNumber1PV = builder.longIn(
            "SPNUM1", DESC="Controller setpoints number Pump 1",
            initial_value=2 if pump > 1 else 1)

        self.setpointNumber2PV = builder.longIn(
            "SPNUM2", DESC="Controller setpoints number Pump 2",
            initial_value=4 if pump > 1 else 3)

        self.setpoint1 = setpointRecords(
            1, sp1on, sp1off, state=running and initialPressure <= sp1on)
        self.setpoint2 = setpointRecords(
            2, sp2on, sp2off, state=running and initialPressure <= sp2on)

        # Front panel text.  Harmless on an MPC2, which has no display.
        self.textPV = builder.stringOut("SETTEXT",
                                        DESC="Text String",
                                        initial_value=prefix)

        # --- simulation only ------------------------------------------------
        # The pressure a pump can hold, how fast it gets there and where it
        # ends up when nothing is pumping all belong to the volume - see
        # vacuum_model - because they are properties of the length of pipe and
        # of every pump on it, not of this one pump.
        self.simStartDelayPV = builder.aOut(
            "SIM:START_DELAY", DRVL=0.0, DRVH=60.0, LOPR=0.0, HOPR=60.0,
            EGU="s", PREC=1,
            DESC="Sim time spent Waiting", initial_value=startDelay)

        self.simTripPressurePV = builder.aOut(
            "SIM:TRIPP", DRVL=PRESSURE_MIN, DRVH=PRESSURE_MAX,
            LOPR=PRESSURE_MIN, HOPR=PRESSURE_MAX, EGU="mbar", PREC=11,
            DESC="Sim high pressure trip level", initial_value=tripPressure)

        self.simTripPV = builder.Action(
            "SIM:TRIP", ZNAM="Trip", ONAM="Trip",
            DESC="Sim force a pump error",
            on_update=lambda v: self.trip(PumpError.MALFUNCTION,
                                          "Fault injected by simulation"))

    # -- commands ------------------------------------------------------------
    #
    # The setters below are what an ion pump group fans a demand out through,
    # and what :START's own callback runs.  They go through acceptDemand rather
    # than .set() so that writing the demand record does not call them again -
    # see device_groups, and return early for the callback that write makes.

    def setStart(self, value):
        """:START - 1 starts the supply, 0 stops it."""
        if not acceptDemand(self.startPV, 1 if value else 0):
            return
        if value:
            self.start()
        else:
            self.stop()

    def setSize(self, value):
        """:SETSIZE - tell the controller how big this pump is."""
        if not acceptDemand(self.setSizePV, value):
            return
        self.sizePV.set(int(value))

    def setCal(self, value):
        """:SETCAL - the calibration factor the current is scaled by."""
        if not acceptDemand(self.setCalPV, value):
            return
        self.calPV.set(value)

    def start(self):
        """Run the supply up, going through Waiting as the MPC does.

        A pump with a latched error will not start until the controller has
        been reset, which is how the real controller behaves.
        """
        if self.errorPV.get() != PumpError.OK:
            return
        if self.state not in (SupplyStatus.WAITING, SupplyStatus.RUNNING):
            self.setState(SupplyStatus.WAITING)

    def stop(self):
        """Switch the supply off and leave the pump in Standby."""
        if self.state != SupplyStatus.STANDBY:
            self.setState(SupplyStatus.STANDBY)

    def trip(self, error, message):
        """Latch an error and drop the supply into Pump Error."""
        self.errorPV.set(error)
        self.errorMessagePV.set(message)
        self.setState(SupplyStatus.PUMP_ERROR)

    def reset(self):
        """Clear a latched error.  Called by the controller's :RESET.

        The pump is left in Standby rather than restarted, so :START has to be
        written again to bring the supply back up.
        """
        self.errorPV.set(PumpError.OK)
        self.errorMessagePV.set("")
        if self.state == SupplyStatus.PUMP_ERROR:
            self.setState(SupplyStatus.STANDBY)

    # -- simulation ----------------------------------------------------------

    def pumpingSpeed(self):
        """The speed this pump is contributing to its space, l/s.

        A supply that is not running is not pumping, whatever size it is; this
        is what makes a space improve as more of its pumps are started.
        """
        return self.sizePV.get() if self.state == SupplyStatus.RUNNING else 0.0

    def tick(self, delta):
        """Advance the simulation by delta seconds and publish the readbacks."""
        self.advanceState(delta)
        self.checkPressure()
        self.publish()

    def advanceState(self, delta):
        self.stateElapsed += delta
        if (self.state == SupplyStatus.WAITING
                and self.stateElapsed >= self.simStartDelayPV.get()):
            self.setState(SupplyStatus.RUNNING)

    def checkPressure(self):
        """Trip the supply if the volume it is on is too high to pump."""
        if (self.state == SupplyStatus.RUNNING
                and self.volume.pressure > self.simTripPressurePV.get()):
            self.trip(PumpError.HIGH_PRESSURE,
                      f"High pressure {self.volume.pressure:.2e} mbar")

    def publish(self):
        pumping = self.state == SupplyStatus.RUNNING

        if pumping:
            pressure = clampPressure(self.volume.pressure * jitter())
            current = (pressure * self.sizePV.get() * self.calPV.get()
                       * AMPS_PER_MBAR_PER_LITRE)
            # The supply sits just under its strapping voltage, never over it.
            voltage = self.strappingPV.get() * droop()
        else:
            # An MPC has no pressure gauge in it: it works the pressure out
            # from the discharge current.  A supply that is off draws no
            # current, so it reads no pressure at all rather than whatever the
            # space it is sat on happens to be at.
            pressure = 0.0
            current = 0.0
            voltage = 0.0

        self.pressurePV.set(pressure)
        self.pressureLogPV.set(pressureLog(pressure))
        self.currentPV.set(current)
        self.voltagePV.set(int(voltage))

        self.setpoint1.update(pressure, pumping)
        self.setpoint2.update(pressure, pumping)

    def setState(self, state):
        self.state = state
        self.stateElapsed = 0.0
        self.statusPV.set(state)
        self.startingPV.set(1 if state == SupplyStatus.WAITING else 0)


class setpointRecords:
    """One of a pump's two pressure setpoints - the :SP<n>* records.

    A setpoint drives a relay in the controller that other equipment (a PLC
    permitting a valve to open, for instance) can read.  The relay comes on
    once the pressure has fallen below the ON level and stays on until it
    rises past the OFF level, so the two levels give it its hysteresis.
    """

    def __init__(self, number, on, off, state=False):
        self.number = number

        self.onPV = builder.aIn(f"SP{number}ON", LOPR=0.0, HOPR=1000.0,
                                EGU="mbar", PREC=1,
                                DESC=f"Setpoint {number} On",
                                initial_value=on)

        self.offPV = builder.aIn(f"SP{number}OFF", LOPR=0.0, HOPR=1000.0,
                                 EGU="mbar", PREC=1,
                                 DESC=f"Setpoint {number} Off",
                                 initial_value=off)

        self.statePV = builder.boolIn(f"SP{number}STATE",
                                      ZNAM="Off", ONAM="On",
                                      DESC=f"Setpoint {number} State",
                                      initial_value=1 if state else 0)

        # The drive limits are the range the controller will accept; the
        # display limits are the wider range the readbacks are scaled over.
        self.setOnPV = builder.aOut(f"SETSP{number}ON",
                                    DRVL=1.0e-10, DRVH=1.0e-4,
                                    LOPR=0.0, HOPR=1000.0,
                                    EGU="mbar", PREC=1,
                                    DESC=f"Setpoint {number} On",
                                    initial_value=on,
                                    on_update=self.onPV.set)

        self.setOffPV = builder.aOut(f"SETSP{number}OFF",
                                     DRVL=1.0e-10, DRVH=1.0e-4,
                                     LOPR=0.0, HOPR=1000.0,
                                     EGU="mbar", PREC=1,
                                     DESC=f"Setpoint {number} Off",
                                     initial_value=off,
                                     on_update=self.offPV.set)

    def setOn(self, value):
        """:SETSP<n>ON - the level the relay comes on below."""
        if not acceptDemand(self.setOnPV, value):
            return
        self.onPV.set(value)

    def setOff(self, value):
        """:SETSP<n>OFF - the level it drops out again above."""
        if not acceptDemand(self.setOffPV, value):
            return
        self.offPV.set(value)

    def update(self, pressure, pumping):
        """Work the relay from the pressure the pump is reading.

        A stopped supply reads no pressure, and a zero would otherwise satisfy
        the ON level and have a pump that is not pumping assert that its
        pressure is good.  Hold the relay off until the supply is back up.
        """
        if not pumping:
            self.statePV.set(0)
        elif pressure <= self.onPV.get():
            self.statePV.set(1)
        elif pressure > self.offPV.get():
            self.statePV.set(0)


class groupSetpointRecords:
    """A group's view of one setpoint - the :SP<n>* records of the group.

    Named and shaped like a pump's own setpointRecords so that a group can be
    a member of another group without anything having to tell the two apart.
    The ON level is the highest any member is set to and the OFF level the
    lowest, which is the pair of `sel` records the template uses; there is no
    state to report, because a group has no relay of its own.
    """

    def __init__(self, number, members):
        self.number = number
        self.members = members

        self.onPV = builder.aIn(f"SP{number}ON", LOPR=0.0, HOPR=1000.0,
                                EGU="mbar", PREC=1,
                                DESC=f"Setpoint {number} On",
                                initial_value=self.setpoints()[0].onPV.get())

        self.offPV = builder.aIn(f"SP{number}OFF", LOPR=0.0, HOPR=1000.0,
                                 EGU="mbar", PREC=1,
                                 DESC=f"Setpoint {number} Off",
                                 initial_value=self.setpoints()[0].offPV.get())

        self.setOnPV = builder.aOut(f"SETSP{number}ON",
                                    DRVL=1.0e-10, DRVH=1.0e-4,
                                    LOPR=0.0, HOPR=1000.0,
                                    EGU="mbar", PREC=1,
                                    DESC=f"Setpoint {number} On",
                                    initial_value=self.onPV.get(),
                                    on_update=self.setOn)

        self.setOffPV = builder.aOut(f"SETSP{number}OFF",
                                     DRVL=1.0e-10, DRVH=1.0e-4,
                                     LOPR=0.0, HOPR=1000.0,
                                     EGU="mbar", PREC=1,
                                     DESC=f"Setpoint {number} Off",
                                     initial_value=self.offPV.get(),
                                     on_update=self.setOff)

    def setpoints(self):
        """This numbered setpoint on each member, whatever the member is."""
        return [getattr(member, f"setpoint{self.number}")
                for member in self.members]

    def setOn(self, value):
        if not acceptDemand(self.setOnPV, value):
            return
        for setpoint in self.setpoints():
            setpoint.setOn(value)

    def setOff(self, value):
        if not acceptDemand(self.setOffPV, value):
            return
        for setpoint in self.setpoints():
            setpoint.setOff(value)

    def publish(self):
        setpoints = self.setpoints()
        self.onPV.set(highest(setpoints, "onPV"))
        self.offPV.set(lowest(setpoints, "offPV"))


class ionPumpGroupRecord(deviceGroup):
    """Up to 8 pumps, or groups of pumps - digitelMpcIonpGroup.template.

    Writing 1 to :START starts every pump underneath, including through any
    member that is itself a group, which is how one write to a straight's
    GIONP starts all sixteen of its supplies.  Everything else is the worst
    reading of the members: the highest pressure, the highest current, the
    furthest on error.

    :STA is the one that is not simply a maximum.  The template's :CALSTA is
    `A>C?A:(B<C?B:C)` over the highest status A, the lowest B and Running C,
    which reads as: anything past Running anywhere wins, otherwise anything
    short of Running anywhere wins, otherwise the group is Running.  So a group
    shows a fault on one pump, or that a pump has yet to come up, in preference
    to claiming the whole group is running.
    """

    def __init__(self, prefix, members, delay=0.0):
        super().__init__(prefix, members, delay=delay)

        self.currentPV = builder.aIn("I", LOPR=0.0, HOPR=10.0, EGU="A", PREC=1,
                                     DESC="Pump Current",
                                     initial_value=highest(self.members,
                                                           "currentPV"))

        self.pressurePV = builder.aIn("P", LOPR=1.0e-12, HOPR=1000.0,
                                      EGU="mbar", PREC=11,
                                      DESC="Pressure",
                                      initial_value=highest(self.members,
                                                            "pressurePV"))

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-12.0, HOPR=3.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=pressureLog(
                                             self.pressurePV.get()))

        self.voltagePV = builder.longIn("V", LOPR=0, HOPR=10, EGU="V",
                                        DESC="Pump Voltage",
                                        initial_value=highest(self.members,
                                                              "voltagePV"))

        self.strappingPV = builder.longIn("HV", LOPR=5600, HOPR=7000, EGU="V",
                                          DESC="HV Strapping",
                                          initial_value=highest(self.members,
                                                                "strappingPV"))

        self.statusPV = builder.mbbIn("STA", *SUPPLY_STATES,
                                      DESC="Supply Status", UNSV="MAJOR",
                                      initial_value=self.status())

        self.errorPV = builder.mbbIn("ERR", *ERROR_STATES,
                                     DESC="Error Code", UNSV="MINOR",
                                     initial_value=highest(self.members,
                                                           "errorPV"))

        self.sizePV = builder.longIn("SIZE", LOPR=0, HOPR=1200, EGU="l/s",
                                     DESC="Pump Size",
                                     initial_value=highest(self.members,
                                                           "sizePV"))

        # A readback in disguise: the template loads this one from :SIZE every
        # second through a closed loop DOL rather than sending anything down,
        # so writing to it does not stick.  Kept that way.
        self.setSizePV = builder.aOut("SETSIZE", DRVL=0, DRVH=1200,
                                      LOPR=0, HOPR=1200, EGU="l/s", PREC=0,
                                      DESC="Pump Size",
                                      initial_value=self.sizePV.get())

        self.calPV = builder.aIn("CAL", LOPR=0.0, HOPR=9.99, PREC=2,
                                 DESC="Calibration Factor",
                                 initial_value=median(self.members, "calPV"))

        self.setCalPV = builder.aOut("SETCAL", DRVL=0.0, DRVH=9.99,
                                     LOPR=0.0, HOPR=9.99, PREC=2,
                                     DESC="Calibration Factor",
                                     initial_value=self.calPV.get(),
                                     on_update=self.setCal)

        self.startPV = builder.boolOut("START", ZNAM="Stop", ONAM="Start",
                                       DESC="HV on/off",
                                       always_update=True,
                                       initial_value=0,
                                       on_update=lambda v: self.setStart(v))

        self.startingPV = builder.boolIn("STARTING", ZNAM="", ONAM="Starting",
                                         DESC="Starting Pumps",
                                         initial_value=0)

        self.setpoint1 = groupSetpointRecords(1, self.members)
        self.setpoint2 = groupSetpointRecords(2, self.members)

    # -- demands fanned out to the members ------------------------------------

    def setStart(self, value):
        """Start or stop every pump underneath this group.

        The template staggers these by :SEQSTART's DLY fields to spread the
        inrush; see device_groups for why the stagger is not slept through here.
        A member that is itself a group passes the demand on down.
        """
        if not acceptDemand(self.startPV, 1 if value else 0):
            return
        self.startingPV.set(1)
        try:
            for member in self.members:
                member.setStart(value)
        finally:
            self.startingPV.set(0)

    def setCal(self, value):
        if not acceptDemand(self.setCalPV, value):
            return
        for member in self.members:
            member.setCal(value)
        self.calPV.set(value)

    # -- readbacks ------------------------------------------------------------

    def status(self):
        """:CALSTA - see the class docstring for how this reads."""
        furthest = highest(self.members, "statusPV")
        least = lowest(self.members, "statusPV")
        if furthest > SupplyStatus.RUNNING:
            return furthest
        if least < SupplyStatus.RUNNING:
            return least
        return SupplyStatus.RUNNING

    def publish(self):
        self.currentPV.set(highest(self.members, "currentPV"))
        self.pressurePV.set(highest(self.members, "pressurePV"))
        self.pressureLogPV.set(pressureLog(self.pressurePV.get()))
        self.voltagePV.set(highest(self.members, "voltagePV"))
        self.strappingPV.set(highest(self.members, "strappingPV"))
        self.statusPV.set(self.status())
        self.errorPV.set(highest(self.members, "errorPV"))
        self.sizePV.set(highest(self.members, "sizePV"))
        self.setSizePV.set(self.sizePV.get())
        self.calPV.set(median(self.members, "calPV"))
        self.startingPV.set(highest(self.members, "startingPV"))
        self.setpoint1.publish()
        self.setpoint2.publish()


def pressureLog(pressure):
    """log10 of a pressure, for :PLOG.

    A stopped pump reads a pressure of zero, which has no logarithm, so peg
    :PLOG at the bottom of the range it is scaled over instead.
    """
    return math.log10(pressure if pressure > 0.0 else PRESSURE_MIN)
