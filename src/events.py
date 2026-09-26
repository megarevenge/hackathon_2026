"""Trajectory rules, independent of detector and video libraries."""
import math
from collections import defaultdict, deque
from itertools import combinations
from .risk import bounded_score
from .road_rules import RoadRules


def inside(p, polygon):
    if len(polygon) < 3:
        return False
    x, y = p
    hit = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > y) != (b[1] > y):
            if x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
                hit = not hit
    return hit


def near_polygon(point, polygon, margin):
    if inside(point,polygon):
        return True
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        dx,dy=b[0]-a[0],b[1]-a[1]
        length=dx*dx+dy*dy
        u=max(0,min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length)) if length else 0
        if math.hypot(point[0]-a[0]-u*dx,point[1]-a[1]-u*dy)<=margin:
            return True
    return False


def merge_events(events, duration, gap=0.3, minimum=0.5):
    by_label = defaultdict(list)
    for s, e, label in events:
        s, e = max(0.0, float(s)), min(float(duration), float(e))
        if math.isfinite(s) and math.isfinite(e) and s < e:
            by_label[label].append((s, e))
    result = []
    for label, spans in by_label.items():
        merged = []
        for s, e in sorted(spans):
            if merged and s <= merged[-1][1] + gap:
                merged[-1][1] = max(e, merged[-1][1])
            else:
                merged.append([s, e])
        result.extend([[round(s, 4), min(duration,round(e, 4)), label] for s, e in merged if e-s >= minimum])
    return sorted(result)


class RuleEngine:
    def __init__(self, config):
        self.c = config
        self.history = defaultdict(deque)
        self.active = {}
        self.completed = []
        self.last_t = None
        self.road_rules = RoadRules(config)
        self.last_seen = {}

    def step(self, observations, t, signals=None):
        """Observations contain id, class, point (normalized bottom center), box."""
        if self.last_t is not None and t <= self.last_t:
            raise ValueError('Timestamps must increase.')
        self.last_t = t
        observations = [o for o in observations if not o.get('predicted',False)]
        present = {o['id'] for o in observations}
        for ident in present:
            self.last_seen[ident] = t
        for ident in list(self.history):
            if ident not in present and t-self.history[ident][-1][0] > self.c['event_gap_seconds']:
                del self.history[ident]
                self.last_seen.pop(ident,None)
        states, moving = {}, []
        for o in observations:
            ident, p = o['id'], o['point']
            h = self.history[ident]
            if h and t-h[-1][0] > self.c['event_gap_seconds']:
                h.clear()
            h.append((t, p))
            while len(h) > 2 and t-h[0][0] > 1:
                h.popleft()
            dt = t-h[0][0]
            v = ((p[0]-h[0][1][0])/dt, (p[1]-h[0][1][1])/dt) if dt > 0 else (0, 0)
            speed = math.hypot(*v)
            mature = dt >= 0.4
            moving.append((o, v, speed, mature))
            if not self.c.get('calibrated'):
                continue
            road = inside(p, self.c['road'])
            if o['class'] == 'person':
                if road and not any(near_polygon(p, poly, self.c['crosswalk_margin']) for poly in self.c['crosswalks']+self.c['excluded_zones']):
                    states[('jaywalking', ident)] = 0.5
                continue
            if road and mature and speed < self.c['stationary_speed'] and not any(inside(p, q) for q in self.c['queue_zones']+self.c['excluded_zones']):
                states[('stopped_vehicle', ident)] = self.c['stopped_seconds']
            for lane in self.c['lanes']:
                if inside(p, lane['polygon']) and mature and speed > self.c['stationary_speed']:
                    d = lane['direction']
                    cosine = (v[0]*d[0]+v[1]*d[1])/(speed*math.hypot(*d))
                    if cosine < -0.65:
                        states[('wrong_way', ident)] = self.c['wrong_way_seconds']
        if self.c.get('calibrated'):
            self.road_rules.step(moving,t,signals or {})
            for index, crossing in enumerate(self.c['crosswalks']):
                people = [o for o,_,_,_ in moving if o['class']=='person' and inside(o['point'],crossing)]
                for o,_,speed,mature in moving:
                    key = ('failure_to_yield',o['id'])
                    trigger = people and mature and speed>self.c['stationary_speed']
                    if o['class']!='person' and inside(o['point'],crossing) and (trigger or key in self.active):
                        states[key] = .25
            # Each configured direction group must have a queue in every lane.
            groups = defaultdict(list)
            for lane in self.c['lanes']:
                groups[lane.get('group', 'default')].append(lane)
            for group, lanes in groups.items():
                counts = [sum(o['class'] != 'person' and mature and speed < self.c['stationary_speed']
                              and inside(o['point'], lane['polygon']) for o, v, speed, mature in moving) for lane in lanes]
                if counts and min(counts) >= 1 and sum(counts) >= self.c['congestion_min_vehicles']:
                    states[('congestion', group)] = self.c['congestion_seconds']
        for key in list(self.active):
            if key not in states:
                # A brief missed observation does not fragment a confirmed event.
                # Observed contrary evidence closes it immediately.
                if key[1] not in present and key[1] in self.last_seen and t-self.last_seen[key[1]] <= self.c['event_gap_seconds']:
                    continue
                start, last, threshold = self.active.pop(key)
                if last-start >= threshold:
                    self.completed.append([start, t if key[1] in present else last, key[0]])
        for key, threshold in states.items():
            start = self.active.get(key, (t, t, threshold))[0]
            self.active[key] = (start, t, threshold)
        risk_tracks = moving
        if self.c.get('calibrated'):
            risk_tracks = [x for x in moving if inside(x[0]['point'],self.c['road']) and not any(inside(x[0]['point'],p) for p in self.c['excluded_zones'])]
        return bounded_score(self.risk(risk_tracks)) if self.c.get('risk_enabled') else 0.0

    @staticmethod
    def risk(moving):
        # Image-plane closest approach, NOT metric physical TTC or calibrated probability.
        result = 0.0
        candidates = [x for x in moving if x[3]]
        for (a, av, _, _), (b, bv, _, _) in combinations(candidates, 2):
            if a['class'] == b['class'] == 'person':
                continue
            p = [b['point'][i]-a['point'][i] for i in (0, 1)]
            v = [bv[i]-av[i] for i in (0, 1)]
            vv = sum(x*x for x in v)
            if vv < 1e-6:
                continue
            tau = -sum(p[i]*v[i] for i in (0, 1))/vv
            if not 0 < tau <= 5:
                continue
            miss = math.hypot(*(p[i]+tau*v[i] for i in (0, 1)))
            radius = max(0.005, min(a['box'][2]-a['box'][0], b['box'][2]-b['box'][0])*0.35)
            if miss < radius:
                result = max(result, (1-miss/radius)*(1-tau/6))
        return min(1.0, max(0.0, result))

    def finish(self, duration):
        events = list(self.completed) + self.road_rules.finish(duration)
        for (label, _), (start, last, threshold) in self.active.items():
            end = duration if duration-last <= 1/self.c['sample_fps'] + .05 else last
            if end-start >= threshold:
                events.append([start, end, label])
        return merge_events(events, duration, self.c['merge_gap_seconds'], self.c['min_event_seconds'])
