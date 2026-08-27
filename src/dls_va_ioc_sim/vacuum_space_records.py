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

**The half of the template that reads a group is built as the template's own
links rather than copied in Python.**  Those link fields are the only place a
real IOC writes down which groups a space covers - there is no record that
names them - so reading them back is how anything outside the IOC works out
what a space is made of.  :IONPSTA.INPA is the ion pump group's :STA here
exactly as it is on the real thing, and the same goes for :IMGSTA, :VALVESTA,
:P, :PLOG, :CTL:SP, :PRO:SP, the three :RL?:SP readbacks and the three
:STARTING/:OPENING/:SWITCHING flags.  A script that reads a cell's spaces off
a real IOC reads this one unchanged.

Demands go the other way and stay in Python: a group fans one out by calling
its members' methods rather than writing their records, and passing that
through a database link would take the demand out of reach of
device_groups.acceptDemand.  So every input link of the template is here, and
no output link is.

Two consequences of :STA being a longIn where the template has an mbbiDirect
(pythonSoftIOC builds those without device support, so they cannot be written
from Python):

- :STA has no INP, so nothing would ever process :CALSTA and the four status
  calcs under it.  :CALSTA is given SCAN "1 second" instead - the rate the
  template's :STA polls it at - and its PP links still process the four.
- The status number is therefore worked out twice, by that chain and in
  Python.  Both read the same records with the same formulas and agree; :STA
  is the one a screen reads and the one this module is answerable for.

**Its alarm severity is aggregated by hand, and is the point of :STA.**
A screen colours a space's lamp on the severity of :STA, not on its value, so
an alarm that does not climb the tree leaves every space on a synoptic green
while a valve underneath it is shut.  The template gets that for nothing: each
of the four status calcs carries its own limit fields, and every link up from
them - into :CALSTA, and :CALSTA into :STA - is tagged MS, so the worst
severity below arrives at the top.  The chain now really is here, but it stops
at :CALSTA: the last link, :CALSTA into :STA, is the one a longIn cannot have.
So a space still works out what each of those records would have raised and
publishes the worst of them on :STA itself.  See statusSeverity().
"""

import math
from typing import Any

from softioc import alarm, builder

from .gauge_records import CC_CONTROL_MODES, ENABLE_MODES, GaugeStatus
from .ion_pump_records import SupplyStatus

# Plain database records - an `ai` that really does read another record, rather
# than the one softioc's builder makes with a Python object behind it.  There
# is no builder wrapper for these because there is nothing to wrap: nothing in
# Python reads or writes them.  epicsdbbuilder publishes one factory per record
# type as the dbd is loaded, with setattr, so a type checker sees an empty
# class - the annotation here is the whole of the suppression for that.
records: Any = builder.records

# The pressure :PSTA calls high, and :P's own HIGH.  A constant in the
# template, written into two records rather than passed in as a macro.
HIGH_PRESSURE = 1.0e-7

# What every record that reads a group scans at.  The template puts this on
# each of them individually and they all agree, so it is one name here.
SCAN_RATE = "1 second"

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

    Each group is linked to by name, `f"{group.prefix}:SUFFIX"` standing in for
    the template's `$(macro):SUFFIX`, so every link field below can be read
    line for line against space.template - and read off a running simulation
    with the same caget as off the real IOC.

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
        self.pressureLogPV = records.ai(
            "PLOG", DESC="log Pressure", SCAN=SCAN_RATE,
            INP=f"{gauge.prefix}:PLOG",
            EGU="log", PREC=3, LOPR=-11.0, HOPR=3.0,
            **PRESSURE_LOG_ALARM.fields())

        self.pressurePV = records.ai(
            "P", DESC="Pressure", SCAN=SCAN_RATE,
            INP=f"{gauge.prefix}:P",
            EGU="mbar", PREC=11, LOPR=1.0e-11, HOPR=1000.0,
            **PRESSURE_ALARM.fields())

        # --- status ----------------------------------------------------------
        # The four calcs are built before the :CALSTA over them, which is the
        # other way round from the template: :CALSTA links to them by object
        # rather than by name, so they have to exist first.  A link to another
        # space record is written that way - it cannot then name one that was
        # never built - and a link to a group is written as the template writes
        # it, `$(macro):SUFFIX`, because that is the thing being reproduced.
        # The .db comes out in alphabetical order either way.
        self.imgStatusPV = records.calc(
            "IMGSTA", DESC="IMGs Status", CALC="(A<2||A=8)?0:1",
            INPA=f"{img.prefix}:STA",
            **IMG_STATUS_ALARM.fields())

        self.ionPumpStatusPV = records.calc(
            "IONPSTA", DESC="Pumps Status", CALC="(A=4)?0:2",
            INPA=f"{ionp.prefix}:STA",
            **IONP_STATUS_ALARM.fields())

        self.valveStatusPV = records.calc(
            "VALVESTA", DESC="Valves Status", CALC="(A=1)?0:4",
            INPA=f"{valve.prefix}:STA MS",
            **VALVE_STATUS_ALARM.fields())

        self.pressureStatusPV = records.calc(
            "PSTA", DESC="Pressure Status", CALC="(A<B)?0:8",
            INPA=builder.MS(self.pressurePV), INPB=HIGH_PRESSURE,
            **PRESSURE_STATUS_ALARM.fields())

        # SCAN is this record's one deviation from the template, and it stands
        # in for the link :STA cannot have: there, :STA scans at a second and
        # processes :CALSTA through its own INP, and :CALSTA processes the four
        # above through these.  A longIn has no INP to do that with, so :CALSTA
        # drives itself at the same rate and the rest of the chain is intact.
        self.calcStatusPV = records.calc(
            "CALSTA", DESC="Calculate Status", SCAN=SCAN_RATE, CALC="A|B|C|D",
            INPA=builder.MS(builder.PP(self.imgStatusPV)),
            INPB=builder.MS(builder.PP(self.ionPumpStatusPV)),
            INPC=builder.MS(builder.PP(self.valveStatusPV)),
            INPD=builder.MS(builder.PP(self.pressureStatusPV)))

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

        self.startingPV = records.bi(
            "STARTING", DESC="Starting Pumps", SCAN=SCAN_RATE,
            INP=f"{ionp.prefix}:STARTING",
            ZNAM="", ONAM="Starting")

        # --- control the valves ----------------------------------------------
        self.conPV = builder.mbbOut("CON",
                                    "Open",
                                    "Close",
                                    "Reset",
                                    DESC="Control Valves",
                                    always_update=True,
                                    on_update=lambda v: valve.setCon(v))

        self.openingPV = records.bi(
            "OPENING", DESC="Opening Valves", SCAN=SCAN_RATE,
            INP=f"{valve.prefix}:OPENING",
            ZNAM="", ONAM="Opening")

        # --- control the cold cathodes ---------------------------------------
        self.cchvPV = builder.boolOut("CCHV", ZNAM="Off", ONAM="On",
                                      DESC="Cold Cathode Enable",
                                      always_update=True,
                                      initial_value=img.cchvPV.get(),
                                      on_update=lambda v: img.setCchv(v))

        self.switchingPV = records.bi(
            "SWITCHING", DESC="Switching", SCAN=SCAN_RATE,
            INP=f"{img.prefix}:SWITCHING",
            ZNAM="", ONAM="Switching")

        # --- the cold cathode enable setpoint, on the Piranis ----------------
        self.ctlSetpointPV = records.ai(
            "CTL:SP", DESC="IMG Enable", SCAN=SCAN_RATE,
            INP=f"{pirg.prefix}:CTL:SP MS",
            EGU="mbar", PREC=1, LOPR=2.7e-3, HOPR=9.5e-1)

        self.ctlSetSetpointPV = builder.aOut("CTL:SETSP",
                                             DRVL=2.7e-3, DRVH=9.5e-1,
                                             LOPR=2.7e-3, HOPR=9.5e-1,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Enable",
                                             initial_value=pirg.ctlSetpointPV.get(),
                                             on_update=pirg.setCtlSetpoint)

        self.ctlEnablePV = builder.mbbOut("CTL:ENABLE", *CC_CONTROL_MODES,
                                          DESC="Enable Control Setpoint",
                                          always_update=True,
                                          initial_value=pirg.ctlEnablePV.get(),
                                          on_update=pirg.setCtlEnable)

        # --- the IMG overpressure protection setpoint ------------------------
        self.proSetpointPV = records.ai(
            "PRO:SP", DESC="IMG Overpressure", SCAN=SCAN_RATE,
            INP=f"{img.prefix}:PRO:SP MS",
            EGU="mbar", PREC=1, LOPR=1.3e-5, HOPR=1.0e-2)

        self.proSetSetpointPV = builder.aOut("PRO:SETSP",
                                             DRVL=1.3e-5, DRVH=1.0e-2,
                                             LOPR=1.3e-5, HOPR=1.0e-2,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Overpressure",
                                             initial_value=img.proSetpointPV.get(),
                                             on_update=img.setPro)

        # --- the three interlock relays --------------------------------------
        # Named for what they do rather than which relay they are, the way the
        # template's descriptions do: the space is the level an operator works
        # at and RLY/RLA/RLB mean nothing there.  Which is why each one is told
        # where to read from: :RLB is the Pirani group's :RLY, and only the
        # template says so.
        self.valveInterlock = spaceRelayRecords(
            "RLY", f"{img.prefix}:RLY:SP", img.valveInterlock, "Valve I/L",
            lopr=2.7e-10, hopr=1.2e-2)

        self.mpsInterlock = spaceRelayRecords(
            "RLA", f"{img.prefix}:RLA:SP", img.mpsInterlock, "MPS Interlock",
            lopr=2.7e-10, hopr=1.2e-2)

        self.ionPumpInterlock = spaceRelayRecords(
            "RLB", f"{pirg.prefix}:RLY:SP", pirg.ionPumpInterlock,
            "Ion Pump I/L",
            lopr=2.7e-3, hopr=1.2e2)

    # -- readbacks -------------------------------------------------------------

    def statusCalcs(self):
        """The template's four status calcs, each as (value, severity).

        Each is one thing being wrong: a gauge that is not reading, a pump that
        is not running, a valve that is not open, and a pressure over
        HIGH_PRESSURE.  The value is the bit that thing contributes to :STA and
        the severity is what its calc record's own limit fields would raise.

        The four records themselves are built and really do process - they are
        where the group names are written down.  This is worked out again here
        because the one link that would carry their answer up, :CALSTA into
        :STA, is the one a longIn cannot have; each method below names the
        record it is standing in for, and the two must not drift.
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

        The pressure comes from the gauge group rather than off :P, which is a
        link now and has no value to read in Python.  It is the same number:
        :P's INP is that record.
        """
        pressure = self.gauge.pressurePV.get()
        value = 0 if pressure < HIGH_PRESSURE else STATUS_PRESSURE
        return value, max(PRESSURE_STATUS_ALARM.of(value),
                          PRESSURE_ALARM.of(pressure))

    def status(self):
        """:CALSTA - `A|B|C|D`, the four bits in one number.

        A space with nothing wrong reads zero.  The record of that name works
        the same number out from the same four calcs; this is the copy :STA is
        written from, because a longIn has no link to read :CALSTA with.
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
        """:STA, and nothing else.

        Everything else a space reads is a link to the group it reads it from,
        scanning at a second, so EPICS does it and there is nothing to copy.
        :STA is the one record left, because a longIn is written from Python
        and because it is the one carrying the severity a screen colours on.
        """
        self.publishStatus()


class spaceRelayRecords:
    """One interlock setpoint of a space - its :RLY:, :RLA: or :RLB: records.

    Three records that pass a setpoint and its enable down to the matching
    interlock of a gauge group, and read the group's answer back.  The space
    is one level further out than the group, so there is nothing to combine
    here: it has exactly one group to talk to.

    `source` is the group record :?:SP reads, in full, because the space's name
    for an interlock is not the group's: :RLB is the Pirani group's :RLY, and
    only the template says which is which.
    """

    def __init__(self, name, source, interlock, desc, lopr, hopr):
        self.interlock = interlock

        self.setpointPV = records.ai(
            f"{name}:SP", DESC=desc, SCAN=SCAN_RATE,
            INP=f"{source} MS",
            EGU="mbar", PREC=1, LOPR=lopr, HOPR=hopr)

        self.setSetpointPV = builder.aOut(f"{name}:SETSP",
                                          DRVL=lopr, DRVH=hopr,
                                          LOPR=lopr, HOPR=hopr,
                                          EGU="mbar", PREC=1,
                                          DESC=desc,
                                          initial_value=interlock.setpointPV.get(),
                                          on_update=interlock.setSetpoint)

        self.enablePV = builder.mbbOut(f"{name}:ENABLE", *ENABLE_MODES,
                                       DESC="Enable Relay Setpoint",
                                       always_update=True,
                                       initial_value=interlock.enablePV.get(),
                                       on_update=interlock.setEnable)
