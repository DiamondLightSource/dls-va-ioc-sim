# Simulation of the vacuum and personal safety equipment on a front end.
#
# Everything a PLC would decide on a real front end is decided here instead,
# since there is no PLC in the simulation.
#
# This file is the machine: what exists, what is wired to what, and the tick
# loop.  It is built in the order a builder XML is - devices, then the groups
# over them, then the spaces over the groups - with one thing in the middle
# that a builder XML has no way to say: the vacuum layout, which is what tells
# the simulation which devices share a length of beam pipe.

import asyncio
import logging

from softioc import softioc, builder, asyncio_dispatcher

from dls_va_ioc_sim.device_groups import orderedGroups
from dls_va_ioc_sim.fe_seq_records import fvalveRecord, valveGroupRecord, valveRecord
from dls_va_ioc_sim.gauge_records import (
    gaugeGroupRecord,
    gaugeSetRecord,
    imgGroupRecord,
    pirgGroupRecord,
)
from dls_va_ioc_sim.ion_pump_records import ionPumpGroupRecord, ionPumpRecord, mpcRecord
from dls_va_ioc_sim.vacuum_model import gate, vacuumLayout, vacuumVolume
from dls_va_ioc_sim.vacuum_space_records import spaceRecord

dispatcher = asyncio_dispatcher.AsyncioDispatcher()

# How often the simulated devices recalculate their readbacks.  One second
# matches the SCAN rate the real templates poll their hardware at.
SIMULATION_PERIOD = 1.0

noOfAbsorbers = 2
dom = "FE99B"

# ---------------------------------------------------------------------------
# Valves, absorbers and shutters
#
# Two gate valves in series along the pipe, then the things that move in the
# beam rather than in the pipe: two absorbers, two shutters and the fast valve
# that protects the ring.
# ---------------------------------------------------------------------------

v1 = valveRecord(f"{dom}-VA-VALVE-01")
v2 = valveRecord(f"{dom}-VA-VALVE-02")
absb1 = valveRecord(f"{dom}-RS-ABSB-01")
sht1 = valveRecord(f"{dom}-PS-SHTR-01")
sht2 = valveRecord(f"{dom}-PS-SHTR-02")
fv = fvalveRecord(f"{dom}-VA-FVALV-01")

if noOfAbsorbers > 1:
    absb2 = valveRecord(f"{dom}-RS-ABSB-02")
    absb2.addOpenRequirement(v2)
    absb2.addOpenRequirement(sht1)
    absb2.addOpenRequirement(sht2)

    absb1.addOpenRequirement(v1)
    absb1.addOpenRequirement(fv)
else:
    absb1.addOpenRequirement(v1)
    absb1.addOpenRequirement(fv)
    absb1.addOpenRequirement(sht1)
    absb1.addOpenRequirement(sht2)
    absb1.addOpenRequirement(v2)

# ---------------------------------------------------------------------------
# Ion pumps
#
# Three Digitel MPC controllers with the pumps spread two to a controller, the
# way a front end is usually laid out - c.f. pump_sub_a.xml in the FE support
# module.  The last pump is a smaller one on its own controller.
# ---------------------------------------------------------------------------

mpc1 = mpcRecord(f"{dom}-VA-MPC-01")
mpc2 = mpcRecord(f"{dom}-VA-MPC-02")
mpc3 = mpcRecord(f"{dom}-VA-MPC-03")

ionp1 = ionPumpRecord(mpc1, f"{dom}-VA-IONP-01", pump=1, size=500)
ionp2 = ionPumpRecord(mpc1, f"{dom}-VA-IONP-02", pump=2, size=500)
ionp3 = ionPumpRecord(mpc2, f"{dom}-VA-IONP-03", pump=1, size=500)
ionp4 = ionPumpRecord(mpc2, f"{dom}-VA-IONP-04", pump=2, size=500)
ionp5 = ionPumpRecord(mpc3, f"{dom}-VA-IONP-05", pump=1, size=150)

ionPumps = [ionp1, ionp2, ionp3, ionp4, ionp5]

# ---------------------------------------------------------------------------
# Gauges
#
# Two gauge sets, each an MKS 937B carrying two cold cathodes and two Piranis,
# following SR-VA's gaugeSet.xml.  Each controller serves two pairs, and the
# layout below puts one pair of each on the absorber vessel, the way the real
# controllers are shared between adjacent sections of beam pipe.
# ---------------------------------------------------------------------------

gaugeSets = [
    gaugeSetRecord(dom, "01", idA="01", idB="02"),
    gaugeSetRecord(dom, "02", idA="03", idB="04"),
]

gauge1, gauge2 = gaugeSets[0].gauges
gauge3, gauge4 = gaugeSets[1].gauges

# ---------------------------------------------------------------------------
# The vacuum layout
#
# None of this is served over Channel Access: it is the simulation's own model
# of the beam pipe, and no real front end has a record for how many litres a
# section holds.  See vacuumModel.
#
# Written in beam order, so a gate valve separates whatever is either side of
# it in the list and there is no second place for the topology to disagree with
# itself:
#
#   upstream -> VALVE-01 -> absorber vessel -> VALVE-02 -> downstream
#
# The fast valve has no gate entry.  It protects the ring rather than the front
# end, and there is nothing upstream of the first volume for it to isolate from;
# add a volume for the storage ring if that ever has to do something.
#
# Gas loads are chosen for what each volume reaches with all of its pumps
# running, and capacities for the length of pipe each covers.
# ---------------------------------------------------------------------------

upstream = vacuumVolume(
    "upstream",
    litres=30.0,
    gasLoad=5.0e-7,
    pumps=[f"{dom}-VA-IONP-01", f"{dom}-VA-IONP-02"],
    gauges=[f"{dom}-VA-GAUGE-01"],
)

absorber = vacuumVolume(
    "absorber vessel",
    litres=60.0,
    gasLoad=1.2e-6,
    basePressure=3.0e-10,
    pumps=[f"{dom}-VA-IONP-03", f"{dom}-VA-IONP-04"],
    gauges=[f"{dom}-VA-GAUGE-02", f"{dom}-VA-GAUGE-03"],
)

downstream = vacuumVolume(
    "downstream",
    litres=20.0,
    gasLoad=1.5e-7,
    pumps=[f"{dom}-VA-IONP-05"],
    gauges=[f"{dom}-VA-GAUGE-04"],
)

vacuum = vacuumLayout(
    upstream,
    gate(f"{dom}-VA-VALVE-01"),
    absorber,
    gate(f"{dom}-VA-VALVE-02"),
    downstream,
)

vacuum.attach(ionPumps + [gauge1, gauge2, gauge3, gauge4, v1, v2])

# ---------------------------------------------------------------------------
# Groups
#
# A group stands for several devices at once, and its members may themselves be
# groups - see deviceGroups.  Writing to GIONP-01:START starts both pumps on
# the upstream volume; writing to GIONP-04:START starts all five on the front
# end, because its members are the three groups below it.
#
# The valve groups are what a space controls its valves through, and both gate
# valves belong to the absorber vessel's group: either of them closing isolates
# it.
# ---------------------------------------------------------------------------

upstreamPumps = ionPumpGroupRecord(f"{dom}-VA-GIONP-01", [ionp1, ionp2])
absorberPumps = ionPumpGroupRecord(f"{dom}-VA-GIONP-02", [ionp3, ionp4])
downstreamPumps = ionPumpGroupRecord(f"{dom}-VA-GIONP-03", [ionp5])

upstreamGauges = gaugeGroupRecord(f"{dom}-VA-GGAUG-01", [gauge1])
absorberGauges = gaugeGroupRecord(f"{dom}-VA-GGAUG-02", [gauge2, gauge3])
downstreamGauges = gaugeGroupRecord(f"{dom}-VA-GGAUG-03", [gauge4])

upstreamImgs = imgGroupRecord(f"{dom}-VA-GIMG-01", [gauge1.img])
absorberImgs = imgGroupRecord(f"{dom}-VA-GIMG-02", [gauge2.img, gauge3.img])
downstreamImgs = imgGroupRecord(f"{dom}-VA-GIMG-03", [gauge4.img])

upstreamPirgs = pirgGroupRecord(f"{dom}-VA-GPIRG-01", [gauge1.pirg])
absorberPirgs = pirgGroupRecord(f"{dom}-VA-GPIRG-02", [gauge2.pirg, gauge3.pirg])
downstreamPirgs = pirgGroupRecord(f"{dom}-VA-GPIRG-03", [gauge4.pirg])

upstreamValves = valveGroupRecord(f"{dom}-VA-GVALV-01", [v1])
absorberValves = valveGroupRecord(f"{dom}-VA-GVALV-02", [v1, v2])
downstreamValves = valveGroupRecord(f"{dom}-VA-GVALV-03", [v2])

# The whole front end, as groups of groups.  SR21C-VA-IOC-01.xml builds its
# straight this way: SR21C-VA-GIONP-01's members are SR21A-VA-GIONP-01 and
# SR21S-VA-GIONP-01, and one write reaches all sixteen supplies.
frontEndPumps = ionPumpGroupRecord(
    f"{dom}-VA-GIONP-04", [upstreamPumps, absorberPumps, downstreamPumps]
)
frontEndGauges = gaugeGroupRecord(
    f"{dom}-VA-GGAUG-04", [upstreamGauges, absorberGauges, downstreamGauges]
)
frontEndImgs = imgGroupRecord(
    f"{dom}-VA-GIMG-04", [upstreamImgs, absorberImgs, downstreamImgs]
)
frontEndPirgs = pirgGroupRecord(
    f"{dom}-VA-GPIRG-04", [upstreamPirgs, absorberPirgs, downstreamPirgs]
)
frontEndValves = valveGroupRecord(
    f"{dom}-VA-GVALV-04", [upstreamValves, absorberValves, downstreamValves]
)

vacuumGroups = orderedGroups(
    [
        upstreamPumps,
        absorberPumps,
        downstreamPumps,
        frontEndPumps,
        upstreamGauges,
        absorberGauges,
        downstreamGauges,
        frontEndGauges,
        upstreamImgs,
        absorberImgs,
        downstreamImgs,
        frontEndImgs,
        upstreamPirgs,
        absorberPirgs,
        downstreamPirgs,
        frontEndPirgs,
        upstreamValves,
        absorberValves,
        downstreamValves,
        frontEndValves,
    ]
)

# ---------------------------------------------------------------------------
# Vacuum spaces
#
# The device an operator is shown: a pressure, a status lamp and the controls
# for a section of machine.  A space owns nothing - it reads its groups and
# writes to them - so what it covers is decided entirely by which groups it is
# given.  SPACE-04 covers the whole front end because its groups do.
# ---------------------------------------------------------------------------

vacuumSpaces = [
    spaceRecord(
        f"{dom}-VA-SPACE-01",
        ionp=upstreamPumps,
        gauge=upstreamGauges,
        img=upstreamImgs,
        pirg=upstreamPirgs,
        valve=upstreamValves,
    ),
    spaceRecord(
        f"{dom}-VA-SPACE-02",
        ionp=absorberPumps,
        gauge=absorberGauges,
        img=absorberImgs,
        pirg=absorberPirgs,
        valve=absorberValves,
    ),
    spaceRecord(
        f"{dom}-VA-SPACE-03",
        ionp=downstreamPumps,
        gauge=downstreamGauges,
        img=downstreamImgs,
        pirg=downstreamPirgs,
        valve=downstreamValves,
    ),
    spaceRecord(
        f"{dom}-VA-SPACE-04",
        ionp=frontEndPumps,
        gauge=frontEndGauges,
        img=frontEndImgs,
        pirg=frontEndPirgs,
        valve=frontEndValves,
    ),
]

# Everything the simulation has to step on a timer, in the order it has to be
# stepped in.  The vacuum layout comes first: it works out the pressures that
# the pumps and gauges then report in the same pass.  Groups read those, and
# a group of groups reads groups, which is what orderedGroups sorts out.
# Spaces read the finished groups and so come last.
simulatedDevices = [vacuum] + ionPumps + list(gaugeSets) + vacuumGroups + vacuumSpaces

# ---------------------------------------------------------------------------
# Beamline control
# ---------------------------------------------------------------------------

builder.SetDeviceName(f"{dom}-CS-BEAM-01")
builder.mbbOut(
    "BLCON", "Open", "Close", "Abort", "Unknown", always_update=True, initial_value=3
)

builder.mbbOut("STA", "Fault", "Open", "Opening", "Closed", "Closing")


# Boilerplate get the IOC started
builder.LoadDatabase()
softioc.iocInit(dispatcher)


async def simulate():
    """Step every simulated device on a fixed period, for as long as we run.

    The devices are taken from the module rather than passed in as arguments:
    the dispatcher hands extra arguments to the coroutine differently
    depending on the pythonSoftIOC version (a single tuple in 4.7, varargs in
    4.0), and passing none at all behaves the same on both.
    """
    reported = set()
    while True:
        await asyncio.sleep(SIMULATION_PERIOD)
        for device in simulatedDevices:
            try:
                device.tick(SIMULATION_PERIOD)
            except Exception:
                # A dispatched coroutine that raises is dropped without a
                # word, which leaves the IOC up but every readback frozen.
                # Say so, once per device, and keep the others going.
                if device.prefix not in reported:
                    reported.add(device.prefix)
                    logging.exception("Simulation failed for %s", device.prefix)


dispatcher(simulate)

# Finally leave the IOC running with an interactive shell.  The volumes are in
# scope there, which is where a leak is sprung now that none of the simulation
# is served:
#
#   >>> absorber.gasLoad = 1.0e-4       a leak the pumps have to hold against
#   >>> absorber.forcedPressure = 1e-3  pin it, and its group with it
#   >>> absorber.forcedPressure = None  back to the model
softioc.interactive_ioc(globals())
