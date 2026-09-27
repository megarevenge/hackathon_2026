"""Crossing visits, not indefinite presence, are evidence of failure to yield."""
import math


class YieldRule:
    def __init__(self, config):
        self.c = config
        self.previous = {}
        self.visits = {}
        self.events = []
        self.evidence = []

    def close(self, key, end):
        visit = self.visits.pop(key)
        if visit['confirmed'] and end > visit['start']:
            self.events.append([visit['start'], end, 'failure_to_yield'])
            self.evidence.append(dict(label='failure_to_yield', track_id=key[0], crossing=key[1],
                                      start=visit['start'], end=end,
                                      reason='Observed crossing entry, pedestrian occupancy and vehicle displacement'))

    def step(self, moving, t):
        from .events import inside
        vehicles = {o['id']: (o, speed, mature) for o, _, speed, mature in moving
                    if o['class'] != 'person'}
        for key, visit in list(self.visits.items()):
            if key[0] not in vehicles and t-visit['last'] > self.c['event_gap_seconds']:
                self.close(key, visit['last'])
        for ident, (obj, speed, mature) in vehicles.items():
            point = obj['point']
            previous = self.previous.get(ident)
            fresh = previous and 0 < t-previous[0] <= self.c['event_gap_seconds']
            for index, crossing in enumerate(self.c['crosswalks']):
                key = (ident, index)
                if key in self.visits and t-self.visits[key]['last'] > self.c['event_gap_seconds']:
                    self.close(key, self.visits[key]['last'])
                if not inside(point, crossing):
                    if key in self.visits:
                        self.close(key, t)
                    continue
                # Require a witnessed entry. A stationary vehicle first seen within
                # a crossing is not evidence that it drove through a pedestrian.
                if key not in self.visits and fresh and not inside(previous[1], crossing):
                    self.visits[key] = dict(start=t, last=t, point=point, confirmed=False,
                                            pedestrian_point=None)
                visit = self.visits.get(key)
                if visit is None:
                    continue
                visit['last'] = t
                people = [p for p, _, _, _ in moving if p['class'] == 'person'
                          and inside(p['point'], crossing)
                          and not any(inside(p['point'], poly) for poly in self.c['excluded_zones'])]
                if people and visit['pedestrian_point'] is None:
                    visit['pedestrian_point'] = point
                width = max(.005, obj['box'][2]-obj['box'][0])
                anchor = visit['pedestrian_point']
                displacement = math.dist(point, anchor) if anchor is not None else 0
                if (people and mature and speed > self.c['stationary_speed']*2
                        and displacement >= width*self.c['yield_min_displacement_boxes']):
                    visit['confirmed'] = True
                if not people:
                    visit['pedestrian_point'] = None
            self.previous[ident] = (t, point)
        for ident, (last, _) in list(self.previous.items()):
            if t-last > self.c['event_gap_seconds']:
                del self.previous[ident]

    def finish(self, duration):
        for key, visit in list(self.visits.items()):
            end = duration if duration-visit['last'] <= 1/self.c['sample_fps']+.05 else visit['last']
            self.close(key, end)
        return list(self.events)
