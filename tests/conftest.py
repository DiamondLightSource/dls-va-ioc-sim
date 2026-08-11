from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"


@pytest.fixture
def cellXml() -> Path:
    """A real storage ring cell's builder XML, copied out of SR-BUILDER.

    SR03C is the cell the framework was written against: eighteen supplies on
    nine controllers, eight gauge pairs, four valves, a group tree three deep
    and three spaces.  It also carries every kind of thing the parse skips -
    RGAs, the fast vacuum system, PLC glue - so a change that starts building
    one of those shows up here.
    """
    return DATA / "SR03C-VA-IOC-01.xml"
