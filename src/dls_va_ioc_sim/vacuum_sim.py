"""Helpers shared by the simulated vacuum devices."""

import math
import random

# The range of pressures the simulation works in, taken from the widest range
# the vacuum records have to represent: an ion pump's :P reaches down into UHV
# at the bottom, and a Pirani reads past atmosphere at the top.
PRESSURE_MIN = 1.0e-12
PRESSURE_MAX = 1.0e3


def clamp(value, low, high):
    """Hold a value inside the range the record reporting it can represent."""
    return min(max(value, low), high)


def clampPressure(pressure):
    """Keep a pressure inside the range the vacuum records can represent."""
    return clamp(pressure, PRESSURE_MIN, PRESSURE_MAX)


def slideLog(value, target, delta, timeConstant):
    """Move value one step of delta seconds towards target, in log space.

    Vacuum pressures cover decades.  Approaching a target linearly spends all
    of the time in the top decade and then appears to stop; interpolating the
    logarithm instead gives the straight-ish slide a real pump down shows on
    the log plots people actually look at.
    """
    remaining = math.exp(-delta / max(timeConstant, delta))
    return math.exp(math.log(target)
                    + (math.log(value) - math.log(target)) * remaining)


def jitter(fraction=0.02):
    """A small multiplicative wobble, so readbacks look alive, not flat."""
    return 1.0 + random.uniform(-fraction, fraction)


def droop(fraction=0.004):
    """A one sided wobble, for readbacks that must not overshoot their limit."""
    return 1.0 - random.uniform(0.0, fraction)
