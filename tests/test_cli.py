import subprocess
import sys

import pytest

from dls_va_ioc_sim import __version__


def test_cli_version():
    cmd = [sys.executable, "-m", "dls_va_ioc_sim", "--version"]
    assert subprocess.check_output(cmd).decode().strip() == __version__


def test_the_three_subcommands_are_offered():
    cmd = [sys.executable, "-m", "dls_va_ioc_sim", "--help"]
    help = subprocess.check_output(cmd).decode()

    for subcommand in ("generate", "run", "dbdump"):
        assert subcommand in help


def test_a_bare_invocation_asks_for_a_subcommand():
    result = subprocess.run(
        [sys.executable, "-m", "dls_va_ioc_sim"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "command" in result.stderr


def test_the_public_api_is_importable_without_touching_epics():
    """`generate` needs no records, so the parse and the vacuum model must
    stay importable on a machine with no EPICS on it."""
    from dls_va_ioc_sim import parseXml, vacuumVolume  # noqa: F401


@pytest.mark.parametrize("subcommand", ["generate", "run", "dbdump"])
def test_each_subcommand_has_help(subcommand):
    cmd = [sys.executable, "-m", "dls_va_ioc_sim", subcommand, "--help"]
    assert subprocess.check_output(cmd).decode().startswith("usage:")
