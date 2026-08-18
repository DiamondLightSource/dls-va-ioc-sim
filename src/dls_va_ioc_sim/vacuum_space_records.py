"""A vacuum space - the device that stands for a section of machine.

    space.template -> spaceRecord

A space is the thing an operator is shown and the thing a screen has a button
for: one line per section of machine, with a pressure on it, a status lamp, and
controls that start its pumps, open its valves and strike its cold cathodes.

It owns none of that.  Every record here either reads a group or writes to one,
exactly as the template's links do - :P is the gauge group's pressure, :STA is
four bits worked out from the IMG, ion pump and valve groups, and :START is a
write straight through to the ion pump group.  Which devices a space covers is
therefore entirely a matter of which groups it is given:

    spaceRecord("FE99B-VA-SPACE-01",
                ionp=ionPumpGroup, gauge=gaugeGroup, img=imgGroup,
                pirg=pirgGroup, valve=valveGroup)

That is how vacuumSpace.spaceTemplate is written in a builder XML, and it is
why a space says nothing about the beam pipe.  A space can cover devices either
side of a valve, or one gauge on a vessel with three; the groups decide.  What
lengths of pipe there are, and which valve stands between which two of them, is
the vacuum layout's business - see vacuum_model.

Nothing here is simulated.  A space is pure aggregation, so it needs no state
of its own and no SIM: records: give it groups of simulated devices and it
reports what they are doing.

**Its alarm severity is aggregated the same way, and is the point of :STA.**
A screen colours a space's lamp on the severity of :STA, not on its value, so
an alarm that does not climb the tree leaves every space on a synoptic green
while a valve underneath it is shut.  The template gets that for nothing: each
of the four status calcs carries its own limit fields, and every link up from
them - into :CALSTA, and :CALSTA into :STA - is tagged MS, so the worst
severity below arrives at the top.  There are no links here, so a space works
out what each of those records would have raised and publishes the worst of
them on :STA itself.  See statusSeverity().
"""

import math

from softioc import alarm, builder

from .gauge_records import CC_CONTROL_MODES, ENABLE_MODES, GaugeStatus
from .ion_pump_records import SupplyStatus

# The pressure :PSTA calls high, and :P's own HIGH.  A constant in the
# template, written into two records rather than passed in as a macro.
HIGH_PRESSURE = 1.0e-7

# The bits of :STA, from the template's comment.
STATUS_GAUGE, STATUS_IONP, STATUS_VALVE, STATUS_PRESSURE = 1, 2, 4, 8

# 1 is Open, in both a valve's status and a valve group's.
VALVE_OPEN = 1


class alarmLimit:
    """A record's alarm limit and the severity it raises - HIGH/HSV or HIHI/HHSV.

    The limit is needed in two places and must be the same number in both: as
    fields on the record, so that EPICS raises the alarm, and in Python, so
    that a space can carry that severity up to :STA the way the template's MS
    links do.  Keeping the pair in one object is what stops the two drifting -
    an alarm this simulation publishes further up has to be one the record
    itself would really have raised.

    `of()` is EPICS's own test: checkAlarms() is `value >= limit`, and takes
    HIHI before HIGH, which is why each of these carries only the one limit the
    template gives it.
    """

    SEVERITIES = {
        "NO_ALARM": alarm.NO_ALARM,
        "MINOR": alarm.MINOR_ALARM,
        "MAJOR": alarm.MAJOR_ALARM,
    }
    SEVERITY_FIELDS = {"HIGH": "HSV", "HIHI": "HHSV"}

    def __init__(self, limit, severity, field="HIGH"):
        self.limit = limit
        self.severity = severity
        self.field = field

    def fields(self):
        """The two record fields, to be passed straight to a builder call."""
        return {self.field: self.limit,
                self.SEVERITY_FIELDS[self.field]: self.severity}

    def of(self, value):
        """The severity this limit gives a value - what the record would raise."""
        if value >= self.limit:
            return self.SEVERITIES[self.severity]
        return alarm.NO_ALARM


# :P and :PLOG, which alarm at the same pressure in two different units.
PRESSURE_ALARM = alarmLimit(HIGH_PRESSURE, "MINOR")
PRESSURE_LOG_ALARM = alarmLimit(math.log10(HIGH_PRESSURE), "MINOR")

# The four status calcs, each with the limit and severity the template gives
# it.  A calc reads either 0 or its own bit, so every limit here sits between
# the two - written as the template writes them rather than reduced to "is the
# bit set", so that the two files can be read side by side.
#
# :IMGSTA is the odd one, and deliberately: its HIGH is above the only value it
# can ever reach, so a gauge that is not reading sets bit 0 of :STA and raises
# nothing.  The template has the MAJOR version of it sat commented out
# underneath, and its INPA is the one link of the four with no MS on it, so
# nothing the gauge itself raises gets up this way either.  A dead gauge is
# visible in the number and does not colour the space.
IMG_STATUS_ALARM = alarmLimit(1.5, "MINOR")
IONP_STATUS_ALARM = alarmLimit(0.5, "MINOR")
VALVE_STATUS_ALARM = alarmLimit(3.5, "MAJOR", field="HIHI")
PRESSURE_STATUS_ALARM = alarmLimit(7.5, "MINOR")

# :STA is an mbbiDirect in the template, a 16 bit word read a bit at a time.
# pythonSoftIOC only builds those without device support, so they cannot be
# written from Python; a longIn carries the same number and only loses the
# .B0-.B3 field access.  Deliberate - see the module docstring conventions.


class spaceRecord:
    """One vacuum space - space.template.

    The five groups are the template's five macros.  Any of them may be a
    group of groups, which is how a space covers a whole straight: give it the
    top of a tree of groups and every device underneath is reached.

    Some of what the template links to does not exist on the MKS 937B gauges
    this simulation is built from, only on the 937A the template was written
    for.  Those records are mapped onto the 937B's numbered relays by the
    gauge groups, so :RLY: is the valve interlock, :RLA: the MPS interlock and
    :RLB: the ion pump interlock, as the template's own descriptions say.
    :PRO:ENABLE is the one record with nowhere to go, and is not created.
    """

    def __init__(self, prefix, ionp, gauge, img, pirg, valve):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.ionp = ionp
        self.gauge = gauge
        self.img = img
        self.pirg = pirg
        self.valve = valve

        # --- pressure, straight off the gauge group --------------------------
        self.pressureLogPV = builder.aIn("PLOG", LOPR=-11.0, HOPR=3.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=gauge.pressureLogPV.get(),
                                         **PRESSURE_LOG_ALARM.fields())

        self.pressurePV = builder.aIn("P", LOPR=1.0e-11, HOPR=1000.0,
                                      EGU="mbar", PREC=11,
                                      DESC="Pressure",
                                      initial_value=gauge.pressurePV.get(),
                                      **PRESSURE_ALARM.fields())

        # --- status ----------------------------------------------------------
        self.statusPV = builder.longIn("STA", LOPR=0, HOPR=15,
                                       DESC="Status",
                                       initial_value=self.status())

        # initial_value carries no severity, and a space that comes up with its
        # valves shut is in alarm from the start rather than from the first
        # tick a second later.
        self.publishStatus()

        # --- control the pumps -----------------------------------------------
        self.startPV = builder.boolOut("START", ZNAM="Stop", ONAM="Start",
                                       DESC="Control Pumps",
                                       always_update=True,
                                       initial_value=0,
                                       on_update=lambda v: ionp.setStart(v))

        self.startingPV = builder.boolIn("STARTING", ZNAM="", ONAM="Starting",
                                         DESC="Starting Pumps",
                                         initial_value=0)

        # --- control the valves ----------------------------------------------
        self.conPV = builder.mbbOut("CON",
                                    "Open",
                                    "Close",
                                    "Reset",
                                    DESC="Control Valves",
                                    always_update=True,
                                    on_update=lambda v: valve.setCon(v))

        self.openingPV = builder.boolIn("OPENING", ZNAM="", ONAM="Opening",
                                        DESC="Opening Valves",
                                        initial_value=0)

        # --- control the cold cathodes ---------------------------------------
        self.cchvPV = builder.boolOut("CCHV", ZNAM="Off", ONAM="On",
                                      DESC="Cold Cathode Enable",
                                      always_update=True,
                                      initial_value=img.cchvPV.get(),
                                      on_update=lambda v: img.setCchv(v))

        self.switchingPV = builder.boolIn("SWITCHING", ZNAM="", ONAM="Switching",
                                          DESC="Switching",
                                          initial_value=0)

        # --- the cold cathode enable setpoint, on the Piranis ----------------
        self.ctlSetpointPV = builder.aIn("CTL:SP", LOPR=2.7e-3, HOPR=9.5e-1,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Enable",
                                         initial_value=pirg.ctlSetpointPV.get())

        self.ctlSetSetpointPV = builder.aOut("CTL:SETSP",
                                             DRVL=2.7e-3, DRVH=9.5e-1,
                                             LOPR=2.7e-3, HOPR=9.5e-1,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Enable",
                                             initial_value=self.ctlSetpointPV.get(),
                                             on_update=pirg.setCtlSetpoint)

        self.ctlEnablePV = builder.mbbOut("CTL:ENABLE", *CC_CONTROL_MODES,
                                          DESC="Enable Control Setpoint",
                                          always_update=True,
                                          initial_value=pirg.ctlEnablePV.get(),
                                          on_update=pirg.setCtlEnable)

        # --- the IMG overpressure protection setpoint ------------------------
        self.proSetpointPV = builder.aIn("PRO:SP", LOPR=1.3e-5, HOPR=1.0e-2,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Overpressure",
                                         initial_value=img.proSetpointPV.get())

        self.proSetSetpointPV = builder.aOut("PRO:SETSP",
                                             DRVL=1.3e-5, DRVH=1.0e-2,
                                             LOPR=1.3e-5, HOPR=1.0e-2,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Overpressure",
                                             initial_value=self.proSetpointPV.get(),
                                             on_update=img.setPro)

        # --- the three interlock relays --------------------------------------
        # Named for what they do rather than which relay they are, the way the
        # template's descriptions do: the space is the level an operator works
        # at and RLY/RLA/RLB mean nothing there.
        self.valveInterlock = spaceRelayRecords(
            "RLY", img.valveInterlock, "Valve I/L",
            lopr=2.7e-10, hopr=1.2e-2)

        self.mpsInterlock = spaceRelayRecords(
            "RLA", img.mpsInterlock, "MPS Interlock",
            lopr=2.7e-10, hopr=1.2e-2)

        self.ionPumpInterlock = spaceRelayRecords(
            "RLB", pirg.ionPumpInterlock, "Ion Pump I/L",
            lopr=2.7e-3, hopr=1.2e2)

    # -- readbacks -------------------------------------------------------------

    def statusCalcs(self):
        """The template's four status calcs, each as (value, severity).

        Each is one thing being wrong: a gauge that is not reading, a pump that
        is not running, a valve that is not open, and a pressure over
        HIGH_PRESSURE.  The value is the bit that thing contributes to :STA and
        the severity is what its calc record's own limit fields would raise -
        which is the half of the template that has to be worked out here,
        because there are no links to carry it.
        """
        return (self.imgStatus(), self.ionPumpStatus(),
                self.valveStatus(), self.pressureStatus())

    def imgStatus(self):
        """:IMGSTA - `(A<2||A=8)?0:1`, a gauge that is not reading.

        A gauge is believable while it is OK, and a cold cathode that has
        bottomed out (Below Range) is still telling the truth.  This calc never
        alarms; see IMG_STATUS_ALARM for why that is the template and not an
        omission.
        """
        gaugeStatus = self.img.statusPV.get()
        believable = (gaugeStatus < GaugeStatus.ABOVE_RANGE
                      or gaugeStatus == GaugeStatus.BELOW_RANGE)
        value = 0 if believable else STATUS_GAUGE
        return value, IMG_STATUS_ALARM.of(value)

    def ionPumpStatus(self):
        """:IONPSTA - `(A=4)?0:2`, a pump that is not running.  MINOR."""
        running = self.ionp.statusPV.get() == SupplyStatus.RUNNING
        value = 0 if running else STATUS_IONP
        return value, IONP_STATUS_ALARM.of(value)

    def valveStatus(self):
        """:VALVESTA - `(A=1)?0:4`, a valve that is not open.  MAJOR.

        The one an operator sees most: 4 is over this calc's HIHI, so any valve
        under this space that is not Open puts the space itself into a MAJOR
        alarm - a valve closing turns its space red, and every space is red
        until the valves have been reset and opened.

        INPA carries MS as well, so a valve group's own severity would arrive
        here too.  Nothing is lost by not having it: the worst the real group's
        :STA raises is the MAJOR on Fault, which is exactly what the limit
        above already gives every state that is not Open.
        """
        value = 0 if self.valve.staPV.get() == VALVE_OPEN else STATUS_VALVE
        return value, VALVE_STATUS_ALARM.of(value)

    def pressureStatus(self):
        """:PSTA - `(A<B)?0:8`, the space over HIGH_PRESSURE.  MINOR.

        INPA is `:P MS`, and :P's own HIGH is the same pressure, so the two say
        the same thing - but both are worked out, because they are two records'
        limits and only one of them is this calc's.
        """
        pressure = self.pressurePV.get()
        value = 0 if pressure < HIGH_PRESSURE else STATUS_PRESSURE
        return value, max(PRESSURE_STATUS_ALARM.of(value),
                          PRESSURE_ALARM.of(pressure))

    def status(self):
        """:CALSTA - `A|B|C|D`, the four bits in one number.

        A space with nothing wrong reads zero.  Neither :CALSTA nor the four
        calcs under it are published: they are internal to the template, like a
        group's :CALSTA and :SELSTA, and are computed here instead.
        """
        value = 0
        for bit, _ in self.statusCalcs():
            value |= bit
        return value

    def statusSeverity(self):
        """The severity :STA publishes - the worst of the four calcs'.

        `max` is EPICS's own rule: severities are ordered NO_ALARM < MINOR <
        MAJOR < INVALID, and recGblSetSevr, which is what an MS link ends up
        calling, only ever raises.  So a space sat at 1e-6 with a valve shut is
        MAJOR, not MINOR, exactly as it would be with the links in.
        """
        return max(severity for _, severity in self.statusCalcs())

    def publishStatus(self):
        """:STA - the number, and the severity a screen colours the space on.

        Both in the one write, because they are one event: a space going into
        alarm and its :STA changing are the same thing happening.

        The alarm *status* that goes with the severity is LINK, which is what
        recGblSetSevr is given when a severity arrives over an MS link - so a
        space in alarm here reads STAT LINK, SEVR MAJOR exactly as the real one
        does, rather than carrying a limit alarm of its own that :STA has not
        got.
        """
        severity = self.statusSeverity()
        self.statusPV.set(self.status(), severity=severity,
                          alarm=alarm.LINK_ALARM if severity else alarm.NO_ALARM)

    def tick(self, delta):
        self.publish()

    def publish(self):
        # The pressures carry no severity up from the gauge group, because the
        # template's links to them are the two with no MS on them: a space
        # shows its own HIGH and nothing of a gauge that has stopped reading.
        # :PSTA is where a gauge group's trouble is meant to reach a space, and
        # :IMGSTA is where it is deliberately dropped - see imgStatus().
        self.pressureLogPV.set(self.gauge.pressureLogPV.get())
        self.pressurePV.set(self.gauge.pressurePV.get())
        self.publishStatus()

        self.startingPV.set(self.ionp.startingPV.get())
        self.openingPV.set(self.valve.openingPV.get())
        self.switchingPV.set(self.img.switchingPV.get())

        self.ctlSetpointPV.set(self.pirg.ctlSetpointPV.get())
        self.proSetpointPV.set(self.img.proSetpointPV.get())

        self.valveInterlock.publish()
        self.mpsInterlock.publish()
        self.ionPumpInterlock.publish()


class spaceRelayRecords:
    """One interlock setpoint of a space - its :RLY:, :RLA: or :RLB: records.

    Three records that pass a setpoint and its enable down to the matching
    interlock of a gauge group, and read the group's answer back.  The space
    is one level further out than the group, so there is nothing to combine
    here: it has exactly one group to talk to.
    """

    def __init__(self, name, interlock, desc, lopr, hopr):
        self.interlock = interlock

        self.setpointPV = builder.aIn(f"{name}:SP", LOPR=lopr, HOPR=hopr,
                                      EGU="mbar", PREC=1,
                                      DESC=desc,
                                      initial_value=interlock.setpointPV.get())

        self.setSetpointPV = builder.aOut(f"{name}:SETSP",
                                          DRVL=lopr, DRVH=hopr,
                                          LOPR=lopr, HOPR=hopr,
                                          EGU="mbar", PREC=1,
                                          DESC=desc,
                                          initial_value=self.setpointPV.get(),
                                          on_update=interlock.setSetpoint)

        self.enablePV = builder.mbbOut(f"{name}:ENABLE", *ENABLE_MODES,
                                       DESC="Enable Relay Setpoint",
                                       always_update=True,
                                       initial_value=interlock.enablePV.get(),
                                       on_update=interlock.setEnable)

    def publish(self):
        self.setpointPV.set(self.interlock.setpointPV.get())
