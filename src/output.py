"""Validate submission format without impersonating the official evaluator."""
import math
from .config import CLASSES


def validate_prediction(events, risk, duration):
    previous = {}
    for start,end,label in sorted(events):
        if label not in CLASSES or not all(math.isfinite(float(v)) for v in (start,end)) or not 0<=start<end<=duration:
            raise ValueError(f'Invalid event: {[start,end,label]}')
        if label in previous and start<previous[label]:
            raise ValueError(f'Overlapping {label} events')
        previous[label]=end
    last=-1.0
    for timestamp,score in risk:
        if not all(math.isfinite(float(v)) for v in (timestamp,score)) or not 0<=score<=1 or not last<timestamp<duration:
            raise ValueError(f'Invalid risk pair: {[timestamp,score]}')
        last=timestamp
