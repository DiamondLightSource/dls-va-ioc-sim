# Build the records one builder XML declares, without generating a file.
#
# generate_ioc.py is the usual way in: it writes an instance out as Python, to
# be read, edited and run, which is what you want when the beam pipe has to be
# thought about or the XML has a nuance in it.  This is the other way - an
# instance that stays three lines long and re-reads its XML every time it
# starts.  Both go through builder_xml.parseXml, so neither can drift.

import asyncio
import logging

from softioc import asyncio_dispatcher, builder, softioc

from .builder_xml import attachLayout, parseXml
from .device_groups import orderedGroups
from .epics_ports import setPortDefaults
from .fe_seq_records import valveGroupRecord, valveRecord
from .gauge_records import (
    gaugeGroupRecord,
    gaugeSetRecord,
    imgGroupRecord,
    pirgGroupRecord,
)
from .ion_pump_records import ionPumpGroupRecord, ionPumpRecord, mpcRecord
from .rack_records import commonD2Record, commonRecord, plcInfoRecord
from .rga_records import rgaRecord
from .vacuum_space_records import spaceRecord

# How often the simulated devices recalculate their readbacks.  One second
# matches the SCAN rate the real templates poll their hardware at.
SIMULATION_PERIOD = 1.0

COMMON_CLASSES = {
    "common": commonRecord,
    "commonD2": commonD2Record,
}

GROUP_CLASSES = {
    "ionp": ionPumpGroupRecord,
    "gauge": gaugeGroupRecord,
    "img": imgGroupRecord,
    "pirg": pirgGroupRecord,
    "valve": valveGroupRecord,
}


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
        self.valves = {}
        self.rgas = {}
        self.plcs = {}
        self.racks = {}
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

        for prefix in declarations.valves:
            self.valves[prefix] = valveRecord(prefix)

        for rga in declarations.rgas:
            self.rgas[rga.prefix] = rgaRecord(rga.prefix)

        # The rack and the PLC: not vacuum devices, on no volume and on no
        # tick list, built because the screens read them.
        for plc in declarations.plcs:
            self.plcs[plc.prefix] = plcInfoRecord(plc.prefix)

        for common in declarations.commons:
            self.racks[common.dom] = COMMON_CLASSES[common.kind](common.dom)

        # declarations.groups is innermost first, which is what construction
        # needs - a group seeds its records from its members'.
        members = {"ionp": self.pumps, "gauge": self.gauges,
                   "img": self.imgs, "pirg": self.pirgs,
                   "valve": self.valves}
        for group in declarations.groups:
            own = members[group.kind]
            self.groups[group.prefix] = GROUP_CLASSES[group.kind](
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
        devices = self.tickList()

        # A generated instance settles its own ports in its header; this one
        # has no header to put them in, so it says so here.  Without this,
        # `dls-va-ioc-sim run` served a cell on whatever Channel Access
        # configuration it inherited - which on a machine at Diamond is the
        # real one.
        setPortDefaults()

        builder.LoadDatabase()
        dispatcher = asyncio_dispatcher.AsyncioDispatcher()
        # enable_pva=False - see epics_ports.  A PVXS server would otherwise
        # serve these same PVs on the standard pvAccess port.
        softioc.iocInit(dispatcher, enable_pva=False)

        async def simulate():
            # No arguments: the dispatcher hands extra ones to the coroutine
            # differently depending on the pythonSoftIOC version, and passing
            # none at all behaves the same on both.
            reported = set()
            while True:
                await asyncio.sleep(period)
                for device in devices:
                    try:
                        device.tick(period)
                    except Exception:
                        # A dispatched coroutine that raises is dropped
                        # without a word, which leaves the IOC up but every
                        # readback frozen.  Say so, once, and keep going.
                        if device.prefix not in reported:
                            reported.add(device.prefix)
                            logging.exception("Simulation failed for %s",
                                              device.prefix)

        dispatcher(simulate)

        if interactive:
            softioc.interactive_ioc(namespace or {})
        else:
            softioc.non_interactive_ioc()


def iocFromXml(path, cell=None, running=True):
    """Parse one builder XML and build everything it declares."""
    return parsedIoc(parseXml(path, cell=cell), running=running)
