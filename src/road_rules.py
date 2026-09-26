"""Configured traffic rules. Geometry and signal ownership must be verified."""
import math


def side(point, line):
    a,b = line
    return (b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0])


def crosses(a,b,line):
    return side(a,line)*side(b,line) < 0 and side(line[0],[a,b])*side(line[1],[a,b]) <= 0


class RoadRules:
    def __init__(self, config):
        self.c = config
        self.previous = {}
        self.pending = {}
        self.turns = {}
        self.events = []

    def step(self, moving, t, signals):
        from .events import inside
        current = {o['id']:o for o,_,_,_ in moving if o['class'] != 'person'}
        for key, state in list(self.pending.items()):
            label, ident, rule_id = key
            start,last,rule = state
            obj = current.get(ident)
            expired = t-last > self.c['event_gap_seconds']
            done = False
            if obj:
                if label == 'red_light':
                    done = not inside(obj['point'],rule['intersection'])
                elif label == 'stop_line':
                    done = signals.get(rule['signal_id']) == 'green'
                elif label == 'solid_line_crossing':
                    x1,y1,x2,y2 = obj['box']
                    # Both projected tire-contact corners have reached the new side.
                    done = side([x1,y2],rule['line'])*rule['_new_side'] > 0 and side([x2,y2],rule['line'])*rule['_new_side'] > 0
                state[1] = t
            if done or (expired and obj is None):
                end = t if done else last
                if end > start:
                    self.events.append([start,end,label])
                del self.pending[key]
        for obj,v,speed,mature in moving:
            if obj['class'] == 'person':
                continue
            ident,p = obj['id'],obj['point']
            previous = self.previous.get(ident)
            valid_previous = previous and 0 < t-previous[0] <= self.c['event_gap_seconds']
            for i,rule in enumerate(self.c['stop_lines']):
                state = signals.get(rule['signal_id'],'unknown')
                forward = v[0]*rule['direction'][0]+v[1]*rule['direction'][1] > 0
                key = ('red_light',ident,i)
                if valid_previous and state=='red' and forward and crosses(previous[1],p,rule['line']):
                    self.pending.setdefault(key,[previous[0],t,rule])
                key = ('stop_line',ident,i)
                if state=='red' and mature and speed < self.c['stationary_speed'] and inside(p,rule['violation_zone']):
                    self.pending.setdefault(key,[t,t,rule])
            for i,rule in enumerate(self.c['solid_lines']):
                if valid_previous and crosses(previous[1],p,rule['line']):
                    enriched = dict(rule, _new_side=math.copysign(1,side(p,rule['line'])))
                    self.pending.setdefault(('solid_line_crossing',ident,i),[previous[0],t,enriched])
            for i,rule in enumerate(self.c['turn_rules']):
                key = (ident,i)
                if inside(p,rule['entry']):
                    self.turns[key] = [None,t]
                state = self.turns.get(key)
                if state:
                    state[1] = t
                    if state[0] is None and inside(p,rule['maneuver']):
                        state[0] = t
                    if state[0] is not None and inside(p,rule['exit']):
                        if t > state[0]:
                            self.events.append([state[0],t,rule['label']])
                        del self.turns[key]
            self.previous[ident] = (t,p)
        for ident,(last,_) in list(self.previous.items()):
            if t-last > self.c['event_gap_seconds']:
                del self.previous[ident]
        for key,(_,last) in list(self.turns.items()):
            if t-last > self.c['event_gap_seconds']:
                del self.turns[key]

    def finish(self,duration):
        events = list(self.events)
        for (label,_,_), (start,last,_) in self.pending.items():
            end = duration if duration-last <= self.c['event_gap_seconds'] else last
            if end>start:
                events.append([start,end,label])
        return events
