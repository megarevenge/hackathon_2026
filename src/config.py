"""Explicit camera configuration. Empty geometry never implies a road boundary."""
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSES = ['accident', 'near_miss', 'red_light', 'wrong_way', 'illegal_u_turn',
           'stopped_vehicle', 'jaywalking', 'failure_to_yield', 'illegal_turn',
           'solid_line_crossing', 'stop_line', 'congestion', 'road_obstacle', 'fire_smoke']


def load_config(path=None):
    path = path or os.environ.get('TRAFFIC_CAMERA', ROOT / 'configs/camera.json')
    return validate_config(json.loads(Path(path).read_text()))


def validate_config(c):
    if not isinstance(c, dict):
        raise ValueError('Configuration must be a JSON object.')
    c = dict(c)
    for key in ('road', 'crosswalks', 'queue_zones', 'lanes', 'excluded_zones',
                'signals','stop_lines','solid_lines','turn_rules'):
        c.setdefault(key, [])
        if not isinstance(c[key], list):
            raise ValueError(f'{key} must be a list.')
    if c.get('calibrated') and len(c['road']) < 3:
        raise ValueError('A calibrated camera needs a road polygon.')
    for key in ('calibrated', 'risk_enabled'):
        if key in c and not isinstance(c[key], bool):
            raise ValueError(f'{key} must be true or false.')
    polygons = [c['road']] + c['crosswalks'] + c['queue_zones'] + c['excluded_zones']
    signal_ids = set()
    for signal in c['signals']:
        if signal['id'] in signal_ids:
            raise ValueError('Signal IDs must be unique.')
        signal_ids.add(signal['id'])
        if set(signal['lamps']) != {'red','green'}:
            raise ValueError('Signals need separate red and green lamp ROIs.')
        for roi in signal['lamps'].values():
            if len(roi)!=4 or not all(math.isfinite(float(v)) and 0<=v<=1 for v in roi) or not (roi[0]<roi[2] and roi[1]<roi[3]):
                raise ValueError('Signal ROI must be [x1,y1,x2,y2] in [0,1].')
    for rule in c['stop_lines']:
        if rule['signal_id'] not in signal_ids:
            raise ValueError('Stop lines require an explicitly configured controlling signal.')
        polygons.extend([rule['intersection'],rule['violation_zone']])
        d = rule['direction']
        if len(d)!=2 or not all(math.isfinite(float(v)) for v in d) or math.hypot(*d)==0:
            raise ValueError('Stop-line direction must be a nonzero finite vector.')
    for rule in c['stop_lines']+c['solid_lines']:
        line = rule['line']
        if len(line)!=2 or line[0]==line[1] or any(len(p)!=2 or not all(math.isfinite(float(v)) and 0<=v<=1 for v in p) for p in line):
            raise ValueError('Lines need two distinct normalized endpoints.')
    for rule in c['turn_rules']:
        if rule['label'] not in ('illegal_turn','illegal_u_turn'):
            raise ValueError('Turn rules must use illegal_turn or illegal_u_turn.')
        polygons.extend([rule['entry'],rule['maneuver'],rule['exit']])
    for lane in c['lanes']:
        polygons.append(lane['polygon'])
        d = lane['direction']
        if len(d) != 2 or not all(math.isfinite(float(v)) for v in d) or math.hypot(*d) == 0:
            raise ValueError('Every lane needs a finite, nonzero direction vector.')
    for polygon in polygons:
        if polygon and len(polygon) < 3:
            raise ValueError('Polygons need at least three points.')
        for p in polygon:
            if len(p) != 2 or not all(math.isfinite(float(v)) and 0 <= v <= 1 for v in p):
                raise ValueError('Coordinates must be normalized to [0, 1].')
    defaults = dict(sample_fps=8, confidence=0.1, image_size=640, device='cpu',
                    stopped_seconds=10, stationary_speed=0.008, wrong_way_seconds=1.5,
                    min_event_seconds=0.5, merge_gap_seconds=0.3, congestion_seconds=5,
                    congestion_min_vehicles=4, risk_enabled=False, event_gap_seconds=0.5,
                    display_hold_seconds=0.5, track_buffer_seconds=2.0, crosswalk_margin=0.0)
    for key, value in defaults.items():
        c.setdefault(key, value)
    for key in ('sample_fps', 'stopped_seconds', 'stationary_speed', 'wrong_way_seconds',
                'min_event_seconds', 'congestion_seconds', 'congestion_min_vehicles',
                'event_gap_seconds','display_hold_seconds','track_buffer_seconds'):
        if not math.isfinite(float(c[key])) or float(c[key]) <= 0:
            raise ValueError(f'{key} must be positive and finite.')
    if not 0 < float(c['confidence']) <= 1 or not 0 <= float(c['merge_gap_seconds']) <= 2:
        raise ValueError('Invalid confidence or merge gap.')
    if not isinstance(c['image_size'], int) or not 128 <= c['image_size'] <= 1920:
        raise ValueError('image_size must be an integer between 128 and 1920.')
    if not 0 <= float(c['crosswalk_margin']) <= .05:
        raise ValueError('crosswalk_margin must be between 0 and .05.')
    return c
