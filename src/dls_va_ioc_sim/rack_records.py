"""The rack and the PLC a cell's IOC carries beside the vacuum itself.

    ether_ip_plcInfo.template          -> plcInfoRecord
    dlsPLC NX102_powerSupply.template  -> powerSupplyRecord
    userIO.bi, as SR-VA declares it    -> healthyRecord
    SR-VA.common (etc/makeIocs/common.xml)
                                       -> commonRecord, all of the above for
                                          one cell, in the same devices

None of it is a vacuum device.  A fan, a 24 V supply and the PLC's own health
neither hold gas nor pump it, so nothing here goes on the vacuum layout or the
tick list, and nothing here reads a volume.  It exists because the screens do:
a cell overview shows the rack next to the vacuum, and an unserved PV reads as
a broken screen rather than as a simulation that stops short of the rack.

**Every value is set once, at construction, and stays there.** These are
readbacks off a PLC that is not here, and there is nothing in the simulation
that would move them - a fan either spins or it does not, and a 24 V supply
that never sags is a better lie than one that wanders for no reason.  If one of
them ever has to move, give the class a `tick()` and put it on the instance's
tick list; nothing else here would change.

The one exception is `:PCURRENT:RESET`, which is a button on the power supply
screen: it is a real demand, so it does the one thing it says.
"""

from softioc import builder

from .rga_records import rgaPowerCycleRecord

# A userIO.bi in this state reads HEALTHY rather than FAIL - see healthyRecord.
HEALTHY = 1

# What one 24 V rack supply is published as.  Nothing works these out and
# nothing moves them; they are here to be a believable set of numbers on the
# power supply screen rather than a row of zeroes.  They do hang together: a
# supply 13% through its life has 8.7 of its 10 years left, and has been on
# for the 30 days since the rack was last powered down.
NOMINAL_VOLTAGE = 24.0
NOMINAL_CURRENT = 2.4
NOMINAL_PEAK_CURRENT = 3.1
NOMINAL_YEARS_LEFT = 8.7
NOMINAL_PERCENT_LEFT = 87.0
NOMINAL_TOTAL_RUNTIME = 11400.0  # hours
NOMINAL_CONTINUOUS_RUNTIME = 43200.0  # minutes, 30 days
NOMINAL_TEMPERATURE = 32.0

# What SR-VA.common puts in every cell, as etc/makeIocs/common.xml declares it.
# The numbers are the rack numbers on the screens, and the RGA names are the
# three heads the PLC has a power cycle line to.
RACK_FANS = (
    ("FANC-01", "Rack 01 Fan Status"),
    ("FANC-02", "Rack 02 Fan Status"),
    ("FANC-03", "Rack 03 Fan Status"),
)
DUPLEX_SUPPLIES = (
    ("PSU-01", "Rack 01 Duplex PSU Status"),
    ("PSU-02", "Rack 02 Duplex PSU Status"),
)
POWER_CYCLED_RGAS = ("S-VA-RGA-01", "A-VA-RGA-01", "A-VA-RGA-02")


class plcInfoRecord:
    """What the EtherIP driver says about the PLC - ether_ip_plcInfo.template.

    Declared by `ether_ip.EtherIPInit`, one per PLC, on the valve controller's
    own device name.  On the real IOC all three records are pseudo-tags the
    driver answers itself rather than anything in the PLC's program, so they
    say whether the *link* is up: a screen showing NotHealthy is saying the
    IOC has lost the PLC, which is exactly what a simulation cannot have
    happen, so it comes up Healthy and stays there.
    """

    def __init__(self, prefix, name="NX102", status="Run"):
        builder.SetDeviceName(prefix)
        self.prefix = prefix

        self.plcNamePV = builder.stringIn(
            "PLCNAME", DESC="Read PLC Name", initial_value=name
        )

        self.plcStatusPV = builder.stringIn(
            "PLCSTATUS", DESC="Read PLC Status", initial_value=status
        )

        # ZNAM/ZSV are the template's own defaults: not healthy is a MAJOR
        # alarm, which is what colours the link red on a cell overview.
        self.healthyPV = builder.boolIn(
            "PLCHEALTHY",
            ZNAM="NotHealthy",
            ZSV="MAJOR",
            ONAM="Healthy",
            OSV="NO_ALARM",
            DESC="Read PLC Healthy Flag",
            initial_value=HEALTHY,
        )


class powerSupplyRecord:
    """One 24 V supply in a rack - dlsPLC's NX102_powerSupply.template.

    The device name carries the supply's number within its duplex pair, as the
    XML declares it: SR03C-VA-PSU-01:1 and :2 are the two halves of rack 1's
    pair, and the pair's own :STA is a healthyRecord beside them.

    The template scales the PLC's integers with ASLO, so what is published is
    already in volts, amps and degrees; there is no raw value here for the
    same reason there is no PLC.
    """

    def __init__(
        self,
        prefix,
        voltage=NOMINAL_VOLTAGE,
        current=NOMINAL_CURRENT,
        temperature=NOMINAL_TEMPERATURE,
    ):
        builder.SetDeviceName(prefix)
        self.prefix = prefix

        self.voltagePV = builder.aIn(
            "VOLTAGE",
            EGU="V",
            PREC=2,
            DESC="EtherIP Power Supply Voltage",
            initial_value=voltage,
        )

        self.currentPV = builder.aIn(
            "CURRENT",
            EGU="A",
            PREC=2,
            DESC="EtherIP Power Supply Current",
            initial_value=current,
        )

        self.yearsLeftPV = builder.aIn(
            "YEARS:LEFT",
            EGU="Yrs",
            PREC=1,
            DESC="EtherIP Power Supply Years Left",
            initial_value=NOMINAL_YEARS_LEFT,
        )

        self.percentLeftPV = builder.aIn(
            "PERCENT:LEFT",
            EGU="%",
            PREC=1,
            DESC="EtherIP Power Supply Percent Left",
            initial_value=NOMINAL_PERCENT_LEFT,
        )

        self.peakCurrentPV = builder.aIn(
            "PCURRENT",
            EGU="A",
            PREC=2,
            DESC="EtherIP Power Supply Peak Current",
            initial_value=NOMINAL_PEAK_CURRENT,
        )

        # The one demand in this module.  A dead button is a worse lie than no
        # button, and what the real one does is drop the peak back onto the
        # current the supply is drawing now.
        self.resetPeakCurrentPV = builder.aOut(
            "PCURRENT:RESET",
            always_update=True,
            initial_value=0,
            on_update=self.resetPeakCurrent,
        )

        self.totalRuntimePV = builder.aIn(
            "TOTAL:RUNTIME",
            EGU="hrs",
            DESC="EtherIP Power Supply Total Run Time",
            initial_value=NOMINAL_TOTAL_RUNTIME,
        )

        self.continuousRuntimePV = builder.aIn(
            "CONT:RUNTIME",
            EGU="mins",
            DESC="EtherIP Power Supply Continuous Runtime",
            initial_value=NOMINAL_CONTINUOUS_RUNTIME,
        )

        # No EGU: the template gives it none, and a screen that expects one is
        # a bug in the template rather than something to fix here.
        self.temperaturePV = builder.aIn(
            "TEMPERATURE",
            PREC=1,
            DESC="EtherIP Power Supply Temperature",
            initial_value=temperature,
        )

    def resetPeakCurrent(self, value):
        self.peakCurrentPV.set(self.currentPV.get())


class healthyRecord:
    """A rack thing that is either HEALTHY or FAIL - a userIO.bi.

    SR-VA declares five of them per cell, all in the same shape: a bi on
    `<device>:STA` reading one bit of the PLC's digital input word, FAIL being
    a MAJOR alarm.  The bit is what a simulation has nothing behind, so the
    record is built without the input link and holds the state it was given.
    """

    def __init__(self, prefix, description, state=HEALTHY):
        builder.SetDeviceName(prefix)
        self.prefix = prefix

        self.statusPV = builder.boolIn(
            "STA",
            ZNAM="FAIL",
            ZSV="MAJOR",
            ONAM="HEALTHY",
            OSV="NO_ALARM",
            DESC=description,
            initial_value=state,
        )


class commonRecord:
    """One cell's rack and PLC housekeeping - SR-VA.common.

    `<SR-VA.common dom="SR03C"/>` is one line in a cell's builder XML and
    expands to the whole of `SR-VA/etc/makeIocs/common.xml`.  What is built
    here is the part of that expansion which is a PV an operator is shown:

        <dom>-VA-FANC-01:STA .. -03:STA     the three rack fans
        <dom>-VA-PSU-01:STA, -02:STA        each duplex pair, as a whole
        <dom>-VA-PSU-01:1, :2               the four supplies in those pairs,
        <dom>-VA-PSU-02:1, :2               with voltage, current and the rest
        SR<cell>S-VA-RGA-01:PWRC            the RGA power cycle lines, which
        SR<cell>A-VA-RGA-01:PWRC            the PLC drives and which are
        SR<cell>A-VA-RGA-02:PWRC            named after the heads, not the cell

    The rest of common.xml is left out on purpose: the digital input words the
    fan and PSU bits are really read from, the terminal servers, the IR vacuum
    interface, the controller status and the IOC's own housekeeping are all
    either a link to hardware that is not here or glue between records that
    are.  What a screen shows is above; what makes it work on the real machine
    is not simulated.
    """

    def __init__(self, dom):
        self.prefix = dom
        self.dom = dom
        # SR03C -> 03.  The RGAs are in this cell's S and A domains rather
        # than in the C domain the IOC is named for, which is why common.xml
        # writes them out by cell rather than by dom.
        self.cell = "".join(character for character in dom if character.isdigit())

        self.fans = [
            healthyRecord(f"{dom}-VA-{device}", description)
            for device, description in RACK_FANS
        ]

        self.supplyPairs = [
            healthyRecord(f"{dom}-VA-{device}", description)
            for device, description in DUPLEX_SUPPLIES
        ]

        self.supplies = [
            powerSupplyRecord(f"{dom}-VA-{device}:{half}")
            for device, _ in DUPLEX_SUPPLIES
            for half in (1, 2)
        ]

        self.rgaPowerCycles = [
            rgaPowerCycleRecord(f"SR{self.cell}{suffix}")
            for suffix in POWER_CYCLED_RGAS
        ]
