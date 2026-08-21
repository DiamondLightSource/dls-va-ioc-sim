"""What the parse makes of a real cell's builder XML.

builder_xml holds no EPICS records, so this is plain data in and plain data
out.  The thing worth guarding is that nothing is ever skipped in silence: a
tag is either translated, or listed with a reason, or reported as NOT
RECOGNISED - and a change that starts building RGAs or PLC glue shows up here.
"""

from dls_va_ioc_sim.builder_xml import UNRECOGNISED, parseXml


def names(declarations, kind):
    """The prefixes of every simple device of one registry kind."""
    return [device.prefix for device in declarations.devicesOfKind(kind)]


def test_the_cell_is_renamed_so_it_cannot_be_the_real_machine(cellXml):
    declarations = parseXml(cellXml, cell="99")

    assert declarations.name == "SR99C-VA-IOC-01"
    names = declarations.deviceNames()
    assert names, "the fixture should declare devices"
    assert not [name for name in names if name.startswith("SR03")]
    assert all(name.startswith("SR99") for name in names)


def test_the_cell_defaults_to_leaving_names_alone(cellXml):
    declarations = parseXml(cellXml)
    assert declarations.name == "SR03C-VA-IOC-01"


def test_every_tag_is_either_translated_or_accounted_for(cellXml):
    declarations = parseXml(cellXml, cell="99")

    unrecognised = declarations.unrecognised()
    assert unrecognised == [], (
        "a tag this framework has never seen turned up in the fixture; "
        "translate it or add it to IGNORED_TAGS with a reason"
    )
    for tag, (count, reason) in declarations.ignored.items():
        assert count > 0
        assert reason and reason != UNRECOGNISED, tag


def test_the_report_says_what_was_dropped_and_why(cellXml):
    declarations = parseXml(cellXml, cell="99")
    report = declarations.report()

    assert "SR99C-VA-IOC-01" in report
    assert "found" in report
    # The cell groups take an insertion device member that belongs to another
    # IOC, so it cannot be built here and the report has to say so.
    assert "dropped" in report
    assert "belongs to another IOC" in report


def test_groups_come_out_innermost_first(cellXml):
    """A group is built after its members, so the order the parse hands back
    has to put a group of groups last - parsed_ioc relies on it."""
    declarations = parseXml(cellXml, cell="99")

    built: set[str] = set()
    for group in declarations.groups:
        for member in group.members:
            # A member is either a device or a group already built.
            assert member not in {g.prefix for g in declarations.groups} - built
        built.add(group.prefix)


def test_a_space_names_five_groups_that_exist(cellXml):
    declarations = parseXml(cellXml, cell="99")
    groups = {group.prefix for group in declarations.groups}

    assert declarations.spaces
    for space in declarations.spaces:
        assert set(space.groups) == {"ionp", "gauge", "img", "pirg", "valve"}
        for prefix in space.groups.values():
            assert prefix in groups


def test_the_plc_and_the_cell_rack_are_read_off_the_xml(cellXml):
    """Two single lines of XML that a screen needs the PVs of: the EtherIP
    link, which publishes its own health, and SR-VA.common, which is a whole
    rack of fans and 24 V supplies named after the cell."""
    declarations = parseXml(cellXml, cell="99")

    assert names(declarations, "plc") == ["SR99C-VA-VLVCC-01"]
    # A rack is named by the domain every device in it is named after.
    assert names(declarations, "common") == ["SR99C"]
    assert names(declarations, "commonD2") == []


def test_the_diamond_two_rack_is_read_as_its_own_kind(d2CellXml):
    """commonD2.xml is replacing common.xml cell by cell, so both are read for
    as long as both are in use, and which one a cell had has to survive the
    parse - the two files put the RGA power cycle lines on different heads."""
    declarations = parseXml(d2CellXml, cell="99")

    assert names(declarations, "commonD2") == ["SR99C"]
    assert names(declarations, "common") == []


def test_a_diamond_two_cell_has_nothing_unrecognised_in_it_either(d2CellXml):
    declarations = parseXml(d2CellXml, cell="99")

    assert declarations.unrecognised() == []


def test_every_pump_belongs_to_a_controller_that_was_declared(cellXml):
    declarations = parseXml(cellXml, cell="99")
    controllers = {controller.prefix for controller in declarations.controllers}

    assert declarations.pumps
    for pump in declarations.pumps:
        assert pump.controller in controllers


def test_the_volume_guess_covers_every_device(cellXml):
    """The generated layout has to place every pump and gauge on some volume,
    or attachLayout refuses it - see the check in attachLayout itself."""
    declarations = parseXml(cellXml, cell="99")

    placed: set[str] = set()
    for domain in declarations.domains():
        guess = declarations.volumeGuess(domain)
        placed.update(guess["pumps"])
        placed.update(guess["gauges"])

    assert placed == set(declarations.deviceNames())
