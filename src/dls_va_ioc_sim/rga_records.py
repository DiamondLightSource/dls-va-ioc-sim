"""A minimal stand-in for one MicroVision Plus RGA head - rgamv2.template.

One record each of two templates:

    rgamv2.template        -> rgaRecord            -- :STA, and nothing else
    rgaPowerCycle.template -> rgaPowerCycleRecord  -- :PWRC

An RGA takes no gas load and pumps nothing, so it holds no `volume` and is
never on the vacuum layout or the tick list - see vacuum_model.  There is
nothing here to step on a timer: :STA is set once, at construction, and stays
there.

Why just :STA: the real template computes it as

    CALC = "((A>4)&&(A<=8)&&(B#1))?0:A"
    INPA = "$(device):HEADSTA MS"      (Maximize Severity)
    INPB = "$(device):FILSTA"

so its value and severity both come from HEADSTA once the filament is On -
FILSTA=On makes B#1 false, so the CALC always takes the "A" branch and STA
becomes HEADSTA, severity included via the MS link.  (The zeroing that branch
skips exists only to blank a barchart status quoted while the filament is not
actually running, which cannot happen here.)  Nothing else of the template is
built, so there is no real HEADSTA to read it from: the simulation publishes
the number a real head would settle on with the filament On and scanning a
Barchart 1-50 (HEADSTA=5) - the lowest numbered head state that is NO_ALARM,
which is what puts the :STA lamp on a synoptic green rather than the MINOR
alarm every idle/local-control state carries.
"""

from softioc import builder

# HEADSTA=5, "Barchart 1-50" - see the module docstring for why this value.
STATUS_FILAMENT_ON_BARCHART_1_50 = 5


class rgaRecord:
    """An RGA head - rgamv2.template, :STA only."""

    def __init__(self, prefix):
        builder.SetDeviceName(prefix)
        self.prefix = prefix

        self.statusPV = builder.longIn(
            "STA", DESC="Overall status", initial_value=STATUS_FILAMENT_ON_BARCHART_1_50
        )


class rgaPowerCycleRecord:
    """The line that power cycles a head - SR-VA's rgaPowerCycle.template.

    One record, and on the real IOC it is an ao straight onto an EtherIP tag:
    writing to it drops the head's supply and brings it back.  The head is
    wired to the cell's PLC rather than to the RGA controller, which is why
    SR-VA declares this in `common.xml` with the rest of the rack and not
    beside the head itself.

    There is nothing here to cycle - rgaRecord's :STA is a constant - so the
    write is accepted and the demand is what the button on the screen reads
    back.  It is built with the template's own VAL of 1.
    """

    def __init__(self, prefix):
        builder.SetDeviceName(prefix)
        self.prefix = prefix

        self.powerCyclePV = builder.aOut(
            "PWRC", DESC="Control", always_update=True, initial_value=1
        )
