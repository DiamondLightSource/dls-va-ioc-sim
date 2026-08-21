"""Every builder template this framework simulates, in one table.

This is the one place a builder XML tag is given a meaning.  A tag is in
exactly one of four states, and this module is where each of them is decided:

    DEVICES, GROUPS, BESPOKE    translated - it is simulated, and this says how
    IGNORED_TAGS                known about and deliberately not built, with
                                the reason `report()` gives for it
    IGNORED_MODULES             a whole support module that is nothing to do
                                with vacuum
    anything else               NOT RECOGNISED, which is the thing worth
                                looking at

**Adding a template is one entry in DEVICES and the record class it names.**
That was six edits across four files before this table existed - the tag, a
declaration class, the parse branch, the "translated" set, the generated file's
import line, the generator's class-name lookup and the builder's class lookup -
and every one of them could be forgotten separately.  The last template added,
`SR-VA.commonD2`, cost 52 lines of that plumbing to deliver 31 lines of device.

A template names its class by module and class *as strings* rather than by
importing it.  That is deliberate, and it buys two things:

  * `builder_xml` holds the parse without importing a single record class,
    which is what stops the parse and the records disagreeing about what an
    XML means, and
  * `generate_ioc` writes a class name and an import line into the file it
    generates without importing softioc at all - and takes both off the same
    entry that `parsed_ioc` resolves to the real class, so the file it writes
    can no longer name a class the import block forgot.

The cost is that a typo in a name here is not a syntax error.  It is a test
error instead: tests/test_device_registry.py resolves every entry in the table,
so a name that does not exist fails at once and for every entry, rather than
when some cell that happens to declare that tag is next generated.

Three shapes of template, because there really are three:

    deviceTemplate   one element of XML, one device, named by one attribute.
                     Valves, RGAs, the PLC link, a cell's rack.  These are what
                     gets added, and they are entirely table-driven.
    groupTemplate    a group over devices of one kind, whose members come out
                     of eight numbered slots and may themselves be groups.
    bespokeTemplate  ion pumps, gauge pairs and spaces, which need a parser of
                     their own - a supply has to be matched to the controller
                     it is declared against by *builder* name, an IMG and a
                     PIRG have to be put back together into the pair they are
                     two halves of, and a space has to check that its five
                     groups were all built.  The entry carries the tags and the
                     class, so the tags count as translated and the class is
                     imported like any other; the parsing stays in builder_xml
                     and the emission stays in generate_ioc.
"""

from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class templateBase:
    """What every simulated template has: the tags, and the class to build."""

    tags: tuple[str, ...]
    kind: str
    module: str
    className: str

    @property
    def cls(self):
        """The record class itself, imported on demand.

        Only `parsed_ioc` needs this - it builds the records directly.  The
        generator wants the *name* and the import line, which are `className`
        and `module` above, and it gets those without importing softioc.
        """
        return getattr(import_module(f".{self.module}", __package__), self.className)


@dataclass(frozen=True)
class deviceTemplate(templateBase):
    """One element of builder XML that is one simulated device.

    `attribute` is which XML attribute carries the name - `device` for almost
    everything, `dom` for a whole rack declared for a cell.  `section` is which
    banner of a generated instance it is written under, and `variableStem`
    overrides the generated variable name for a device whose name has no device
    family in it to build one from.

    `label` is what the parse report calls these, as a plural: it reads as
    "4 valves", and two kinds sharing a label - the two rack files do - are
    counted together under it.  Giving a template a label is what puts it in
    the report, which is the thing anyone reads first when a new cell is
    simulated for the first time.

    `macros` are further attributes to read off the element and hand to the
    class as keyword arguments, for a template whose device names are written
    through `$(macro)` rather than in full.  Only the ones a cell actually
    quotes are passed, so the class's own defaults stand for every cell that
    does not override them - which is what a `$(name=default)` in a template
    means, and how the builder class the XML expands through will do it.  The
    values are macro text and are *not* cell-renamed: they are domain letters
    and the like, not device names.
    """

    attribute: str = "device"
    section: str = "devices"
    variableStem: str | None = None
    label: str = "devices"
    macros: tuple[str, ...] = ()


@dataclass(frozen=True)
class groupTemplate(templateBase):
    """A group over devices of one kind, or over other groups of that kind.

    `slot` is the prefix of the eight numbered member attributes the XML
    declares - `ionp1` .. `ionp8` - and defaults to the kind, which is what
    every group has used so far.  `via` is set only where a group's members are
    reached *through* another device: an IMG group takes the `.img` of each
    gauge pair, because the pair is what this framework builds and the two
    sensors hang off it.
    """

    slot: str = ""
    via: str | None = None

    def slotName(self, number):
        return f"{self.slot or self.kind}{number}"


@dataclass(frozen=True)
class bespokeTemplate(templateBase):
    """A template whose parsing or emission is hand written.

    Here for its tags and its class, and for the note that says why it is not
    one of the two above.  Nothing dispatches on it.
    """

    note: str = ""


# ---------------------------------------------------------------------------
# The templates that are one line of XML and one device
# ---------------------------------------------------------------------------
#
# In the order they are written into a generated instance.  Adding one here is
# all it takes for the parse to read it, the generator to write it out under
# the right banner with the right import, and `dls-va-ioc-sim run` to build it.

DEVICES = (
    deviceTemplate(
        tags=("dlsPLC.NX102_vacValveDebounce", "dlsPLC.vacValveDebounce"),
        kind="valve",
        module="fe_seq_records",
        className="valveRecord",
        section="valves",
        label="valves",
    ),
    deviceTemplate(
        tags=("rgamv2.rgamv2",),
        kind="rga",
        module="rga_records",
        className="rgaRecord",
        section="rgas",
        label="RGAs",
    ),
    deviceTemplate(
        tags=("ether_ip.EtherIPInit",),
        kind="plc",
        module="rack_records",
        className="plcInfoRecord",
        section="rack",
        label="PLCs",
    ),
    deviceTemplate(
        tags=("SR-VA.common",),
        kind="common",
        module="rack_records",
        className="commonRecord",
        # One line of XML for a whole cell's rack, so it is named by the domain
        # it covers rather than by a device - and there is no device family in
        # "SR03C" for the generator to build a variable name out of.
        attribute="dom",
        section="rack",
        variableStem="common",
        label="racks",
    ),
    deviceTemplate(
        tags=("SR-VA.commonD2",),
        kind="commonD2",
        module="rack_records",
        className="commonD2Record",
        attribute="dom",
        section="rack",
        variableStem="common",
        label="racks",
        # commonD2.xml writes its three RGA power cycle lines as
        # device="SR$(cell)$(straight1)-VA-RGA-01" and so on, where common.xml
        # writes S, A and A in full.  The cells written so far quote none of
        # the three, so they come from the builder class this expands through;
        # a cell that does quote one is honoured here.  See commonD2Record for
        # what they default to and why.
        macros=("straight1", "girder1", "girder2"),
    ),
)


# ---------------------------------------------------------------------------
# The groups
# ---------------------------------------------------------------------------
#
# mks937a and mks937b are listed together throughout - see builder_xml's note
# on why a 937A is simulated as a 937B.

GROUPS = (
    groupTemplate(
        tags=("digitelMpc.digitelMpcIonpGroup",),
        kind="ionp",
        module="ion_pump_records",
        className="ionPumpGroupRecord",
    ),
    groupTemplate(
        tags=("mks937a.mks937aGaugeGroup", "mks937b.mks937bGaugeGroup"),
        kind="gauge",
        module="gauge_records",
        className="gaugeGroupRecord",
    ),
    groupTemplate(
        tags=("mks937a.mks937aImgGroup", "mks937b.mks937bImgGroup"),
        kind="img",
        module="gauge_records",
        className="imgGroupRecord",
        # An IMG group's members are cold cathodes, and a cold cathode is
        # reached through the gauge pair it is half of.
        via="img",
    ),
    groupTemplate(
        tags=("mks937a.mks937aPirgGroup", "mks937b.mks937bPirgGroup"),
        kind="pirg",
        module="gauge_records",
        className="pirgGroupRecord",
        via="pirg",
    ),
    groupTemplate(
        tags=("dlsPLC.vacValveGroup",),
        kind="valve",
        module="fe_seq_records",
        className="valveGroupRecord",
    ),
)

# The five kinds a vacuum space reads and writes, in the order spaceRecord takes
# them.  Derived, so a group added above cannot be left out of it.
GROUP_KINDS = tuple(group.kind for group in GROUPS)


# ---------------------------------------------------------------------------
# The templates that need a parser of their own
# ---------------------------------------------------------------------------

# The tags each hand-written parser keys on.  Named, because builder_xml reads
# these rather than repeating the strings - so a tag listed as translated and a
# tag the parser actually looks for cannot be different tags.
MPC_TAGS = ("digitelMpc.digitelMpc", "QPC.digitelQpc")
# One supply channel on a controller, driving one ion pump.  The templates and
# the QPC's own `SPLY=` attribute call it a supply; everything this framework
# parses it into is called a pump - pumpDeclaration, declarations.pumps,
# _parsePumps, ionPumpRecord - so the tags are too.
PUMP_TAGS = ("digitelMpc.digitelMpcIonp", "QPC.digitelQpcIonp")
GAUGE_SET_TAGS = ("SR-VA.gaugeSet", "SR-VA.gaugeSetA")
CONTROLLER_TAGS = ("mks937a.mks937a", "mks937b.mks937b")
IMG_TAGS = ("mks937a.mks937aImg", "mks937b.mks937bImg")
PIRG_TAGS = ("mks937a.mks937aPirg", "mks937b.mks937bPirg")
SPACE_TAGS = ("vacuumSpace.spaceTemplate",)

BESPOKE = (
    bespokeTemplate(
        tags=MPC_TAGS,
        kind="controller",
        module="ion_pump_records",
        className="mpcRecord",
        note="a QPC names its device in QPC=, an MPC in device=, and the "
        "supplies below refer to either by its builder name",
    ),
    bespokeTemplate(
        tags=PUMP_TAGS,
        kind="pump",
        module="ion_pump_records",
        className="ionPumpRecord",
        note="has to be matched to its controller by builder name, and a QPC "
        "quotes one setpoint pair where an MPC quotes two",
    ),
    bespokeTemplate(
        tags=GAUGE_SET_TAGS + CONTROLLER_TAGS + IMG_TAGS + PIRG_TAGS,
        kind="gaugeSet",
        module="gauge_records",
        className="gaugeSetRecord",
        note="either declared whole, or as a controller with each sensor "
        "against it - in which case an IMG-31 and a PIRG-31 have to be put "
        "back together into GAUGE-31",
    ),
    bespokeTemplate(
        tags=SPACE_TAGS,
        kind="space",
        module="vacuum_space_records",
        className="spaceRecord",
        note="reads and writes five groups, and is dropped unless all five "
        "were built here",
    ),
)


# ---------------------------------------------------------------------------
# Known about and deliberately not built
# ---------------------------------------------------------------------------
#
# A builder XML carries terminal servers, autosave, the fast vacuum system and
# a great deal of PLC glue, none of which this framework has a class for and
# none of which a vacuum simulation needs.  Each one is counted and reported
# with the reason below, so a tag can never silently change what is built.

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
IGNORED_MODULES = (
    "ethercat",
    "ipac",
    "Hy8401ip",
    "Hy8414",
    "DLS8515",
    "mrfTiming",
    "TimingTemplates",
)


# ---------------------------------------------------------------------------
# Derived, so nothing above has to be restated
# ---------------------------------------------------------------------------
#
# TRANSLATED_TAGS used to be a set written out by hand beside the parse, and
# the two could disagree in both directions: a tag parsed but left out of the
# set was built *and* reported NOT RECOGNISED, and a tag in the set with no
# branch behind it was dropped with no report at all.  Deriving it means the
# parse and the report read the same table.

TEMPLATES = DEVICES + GROUPS + BESPOKE

DEVICE_BY_TAG = {tag: template for template in DEVICES for tag in template.tags}
DEVICE_BY_KIND = {template.kind: template for template in DEVICES}

GROUP_BY_TAG = {tag: template for template in GROUPS for tag in template.tags}
GROUP_BY_KIND = {template.kind: template for template in GROUPS}

TRANSLATED_TAGS = frozenset(tag for template in TEMPLATES for tag in template.tags)


@dataclass(frozen=True)
class deviceSection:
    """One banner of a generated instance, and the devices written under it.

    The prose is the generated file's own documentation, so it lives beside the
    templates it describes rather than in the generator: a template that needs
    a banner of its own brings the words for it.  `variable` is the list the
    devices are collected into afterwards, where anything later needs one.
    """

    name: str
    banner: str
    variable: str | None = None

    @property
    def templates(self):
        return tuple(device for device in DEVICES if device.section == self.name)


def importsFor(templates):
    """(module, class names) for these templates, one entry per module.

    What a generated instance has to import to build them.  Sorted throughout,
    so generating twice gives the same file - the byte-for-byte diff this
    framework is verified with depends on it.
    """
    byModule = {}
    for template in templates:
        byModule.setdefault(template.module, set()).add(template.className)
    return [
        (module, tuple(sorted(names))) for module, names in sorted(byModule.items())
    ]
