"""A single finite [0, 1] contract at every public risk boundary."""
import math
from itertools import combinations


def bounded_score(value):
    value = float(value)
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


class PersistentRisk:
    """Causal image-plane conflict signal; not a calibrated probability.

    Requires sustained pairwise closing evidence, suppresses low-confidence
    tracks and ordinary co-moving traffic. Never consumes event predictions.
    """
    def __init__(self, config):
        self.c = config
        self.pairs = {}

    def step(self, moving, t):
        from .events import RuleEngine
        active, result = set(), 0.0
        candidates = [x for x in moving if x[3] and x[0].get('confidence', 1) >= self.c['risk_confidence']]
        for a, b in combinations(candidates, 2):
            if a[0]['class'] == b[0]['class'] == 'person':
                continue
            # Nearly parallel traffic with similar speed is not a conflict.
            if a[2] > 0 and b[2] > 0:
                cosine = sum(x*y for x,y in zip(a[1],b[1]))/(a[2]*b[2])
                if cosine > .95 and min(a[2],b[2])/max(a[2],b[2]) > .7:
                    continue
            if max(a[2], b[2]) < self.c['stationary_speed']*2:
                continue
            raw = bounded_score(RuleEngine.risk([a,b]))
            if raw <= 0:
                continue
            key = tuple(sorted((a[0]['id'], b[0]['id'])))
            active.add(key)
            start, last = self.pairs.get(key, (t,t))
            if t-last > self.c['event_gap_seconds']:
                start = t
            self.pairs[key] = (start,t)
            if t-start >= self.c['risk_confirmation_seconds']:
                result = max(result, raw)
        self.pairs = {key:value for key,value in self.pairs.items() if key in active}
        return bounded_score(result)


def smooth_score(previous, current, dt, time_constant=0.25):
    alpha = 1.0 - math.exp(-max(0.0, dt) / time_constant)
    return bounded_score((1-alpha)*bounded_score(previous) + alpha*bounded_score(current))
