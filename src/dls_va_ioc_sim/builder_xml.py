# Read the builder XML a real vacuum IOC is built from.
#
# This is the parse and nothing else: it turns an XML into a plain description
# of what the IOC contains - controllers, supplies, gauge pairs, valves, the
# tree of groups over them and the spaces over that - and holds no EPICS
# records at all.  Two things are built on it:
#
#   generate_ioc.py   writes a simulation instance out as Python, to be read,
#                    edited and run.  This is the usual way in.
#   iocFromXml()     builds the same devices directly, for an instance that
#                    would rather read its XML at start up than be generated.
#
# Keeping the parse free of records is what stops those two disagreeing: there
# is one statement of what an XML means, and both go through it.
#
# What is parsed, and what it becomes:
#
#   SR-VA.gaugeSet, SR-VA.gaugeSetA          gaugeSetRecord
#   mks937{a,b}.mks937{a,b}                  gaugeSetRecord, paired up from
#     + .mks937aImg + .mks937aPirg           the IMG and PIRG declarations
#   digitelMpc.digitelMpc                    mpcRecord
#   digitelMpc.digitelMpcIonp                ionPumpRecord
#   QPC.digitelQpc, QPC.digitelQpcIonp       mpcRecord, ionPumpRecord
#   dlsPLC.NX102_vacValveDebounce            valveRecord
#   dlsPLC.vacValveDebounce                  valveRecord
#   rgamv2.rgamv2                            rgaRecord, :STA only
#   ether_ip.EtherIPInit                     plcInfoRecord, the PLC's own health
#   SR-VA.common                             commonRecord, the cell's rack
#   SR-VA.commonD2                           commonD2Record, the same in D2
#   digitelMpc.digitelMpcIonpGroup           ionPumpGroupRecord
#   mks937{a,b}.mks937{a,b}GaugeGroup        gaugeGroupRecord
#   mks937{a,b}.mks937{a,b}ImgGroup          imgGroupRecord
#   mks937{a,b}.mks937{a,b}PirgGroup         pirgGroupRecord
#   dlsPLC.vacValveGroup                     valveGroupRecord
#   vacuumSpace.spaceTemplate                spaceRecord
#
# Everything else is ignored and counted, and `report()` says what was ignored
# and why.  That is deliberate: an XML carries terminal servers, autosave,
# RGAs, the fast vacuum system and a great deal of PLC glue, none of which this
# framework has a class for and none of which a vacuum simulation needs.  A tag
# this does not know about can never silently change what is built - it is
# either translated or listed, and one it has never seen says NOT RECOGNISED.
#
# Two standing approximations, both because they cost nothing here:
#
#   * mks937a is treated as mks937b.  The real difference is where a gauge's
#     interlock setpoints live - on the gauge for a 937A, on numbered relays
#     for a 937B - and the machine is going 937B everywhere.  So SR-VA's
#     gaugeSetA and gaugeSet build the same thing, and the mks937a and mks937b
#     group tags are the same group.
#   * A QPC is treated as a Digitel MPC.  It is a different controller with
#     the same job, and the simulation only cares that a supply of some size
#     is pumping on a volume.
#
# The vacuum layout is not here and cannot be.  Nothing in a builder XML says
# which valve stands between which two lengths of pipe, how many litres a
# section holds or what it outgasses - see the dls-vacuum-space-model skill.

import os
import re
import xml.etree.ElementTree as elementTree

# A device name starts with a two or three letter machine area and a two digit
# cell - SR03S, FE03I, BL03I.  Only the digits are rewritten, so that a
# simulation of SR03C comes up as SR99C and cannot be mistaken for the real one.
CELL_PATTERN = re.compile(r"^([A-Z]{2,3})(\d{2})")

# The tags each family is declared under.  mks937a and mks937b are listed
# together throughout: see the note at the top of the file.
GAUGE_SET_TAGS = ("SR-VA.gaugeSet", "SR-VA.gaugeSetA")
CONTROLLER_TAGS = ("mks937a.mks937a", "mks937b.mks937b")
IMG_TAGS = ("mks937a.mks937aImg", "mks937b.mks937bImg")
PIRG_TAGS = ("mks937a.mks937aPirg", "mks937b.mks937bPirg")
VALVE_TAGS = ("dlsPLC.NX102_vacValveDebounce", "dlsPLC.vacValveDebounce")

# A cell's rack, and which of SR-VA's two files declares it.  commonD2.xml is
# replacing common.xml cell by cell, so both have to be read for as long as
# both are in use; the kind is what the generator picks a class by.
COMMON_TAGS = {
    "SR-VA.common": "common",
    "SR-VA.commonD2": "commonD2",
}

# Group tag -> (kind, the prefix its member attributes use).  The kind is what
# the rest of this module and the generator index everything by.
GROUP_TAGS = {
    "digitelMpc.digitelMpcIonpGroup": ("ionp", "ionp"),
    "mks937a.mks937aGaugeGroup": ("gauge", "gauge"),
    "mks937b.mks937bGaugeGroup": ("gauge", "gauge"),
    "mks937a.mks937aImgGroup": ("img", "img"),
    "mks937b.mks937bImgGroup": ("img", "img"),
    "mks937a.mks937aPirgGroup": ("pirg", "pirg"),
    "mks937b.mks937bPirgGroup": ("pirg", "pirg"),
    "dlsPLC.vacValveGroup": ("valve", "valve"),
}

GROUP_KINDS = ("ionp", "gauge", "img", "pirg", "valve")

# Tags that are known about and deliberately not built, with the reason
# report() gives for each.  Anything not in here and not translated is
# reported as unrecognised, which is the thing worth looking at.
IGNORED_TAGS = {
    "rga.rga": "no RGA class",
    "rga.rgaGroup": "no RGA class",
    "mks937a.mks937aImgMean": "beam desorption average, not a space device",
    "mks937b.mks937bImgMean": "beam desorption average, not a space device",
    "mks937a.mks937aGaugeEGU": "an EGU conversion on a gauge already built",
    "mks937a.auto_mks937aPlogADC": "an EGU conversion on a gauge already built",
    # A gauge read straight off an ADC rather than off a controller, in the
    # oldest cells.  There is no IMG/PIRG pair behind it, so there is nothing
    # for gaugeRecord to combine - any group whose only member is one of these
    # is dropped, and says so.
    "mks937a.mks937aGauge": "an analogue input gauge, with no IMG/PIRG pair",
    "digitelMpc.digitelMpcTsp": "no TSP class",
    "digitelMpc.digitelMpcTspGroup": "no TSP class",
    "dlsPLC.fastVacuumMaster": "fast vacuum detection, no class",
    "dlsPLC.fastVacuumChannel": "fast vacuum detection, no class",
    "FastVacuum.Master16": "fast vacuum detection, no class",
    "FastVacuum.auto_Channel16": "fast vacuum detection, no class",
    "FastVacuum.auto_ChannelUn": "fast vacuum detection, no class",
    "dlsPLC.NX102_interlock": "PLC glue, no PLC here",
    "dlsPLC.NX102_readBool": "PLC glue, no PLC here",
    "dlsPLC.NX102_readReal": "PLC glue, no PLC here",
    "dlsPLC.NX102_digitalIn": "PLC glue, no PLC here",
    "dlsPLC.NX102_powerSupply": "PLC glue, no PLC here",
    "dlsPLC.NX102_controller_status": "PLC glue, no PLC here",
    "dlsPLC.NX102_IRVacuum": "PLC glue, no PLC here",
    "dlsPLC.read100": "PLC glue, no PLC here",
    "dlsPLC.auto_dlsPLC_CommsStatus": "PLC glue, no PLC here",
    "FINS.FINSUDPInit": "a link to hardware that is not here",
    "FINS.FINSHostlink": "a link to hardware that is not here",
    "FINS.FINSTemplate": "a link to hardware that is not here",
    "asyn.AsynIP": "a link to hardware that is not here",
    "asyn.AsynSerial": "a link to hardware that is not here",
    "terminalServer.Moxa": "a link to hardware that is not here",
    "SR-VA.auto_psu24vStatus": "IOC and rack housekeeping",
    "SR-VA.auto_ecatDuplexPSUStatus": "IOC and rack housekeeping",
    "rackFan.rackFan": "IOC and rack housekeeping",
    "userIO.bi": "IOC and rack housekeeping",
    "autosave.Autosave": "IOC and rack housekeeping",
    "pvlogging.PvLogging": "IOC and rack housekeeping",
    "devIocStats.devIocStatsHelper": "IOC and rack housekeeping",
    "IOCinfo.IOCinfo": "IOC and rack housekeeping",
    "EPICS_BASE.EpicsEnvSet": "IOC and rack housekeeping",
    "EPICS_BASE.StartupCommand": "IOC and rack housekeeping",
    "records.ao": "a field override on a record already built",
}

# Whole modules that are never anything to do with vacuum.  Matched on the part
# before the dot, so a new template from one of them is ignored quietly rather
# than turning up as unrecognised.
IGNORED_MODULES = ("ethercat", "ipac", "Hy8401ip", "Hy8414", "DLS8515",
                   "mrfTiming", "TimingTemplates")

# What a volume with no better information is given.  A pressure of 7e-10 mbar
# is where a storage ring arc sits, and gasLoad / speed is what sets it, so a
# volume's gas load is guessed from the pumping speed installed on it.  Litres
# are guessed from the number of supplies, which is the only measure of length
# an XML offers.  Both are wrong and both are meant to be edited.
NOMINAL_PRESSURE = 7.0e-10
LITRES_PER_PUMP = 2.5
MINIMUM_LITRES = 10.0


def renameCell(name, cell):
    """Move a device name into a cell that does not exist."""
    if cell is None or not name:
        return name
    return CELL_PATTERN.sub(lambda match: match.group(1) + cell, name)


def _distinct(names):
    """The XML pads every group to eight slots by repeating a member."""
    seen = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def _number(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class controllerDeclaration:
    """A Digitel MPC or a QPC, and the builder name the XML refers to it by."""

    def __init__(self, prefix, builderName):
        self.prefix = prefix
        self.builderName = builderName


class pumpDeclaration:
    """One ion pump supply: which controller, which slot, how big."""

    def __init__(self, prefix, controller, pump, size, strapping, setpoints):
        self.prefix = prefix
        self.controller = controller        # a controller prefix
        self.pump = pump
        self.size = size
        self.strapping = strapping
        self.setpoints = setpoints          # sp1on / sp1off / sp2on / sp2off


class gaugeSetDeclaration:
    """One 937B controller and the one or two gauge pairs fitted to it."""

    def __init__(self, dom, setNumber, idA, idB=None):
        self.dom = dom
        self.setNumber = setNumber
        self.idA = idA
        self.idB = idB

    @property
    def prefix(self):
        return f"{self.dom}-VA-GCTLR-{self.setNumber}"

    def ids(self):
        return [id for id in (self.idA, self.idB) if id]

    def deviceNames(self, family):
        """GAUGE, IMG or PIRG names, in the order the controller makes them."""
        return [f"{self.dom}-VA-{family}-{id}" for id in self.ids()]


class rgaDeclaration:
    """One RGA head - rgamv2.rgamv2, built as rgaRecord's :STA only."""

    def __init__(self, prefix):
        self.prefix = prefix


class plcDeclaration:
    """The PLC behind an EtherIP port - ether_ip.EtherIPInit.

    The port and the IP address it names are a link to hardware that is not
    here and are not kept; the device name is, because the driver publishes
    the link's own health on it and the screens read that.
    """

    def __init__(self, prefix):
        self.prefix = prefix


class commonDeclaration:
    """A cell's rack and PLC housekeeping - SR-VA.common or SR-VA.commonD2.

    One line of XML that expands to the whole of SR-VA's common.xml, so the
    domain is all it carries: every device in it is named after the cell.  The
    kind says which of the two files it was - they differ only in which RGA
    heads the PLC has a power cycle line to.
    """

    def __init__(self, dom, kind="common"):
        self.dom = dom
        self.kind = kind


class groupDeclaration:
    """One group: what kind, what is in it, and how long it staggers them."""

    def __init__(self, prefix, kind, members, delay):
        self.prefix = prefix
        self.kind = kind
        self.members = members              # device or group prefixes
        self.delay = delay


class spaceDeclaration:
    """One vacuum space and the five groups it reads and writes."""

    def __init__(self, prefix, groups):
        self.prefix = prefix
        self.groups = groups                # kind -> group prefix


class xmlDeclarations:
    """Everything one builder XML says that this framework can simulate.

    Plain data: no EPICS records, no softioc import, nothing to start.  Build
    it with parseXml, then either generate an instance from it or hand it to
    iocFromXml.
    """

    def __init__(self, name, source, cell=None):
        self.name = name
        self.source = source
        self.cell = cell

        self.controllers = []           # controllerDeclaration
        self.pumps = []                 # pumpDeclaration
        self.gaugeSets = []             # gaugeSetDeclaration
        self.valves = []                # prefixes
        self.rgas = []                  # rgaDeclaration
        self.plcs = []                  # plcDeclaration
        self.commons = []               # commonDeclaration
        self.groups = []                # groupDeclaration, innermost first
        self.spaces = []                # spaceDeclaration

        self.dropped = []               # (what, why)
        self.ignored = {}               # tag -> [count, reason]

    def __repr__(self):
        return (f"<xmlDeclarations {self.name}: {len(self.pumps)} pumps, "
                f"{len(self.gaugeNames())} gauge pairs, "
                f"{len(self.valves)} valves, {len(self.rgas)} RGAs, "
                f"{len(self.groups)} groups, {len(self.spaces)} spaces>")

    # -- what the XML declared ------------------------------------------------

    def pumpNames(self):
        return [pump.prefix for pump in self.pumps]

    def gaugeNames(self):
        return [name for gaugeSet in self.gaugeSets
                for name in gaugeSet.deviceNames("GAUGE")]

    def deviceNames(self):
        """Every pump and gauge, which is everything a volume can hold."""
        return self.pumpNames() + self.gaugeNames()

    def domains(self):
        """The device domains, in the order they first appear."""
        order = []
        for name in self.deviceNames():
            domain = name.split("-")[0]
            if domain not in order:
                order.append(domain)
        return order

    def volumeGuess(self, domain):
        """A first cut at one volume covering a whole domain.

        Not the beam pipe - the beam pipe is not in the XML.  This is only
        somewhere to start that runs and settles at a believable pressure, so
        that a generated instance can be run before it has been thought about.
        """
        pumps = [pump for pump in self.pumps
                 if pump.prefix.startswith(domain + "-")]
        gauges = [name for name in self.gaugeNames()
                  if name.startswith(domain + "-")]
        speed = sum(pump.size for pump in pumps)
        return {
            "domain": domain,
            "pumps": [pump.prefix for pump in pumps],
            "gauges": gauges,
            "litres": max(MINIMUM_LITRES, LITRES_PER_PUMP * len(pumps)),
            "gasLoad": (NOMINAL_PRESSURE * speed) if speed else 1.0e-7}

    # -- what was and was not translated -------------------------------------

    def unrecognised(self):
        return sorted(tag for tag, (_, reason) in self.ignored.items()
                      if reason == UNRECOGNISED)

    def report(self):
        """What this found, what it skipped, and what it did not recognise."""
        lines = [f"{self.name}  (from {self.source})",
                 f"  found    {len(self.pumps)} ion pumps on "
                 f"{len(self.controllers)} controllers",
                 f"           {len(self.gaugeNames())} gauge pairs on "
                 f"{len(self.gaugeSets)} controllers",
                 f"           {len(self.valves)} valves, "
                 f"{len(self.rgas)} RGAs, "
                 f"{len(self.groups)} groups, {len(self.spaces)} spaces",
                 f"           {len(self.plcs)} PLCs, "
                 f"{len(self.commons)} racks"]
        if self.dropped:
            lines.append("  dropped")
            for what, why in self.dropped:
                lines.append(f"           {what:<36} {why}")
        if self.ignored:
            lines.append("  ignored")
            for tag in sorted(self.ignored):
                count, reason = self.ignored[tag]
                lines.append(f"           {tag:<36} x{count:<4} {reason}")
        return "\n".join(lines)


UNRECOGNISED = "NOT RECOGNISED - look at this"


# ---------------------------------------------------------------------------
# The parse
# ---------------------------------------------------------------------------


def parseXml(path, cell=None):
    """Read one builder XML into an xmlDeclarations.

    cell="99" rewrites the two digit cell number in every device name, so a
    simulation of SR03C comes out as SR99C and cannot be taken for the real
    machine on a network it should not be on.

    The path may be a str or anything os.fspath accepts; the IOC's name comes
    from its filename, the way the real build does it.
    """
    path = os.fspath(path)
    root = elementTree.parse(path).getroot()
    name = renameCell(path.replace("\\", "/").split("/")[-1].split(".")[0],
                      cell)
    declarations = xmlDeclarations(name, path, cell=cell)

    def device(element, attribute="device"):
        return renameCell(element.get(attribute, ""), cell)

    def ignore(tag, reason):
        entry = declarations.ignored.setdefault(tag, [0, reason])
        entry[0] += 1

    # A builder XML refers to a controller by its *builder* name -
    # MPC="MPC_S_01" - not by its device name, so both have to be indexed
    # before the things hanging off them can be resolved.
    mpcByName = {}
    gaugeControllerByName = {}

    for element in root:
        if element.tag in ("digitelMpc.digitelMpc", "QPC.digitelQpc"):
            # A QPC names its device in QPC=, an MPC in device=.
            prefix = device(element,
                            "QPC" if element.tag.startswith("QPC") else "device")
            builderName = element.get("name", prefix)
            declarations.controllers.append(
                controllerDeclaration(prefix, builderName))
            mpcByName[builderName] = prefix
        elif element.tag in CONTROLLER_TAGS:
            gaugeControllerByName[element.get("name", "")] = device(element)

    _parsePumps(declarations, root, cell, mpcByName, device)
    _parseGauges(declarations, root, cell, gaugeControllerByName)

    for element in root:
        if element.tag in VALVE_TAGS:
            declarations.valves.append(device(element))
        elif element.tag == "rgamv2.rgamv2":
            declarations.rgas.append(rgaDeclaration(device(element)))
        elif element.tag == "ether_ip.EtherIPInit":
            declarations.plcs.append(plcDeclaration(device(element)))
        elif element.tag in COMMON_TAGS:
            declarations.commons.append(
                commonDeclaration(device(element, "dom"),
                                  kind=COMMON_TAGS[element.tag]))

    _parseGroups(declarations, root, cell)
    _parseSpaces(declarations, root, cell, device)

    translated = (set(GAUGE_SET_TAGS) | set(CONTROLLER_TAGS) | set(IMG_TAGS)
                  | set(PIRG_TAGS) | set(VALVE_TAGS) | set(GROUP_TAGS)
                  | {"digitelMpc.digitelMpc", "digitelMpc.digitelMpcIonp",
                     "QPC.digitelQpc", "QPC.digitelQpcIonp",
                     "vacuumSpace.spaceTemplate", "rgamv2.rgamv2",
                     "ether_ip.EtherIPInit"}
                  | set(COMMON_TAGS))
    for element in root:
        if element.tag in translated:
            continue
        if element.tag in IGNORED_TAGS:
            ignore(element.tag, IGNORED_TAGS[element.tag])
        elif element.tag.split(".")[0] in IGNORED_MODULES:
            ignore(element.tag, "nothing to do with vacuum")
        else:
            ignore(element.tag, UNRECOGNISED)

    return declarations


def _parsePumps(declarations, root, cell, mpcByName, device):
    """Ion pump supplies, in XML order.

    Order matters: a controller numbers its supplies as they are declared.
    """
    for element in root:
        if element.tag == "digitelMpc.digitelMpcIonp":
            controllerName, pump = element.get("MPC"), element.get("pump")
            size = _number(element.get("size"), 500.0)
            setpoints = {
                "sp1on": _number(element.get("sp1on"), 1.0e-7),
                "sp1off": _number(element.get("sp1off"), 2.0e-7),
                "sp2on": _number(element.get("sp2on"), 1.0e-7),
                "sp2off": _number(element.get("sp2off"), 2.0e-7)}
        elif element.tag == "QPC.digitelQpcIonp":
            # A QPC calls the same things by different names, and quotes one
            # setpoint pair rather than two.
            controllerName, pump = element.get("QPC"), element.get("SPLY")
            size = _number(element.get("SIZE"), 500.0)
            on = _number(element.get("spon"), 1.0e-7)
            off = _number(element.get("spoff"), 2.0e-7)
            setpoints = {"sp1on": on, "sp1off": off, "sp2on": on, "sp2off": off}
        else:
            continue

        controller = mpcByName.get(controllerName)
        if controller is None:
            declarations.dropped.append(
                (device(element),
                 f"on controller {controllerName}, which is not declared here"))
            continue

        declarations.pumps.append(pumpDeclaration(
            device(element), controller, int(pump or 1), int(size),
            int(_number(element.get("HV"), 7000)), setpoints))


def _parseGauges(declarations, root, cell, gaugeControllerByName):
    """Gauge pairs, from either of the two ways of declaring them."""
    for element in root:
        if element.tag in GAUGE_SET_TAGS and element.get("id_a"):
            declarations.gaugeSets.append(gaugeSetDeclaration(
                renameCell(element.get("dom", ""), cell),
                element.get("gc_no", "01"),
                element.get("id_a"), element.get("id_b")))

    _parseLooseGauges(declarations, root, cell, gaugeControllerByName)


def _parseLooseGauges(declarations, root, cell, gaugeControllerByName):
    """Put separate IMG and PIRG declarations back together into pairs.

    The older cells declare a controller and then each sensor against it by
    builder name, so which gauge is which pair has to come from the ids: an
    IMG-31 and a PIRG-31 are the two halves of GAUGE-31.  Channel order is
    kept, because relays are numbered across a controller in creation order.
    """
    sensors = {}        # controller builder name -> {id: [img, pirg, ch]}
    for element in root:
        if element.tag in IMG_TAGS:
            slot = 0
        elif element.tag in PIRG_TAGS:
            slot = 1
        else:
            continue
        builderName = element.get("GCTLR", "")
        prefix = renameCell(element.get("device", ""), cell)
        gaugeId = prefix.rsplit("-", 1)[-1]
        pair = sensors.setdefault(builderName, {}).setdefault(
            gaugeId, [None, None, _number(element.get("channel"), 9)])
        pair[slot] = prefix
        if slot == 0:
            pair[2] = _number(element.get("channel"), 9)

    for builderName, pairs in sensors.items():
        controller = gaugeControllerByName.get(builderName)
        if controller is None:
            declarations.dropped.append(
                (f"gauges on {builderName}",
                 "their controller is not declared here"))
            continue

        # Only pairs with both halves fitted: a lone IMG has no Pirani for the
        # gauge record to combine with.
        complete = sorted((pair[2], gaugeId)
                          for gaugeId, pair in pairs.items()
                          if pair[0] and pair[1])
        for gaugeId in sorted(set(pairs) - {found for _, found in complete}):
            declarations.dropped.append(
                (f"{controller} gauge {gaugeId}",
                 "declared without both an IMG and a PIRG"))

        if len(complete) > 2:
            for _, gaugeId in complete[2:]:
                declarations.dropped.append(
                    (f"{controller} gauge {gaugeId}",
                     "a 937B carries two pairs, this is a third"))
            complete = complete[:2]
        if not complete:
            continue

        dom, _, setNumber = controller.rpartition("-VA-GCTLR-")
        ids = [gaugeId for _, gaugeId in complete]
        declarations.gaugeSets.append(gaugeSetDeclaration(
            dom, setNumber, ids[0], ids[1] if len(ids) > 1 else None))


def _parseGroups(declarations, root, cell):
    """Resolve the group tree, innermost first.

    A group's members may be groups, and the XML declares them in no
    particular order, so they are sorted by dependency here - both because a
    group has to be *constructed* after its members and because a generated
    instance has to name them in that order too.
    """
    declared = {}       # prefix -> groupDeclaration, unresolved members
    order = []
    for element in root:
        if element.tag not in GROUP_TAGS:
            continue
        kind, attribute = GROUP_TAGS[element.tag]
        prefix = renameCell(element.get("device", ""), cell)
        members = _distinct(
            renameCell(element.get(f"{attribute}{slot}", ""), cell)
            for slot in range(1, 9))
        declared[prefix] = groupDeclaration(prefix, kind, members,
                                            _number(element.get("delay"), 0.0))
        order.append(prefix)

    # The devices a group of each kind can draw its members from.
    own = {"ionp": set(declarations.pumpNames()),
           "valve": set(declarations.valves),
           "gauge": set(), "img": set(), "pirg": set()}
    for gaugeSet in declarations.gaugeSets:
        own["gauge"].update(gaugeSet.deviceNames("GAUGE"))
        own["img"].update(gaugeSet.deviceNames("IMG"))
        own["pirg"].update(gaugeSet.deviceNames("PIRG"))

    resolved = {}
    building = set()

    def resolve(prefix):
        if prefix in resolved:
            return resolved[prefix]
        if prefix not in declared or prefix in building:
            return None
        building.add(prefix)

        group = declared[prefix]
        members = []
        for member in group.members:
            if member in own[group.kind] or resolve(member) is not None:
                members.append(member)
            else:
                declarations.dropped.append(
                    (f"{prefix} member {member}",
                     "not built here - it belongs to another IOC"))
        building.discard(prefix)

        if not members:
            declarations.dropped.append(
                (prefix, "none of its members are built here"))
            return None

        group.members = members
        resolved[prefix] = group
        declarations.groups.append(group)        # innermost first
        return group

    for prefix in order:
        resolve(prefix)


def _parseSpaces(declarations, root, cell, device):
    built = {group.prefix for group in declarations.groups}
    for element in root:
        if element.tag != "vacuumSpace.spaceTemplate":
            continue
        prefix = device(element)
        groups = {kind: renameCell(element.get(kind, ""), cell)
                  for kind in GROUP_KINDS}
        absent = sorted(kind for kind, group in groups.items()
                        if group not in built)
        if absent:
            declarations.dropped.append(
                (prefix, "its {} group is not built here".format(", ".join(absent))))
            continue
        declarations.spaces.append(spaceDeclaration(prefix, groups))


# ---------------------------------------------------------------------------
# Checking a layout against what was declared
# ---------------------------------------------------------------------------


def attachLayout(layout, devices, declared=None):
    """Resolve the layout's names, then check nothing has been left out.

    vacuumLayout.attach already refuses a name it cannot find.  This is the
    other direction, and it is the one that bites: a device the layout never
    mentions keeps volume = None, its first tick raises, the dispatcher
    swallows the exception and the *rest of that controller's* gauges stop
    updating with no visible symptom at all.
    """
    layout.attach(devices)

    if declared is None:
        declared = [device.prefix for device in devices
                    if hasattr(device, "volume")]

    placed = set()
    for volume in layout.volumes:
        placed.update(volume.pumpNames)
        placed.update(volume.gaugeNames)

    missing = sorted(set(declared) - placed)
    if missing:
        raise KeyError(
            "the vacuum layout leaves devices this IOC builds on no volume, "
            "which freezes their whole controller on the first tick: "
            + ", ".join(missing))
    return layout


# ---------------------------------------------------------------------------
# Building the records directly, for an instance that reads its own XML
# ---------------------------------------------------------------------------


def iocFromXml(path, cell=None, running=True):
    """Build every device, group and space one builder XML declares.

    generate_ioc.py is the usual way in - it writes the same thing out as
    Python, which can then be read and edited.  This is for an instance that
    would rather stay a three line file and re-read its XML at start up.
    """
    from .parsed_ioc import parsedIoc  # noqa: PLC0415  (optional import)
    return parsedIoc(parseXml(path, cell=cell), running=running)
