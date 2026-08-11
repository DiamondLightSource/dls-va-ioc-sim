"""Valves, absorbers, shutters and the interlock chain between them.

    dlsPLC_vacValve.vdb      -> valveRecord       -- one valve
    dlsPLC_fastValve.vdb     -> fvalveRecord      -- one fast valve
    dlsPLC_vacValveGroup.vdb -> valveGroupRecord  -- up to 8 of anything

Which volumes a gate valve separates is not settled here.  A valve knows how
to open and close and what its interlocks make of that; the vacuum layout is
what says which two lengths of pipe it stands between, so that the topology is
written down once rather than once per valve.  See vacuum_model.

The group is missing the records its template aggregates from PLC inputs this
simulation's valves do not have: :OPS, :ADB/:SETADB, :RAWAIRSTA, :ILK,
:INIILK, :RAWILK, :GILK, :IMGILK, :PIRGILK and the :ILK<n>/:GILK<n> bit
records.  A valve here has no air supply, no operation counter and no PLC
interlock word for them to be built from.
"""

from time import sleep

from softioc import builder

from .device_groups import deviceGroup, highest, lowest, setDemand


class valveRecord:
    # Works for: BEAM,ABSB,SHTR,V2,

    # The STA strings that mean the valve is off its seat and passing gas, for
    # isOpen().  Matched exactly rather than by looking for "Open" in the
    # string, which would catch "Opening" as well.
    OPEN_STATES = ("Open",)

    def __init__(self,prefix):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.openList = []
        self.observerList = []
        self.staVals = ["Fault","Open","Opening","Closed","Closing"]
        self.staPV = builder.mbbIn("STA",
                                "Fault",
                                "Open",
                                "Opening",
                                "Closed",
                                "Closing",initial_value = 3)

        self.ilkStaVals = ["Failed","Run Ilks Ok","OK","Disarmed"]
        self.ilkStaPV = builder.mbbIn("ILKSTA",
                                "Failed",
                                "Run Ilks Ok",
                                "OK",
                                "Disarmed",initial_value = 0)

        self.conVals = ["Open","Close","Reset"]
        self.conPV = builder.mbbOut("CON",
                                "Open",
                                "Close",
                                "Reset",
                                on_update=lambda v: self.setCon(v),
                                always_update=True)

        self.lastConPV = builder.mbbIn("LASTCON",
                                "Open",
                                "Close",
                                "Reset")
        self.modePV = builder.boolIn("MODE",
                                    "Operational",
                                    "Service")

        self.openingDelayPV = builder.aOut("OPEN_DELAY",initial_value = 0.5)
        self.closingDelayPV = builder.aOut("CLOSE_DELAY",initial_value = 0.5)


    def setCon(self,value):
        """:CON - open, close or reset this valve.

        A method rather than a record write so that a valve group can fan a
        demand out through it, leaving :CON showing what was asked for as the
        template's CA link does.  setDemand is what stops writing :CON here
        from calling this back for ever - see device_groups.
        """
        setDemand(self.conPV, value)
        if self.conVals[value] == "Open":
            self.open()
        if self.conVals[value] == "Close":
            self.close()
        if self.conVals[value] == "Reset":
            self.reset()
        self.lastConPV.set(value)


    def isOpen(self):
        """True while gas can cross this valve, joining the spaces either side.

        The opening and closing transitions are shorter than one step of the
        vacuum simulation, so they are not treated as open; only a valve that
        has arrived off its seat joins its spaces.
        """
        return self.staVals[self.staPV.get()] in self.OPEN_STATES

    def addOpenRequirement(self,pv):
        self.openList.append(pv)
        pv.addObserver(self)

    def addObserver(self,pv):
        self.observerList.append(pv)

    def closeObservingValves(self):
        for pv in self.observerList:
            pv.reactiveClose()

    def open(self):
        if (self.staVals[self.staPV.get()] != "Open"
                and self.ilkStaVals[self.ilkStaPV.get()] == "OK"):
            self.staPV.set(2)
            delay = self.openingDelayPV.get()
            sleep(delay)
            self.staPV.set(1)

    def close(self):
        self.closeObservingValves()
        if self.staVals[self.staPV.get()] != "Closed":
            self.staPV.set(4)
            delay = self.closingDelayPV.get()
            sleep(delay)
            self.staPV.set(3)

    def reactiveClose(self):
        self.closeObservingValves()
        if self.staVals[self.staPV.get()] != "Closed":
            self.ilkStaPV.set(0)
            self.staPV.set(4)
            sleep(0.5)
            self.staPV.set(3)

        # Make interlocks fail even if valve is aleady closed
        self.ilkStaPV.set(0)

    def reset(self):
        okToReset = True
        for pv in self.openList:
            if "Open" not in pv.staVals[pv.staPV.get()]:
                okToReset = False

        if okToReset:
            sleep(0.5)
            self.ilkStaPV.set(2)


class fvalveRecord(valveRecord):
    # A fast valve is open whether or not its trigger is armed, and arming is
    # what the rest of these states are about, so all three count as open.
    OPEN_STATES = ("Open Armed", "Open Disarmed", "Partially Armed")

    def __init__(self,prefix):
        builder.SetDeviceName(prefix)
        self.prefix = prefix
        self.openList = []
        self.observerList = []
        self.staVals = ["Fault", "Open Armed", "Opening", "Closed",
                        "Closing", "Open Disarmed", "Partially Armed"]
        self.staPV = builder.mbbIn("STA",
                                "Fault",
                                "Open Armed",
                                "Opening",
                                "Closed",
                                "Closing",
                                "Open Disarmed",
                                "Partially Armed",initial_value = 3)


        self.ilkStaVals = ["Failed","Run Ilks Ok","OK","Disarmed"]
        self.ilkStaPV = builder.mbbIn("ILKSTA",
                                "Failed",
                                "Run Ilks Ok",
                                "OK",
                                "Disarmed",initial_value = 0)

        self.conVals = ["Open","Close","Reset","Arm","Partially Arm"]
        self.conPV = builder.mbbOut("CON",
                                "Open",
                                "Close",
                                "Reset",
                                "Arm",
                                "Partially Arm",
                                on_update=lambda v: self.setCon(v),
                                always_update=True)

        self.lastConPV = builder.mbbIn("LASTCON",
                                "Open",
                                "Close",
                                "Reset",
                                "Arm",
                                "Partially Arm")

        self.modePV = builder.boolIn("MODE",
                                    "Operational",
                                    "Service")

        self.openingDelayPV = builder.aOut("OPEN_DELAY",initial_value = 0.5)
        self.closingDelayPV = builder.aOut("CLOSE_DELAY",initial_value = 0.5)


    def setCon(self,value):
        super().setCon(value)
        if self.conVals[value] == "Arm":
            self.arm()

    def open(self):
        if (self.staVals[self.staPV.get()] != "Open"
                and self.ilkStaVals[self.ilkStaPV.get()] == "OK"):
            self.staPV.set(2)
            delay = self.openingDelayPV.get()
            sleep(delay)
            self.staPV.set(5)

    def arm(self):
        if "Open" in self.staVals[self.staPV.get()]:
            self.staPV.set(1)


class valveGroupRecord(deviceGroup):
    """Up to 8 valves, or groups of valves - dlsPLC_vacValveGroup.vdb.

    Writing to :CON opens, closes or resets every valve underneath, through
    any member that is itself a group.  Closing a group closes its valves
    whatever their interlocks say, exactly as the template's :SEQCLOSE writes
    Close to all eight regardless.

    :STA is the template's :CALSTA, `A<C?A:(B>C?(B>D?A:B):C)` over the lowest
    status A, the highest B, Open C and Closing D.  Which is to say: a fault
    anywhere makes the group Fault, otherwise anything not yet open makes the
    group show that instead, and only a group whose valves are all open reads
    Open.  A group is as shut as its most shut valve, which is what a screen
    wants of it.
    """

    STA_VALS = ["Fault", "Open", "Opening", "Closed", "Closing"]
    CON_VALS = ["Open", "Close", "Reset"]

    def __init__(self, prefix, members, delay=0.0):
        super().__init__(prefix, members, delay=delay)

        self.staPV = builder.mbbIn("STA",
                                "Fault",
                                "Open",
                                "Opening",
                                "Closed",
                                "Closing",
                                DESC="Status",
                                initial_value=self.status())

        self.ilkStaPV = builder.mbbIn("ILKSTA",
                                "Failed",
                                "Run Ilks Ok",
                                "OK",
                                "Disarmed",
                                DESC="Interlock Status",
                                initial_value=lowest(self.members, "ilkStaPV"))

        self.conPV = builder.mbbOut("CON",
                                "Open",
                                "Close",
                                "Reset",
                                DESC="Control",
                                always_update=True,
                                on_update=lambda v: self.setCon(v))

        self.lastConPV = builder.mbbIn("LASTCON",
                                "Open",
                                "Close",
                                "Reset",
                                DESC="Control Readback")

        self.openingPV = builder.boolIn("OPENING", ZNAM="", ONAM="Opening",
                                        DESC="Opening Valves",
                                        initial_value=0)

        self.modePV = builder.boolIn("MODE", "Operational", "Service",
                                     DESC="Mode",
                                     initial_value=highest(self.members,
                                                           "modePV"))

    # -- demands fanned out to the members ------------------------------------

    def setCon(self, value):
        """Open, close or reset every valve underneath this group.

        The template staggers these by :SEQOPEN's DLY fields; see device_groups
        for why the stagger is not slept through here.  Each valve's own open()
        does still sleep for its OPEN_DELAY, so a group of several valves takes
        a moment to return.
        """
        setDemand(self.conPV, value)
        self.openingPV.set(1 if self.CON_VALS[value] == "Open" else 0)
        try:
            for member in self.members:
                member.setCon(value)
        finally:
            self.openingPV.set(0)
        self.lastConPV.set(value)

    # -- readbacks ------------------------------------------------------------

    def isOpen(self):
        """True while every valve underneath is off its seat.

        A group joins the volumes either side of it only when all of it is
        open, so it can stand in for a single valve in a vacuum layout.
        """
        return all(member.isOpen() for member in self.members)

    def status(self):
        """:CALSTA - see the class docstring for how this reads."""
        least = lowest(self.members, "staPV")
        furthest = highest(self.members, "staPV")
        if least < 1:
            return least
        if furthest > 1:
            return least if furthest > 4 else furthest
        return 1

    def publish(self):
        self.staPV.set(self.status())
        self.ilkStaPV.set(lowest(self.members, "ilkStaPV"))
        self.modePV.set(highest(self.members, "modePV"))
