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
# is one statement of what an XML means, and both go through it.  This module
# imports no record class and builds no record - what it hands back is plain
# data, and device_registry is where a tag is given its meaning.
#
# **What is parsed, and what it becomes, is device_registry.py.**  That table
# is read here rather than restated, so a template added there is parsed here
# with nothing to edit in this file.  The three shapes it distinguishes -
# devices that are one line of XML, groups over them, and the few that need a
# parser of their own - are the three shapes this module has parsers for.
#
# Everything else is ignored and counted, and `report()` says what was ignored
# and why.  That is deliberate: an XML carries terminal servers, autosave,
# the fast vacuum system and a great deal of PLC glue, none of which this
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
from dataclasses import dataclass, field

from .device_registry import (
    CONTROLLER_TAGS,
    DEVICE_BY_TAG,
    DEVICES,
    GAUGE_SET_TAGS,
    GROUP_BY_TAG,
    GROUP_KINDS,
    IGNORED_MODULES,
    IGNORED_TAGS,
    IMG_TAGS,
    MPC_TAGS,
    PIRG_TAGS,
    PUMP_TAGS,
    SPACE_TAGS,
    TRANSLATED_TAGS,
)

# A device name starts with a two or three letter machine area and a two digit
# cell - SR03S, FE03I, BL03I.  Only the digits are rewritten, so that a
# simulation of SR03C comes up as SR99C and cannot be mistaken for the real one.
CELL_PATTERN = re.compile(r"^([A-Z]{2,3})(\d{2})")

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


@dataclass
class controllerDeclaration:
    """A Digitel MPC or a QPC, and the builder name the XML refers to it by."""

    prefix: str
    builderName: str


@dataclass
class pumpDeclaration:
    """One ion pump supply: which controller, which slot, how big."""

    prefix: str
    controller: str                     # a controller prefix
    pump: int
    size: int
    strapping: int
    setpoints: dict                     # sp1on / sp1off / sp2on / sp2off


@dataclass
class gaugeSetDeclaration:
    """One 937B controller and the one or two gauge pairs fitted to it."""

    dom: str
    setNumber: str
    idA: str
    idB: str | None = None

    @property
    def prefix(self):
        return f"{self.dom}-VA-GCTLR-{self.setNumber}"

    def ids(self):
        return [id for id in (self.idA, self.idB) if id]

    def deviceNames(self, family):
        """GAUGE, IMG or PIRG names, in the order the controller makes them."""
        return [f"{self.dom}-VA-{family}-{id}" for id in self.ids()]


@dataclass
class deviceDeclaration:
    """One device that is one element of XML - a device_registry.deviceTemplate.

    Everything one of these carries is its name and which template declared it:
    a valve, an RGA head, the PLC behind an EtherIP port and a whole cell's
    rack are all one line of XML with one name in it, and the kind is what says
    which class builds it.  The name comes from whichever attribute the
    template names - `device` for almost everything, `dom` for a rack, which is
    the domain every device in that rack is named after.

    A template that needs more than a name needs a declaration of its own, the
    way an ion pump supply or a gauge set does.

    `arguments` are the template's macros, and only the ones the cell actually
    quoted: `commonD2.xml` writes its RGA power cycle lines through
    `$(straight1)` and friends, and a cell that names one is honoured while a
    cell that names none gets the record class's own defaults.
    """

    prefix: str
    kind: str
    arguments: dict = field(default_factory=dict)


@dataclass
class groupDeclaration:
    """One group: what kind, what is in it, and how long it staggers them."""

    prefix: str
    kind: str
    members: list                       # device or group prefixes
    delay: float


@dataclass
class spaceDeclaration:
    """One vacuum space and the five groups it reads and writes."""

    prefix: str
    groups: dict                        # kind -> group prefix


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
        self.devices = []               # deviceDeclaration, every simple kind
        self.groups = []                # groupDeclaration, innermost first
        self.spaces = []                # spaceDeclaration

        self.dropped = []               # (what, why)
        self.ignored = {}               # tag -> [count, reason]

    def __repr__(self):
        return (f"<xmlDeclarations {self.name}: {len(self.pumps)} pumps, "
                f"{len(self.gaugeNames())} gauge pairs, "
                f"{len(self.valves)} valves, "
                f"{len(self.devicesOfKind('rga'))} RGAs, "
                f"{len(self.groups)} groups, {len(self.spaces)} spaces>")

    # -- what the XML declared ------------------------------------------------

    def devicesOfKind(self, *kinds):
        """Every simple device of one or more registry kinds, in XML order."""
        wanted = set(kinds)
        return [device for device in self.devices if device.kind in wanted]

    @property
    def valves(self):
        """The valves, which the vacuum layout has to decide the topology of."""
        return self.devicesOfKind("valve")

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

    def deviceCounts(self):
        """How many of each labelled kind, in the order the registry lists them.

        Two kinds sharing a label are counted together, which is what the two
        rack files do: a cell has one rack whichever of them declared it.
        """
        counts = {}
        for template in DEVICES:
            counts.setdefault(template.label, 0)
            counts[template.label] += len(self.devicesOfKind(template.kind))
        return counts

    def report(self):
        """What this found, what it skipped, and what it did not recognise."""
        # The devices line is built from the registry rather than written out,
        # so a template added there is reported here with nothing to edit.
        devices = ", ".join(f"{count} {label}"
                            for label, count in self.deviceCounts().items())
        lines = [f"{self.name}  (from {self.source})",
                 f"  found    {len(self.pumps)} ion pumps on "
                 f"{len(self.controllers)} controllers",
                 f"           {len(self.gaugeNames())} gauge pairs on "
                 f"{len(self.gaugeSets)} controllers",
                 f"           {len(self.groups)} groups, "
                 f"{len(self.spaces)} spaces",
                 f"           {devices}"]
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
        if element.tag in MPC_TAGS:
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

    # Every template that is one element of XML and one device, off the
    # registry.  Adding one there is all it takes to be read here.
    for element in root:
        template = DEVICE_BY_TAG.get(element.tag)
        if template is not None:
            # Only the macros this cell actually quoted, so that a cell which
            # quotes none gets the record class's defaults rather than a set of
            # empty strings.  Not cell-renamed: a macro value is a domain
            # letter or a tag name, not a device name.
            macros = {name: element.get(name) for name in template.macros
                      if element.get(name)}
            declarations.devices.append(deviceDeclaration(
                device(element, template.attribute), template.kind, macros))

    _parseGroups(declarations, root, cell)
    _parseSpaces(declarations, root, cell, device)

    # TRANSLATED_TAGS is derived from the registry, so it cannot disagree with
    # what the branches above actually handle.  Written out by hand, it could:
    # a tag parsed but left out of the set was built *and* reported NOT
    # RECOGNISED, and one in the set with no branch behind it was dropped in
    # silence, which is the failure this whole report exists to prevent.
    for element in root:
        if element.tag in TRANSLATED_TAGS:
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
        if element.tag not in PUMP_TAGS:
            continue
        if element.tag.startswith("QPC"):
            # A QPC calls the same things by different names, and quotes one
            # setpoint pair rather than two.
            controllerName, pump = element.get("QPC"), element.get("SPLY")
            size = _number(element.get("SIZE"), 500.0)
            on = _number(element.get("spon"), 1.0e-7)
            off = _number(element.get("spoff"), 2.0e-7)
            setpoints = {"sp1on": on, "sp1off": off, "sp2on": on, "sp2off": off}
        else:
            controllerName, pump = element.get("MPC"), element.get("pump")
            size = _number(element.get("size"), 500.0)
            setpoints = {
                "sp1on": _number(element.get("sp1on"), 1.0e-7),
                "sp1off": _number(element.get("sp1off"), 2.0e-7),
                "sp2on": _number(element.get("sp2on"), 1.0e-7),
                "sp2off": _number(element.get("sp2off"), 2.0e-7)}

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
        template = GROUP_BY_TAG.get(element.tag)
        if template is None:
            continue
        prefix = renameCell(element.get("device", ""), cell)
        members = _distinct(
            renameCell(element.get(template.slotName(slot), ""), cell)
            for slot in range(1, 9))
        declared[prefix] = groupDeclaration(prefix, template.kind, members,
                                            _number(element.get("delay"), 0.0))
        order.append(prefix)

    # The devices a group of each kind can draw its members from.  The three
    # gauge kinds name the family the controller creates them under - a gauge
    # group's members are its GAUGEs, an img group's its IMGs.
    own = {"ionp": set(declarations.pumpNames()),
           "valve": {valve.prefix for valve in declarations.valves}}
    for kind in ("gauge", "img", "pirg"):
        own[kind] = {name for gaugeSet in declarations.gaugeSets
                     for name in gaugeSet.deviceNames(kind.upper())}

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
        if element.tag not in SPACE_TAGS:
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
