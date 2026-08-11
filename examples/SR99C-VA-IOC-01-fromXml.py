# Simulation of the vacuum in one storage ring cell, built from the XML.
#
# The same machine as SR99C-VA-IOC-01.py, and the same PV interface, but the
# devices, groups and spaces are read out of the builder XML the real IOC is
# built from instead of being written out by hand.  See builderXml.
#
# What is left is the one thing the XML cannot say: the beam pipe.  Nothing in
# a builder XML says which valve stands between which two lengths of pipe, how
# many litres a section holds or what it outgasses, so that stays here, written
# once, in beam order.
#
# ioc.report() at startup says what was translated, what was dropped and what
# was not recognised - read it the first time an XML is simulated.

from dls_va_ioc_sim.builder_xml import iocFromXml
from dls_va_ioc_sim.vacuum_model import gate, vacuumLayout, vacuumVolume

# cell="99" rewrites SR03 to SR99 in every device name, so this cannot be
# mistaken for the live storage ring vacuum on a network it should not be on.
XML = "/dls_sw/work/R3.14.12.7/support/jjc62351/support/SR-BUILDER/etc/makeIocs/SR03C-VA-IOC-01.xml"

ioc = iocFromXml(XML, cell="99")
print(ioc.report())

# ---------------------------------------------------------------------------
# The vacuum layout - the hand-written part
#
# *** Not in the XML, and it cannot be. ***  A space is an operational
# grouping; a volume is a length of pipe.  What follows is a plausible cell:
# the straight's valve group contains VALVE-01 alone, so that is what isolates
# it, and everything else in the cell is on the arc.
#
#   straight -> V1 -> arc
#
# VALVE-02, -03 and -04 branch off the arc towards the front ends.  A branch
# valve takes a beamline off and leaves the ring's own gas where it was, so it
# is not a gate - a gate splits the volumes either side of it, and these split
# nothing.  The front end beyond one belongs to another IOC, so there is not
# even a volume there to isolate from.
#
# Gas loads are chosen for where each volume settles with all of its pumps
# running, gasLoad / speed: the arc's sixteen supplies come to 6000 l/s, so
# 4.2e-6 over that sits it at about 7e-10 mbar.
#
# Every pump and gauge the XML declares has to appear on some volume - a gauge
# left off has no pressure to read, and ioc.attach() refuses rather than
# letting its whole controller freeze on the first tick.  ioc.layoutTemplate()
# prints them all, by domain, as a starting point.
# ---------------------------------------------------------------------------

straight = vacuumVolume(
    "straight",
    litres=10.0,
    gasLoad=3.0e-7,
    basePressure=2.0e-10,
    pumps=["SR99S-VA-IONP-01", "SR99S-VA-IONP-03"],
    gauges=["SR99S-VA-GAUGE-01", "SR99S-VA-GAUGE-02"],
)

arc = vacuumVolume(
    "arc",
    litres=40.0,
    gasLoad=4.2e-6,
    basePressure=3.0e-10,
    pumps=["SR99A-VA-IONP-%02d" % n for n in range(1, 17)],
    gauges=[
        "SR99A-VA-GAUGE-01",
        "SR99A-VA-GAUGE-02",
        "SR99A-VA-GAUGE-03",
        "SR99A-VA-GAUGE-04",
        "SR99A-VA-GAUGE-31",
        "SR99A-VA-GAUGE-71",
    ],
)

ioc.attach(
    vacuumLayout(
        straight,
        gate("SR99A-VA-VALVE-01"),
        arc,
    )
)

# The volumes are in scope in the interactive shell, which is where a leak is
# sprung now that none of the vacuum model is served:
#
#   >>> arc.gasLoad = 1.0e-4       a leak the sixteen supplies hold against
#   >>> arc.forcedPressure = 1e-3  pin it, and the straight too once V1 is open
#   >>> arc.forcedPressure = None  back to the model
ioc.run(namespace=globals())
