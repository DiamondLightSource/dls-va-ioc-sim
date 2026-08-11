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
"""

import math

from softioc import builder

from .gauge_records import CC_CONTROL_MODES, ENABLE_MODES, GaugeStatus
from .ion_pump_records import SupplyStatus

# The pressure :PSTA calls high, and :P's own HIGH.  A constant in the
# template, written into two records rather than passed in as a macro.
HIGH_PRESSURE = 1.0e-7

# The bits of :STA, from the template's comment.
STATUS_GAUGE, STATUS_IONP, STATUS_VALVE, STATUS_PRESSURE = 1, 2, 4, 8

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
                                         HIGH=math.log10(HIGH_PRESSURE),
                                         HSV="MINOR")

        self.pressurePV = builder.aIn("P", LOPR=1.0e-11, HOPR=1000.0,
                                      EGU="mbar", PREC=11,
                                      DESC="Pressure",
                                      initial_value=gauge.pressurePV.get(),
                                      HIGH=HIGH_PRESSURE, HSV="MINOR")

        # --- status ----------------------------------------------------------
        self.statusPV = builder.longIn("STA", LOPR=0, HOPR=15,
                                       DESC="Status",
                                       initial_value=self.status())

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

    def status(self):
        """The four bits of :STA, from the template's four calc records.

        Each bit is one thing being wrong: a gauge that is not reading, a pump
        that is not running, a valve that is not open, and a pressure over
        HIGH_PRESSURE.  A space with nothing wrong reads zero.
        """
        status = 0

        # A gauge is believable while it is OK, and a cold cathode that has
        # bottomed out (Below Range) is still telling the truth.
        gaugeStatus = self.img.statusPV.get()
        if not (gaugeStatus < GaugeStatus.ABOVE_RANGE
                or gaugeStatus == GaugeStatus.BELOW_RANGE):
            status |= STATUS_GAUGE

        if self.ionp.statusPV.get() != SupplyStatus.RUNNING:
            status |= STATUS_IONP

        # 1 is Open, in both a valve's status and a valve group's.
        if self.valve.staPV.get() != 1:
            status |= STATUS_VALVE

        if self.pressurePV.get() >= HIGH_PRESSURE:
            status |= STATUS_PRESSURE

        return status

    def tick(self, delta):
        self.publish()

    def publish(self):
        self.pressureLogPV.set(self.gauge.pressureLogPV.get())
        self.pressurePV.set(self.gauge.pressurePV.get())
        self.statusPV.set(self.status())

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
