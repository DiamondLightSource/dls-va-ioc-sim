"""The table every builder tag is given its meaning by.

A template names its record class by module and class as strings, so that the
parse can hold the table without importing a single record and the generator
can write an import line without importing softioc.  The price of that is that
a name here is not checked by the interpreter - so it is checked here instead,
for every entry, rather than when some cell that happens to declare that tag is
next generated.

The rest of this is the drift the registry exists to make impossible: a tag
that means two things, a tag both translated and ignored, a class the generated
file names but does not import, a group kind a space cannot take.
"""

import inspect
import subprocess
import sys

import pytest

from dls_va_ioc_sim.device_registry import (
    BESPOKE,
    DEVICE_BY_KIND,
    DEVICE_BY_TAG,
    DEVICES,
    GROUP_BY_KIND,
    GROUP_BY_TAG,
    GROUP_KINDS,
    IGNORED_MODULES,
    IGNORED_TAGS,
    TEMPLATES,
    TRANSLATED_TAGS,
    importsFor,
)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.className)
def test_every_template_names_a_class_that_exists(template):
    """The one thing the interpreter cannot check for us."""
    assert inspect.isclass(template.cls)
    assert template.cls.__name__ == template.className


@pytest.mark.parametrize("template", DEVICES, ids=lambda t: t.kind)
def test_every_device_class_takes_just_a_name(template):
    """What makes a deviceTemplate table-driven at all.

    Both ways in build one of these by calling the class with the one name the
    XML gave, so anything else it takes has to have a default.  A class that
    needs a second argument is not a deviceTemplate - it needs a declaration
    and a parser of its own, the way an ion pump supply does.
    """
    parameters = list(inspect.signature(template.cls).parameters.values())

    assert parameters, f"{template.className} takes no name"
    assert parameters[0].default is inspect.Parameter.empty
    for parameter in parameters[1:]:
        assert parameter.default is not inspect.Parameter.empty, (
            f"{template.className} needs {parameter.name} passed in, so it "
            "cannot be built from the table alone"
        )


MACRO_TEMPLATES = [template for template in DEVICES if template.macros]


@pytest.mark.parametrize("template", MACRO_TEMPLATES, ids=lambda t: t.kind)
def test_every_macro_is_an_optional_argument_of_its_class(template):
    """A macro is passed through as a keyword argument when a cell quotes it,
    so the class has to take it - and has to have a default for it, because
    almost no cell does quote it."""
    parameters = inspect.signature(template.cls).parameters

    for macro in template.macros:
        assert macro in parameters, (
            f"{template.className} takes no {macro}, so a cell that quoted it "
            "would fail on construction"
        )
        assert parameters[macro].default is not inspect.Parameter.empty


def test_the_rack_is_the_one_thing_with_macros():
    """Not a rule, a tripwire: every other template names its devices in full,
    so a second one turning up here is worth a look at whether `macros` is the
    right mechanism for it."""
    assert [template.kind for template in MACRO_TEMPLATES] == ["commonD2"]


def test_no_tag_means_two_things():
    """A tag in two entries would be parsed as whichever came last."""
    seen = {}
    for template in TEMPLATES:
        for tag in template.tags:
            assert tag not in seen, (
                f"{tag} is claimed by both {seen.get(tag)} and {template.kind}"
            )
            seen[tag] = template.kind

    assert len(seen) == len(TRANSLATED_TAGS)


def test_nothing_is_both_translated_and_ignored():
    """Ignored wins in neither direction - the parse checks translated first,
    so a tag in both is built while the report says it was skipped."""
    both = TRANSLATED_TAGS & frozenset(IGNORED_TAGS)
    assert both == frozenset(), f"translated and ignored: {sorted(both)}"

    modules = {tag.split(".")[0] for tag in TRANSLATED_TAGS}
    assert not modules & set(IGNORED_MODULES), (
        "a whole module is ignored that a template is translated from"
    )


def test_the_kinds_are_unique_within_their_shape():
    """Everything indexes devices and groups by kind, so two of a kind would
    lose one of them.  A device and a group may share one - a valve and a valve
    group are both "valve" - because they are looked up in different tables."""
    assert len(DEVICE_BY_KIND) == len(DEVICES)
    assert len(GROUP_BY_KIND) == len(GROUP_KINDS)


def test_a_space_takes_exactly_the_group_kinds_that_exist():
    """GROUP_KINDS is derived from the table, and spaceRecord's signature is
    what it has to agree with: a space reads and writes five groups by name,
    and a group added without a place in a space would never reach one."""
    space = GROUP_BY_KIND["ionp"].cls  # any entry, for the module
    from dls_va_ioc_sim.vacuum_space_records import spaceRecord

    assert space is not spaceRecord  # sanity: they are different classes
    parameters = list(inspect.signature(spaceRecord).parameters)

    assert parameters[0] == "prefix"
    assert tuple(parameters[1:]) == GROUP_KINDS


def test_the_tag_indexes_cover_every_tag():
    assert set(DEVICE_BY_TAG) == {tag for d in DEVICES for tag in d.tags}
    assert set(GROUP_BY_TAG) == {tag for g in GROUP_BY_KIND.values() for tag in g.tags}
    bespoke = {tag for b in BESPOKE for tag in b.tags}
    assert TRANSLATED_TAGS == set(DEVICE_BY_TAG) | set(GROUP_BY_TAG) | bespoke


def test_the_imports_are_grouped_and_sorted():
    """A generated file has to come out the same twice - the byte-for-byte
    diff this framework is verified with depends on it, and a set iterated
    straight into the output would not."""
    imports = importsFor(TEMPLATES)

    modules = [module for module, _ in imports]
    assert modules == sorted(modules)
    assert len(modules) == len(set(modules)), "one entry per module"
    for _, names in imports:
        assert list(names) == sorted(names)

    imported = {(module, name) for module, names in imports for name in names}
    for template in TEMPLATES:
        assert (template.module, template.className) in imported


GENERATE_WITHOUT_EPICS = """
import sys
from dls_va_ioc_sim.builder_xml import parseXml
from dls_va_ioc_sim.generate_ioc import generate

source = generate(parseXml(sys.argv[1], cell="99"))
assert "commonRecord" in source, "it really did generate a rack"

pulled = sorted(name for name in sys.modules
                if name.split(".")[0] in ("softioc", "epicsdbbuilder", "cothread"))
print("EPICS MODULES:", ",".join(pulled))
"""


def test_generating_an_instance_imports_no_epics(cellXml, tmp_path):
    """Why a template names its class as a string rather than importing it.

    The generator writes class names and import lines into a file; it builds no
    records, and it is meant to run on a machine with no EPICS on it at all.
    A registry holding real classes would be imported by the parse and pull the
    whole of softioc in behind it - so the classes are resolved on demand, and
    only parsed_ioc, which really does build records, ever asks for one.

    In a subprocess because the rest of the suite has softioc imported already.
    """
    script = tmp_path / "generate_only.py"
    script.write_text(GENERATE_WITHOUT_EPICS)

    result = subprocess.run(
        [sys.executable, str(script), str(cellXml)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "EPICS MODULES:", (
        "generating an instance pulled EPICS in: " + result.stdout.strip()
    )


def test_a_device_template_says_where_it_is_written():
    """`section` picks the banner it lands under in a generated instance, and
    a name with no section behind it would be written out nowhere at all."""
    from dls_va_ioc_sim.generate_ioc import DEVICE_SECTIONS

    sections = {section.name for section in DEVICE_SECTIONS}
    for template in DEVICES:
        assert template.section in sections, (
            f"{template.kind} is written under {template.section!r}, which is "
            "not a banner - add one to DEVICE_SECTIONS"
        )

    for section in DEVICE_SECTIONS:
        assert section.templates, f"the {section.name} banner has no devices"
