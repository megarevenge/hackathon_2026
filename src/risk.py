"""A single finite [0, 1] contract at every public risk boundary."""
import math


def bounded_score(value):
    value = float(value)
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


def smooth_score(previous, current, dt, time_constant=0.25):
    alpha = 1.0 - math.exp(-max(0.0, dt) / time_constant)
    return bounded_score((1-alpha)*bounded_score(previous) + alpha*bounded_score(current))
