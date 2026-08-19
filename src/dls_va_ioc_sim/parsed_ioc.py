# Build the records one builder XML declares, without generating a file.
#
# generate_ioc.py is the usual way in: it writes an instance out as Python, to
# be read, edited and run, which is what you want when the beam pipe has to be
# thought about or the XML has a nuance in it.  This is the other way - an
# instance that stays three lines long and re-reads its XML every time it
# starts.  Both go through builder_xml.parseXml, so neither can drift.

from .builder_xml import attachLayout, parseXml
from .device_groups import orderedGroups
from .device_registry import DEVICE_BY_KIND, GROUP_BY_KIND
from .gauge_records import gaugeSetRecord
from .ion_pump_records import ionPumpRecord, mpcRecord
from .simulation_loop import SIMULATION_PERIOD, runSimulation
from .vacuum_space_records import spaceRecord


class parsedIoc:
    """The devices, groups and spaces of one builder XML, built.

    The vacuum layout is the caller's: hand it over with attach(), which
    resolves the layout's names and then checks that every pump and gauge is
    on some volume.
    """

    def __init__(self, declarations, running=True):
        self.declarations = declarations
        self.name = declarations.name
        self.layout = None

        self.mpcs = {}
        self.ionPumps = []
        self.pumps = {}
        self.gaugeSets = []
        self.gauges = {}
        self.imgs = {}
        self.pirgs = {}
        # Every simple device, by registry kind then by prefix: valves, RGA
        # heads, the PLC link, the cell's rack.  `devicesOfKind` below is how
        # anything gets at one, and a kind with none declared is an empty dict
        # rather than a missing key.
        self.devices = {template.kind: {} for template in DEVICE_BY_KIND.values()}
        self.groups = {}
        self.spaces = []

        for controller in declarations.controllers:
            self.mpcs[controller.prefix] = mpcRecord(controller.prefix)

        for pump in declarations.pumps:
            record = ionPumpRecord(
                self.mpcs[pump.controller], pump.prefix, pump=pump.pump,
                size=pump.size, strapping=pump.strapping, running=running,
                **pump.setpoints)
            self.ionPumps.append(record)
            self.pumps[record.prefix] = record

        for gaugeSet in declarations.gaugeSets:
            record = gaugeSetRecord(gaugeSet.dom, gaugeSet.setNumber,
                                    idA=gaugeSet.idA, idB=gaugeSet.idB)
            self.gaugeSets.append(record)
            for gauge in record.gauges:
                self.gauges[gauge.prefix] = gauge
                self.imgs[gauge.img.prefix] = gauge.img
                self.pirgs[gauge.pirg.prefix] = gauge.pirg

        # Every simple device the registry knows, built by the class its entry
        # names.  The valves are vacuum devices and go on the layout; the RGA
        # heads, the PLC link and the rack are not, and are built because the
        # screens read them.
        for declaration in declarations.devices:
            template = DEVICE_BY_KIND[declaration.kind]
            self.devices[declaration.kind][declaration.prefix] = template.cls(
                declaration.prefix, **declaration.arguments)

        # declarations.groups is innermost first, which is what construction
        # needs - a group seeds its records from its members'.
        members = {"ionp": self.pumps, "gauge": self.gauges,
                   "img": self.imgs, "pirg": self.pirgs,
                   "valve": self.valves}
        for group in declarations.groups:
            own = members[group.kind]
            self.groups[group.prefix] = GROUP_BY_KIND[group.kind].cls(
                group.prefix,
                [own.get(name) or self.groups[name] for name in group.members],
                delay=group.delay)

        for space in declarations.spaces:
            self.spaces.append(spaceRecord(
                space.prefix,
                **{kind: self.groups[name]
                   for kind, name in space.groups.items()}))

    def __repr__(self):
        return repr(self.declarations).replace("xmlDeclarations", "parsedIoc")

    def devicesOfKind(self, kind):
        """The built devices of one registry kind, by prefix."""
        return self.devices.get(kind, {})

    @property
    def valves(self):
        """The valves, which are the only simple device on the vacuum layout."""
        return self.devices["valve"]

    def report(self):
        return self.declarations.report()

    def layoutTemplate(self):
        """A starting point for the layout, as Python, one volume per domain.

        The source only - layoutSource also hands back the volume variables it
        named, which the generator needs and a caller printing this does not.
        """
        from .generate_ioc import layoutSource  # noqa: PLC0415
        source, _volumes = layoutSource(self.declarations)
        return source

    def attach(self, layout):
        self.layout = attachLayout(
            layout,
            self.ionPumps + list(self.gauges.values())
            + list(self.valves.values()),
            declared=self.declarations.deviceNames())
        return self.layout

    def tickList(self):
        """Everything to step, in the order it has to be stepped in.

        The layout first: it works out the pressures the pumps and gauges then
        report in the same pass.  Groups read those, a group of groups reads
        groups, and spaces read the finished groups.
        """
        if self.layout is None:
            raise ValueError(
                f"{self.name} has no vacuum layout - call attach() before running")
        return ([self.layout] + self.ionPumps + self.gaugeSets
                + orderedGroups(list(self.groups.values())) + self.spaces)

    def run(self, period=SIMULATION_PERIOD, interactive=True, namespace=None):
        """Load the database, start the IOC and step it forever.

        Either way this blocks: interactive drops into the IOC shell, where
        the volumes are in scope and a leak can be sprung by hand, and
        non-interactive just serves - which is what a container wants, and
        what stops the process exiting the moment it has come up.
        """
        # The same loop a generated instance ends with - see simulation_loop.
        # It was written out twice, here and inside generate_ioc's FOOTER as a
        # string, and the two had to be kept in step by hand.
        runSimulation(self.tickList(), period=period, interactive=interactive,
                      namespace=namespace)


def iocFromXml(path, cell=None, running=True):
    """Parse one builder XML and build everything it declares."""
    return parsedIoc(parseXml(path, cell=cell), running=running)
