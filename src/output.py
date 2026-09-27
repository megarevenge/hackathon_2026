"""Validate submission format without impersonating the official evaluator."""
import math
from .config import CLASSES


def risk_summary(rows):
    """Named values for humans; the official [seconds, score] schema is unchanged."""
    scores = []
    for index,row in enumerate(rows):
        if not isinstance(row,(list,tuple)) or len(row)!=2:
            raise ValueError(f'Risk row {index} must be [timestamp_seconds, score_0_to_1].')
        timestamp,score = row
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in row) or timestamp<0 or not 0<=score<=1:
            raise ValueError(f'Invalid risk row {index}: {row}. Only the second column is a score in [0,1].')
        scores.append(score)
    return dict(rows=len(rows),min_score=min(scores,default=0),max_score=max(scores,default=0),
                columns=['timestamp_seconds','score_0_to_1'],within_bounds=True,
                interpretation='Experimental conflict signal, not a calibrated accident probability')


def validate_prediction(events, risk, duration):
    risk_summary(risk)
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
