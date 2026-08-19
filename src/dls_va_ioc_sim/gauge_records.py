"""Simulated MKS 937B gauge controllers, the gauges on them and their relays.

The PV interface mirrors the real EPICS templates in the mks937b support
module, and the classes here compose the same way SR-VA's gaugeSet.xml does:

    mks937b.template          -> gaugeControllerRecord  -- the controller
    mks937bImg.template       -> imgRecord              -- inverted magnetron
    mks937bPirg.template      -> pirgRecord             -- Pirani
    mks937bRelays.template    -> relayRecord            -- one setpoint relay
    mks937bFastRelay.template -> fastRelayRecord        -- the fast relay
    mks937bGauge.template     -> gaugeRecord            -- IMG + Pirani pair
    gaugeSet.xml              -> gaugeSetRecord         -- the whole assembly

The three group classes come from the mks937a support module instead, because
that is where a vacuum space's builder XML gets them from and there is no 937B
equivalent:

    mks937aImgGroup.template   -> imgGroupRecord    -- up to 8 IMGs
    mks937aPirgGroup.template  -> pirgGroupRecord   -- up to 8 Piranis
    mks937aGaugeGroup.template -> gaugeGroupRecord  -- up to 8 pairs

A 937A gauge keeps its interlock setpoints on itself, where a 937B keeps them
on numbered relays, so the group templates ask their members for records the
937B devices here do not have.  They are mapped onto the relay that does the
same job, and the mapping is written out on each group class.

One thing worth knowing before reading on: the pressure a client actually
looks at, GAUGE-nn:P, does not come down the controller's serial link.  On the
real machine the PLC combines the two gauges of a pair and the IOC reads the
answer back as GCTLR-nn:C<n>_RAW over EtherIP - that is what the
dlsPLC.NX102_readReal entries in gaugeSet.xml are for.  The serial link
supplies the individual IMG and Pirani readings, their statuses, and all of
the setpoint and relay configuration.  This simulation stands in for the PLC
as well, so it writes C<n>_RAW itself and then follows the same path through
:PLOG_CALC, :PLOG and :P that the real IOC does.

The pressure the gauges report is not theirs either: it belongs to the volume
each pair is sat on - see vacuum_model - so two gauges on the same volume agree
with each other to within their own noise, and both of them follow whatever the
ion pumps on that volume are doing.  Which volume that is comes from the vacuum
layout rather than from a constructor argument, so that a gauge set can be
built from a builder XML that says nothing about the beam pipe.

Records that only exist to glue StreamDevice, the PLC link or the startup
ordering together are deliberately not created: the :FAN10S / :INITSETSPFAN /
:INITSETSPSDIS / :SETSPFAN fanouts, the :PSEQ / :HYSTSEQ / :SETSPPRESEQ
sequences, :GOODP, :ADEL, :COMMSMATCH, and the :SPOFFWRITE / :SELSPOFF /
:SPOFFNOMINAL chain that negotiates a relay's OFF setpoint (the simulation
just works the OFF setpoint out directly).

Anything named SIM:* is a knob for this simulation only and has no equivalent
on real hardware.
"""

import enum
import math
from typing import Any

from softioc import alarm, builder

from .device_groups import deviceGroup, highest, lowest, setDemand
from .vacuum_sim import clamp, jitter

# What a gauge's readbacks are seeded with before the first tick, for the same
# reason as the ion pumps': the vacuum layout has not yet said which length of
# pipe the gauge is on.
NOMINAL_PRESSURE = 1.0e-9

# :COMMS, as for the ion pump controllers.  A simulated serial link is up.
COMMS_STATES = ("TIMEOUT", "OK")
COMMS_TIMEOUT, COMMS_OK = range(len(COMMS_STATES))


class GaugeStatus(enum.IntEnum):
    """Values of a gauge's :STA record.

    Value 0 is also labelled "OK" by the templates; the simulation only ever
    publishes 1 for a healthy gauge.  Everything from 2 up means the pressure
    reading is not to be trusted, which is what :PDIS keys off.
    """

    OK = 1
    ABOVE_RANGE = 2
    AT_ATMOSPHERE = 3
    LOW_EMISSION = 4
    FILAMENT_OFF = 5
    HV_OFF = 6
    STARTUP_DELAY = 7
    BELOW_RANGE = 8
    CONTROLLED = 9
    PROTECTED_STATE = 10
    NO_GAUGE = 11
    NOT_CONNECTED = 12
    WRONG_GAUGE = 13
    BAD_COMMAND = 14
    LOCKED_OUT = 15


# String and alarm severity for every :STA value.  Below Range is not an alarm:
# a cold cathode under its bottom range is good news.
GAUGE_STATES = (
    ("OK", "NO_ALARM"),
    ("OK", "NO_ALARM"),
    ("Above Range", "MINOR"),
    ("At Atmosphere", "MINOR"),
    ("Low Emission", "MAJOR"),
    ("Filament Off", "MAJOR"),
    ("HV Off", "MAJOR"),
    ("Startup Delay", "MINOR"),
    ("Below Range", "NO_ALARM"),
    ("Controlled", "MINOR"),
    ("Protected State", "MINOR"),
    ("No Gauge", "MAJOR"),
    ("Not Connected", "MAJOR"),
    ("Wrong Gauge", "MAJOR"),
    ("Bad Command", "MAJOR"),
    ("Locked Out", "MAJOR"),
)

# Modules the controller can find in slots CC, A and B - :MCC, :MA and :MB.
MODULE_TYPES = (
    ("Hot Cathode", "NO_ALARM"),
    ("Cold Cathode", "NO_ALARM"),
    ("Dual Pirani", "NO_ALARM"),
    ("Dual Cnv Pirani", "NO_ALARM"),
    ("Dual Thermocpl", "NO_ALARM"),
    ("Dual Cap Manmtr", "NO_ALARM"),
    ("Sngl Pirani", "NO_ALARM"),
    ("Sngl Cnv Pirani", "NO_ALARM"),
    ("Sngl Thrmcpl", "NO_ALARM"),
    ("Sngl Cap Manmtr", "NO_ALARM"),
    ("No Module", "NO_ALARM"),
    ("Wrong Module", "MAJOR"),
)

COLD_CATHODE, DUAL_CNV_PIRANI = 1, 3

# Gauge power - :ISENABLED and the Pirani's :ENABLE.
POWER_STATES = (("Off", "MAJOR"), ("On", "NO_ALARM"))

# A relay's :MODE.  Forced On is an alarm: it means the setpoint is bypassed.
RELAY_MODES = (("On", "MAJOR"), ("Off", "NO_ALARM"), ("Active", "NO_ALARM"))
RELAY_ON, RELAY_OFF, RELAY_ACTIVE = range(len(RELAY_MODES))

# The :*:ENABLE mbbo the 937A group and space templates publish.  Two states,
# both of which a 937B expresses as a relay mode - see relayRecord.setEnable.
ENABLE_MODES = (("Force On I/L", "MAJOR"), ("I/L Operating", "NO_ALARM"))
ENABLE_FORCE_ON, ENABLE_OPERATING = range(len(ENABLE_MODES))

# Whether a relay trips above or below its setpoint - :DIR.
RELAY_DIRECTIONS = (("Above", "MAJOR"), ("Below", "NO_ALARM"))
RELAY_ABOVE, RELAY_BELOW = range(len(RELAY_DIRECTIONS))

# How the cold cathode's enable setpoint behaves - :CTL:MODE.  Forced on means
# the gauge is struck whatever the controlling gauge says, so it is an alarm.
CC_CONTROL_MODES = (
    ("Force On", "MAJOR"),
    ("I/L Safe", "NO_ALARM"),
    ("I/L Operating", "NO_ALARM"),
)
CC_FORCE_ON, CC_INTERLOCK_SAFE, CC_INTERLOCK_OPERATING = range(len(CC_CONTROL_MODES))

# Which channel drives the cold cathode's enable setpoint - :CTL:CCS.
CC_CONTROL_CHANNELS = ("C1", "C2", "NONE", "A1", "B1", "A2", "B2")

# Extended setpoint range - :CTL:SETEXTRANGE.
EXTENDED_RANGE_STATES = ("OFF", "ON")

# Which of the pair is currently believed - GAUGE-nn:GAUGE.
ACTIVE_GAUGES = (("IMG", "NO_ALARM"), ("Pirani", "MINOR"), ("No Gauge", "MAJOR"))
ACTIVE_IMG, ACTIVE_PIRANI, ACTIVE_NONE = range(len(ACTIVE_GAUGES))

# Working range of an inverted magnetron, from the :P record's LOPR and HOPR.
IMG_PRESSURE_MIN, IMG_PRESSURE_MAX = 1.0e-11, 1.0e-2

# Working range of a Pirani, likewise.  Above ATMOSPHERE it reports that it is
# at atmosphere rather than giving a number.
PIRG_PRESSURE_MIN, PIRG_PRESSURE_MAX = 1.0e-3, 1000.0
ATMOSPHERE = 900.0

# The relays a front end gauge set puts on each gauge, in the order
# gaugeSet.xml creates them: description, OFF description, and ON setpoint.
IMG_RELAYS = (
    ("Valve I/L On", "Valve I/L Off", 1.0e-6),
    ("MPS I/L 1", "Relay OFF setpoint", 1.0e-7),
    ("MPS I/L 2", "Relay OFF setpoint", 1.0e-7),
    ("RGA I/L", "Relay OFF setpoint", 1.0e-4),
)

PIRG_RELAYS = (
    ("Ion Pump I/L On", "Ion Pump I/L", 1.0e-2),
    ("MPS I/L 1", "MPS I/L", 1.0e-2),
)


class gaugeControllerRecord:
    """An MKS 937B multi-sensor controller - mks937b.template.

    Gauges and relays attach themselves on construction, as they do in the
    mks937b builder module where each is given its parent GCTLR object.
    """

    def __init__(self, prefix, address="001", version=1.03,
                 slotCC=COLD_CATHODE, slotA=COLD_CATHODE,
                 slotB=DUAL_CNV_PIRANI):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.address = address
        self.gaugeList = []
        self.relayList = []

        self.unitPV = builder.stringIn("UNIT",
                                       DESC="Pressure Unit",
                                       initial_value="MBAR")

        self.controllerVersionPV = builder.aIn("CTLV", PREC=2,
                                               DESC="Controller Version",
                                               initial_value=version)

        # Deprecated on the 937B, whose comms module is integrated and has no
        # ID of its own.  The real template forces it Passive; it never reads.
        self.commsVersionPV = builder.aIn("COMV", PREC=2,
                                          DESC="Communications Version",
                                          initial_value=0.0)

        self.slotCCPV = builder.mbbIn("MCC", *MODULE_TYPES,
                                      DESC="Slot CC", UNSV="MAJOR",
                                      initial_value=slotCC)

        self.slotAPV = builder.mbbIn("MA", *MODULE_TYPES,
                                     DESC="Slot A", UNSV="MAJOR",
                                     initial_value=slotA)

        self.slotBPV = builder.mbbIn("MB", *MODULE_TYPES,
                                     DESC="Slot B", UNSV="MAJOR",
                                     initial_value=slotB)

        self.debugPV = builder.stringIn("DEBUG",
                                        DESC="Generic command",
                                        initial_value="")

        self.commsPV = builder.mbbIn("COMMS", *COMMS_STATES,
                                     DESC="Communication Status",
                                     initial_value=COMMS_OK)

    def addGauge(self, gauge):
        """Register a gauge as being read by this controller."""
        self.gaugeList.append(gauge)

    def addRelay(self, relay):
        """Register a relay as belonging to this controller.

        The controller keeps them in the order they were created so that they
        can be given their GCTLR-nn:RLY<n> aliases and rolled up into
        :RLY_STAT, both of which number the relays controller wide.
        """
        self.relayList.append(relay)
        return len(self.relayList)


class pirgRecord:
    """A Pirani gauge on one channel of a controller - mks937bPirg.template."""

    def __init__(self, controller, prefix, channel, enabled=True):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.controller = controller
        self.channel = channel
        controller.addGauge(self)

        # The relays driven by this gauge, in the order they are created, so
        # that a group can ask for "this gauge's first relay" - which is what
        # a 937A group template means by $(pirg):RLY:*.
        self.relays = []

        self.statusPV = builder.mbbIn("STA", *GAUGE_STATES,
                                      DESC="Status", UNSV="MAJOR",
                                      initial_value=GaugeStatus.OK)

        self.pressurePV = builder.aIn("P", LOPR=0.001, HOPR=1000.0,
                                      EGU="mbar", PREC=3,
                                      DESC="Pressure",
                                      initial_value=PIRG_PRESSURE_MIN)

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-3.0, HOPR=3.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=math.log10(PIRG_PRESSURE_MIN))

        # 1 when :STA says the pressure reading is not to be trusted, which is
        # what the real template uses to disable the :P record.
        self.pressureDisabledPV = builder.aIn("PDIS",
                                              DESC="Pressure invalid",
                                              initial_value=0)

        self.enablePV = builder.mbbOut("ENABLE", *POWER_STATES,
                                       DESC="Enable Pirani power",
                                       always_update=True,
                                       initial_value=1 if enabled else 0,
                                       on_update=lambda v: self.isEnabledPV.set(v))

        self.isEnabledPV = builder.mbbIn("ISENABLED", *POWER_STATES,
                                         DESC="Pirani power",
                                         initial_value=1 if enabled else 0)

        self.offWarningPV = builder.boolIn("OFFWARN", ZNAM="Warning off",
                                           ONAM="Warning on",
                                           DESC="Warning for OFF button",
                                           initial_value=0)

    def update(self, pressure):
        """Publish what this gauge makes of the given chamber pressure.

        Returns the reading the sensor itself is seeing, which the cold cathode
        of the same pair needs whether or not it is a trustworthy number.
        """
        if not self.isEnabledPV.get():
            status = GaugeStatus.HV_OFF
        elif pressure > PIRG_PRESSURE_MAX:
            status = GaugeStatus.ABOVE_RANGE
        elif pressure >= ATMOSPHERE:
            status = GaugeStatus.AT_ATMOSPHERE
        elif pressure < PIRG_PRESSURE_MIN:
            # A Pirani does not read below about a milli-bar; it bottoms out
            # and says so rather than reporting a wrong number.
            status = GaugeStatus.BELOW_RANGE
        else:
            status = GaugeStatus.OK

        reading = clamp(pressure, PIRG_PRESSURE_MIN, PIRG_PRESSURE_MAX)
        publishReading(self, status, reading)
        return reading

    def reading(self):
        """True while :STA says the pressure this gauge reports is good."""
        return self.statusPV.get() <= GaugeStatus.OK


class imgRecord:
    """An inverted magnetron gauge on a controller - mks937bImg.template.

    A cold cathode is only struck once something else says the pressure is low
    enough, which is what the :CTL: records are for: :CTL:SP is the pressure
    the controlling gauge has to be under, and :CTL:MODE says whether that is
    enforced at all.  The simulation uses the Pirani of the same pair as the
    controlling gauge, which is what it physically is - :CTL:CCS is published
    as configuration rather than used to pick the channel.
    """

    def __init__(self, controller, prefix, channel, ctlChannel=0, enabled=True,
                 ctlSetpoint=1.0e-2, ctlHysteresis=1.2e-2,
                 proSetpoint=5.0e-4, fastRelaySetpoint=1.0e-6,
                 startupDelay=5.0, ctlMode=CC_INTERLOCK_SAFE):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.controller = controller
        self.channel = channel
        controller.addGauge(self)

        # As for the Pirani: the relays this gauge drives, in creation order.
        self.relays = []

        # --- gauge power -----------------------------------------------------
        # The real template leaves the cold cathode off until someone asks for
        # it; a simulation of a running front end is more use with them lit.
        self.cchvPV = builder.boolOut("CCHV", ZNAM="Off", ONAM="On",
                                      DESC="Cold Cathode Enable",
                                      always_update=True,
                                      initial_value=1 if enabled else 0)

        self.isEnabledPV = builder.mbbIn("ISENABLED", *POWER_STATES,
                                         DESC="IMG power",
                                         initial_value=1 if enabled else 0)

        self.switchingPV = builder.boolIn("SWITCHING", ZNAM="", ONAM="Switching",
                                          DESC="Switching",
                                          initial_value=0)

        # --- pressure --------------------------------------------------------
        self.statusPV = builder.mbbIn("STA", *GAUGE_STATES,
                                      DESC="Status", UNSV="MAJOR",
                                      initial_value=GaugeStatus.OK)

        self.pressurePV = builder.aIn("P", LOPR=1.0e-11, HOPR=0.01, EGU="mbar", PREC=11,
                                      DESC="Pressure",
                                      initial_value=IMG_PRESSURE_MIN)

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-11.0, HOPR=-2.0, EGU="log",
                                         PREC=3,
                                         DESC="log Pressure",
                                         initial_value=math.log10(IMG_PRESSURE_MIN))

        # Both of these are plain copies of :P in the real template, which
        # forwards it into them and never tracks an extreme.  Kept faithful.
        self.pressureMaxPV = builder.aIn("PMAX", LOPR=1.0e-11, HOPR=0.01,
                                         EGU="mbar", PREC=11,
                                         DESC="Max Pressure",
                                         initial_value=IMG_PRESSURE_MIN)

        self.pressureMinPV = builder.aIn("PMIN", LOPR=1.0e-11, HOPR=0.01,
                                         EGU="mbar", PREC=11,
                                         DESC="Min Pressure",
                                         initial_value=IMG_PRESSURE_MIN)

        self.pressureDisabledPV = builder.aIn("PDIS",
                                              DESC="Pressure invalid",
                                              initial_value=0)

        self.errorNumberPV = builder.aIn("ERRORNUM",
                                         DESC="Last error code",
                                         initial_value=0)

        self.offWarningPV = builder.boolIn("OFFWARN", ZNAM="Warning off",
                                           ONAM="Warning on",
                                           DESC="Warning for OFF button",
                                           initial_value=0)

        # --- control setpoint: the pressure that permits the cold cathode ----
        self.ctlSetpointPV = builder.aIn("CTL:SP", LOPR=2.7e-3, HOPR=9.5e-1,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Enable",
                                         initial_value=ctlSetpoint,
                                         HIHI=9.6e-1, LOLO=2.6e-3,
                                         HIGH=1.1e-2, LOW=9.0e-3,
                                         HHSV="MAJOR", LLSV="MAJOR",
                                         HSV="MINOR", LSV="MINOR")

        # :CTL:OUTSP is the entry point a client writes.  On real hardware it
        # only sets the value locally and a pre-sequence extends the setpoint
        # range before :CTL:SETSP sends it down the link; here both simply
        # arrive at the same readback.
        self.ctlOutSetpointPV = builder.aOut("CTL:OUTSP", DRVL=2.7e-3, DRVH=9.5e-1,
                                             LOPR=2.7e-3, HOPR=9.5e-1,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Enable",
                                             initial_value=ctlSetpoint,
                                             on_update=self.setCtlSetpoint)

        self.ctlSetSetpointPV = builder.aOut("CTL:SETSP", DRVL=2.7e-3, DRVH=9.5e-1,
                                             LOPR=2.7e-3, HOPR=9.5e-1,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Enable",
                                             initial_value=ctlSetpoint,
                                             on_update=self.setCtlSetpoint)

        self.ctlExtendedRangePV = builder.mbbOut("CTL:SETEXTRANGE",
                                                 *EXTENDED_RANGE_STATES,
                                                 DESC="CC Extended Setpoint Enable",
                                                 initial_value=1)

        self.ctlHysteresisPV = builder.aIn("CTL:HYST", LOPR=2.7e-10, HOPR=1.2e-2,
                                           EGU="mbar", PREC=1,
                                           DESC="Setpoint Hysteresis",
                                           initial_value=ctlHysteresis)

        self.ctlSetHysteresisPV = builder.aOut("CTL:SETHYST", DRVL=2.7e-10, DRVH=1.2e-2,
                                               LOPR=2.7e-10, HOPR=1.2e-2,
                                               EGU="mbar", PREC=1,
                                               DESC="CC Hysteresis",
                                               initial_value=ctlHysteresis,
                                               on_update=self.ctlHysteresisPV.set)

        self.ctlMinHysteresisPV = builder.aIn("CTL:MINHYST",
                                              EGU="mbar", PREC=1,
                                              DESC="Minimum hysteresis",
                                              initial_value=ctlSetpoint * 1.2)

        self.ctlDisablePV = builder.Action("CTL:DIS",
                                          DESC="Disable Control Setpoint",
                                          on_update=lambda v: self.ctlModePV.set(
                                              CC_FORCE_ON))

        self.ctlSetModePV = builder.mbbOut("CTL:SETMODE", *CC_CONTROL_MODES,
                                           DESC="CC Operating Mode",
                                           always_update=True,
                                           initial_value=ctlMode,
                                           on_update=lambda v: self.ctlModePV.set(v))

        self.ctlModePV = builder.mbbIn("CTL:MODE", *CC_CONTROL_MODES,
                                       DESC="CC Status", UNSV="MAJOR",
                                       ZRSV="MINOR",
                                       initial_value=ctlMode)

        self.ctlSetChannelPV = builder.mbbOut("CTL:SETCCS", *CC_CONTROL_CHANNELS,
                                              DESC="Control Channel Status",
                                              always_update=True,
                                              initial_value=ctlChannel,
                                              on_update=lambda v:
                                              self.ctlChannelPV.set(v))

        self.ctlChannelPV = builder.mbbIn("CTL:CCS", *CC_CONTROL_CHANNELS,
                                          DESC="Control Channel Status",
                                          UNSV="MAJOR",
                                          initial_value=ctlChannel)

        # --- protection setpoint: overpressure trip -------------------------
        self.proSetpointPV = builder.aIn("PRO:SP", LOPR=1.3e-5, HOPR=1.0e-2,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Overpressure",
                                         initial_value=proSetpoint,
                                         HIHI=1.1e-2, LOLO=1.2e-5,
                                         HIGH=5.1e-4, LOW=4.9e-4,
                                         HHSV="MAJOR", LLSV="MAJOR",
                                         HSV="MINOR", LSV="MINOR")

        self.proOutSetpointPV = builder.aOut("PRO:OUTSP", DRVL=1.3e-5, DRVH=1.0e-2,
                                             LOPR=1.3e-5, HOPR=1.0e-2,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Overpressure",
                                             initial_value=proSetpoint,
                                             on_update=self.proSetpointPV.set)

        self.proDisablePV = builder.Action("PRO:DIS",
                                          DESC="Disable Protection Setpoint")

        self.proConstZeroPV = builder.boolIn("PRO:CONSTZERO", ZNAM="Zero", ONAM="One",
                                             DESC="Constant zero value",
                                             initial_value=0)

        self.proNoWritePV = builder.boolIn("PRO:ILKSETSP:NOWRITE",
                                           ZNAM="Allow", ONAM="Inhibit",
                                           DESC="ILK setpoint access",
                                           initial_value=0)

        # --- simulation only -------------------------------------------------
        self.simStartupDelayPV = builder.aOut("SIM:STARTUP_DELAY", DRVL=0.0, DRVH=60.0,
                                              LOPR=0.0, HOPR=60.0,
                                              EGU="s", PREC=1,
                                              DESC="Sim time to strike the CC",
                                              initial_value=startupDelay)

        self.startupRemaining = 0.0
        self.powered = bool(enabled)
        self.fastRelay = fastRelayRecord(self, fastRelaySetpoint)

    def setCtlSetpoint(self, value):
        """A new IMG enable setpoint brings its minimum hysteresis with it."""
        self.ctlSetpointPV.set(value)
        self.ctlMinHysteresisPV.set(value * 1.2)

    # -- what an IMG group fans a demand out through --------------------------

    def setCchv(self, value):
        """:CCHV - strike the cold cathode, or drop it.

        The method exists so that a group does not have to know whether its
        member is a gauge or another group; update() reads :CCHV each pass, so
        writing the record is all there is to do.
        """
        setDemand(self.cchvPV, 1 if value else 0)

    def setPro(self, value):
        """:PRO:SETSP - the overpressure the controller protects against."""
        setDemand(self.proOutSetpointPV, value)
        self.proSetpointPV.set(value)

    def interlocked(self, controllingPressure):
        """True when the enable setpoint is holding the cold cathode off."""
        if self.ctlModePV.get() == CC_FORCE_ON:
            return False
        return controllingPressure > self.ctlSetpointPV.get()

    def update(self, delta, pressure, controllingPressure):
        """Publish what this gauge makes of the given chamber pressure.

        controllingPressure is what the Pirani of the same pair reads, even
        when the Pirani has bottomed out and flagged itself Below Range: a
        Pirani on its bottom stop is unambiguous evidence of low pressure, and
        the controller strikes the cold cathode on it just the same.
        """
        powered = bool(self.cchvPV.get()) and not self.interlocked(controllingPressure)
        if powered and not self.powered:
            # Striking the discharge takes a moment.
            self.startupRemaining = self.simStartupDelayPV.get()
        self.powered = powered
        self.startupRemaining = max(0.0, self.startupRemaining - delta)

        if not self.cchvPV.get():
            status = GaugeStatus.HV_OFF
        elif not powered:
            status = GaugeStatus.CONTROLLED
        elif self.startupRemaining > 0.0:
            status = GaugeStatus.STARTUP_DELAY
        elif pressure > IMG_PRESSURE_MAX:
            status = GaugeStatus.ABOVE_RANGE
        elif pressure < IMG_PRESSURE_MIN:
            status = GaugeStatus.BELOW_RANGE
        else:
            status = GaugeStatus.OK

        self.isEnabledPV.set(1 if powered else 0)
        self.switchingPV.set(1 if status == GaugeStatus.STARTUP_DELAY else 0)

        published = publishReading(
            self, status, clamp(pressure, IMG_PRESSURE_MIN, IMG_PRESSURE_MAX))
        self.pressureMaxPV.set(published)
        self.pressureMinPV.set(published)

    def reading(self):
        """True while :STA says the pressure this gauge reports is good."""
        return self.statusPV.get() <= GaugeStatus.OK


class fastRelayRecord:
    """The controller's fast relay for one gauge - mks937bFastRelay.template.

    Only cold cathode channels have one, so it is built as part of its IMG
    rather than being wired up separately.
    """

    def __init__(self, gauge, setpoint=1.0e-6):
        builder.SetDeviceName(gauge.prefix)
        self.gauge = gauge

        self.setpointPV = builder.aIn("FRLY:SP", LOPR=1.0e-5, HOPR=1.0e-2,
                                      EGU="mbar", PREC=1,
                                      DESC="Fast Relay Trigger level",
                                      initial_value=setpoint)

        self.setSetpointPV = builder.aOut("FRLY:SETSP", DRVL=2.7e-10, DRVH=1.2e-2,
                                          LOPR=2.7e-10, HOPR=1.2e-2,
                                          EGU="mbar", PREC=1,
                                          DESC="Fast Relay Trigger level",
                                          initial_value=setpoint,
                                          on_update=self.setpointPV.set)

        self.existsPV = builder.aIn("FRLY:EXISTS",
                                    DESC="Fast Relay Output Exists",
                                    initial_value=1)

        self.constZeroPV = builder.boolIn("FRLY:CONSTZERO", ZNAM="Zero", ONAM="One",
                                          DESC="Constant zero value",
                                          initial_value=0)

        self.noWritePV = builder.boolIn("FRLY:ILKSETSP:NOWRITE",
                                        ZNAM="Allow", ONAM="Inhibit",
                                        DESC="ILK setpoint access",
                                        initial_value=0)


class relayRecord:
    """One of the controller's setpoint relays - mks937bRelays.template.

    Relays are named after the gauge that drives them, GAUGE:RLY<n>, but they
    are numbered controller wide as well - the controller's own GCTLR:RLY<n>
    names are aliases onto these, which is what SR-VA's mks937bRelaysAlias
    template does.

    The relay's contact state is not an EPICS record: it is a physical output
    that the PLC reads.  So there is nothing here to simulate beyond the
    setpoint bookkeeping - the readbacks follow their demands, and the OFF
    setpoint keeps at least 10% of hysteresis above the ON setpoint.
    """

    def __init__(self, gauge, number, desc="Valve Interlock",
                 offDesc="Relay OFF setpoint", setpoint=1.0e-6,
                 offSetpoint=0.0, mode=RELAY_ACTIVE, direction=RELAY_BELOW,
                 drvl=2.7e-10, drvh=1.2e-2, lopr=2.7e-10, hopr=1.2e-2):
        prefix = f"{gauge.prefix}:RLY{number}"
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.gauge = gauge
        self.number = number
        self.controllerNumber = gauge.controller.addRelay(self)
        gauge.relays.append(self)

        self.modePV = builder.mbbIn("MODE", *RELAY_MODES,
                                    DESC="Relay mode", UNSV="MAJOR",
                                    initial_value=mode)

        self.setModePV = builder.mbbOut("SETMODE", *RELAY_MODES,
                                        DESC="Relay Mode",
                                        always_update=True,
                                        initial_value=mode,
                                        on_update=lambda v: self.modePV.set(v))

        self.directionPV = builder.mbbIn("DIR", *RELAY_DIRECTIONS,
                                         DESC="Relay direction", UNSV="MAJOR",
                                         initial_value=direction)

        self.setDirectionPV = builder.mbbOut("SETDIR", *RELAY_DIRECTIONS,
                                             DESC="Relay Direction",
                                             always_update=True,
                                             initial_value=direction,
                                             on_update=lambda v:
                                             self.directionPV.set(v))

        self.setpointPV = builder.aIn("SP", LOPR=lopr, HOPR=hopr, EGU="mbar", PREC=1,
                                      DESC=desc,
                                      initial_value=setpoint,
                                      HHSV="MAJOR", LLSV="MAJOR",
                                      HSV="MINOR", LSV="MINOR")

        self.setSetpointPV = builder.aOut("SETSP", DRVL=drvl, DRVH=drvh,
                                          LOPR=drvl, HOPR=drvh,
                                          EGU="mbar", PREC=1,
                                          DESC="Output Relay Setpoint",
                                          initial_value=setpoint,
                                          on_update=self.setSetpoint)

        self.offSetpointPV = builder.aIn("SPOFF", LOPR=lopr, HOPR=hopr, EGU="mbar",
                                         PREC=1,
                                         DESC=offDesc,
                                         initial_value=offLevel(setpoint,
                                                                offSetpoint))

        # The demand for the OFF setpoint.  An ai in the real template, which
        # loads it from the relay_off_level macro and lets the hysteresis
        # negotiation chain read it back.
        self.setOffSetpointPV = builder.aIn("SETSPOFF", EGU="mbar", PREC=2,
                                            DESC=offDesc,
                                            initial_value=offSetpoint)

        self.hysteresisPV = builder.aIn("HYST", EGU="mbar", PREC=1,
                                        DESC="Relay Hysteresis",
                                        initial_value=offLevel(setpoint,
                                                               offSetpoint))

        self.setHysteresisPV = builder.aOut("SETHYST", EGU="mbar", PREC=2,
                                            DESC="Relay Hysteresis",
                                            initial_value=offLevel(setpoint,
                                                                   offSetpoint),
                                            on_update=self.hysteresisPV.set)

        self.disablePV = builder.Action("DIS",
                                        DESC="Disable Relay Setpoint",
                                        on_update=lambda v: self.setModePV.set(
                                            RELAY_OFF))

        self.existsPV = builder.aIn("EXISTS",
                                    DESC=f"Relay {number} Output Exists",
                                    initial_value=1)

        self.constZeroPV = builder.boolIn("CONSTZERO", ZNAM="Zero", ONAM="One",
                                          DESC="Constant zero value",
                                          initial_value=0)

        self.noWritePV = builder.boolIn("ILKSETSP:NOWRITE", ZNAM="Allow",
                                        ONAM="Inhibit",
                                        DESC="ILK setpoint access",
                                        initial_value=0)

    def setSetpoint(self, value):
        """A new ON setpoint drags the OFF setpoint up with it if it has to."""
        self.setpointPV.set(value)
        off = offLevel(value, self.setOffSetpointPV.get())
        self.offSetpointPV.set(off)
        self.hysteresisPV.set(off)

    # -- what a gauge group fans a demand out through -------------------------

    def setDemand(self, value):
        """Set the ON setpoint the way a client writing :SETSP would."""
        setDemand(self.setSetpointPV, value)
        self.setSetpoint(value)

    def setEnable(self, value):
        """A 937A group's :RLY:ENABLE, in 937B relay modes.

        The two states of the template's mbbo are Force On I/L and I/L
        Operating, which are what a 937B calls a relay forced On and a relay
        left Active.  Off has no equivalent on the 937A side and is only
        reachable through the relay's own :SETMODE.
        """
        mode = RELAY_ON if value == ENABLE_FORCE_ON else RELAY_ACTIVE
        setDemand(self.setModePV, mode)
        self.modePV.set(mode)

    def addAliases(self):
        """Give this relay its controller wide GCTLR:RLY<n> names."""
        controller = self.gauge.controller.prefix
        number = self.controllerNumber
        self.modePV.add_alias(f"{controller}:RLY{number}:MODE")
        self.setModePV.add_alias(f"{controller}:RLY{number}:SETMODE")


class gaugeRecord:
    """An IMG and a Pirani reading the same space - mks937bGauge.template.

    This is the device the rest of the machine talks to.  It does not own a
    pressure: it reports what its two sensors make of the vacuum space it is
    sat on, each with its own bit of noise on it, so a second gauge on the same
    space reads very nearly - but not exactly - the same thing.
    """

    def __init__(self, dom, id, img, pirg, combinedPV,
                 ctlSetpoint=1.0e-2, ctlHysteresis=1.2e-2):
        self.prefix = f"{dom}-VA-GAUGE-{id}"
        self.img = img
        self.pirg = pirg
        self.combinedPV = combinedPV

        # The length of pipe this pair reads, filled in by
        # vacuumLayout.attach.  What the gauges actually report arrives with
        # the first tick; until then they quote a nominal UHV pressure.
        # The volume this sits on, filled in by vacuumLayout.attach.
        # Typed loosely because what guarantees it is set is
        # attachLayout's check, which no type checker can see.
        self.volume: Any = None
        pressure = NOMINAL_PRESSURE

        builder.SetDeviceName(self.prefix)

        # The PLC's combined pressure, turned into a logarithm on its way to
        # :PLOG - mks937bPlogEGU.template.
        self.plogCalcPV = builder.aIn("PLOG_CALC",
                                      DESC="Conversion of P to PLOG",
                                      initial_value=math.log10(pressure))

        self.selectPV = builder.aIn("SEL",
                                    DESC="Select Pressure",
                                    initial_value=ACTIVE_IMG)

        self.activeGaugePV = builder.mbbIn("GAUGE", *ACTIVE_GAUGES,
                                           DESC="Active Gauge", UNSV="MAJOR",
                                           initial_value=ACTIVE_IMG)

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-11.0, HOPR=3.0, EGU="log",
                                         PREC=3,
                                         DESC="log Pressure",
                                         initial_value=math.log10(pressure),
                                         HIGH=-7.0, HSV="MINOR")

        self.pressurePV = builder.aIn("P", LOPR=1.0e-11, HOPR=1000.0, EGU="mbar",
                                      PREC=11,
                                      DESC="Pressure",
                                      initial_value=pressure)

        self.noWritePV = builder.boolIn("ILKSETSP:NOWRITE", ZNAM="Allow",
                                        ONAM="Inhibit",
                                        DESC="ILK setpoint access",
                                        initial_value=0)

        # The chamber pressure and how fast it moves belong to the volume this
        # pair is on - see vacuum_model.  Its gas load and the pumps on it are
        # what move it, and neither is a record: none of it exists on the real
        # machine, so none of it is served.

        # The cold cathode's enable setpoint is entered against the Pirani,
        # because the Pirani is the gauge that has to agree before the cold
        # cathode is struck.  These records live in mks937bGauge.template even
        # though they are named after the Pirani, and they write through to
        # the IMG's own control records.
        builder.SetDeviceName(pirg.prefix)

        self.ctlSetpointPV = builder.aIn("CTL:SP", LOPR=2.7e-3, HOPR=9.5e-1,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Enable",
                                         initial_value=ctlSetpoint)

        self.ctlHysteresisPV = builder.aIn("CTL:HYST", LOPR=2.7e-10, HOPR=1.2e-2,
                                           EGU="mbar", PREC=1,
                                           DESC="IMG Enable Off",
                                           initial_value=ctlHysteresis)

        self.ctlSetSetpointPV = builder.aOut("CTL:SETSP", DRVL=2.7e-3, DRVH=9.5e-1,
                                             LOPR=2.7e-3, HOPR=9.5e-1,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Enable",
                                             initial_value=ctlSetpoint,
                                             on_update=self.setCtlSetpoint)

        self.ctlSetSetpointOffPV = builder.aOut("CTL:SETSPOFF", DRVL=2.7e-10,
                                                DRVH=1.2e-2,
                                                LOPR=2.7e-10, HOPR=1.2e-2,
                                                EGU="mbar", PREC=1,
                                                DESC="IMG Enable Off",
                                                initial_value=ctlHysteresis,
                                                on_update=self.setCtlHysteresis)

        self.pirgNoWritePV = builder.boolIn("ILKSETSP:NOWRITE",
                                            ZNAM="Allow", ONAM="Inhibit",
                                            DESC="ILK setpoint access",
                                            initial_value=0)

        # The IMG's operating mode doubles as the Pirani's control enable.
        img.ctlSetModePV.add_alias(f"{pirg.prefix}:CTL:ENABLE")

        # All of the :CTL: records above carry the Pirani's name rather than
        # this pair's, so hang them on the Pirani object as well: a Pirani
        # group aggregates $(pirg):CTL:* and has to be able to find them.
        pirg.ctlSetpointPV = self.ctlSetpointPV
        pirg.ctlSetSetpointPV = self.ctlSetSetpointPV
        pirg.ctlEnablePV = img.ctlSetModePV
        pirg.setCtlSetpoint = self.setCtlSetpoint

        def setCtlEnable(value):
            """Both records by hand: .set() would not fire :CTL:SETMODE's
            callback, and :CTL:MODE is the one the interlock actually reads."""
            img.ctlSetModePV.set(value)
            img.ctlModePV.set(value)

        pirg.setCtlEnable = setCtlEnable

    def setCtlSetpoint(self, value):
        """Pass a new IMG enable setpoint down to the cold cathode."""
        self.ctlSetpointPV.set(value)
        self.img.setCtlSetpoint(value)

    def setCtlHysteresis(self, value):
        self.ctlHysteresisPV.set(value)
        self.img.ctlHysteresisPV.set(value)
        self.img.ctlSetHysteresisPV.set(value)

    def tick(self, delta):
        """Publish what both of this pair's sensors make of their volume."""
        pressure = self.volume.pressure * jitter()
        pirgReading = self.pirg.update(pressure)
        self.img.update(delta, pressure, pirgReading)
        self.publish()

    def publish(self):
        """Combine the pair the way the PLC does, then follow the real path."""
        if self.img.reading():
            combined = self.img.pressurePV.get()
            select = ACTIVE_IMG
        else:
            combined = self.pirg.pressurePV.get()
            # mks937bGauge.template's :SEL calc.  A bottomed out Pirani
            # (Below Range) still counts as a gauge worth believing.
            if (self.pirg.reading()
                    or self.pirg.statusPV.get() == GaugeStatus.BELOW_RANGE):
                select = ACTIVE_PIRANI
            else:
                select = ACTIVE_NONE

        self.combinedPV.set(combined)
        self.plogCalcPV.set(math.log10(combined))
        self.pressureLogPV.set(self.plogCalcPV.get())
        self.pressurePV.set(10.0 ** self.pressureLogPV.get())

        self.selectPV.set(select)
        self.activeGaugePV.set(select)


class gaugeSetRecord:
    """A gauge controller and everything hung off it - SR-VA's gaugeSet.xml.

    One MKS 937B carrying two cold cathodes and two Piranis, so a gauge on each
    of two vacuum spaces; four relays per cold cathode and two per Pirani,
    numbered 1 to 12 across the controller; a fast relay on each cold cathode;
    and the readings the PLC hands over.
    """

    def __init__(self, dom, setNumber, idA, idB=None, address="001"):
        self.controller = gaugeControllerRecord(
            f"{dom}-VA-GCTLR-{setNumber}", address=address)
        self.prefix = self.controller.prefix

        builder.SetDeviceName(self.controller.prefix)

        # What the PLC makes of each pair, read back over EtherIP on the real
        # machine - dlsPLC.NX102_readReal in gaugeSet.xml.
        self.combinedPVs = [
            builder.aIn(f"C{channel}_RAW", LOPR=1.0e-11, HOPR=1000.0, EGU="mbar",
                        PREC=11,
                        DESC=f"Combined pressure {channel}",
                        initial_value=NOMINAL_PRESSURE)
            for channel in (1, 2)]

        # Digital inputs the PLC publishes for the controller.
        self.digitalPVs = [
            builder.boolIn(f"DIG{bit:02d}", ZNAM="Off", ONAM="On",
                           DESC=f"Digital input {bit}",
                           initial_value=0)
            for bit in (1, 2)]

        # Channels 1 and 3 are the cold cathodes (slots CC and A), 5 and 6 the
        # two Piranis in slot B.  idB is optional: a controller with only one
        # pair fitted is common in the older cells, where the gauges are
        # declared one at a time rather than through SR-VA's gaugeSet.  It
        # still has both raw channels, because the controller does.
        fitted = [(idA, 1, 5, 0)]
        if idB is not None:
            fitted.append((idB, 3, 6, 1))

        self.gauges = []
        for index, (id, imgChannel, pirgChannel, ctlChannel) in enumerate(
                fitted):
            img = imgRecord(self.controller, f"{dom}-VA-IMG-{id}",
                            channel=imgChannel, ctlChannel=ctlChannel)
            pirg = pirgRecord(self.controller, f"{dom}-VA-PIRG-{id}",
                              channel=pirgChannel)
            self.gauges.append(
                gaugeRecord(dom, id, img, pirg, self.combinedPVs[index]))

        # Four relays on each cold cathode then two on each Pirani.  The order
        # matters: relays are numbered controller wide in the order they are
        # created, and these have to come out with the same numbers gaugeSet.xml
        # gives them - 1 to 4 and 5 to 8 for the cold cathodes, 9 to 10 and 11
        # to 12 for the Piranis - because that numbering is what the GCTLR:RLY<n>
        # aliases and :RLY_STAT are built from.
        for gauge in self.gauges:
            for number, (desc, offDesc, level) in enumerate(IMG_RELAYS, start=1):
                relayRecord(gauge.img, number, desc=desc, offDesc=offDesc,
                            setpoint=level)
        for gauge in self.gauges:
            for number, (desc, offDesc, level) in enumerate(PIRG_RELAYS, start=1):
                relayRecord(gauge.pirg, number, desc=desc, offDesc=offDesc,
                            setpoint=level, lopr=2.7e-3, hopr=950.0,
                            drvl=2.7e-3, drvh=950.0)

        for relay in self.controller.relayList:
            relay.addAliases()

        # Lowest relay mode across the controller, so that a relay someone has
        # forced On shows up - the records.sel entry in gaugeSet.xml.
        builder.SetDeviceName(self.controller.prefix)
        self.relayStatusPV = builder.mbbIn("RLY_STAT", *RELAY_MODES,
                                           DESC="Relay status", UNSV="MAJOR",
                                           initial_value=RELAY_ACTIVE)

    def tick(self, delta):
        """Publish both of this controller's gauge pairs."""
        for gauge in self.gauges:
            gauge.tick(delta)
        self.relayStatusPV.set(min(relay.modePV.get()
                                   for relay in self.controller.relayList))


def relayOf(gauge, number):
    """A gauge's nth relay, counting from 1 in the order they were created.

    A 937A gauge carries its interlock setpoints itself, so its group template
    asks for $(img):RLY:SETSP and $(img):RLA:SETSP.  A 937B puts them on
    numbered relays instead, and the front end wires relay 1 to the valve
    interlock and relay 2 to the MPS interlock - see IMG_RELAYS and
    PIRG_RELAYS - so those are what RLY: and RLA: mean here.
    """
    return gauge.relays[number - 1]


class imgGroupRecord(deviceGroup):
    """Up to 8 IMGs, or groups of IMGs - mks937aImgGroup.template.

    Writing to :CCHV strikes or drops every cold cathode underneath, through
    any member that is itself a group.  The pressure and status are the worst
    of the members, which for a gauge status means the furthest down the list
    away from OK.

    The 937A records this group aggregates map onto 937B relays as relayOf
    describes: :RLY: is relay 1, the valve interlock, and :RLA: is relay 2, the
    MPS interlock.  :PRO:ENABLE is not created - a 937B's protection setpoint
    is the controller's own and has no relay behind it to force on.
    """

    def __init__(self, prefix, members, delay=0.0):
        super().__init__(prefix, members, delay=delay)

        self.cchvPV = builder.boolOut("CCHV", ZNAM="Off", ONAM="On",
                                      DESC="Cold Cathode Enable",
                                      always_update=True,
                                      initial_value=highest(self.members,
                                                            "cchvPV"),
                                      on_update=lambda v: self.setCchv(v))

        self.switchingPV = builder.boolIn("SWITCHING", ZNAM="", ONAM="Switching",
                                          DESC="Switching",
                                          initial_value=0)

        # Which of the two on/off controls the screen shows - the :CCHV menu
        # button, or a button that asks first.  A member has it and a group did
        # not, so both widgets' visibility rules read a PV that was never
        # there and the control came up blank on any group screen.
        self.offWarningPV = builder.boolIn("OFFWARN", ZNAM="Warning off",
                                           ONAM="Warning on",
                                           DESC="Warning for OFF button",
                                           initial_value=0)

        self.statusPV = builder.mbbIn("STA", *GAUGE_STATES,
                                      DESC="Status", UNSV="MAJOR",
                                      initial_value=highest(self.members,
                                                            "statusPV"))

        self.pressurePV = builder.aIn("P", LOPR=1.0e-11, HOPR=0.01,
                                      EGU="mbar", PREC=11,
                                      DESC="Pressure",
                                      initial_value=highest(self.members,
                                                            "pressurePV"))

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-11.0, HOPR=-2.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=math.log10(
                                             self.pressurePV.get()))

        self.pressureMaxPV = builder.aIn("PMAX", LOPR=1.0e-11, HOPR=0.01,
                                         EGU="mbar", PREC=11,
                                         DESC="Max Pressure",
                                         initial_value=highest(self.members,
                                                               "pressureMaxPV"))

        self.pressureMinPV = builder.aIn("PMIN", LOPR=1.0e-11, HOPR=0.01,
                                         EGU="mbar", PREC=11,
                                         DESC="Min Pressure",
                                         initial_value=lowest(self.members,
                                                              "pressureMinPV"))

        # --- protection setpoint --------------------------------------------
        self.proSetpointPV = builder.aIn("PRO:SP", LOPR=1.3e-5, HOPR=1.0e-2,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Overpressure",
                                         initial_value=highest(self.members,
                                                               "proSetpointPV"))

        self.proSetSetpointPV = builder.aOut("PRO:SETSP",
                                             DRVL=1.3e-5, DRVH=1.0e-2,
                                             LOPR=1.3e-5, HOPR=1.0e-2,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Overpressure",
                                             initial_value=self.proSetpointPV.get(),
                                             on_update=self.setPro)

        # --- the two interlock relays ----------------------------------------
        self.valveInterlock = groupRelayRecords("RLY", 1, self.members,
                                                "Valve I/L", "valveInterlock")
        self.mpsInterlock = groupRelayRecords("RLA", 2, self.members,
                                              "MPS Interlock", "mpsInterlock")

        self.relayExistsPV = builder.aIn("RLA:EXISTS",
                                         DESC="Relay Exists",
                                         initial_value=1)

    # -- demands fanned out to the members ------------------------------------

    def setCchv(self, value):
        """Strike or drop every cold cathode underneath this group."""
        setDemand(self.cchvPV, 1 if value else 0)
        self.switchingPV.set(1)
        try:
            for member in self.members:
                member.setCchv(value)
        finally:
            self.switchingPV.set(0)

    def setPro(self, value):
        setDemand(self.proSetSetpointPV, value)
        for member in self.members:
            member.setPro(value)
        self.proSetpointPV.set(value)

    # -- readbacks ------------------------------------------------------------

    def publish(self):
        self.statusPV.set(highest(self.members, "statusPV"))
        self.pressurePV.set(highest(self.members, "pressurePV"))
        self.pressureLogPV.set(math.log10(self.pressurePV.get()))
        self.pressureMaxPV.set(highest(self.members, "pressureMaxPV"))
        self.pressureMinPV.set(lowest(self.members, "pressureMinPV"))
        self.proSetpointPV.set(highest(self.members, "proSetpointPV"))
        self.valveInterlock.publish()
        self.mpsInterlock.publish()


class pirgGroupRecord(deviceGroup):
    """Up to 8 Piranis, or groups of them - mks937aPirgGroup.template.

    :CTL: is the cold cathode enable setpoint, which is entered against the
    Pirani because the Pirani is the gauge that has to agree before the cold
    cathode is struck; on a 937B those records live under the Pirani's name but
    belong to the pair, and gaugeRecord hangs them on the Pirani for this group
    to find.  :RLY: is relay 1, the ion pump interlock - see relayOf.
    """

    def __init__(self, prefix, members, delay=0.0):
        super().__init__(prefix, members, delay=delay)

        self.statusPV = builder.mbbIn("STA", *GAUGE_STATES,
                                      DESC="Status", UNSV="MAJOR",
                                      initial_value=highest(self.members,
                                                            "statusPV"))

        self.pressurePV = builder.aIn("P", LOPR=0.001, HOPR=1000.0,
                                      EGU="mbar", PREC=1,
                                      DESC="Pressure",
                                      initial_value=highest(self.members,
                                                            "pressurePV"))

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-3.0, HOPR=3.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=math.log10(
                                             self.pressurePV.get()))

        # --- the cold cathode enable setpoint --------------------------------
        self.ctlSetpointPV = builder.aIn("CTL:SP", LOPR=2.7e-3, HOPR=9.5e-1,
                                         EGU="mbar", PREC=1,
                                         DESC="IMG Enable",
                                         initial_value=highest(self.members,
                                                               "ctlSetpointPV"))

        self.ctlSetSetpointPV = builder.aOut("CTL:SETSP",
                                             DRVL=2.7e-3, DRVH=9.5e-1,
                                             LOPR=2.7e-3, HOPR=9.5e-1,
                                             EGU="mbar", PREC=1,
                                             DESC="IMG Enable",
                                             initial_value=self.ctlSetpointPV.get(),
                                             on_update=self.setCtlSetpoint)

        self.ctlEnablePV = builder.mbbOut("CTL:ENABLE", *CC_CONTROL_MODES,
                                          DESC="Enable Control Setpoint",
                                          always_update=True,
                                          initial_value=CC_INTERLOCK_SAFE,
                                          on_update=self.setCtlEnable)

        # --- the ion pump interlock relay ------------------------------------
        self.ionPumpInterlock = groupRelayRecords("RLY", 1, self.members,
                                                  "Ion Pump I/L",
                                                  "ionPumpInterlock")

    # -- demands fanned out to the members ------------------------------------

    def setCtlSetpoint(self, value):
        setDemand(self.ctlSetSetpointPV, value)
        for member in self.members:
            member.setCtlSetpoint(value)
        self.ctlSetpointPV.set(value)

    def setCtlEnable(self, value):
        setDemand(self.ctlEnablePV, value)
        for member in self.members:
            member.setCtlEnable(value)

    # -- readbacks ------------------------------------------------------------

    def publish(self):
        self.statusPV.set(highest(self.members, "statusPV"))
        self.pressurePV.set(highest(self.members, "pressurePV"))
        self.pressureLogPV.set(math.log10(self.pressurePV.get()))
        self.ctlSetpointPV.set(highest(self.members, "ctlSetpointPV"))
        self.ionPumpInterlock.publish()


class gaugeGroupRecord(deviceGroup):
    """Up to 8 combination gauges, or groups - mks937aGaugeGroup.template.

    This is the group a vacuum space takes its pressure from, and it works in
    log space: the template's :PLOG is the `sel` over the members and :P is
    10^PLOG, so the pressure a space publishes has been round tripped through
    its logarithm exactly as the real one has.

    :SEL says which of a pair is currently believed, and the group takes the
    highest - IMG, then Pirani, then No Gauge - so a group reports the least
    trustworthy answer any of its members is giving.
    """

    def __init__(self, prefix, members, delay=0.0):
        super().__init__(prefix, members, delay=delay)

        self.selectPV = builder.aIn("SEL",
                                    DESC="Select Pressure",
                                    initial_value=highest(self.members,
                                                          "selectPV"))

        self.activeGaugePV = builder.mbbIn("GAUGE", *ACTIVE_GAUGES,
                                           DESC="Active Gauge", UNSV="MAJOR",
                                           initial_value=int(self.selectPV.get()))

        self.pressureLogPV = builder.aIn("PLOG", LOPR=-11.0, HOPR=3.0,
                                         EGU="log", PREC=3,
                                         DESC="log Pressure",
                                         initial_value=highest(self.members,
                                                               "pressureLogPV"))

        self.pressurePV = builder.aIn("P", LOPR=1.0e-11, HOPR=1000.0,
                                      EGU="mbar", PREC=11,
                                      DESC="Pressure",
                                      initial_value=10.0 ** self.pressureLogPV.get())

        self.noWritePV = builder.boolIn("ILKSETSP:NOWRITE",
                                        ZNAM="Allow", ONAM="Inhibit",
                                        DESC="ILK setpoint write status",
                                        initial_value=highest(self.members,
                                                              "noWritePV"))

    def publish(self):
        select = highest(self.members, "selectPV")
        self.selectPV.set(select)
        self.activeGaugePV.set(int(select))
        self.pressureLogPV.set(highest(self.members, "pressureLogPV"))
        self.pressurePV.set(10.0 ** self.pressureLogPV.get())
        self.noWritePV.set(highest(self.members, "noWritePV"))


class groupRelayRecords:
    """One interlock setpoint of a gauge group - the :RLY:, :RLA: or :RLB: set.

    A 937A gauge group publishes each interlock as a demand, an enable and a
    readback under a two or three letter prefix.  Each of those maps onto a
    numbered relay of the 937B gauges underneath, and a member that is itself a
    group forwards it on down through the same three records.
    """

    def __init__(self, name, number, members, desc, attribute):
        self.name = name
        self.number = number
        self.members = members
        # What a member that is itself a group calls this same interlock.  It
        # cannot be worked out from the name: an IMG group's :RLY: and a Pirani
        # group's :RLY: are different interlocks on different relays.
        self.attribute = attribute

        self.setpointPV = builder.aIn(f"{name}:SP",
                                      LOPR=2.7e-10, HOPR=1.2e-2,
                                      EGU="mbar", PREC=1,
                                      DESC=desc,
                                      initial_value=highest(self.relays(),
                                                            "setpointPV"))

        self.setSetpointPV = builder.aOut(f"{name}:SETSP",
                                          DRVL=2.7e-10, DRVH=1.2e-2,
                                          LOPR=2.7e-10, HOPR=1.2e-2,
                                          EGU="mbar", PREC=1,
                                          DESC=desc,
                                          initial_value=self.setpointPV.get(),
                                          on_update=self.setSetpoint)

        self.enablePV = builder.mbbOut(f"{name}:ENABLE", *ENABLE_MODES,
                                       DESC="Enable Relay Setpoint",
                                       always_update=True,
                                       initial_value=ENABLE_OPERATING,
                                       on_update=self.setEnable)

    def relays(self):
        """This interlock on each member, whatever the member is.

        A group member answers with its own records for this interlock; a
        gauge answers with the relay wired to it.
        """
        return [getattr(member, self.attribute, None) or
                relayOf(member, self.number)
                for member in self.members]

    def setSetpoint(self, value):
        for relay in self.relays():
            relay.setDemand(value)
        self.setpointPV.set(value)

    def setEnable(self, value):
        setDemand(self.enablePV, value)
        for relay in self.relays():
            relay.setEnable(value)

    def setDemand(self, value):
        """Forward a demand from the group above this one."""
        setDemand(self.setSetpointPV, value)
        self.setSetpoint(value)

    def publish(self):
        self.setpointPV.set(highest(self.relays(), "setpointPV"))


def offLevel(setpoint, requested):
    """The OFF setpoint a relay will actually use.

    The real template negotiates this through :SPOFFNOMINAL and :SELSPOFF: it
    takes whichever is higher of the requested level and 10% above the ON
    setpoint, so a relay always has some hysteresis and cannot chatter.
    """
    return max(setpoint * 1.1, requested)


def publishReading(gauge, status, reading):
    """Publish a gauge's status and pressure, freezing a bad reading.

    Anything above OK means the number is not a pressure the gauge can stand
    behind.  The real templates handle that by disabling the :P record through
    :PDIS with DISS set to INVALID, so :P holds whatever it last read and
    carries an INVALID severity - it does not go on quietly reporting a
    pressure the gauge is in no position to know.  Do the same here rather
    than publishing the live value with an alarm on it.
    """
    gauge.statusPV.set(status)
    gauge.pressureDisabledPV.set(1 if status > GaugeStatus.OK else 0)

    if status > GaugeStatus.OK:
        gauge.pressurePV.set(gauge.pressurePV.get(),
                             severity=alarm.INVALID_ALARM,
                             alarm=alarm.DISABLE_ALARM)
    else:
        gauge.pressurePV.set(reading)

    gauge.pressureLogPV.set(math.log10(gauge.pressurePV.get()))
    return gauge.pressurePV.get()
