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


@pytest.fixture
def d2CellXml() -> Path:
    """The same cell as Diamond-II builds it, copied out of SRVA-D2-BUILDER.

    It is here for what it declares differently: `SR-VA.commonD2` in place of
    `SR-VA.common`, which moves the RGA power cycle lines onto other heads,
    and `rgamv2.rgamv2` heads in place of `rga.rga` ones, which are the only
    RGAs in either fixture that this framework actually builds.
    """
    return DATA / "SR03C-VA-IOC-01-d2.xml"
