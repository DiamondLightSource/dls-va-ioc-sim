"""Simulate a DLS vacuum IOC, and the PLC behind it.

Everything a PLC would decide - valve interlocking, combining a gauge pair,
the pressure itself - is decided here instead and served over Channel Access,
so that a screen, an archiver or another IOC cannot tell it from the real
thing.  The PV interface mirrors the DLS support-module templates device for
device; each module's docstring carries its class -> `*.template` mapping.

The way in is usually the command line - `dls-va-ioc-sim generate` writes an
instance out as Python from the builder XML a real IOC is built from.  What is
exported here is what an instance builds on once it has been written.

Three layers, and the thing to hold onto is that a *space* and a *volume* are
different concepts that used to share a name:

    vacuumVolume    a length of beam pipe.  Holds gas, publishes nothing
    a group         a device standing for several others (GIONP-01, GVALV-04)
    a space         what an operator is shown.  Owns nothing at all

Only the vacuum model and the XML parse are re-exported: the device classes
live in one module per family (`ion_pump_records`, `gauge_records`,
`fe_seq_records`, `vacuum_space_records`) and an instance imports them from there,
which is what keeps a generated file readable as a list of what exists.

.. data:: __version__
    :type: str

    Version number as calculated by https://github.com/pypa/setuptools_scm
"""

from ._version import __version__
from .builder_xml import attachLayout, iocFromXml, parseXml
from .vacuum_model import gate, vacuumLayout, vacuumVolume

__all__ = [
    "__version__",
    "attachLayout",
    "gate",
    "iocFromXml",
    "parseXml",
    "vacuumLayout",
    "vacuumVolume",
]
