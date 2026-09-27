"""Experimental hazard evidence. Image-plane contact is not proof of impact.

Fire/smoke uses a separate learned detector. Obstacles use explicit COCO
animal/item classes, not arbitrary stationary vehicles or background blobs.
"""
import math
from itertools import combinations
from collections import defaultdict, deque
from .tracking import overlap

ROAD_USERS = {'person', 'bicycle', 'motorcycle', 'car', 'bus', 'truck'}
ANIMALS = {'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe'}
ITEMS = {'backpack', 'handbag', 'suitcase', 'bottle', 'chair'}
OBSTACLES = ANIMALS | ITEMS


def pair_geometry(a, b):
    p = [b[0]['point'][i]-a[0]['point'][i] for i in (0,1)]
    v = [b[1][i]-a[1][i] for i in (0,1)]
    vv = sum(x*x for x in v)
    tau = -sum(x*y for x,y in zip(p,v))/vv if vv > 1e-8 else -1
    miss = math.hypot(*(p[i]+max(0,tau)*v[i] for i in (0,1)))
    scale = max(.005, min(o[0]['box'][2]-o[0]['box'][0] for o in (a,b)))
    distance = math.hypot(*p)
    contact = distance < scale*.35 and overlap(a[0]['box'],b[0]['box']) > .15
    return tau, miss, scale, distance, contact


class HazardRules:
    def __init__(self, config):
        self.c = config
        self.pairs = {}
        self.visible = {}
        self.events = []
        self.evidence = []
        self.motion_history = defaultdict(deque)

    def emit(self, label, start, end, ids, reason):
        if end > start:
            self.events.append([start,end,label])
            self.evidence.append(dict(label=label,start=start,end=end,track_ids=list(ids),reason=reason))

    def in_scene(self, point):
        from .events import inside
        return inside(point,self.c['road']) and not any(inside(point,p) for p in self.c['excluded_zones'])

    def step(self, moving, observations, t):
        if not self.c.get('calibrated'):
            return
        self.regions(moving, observations, t)
        if not self.c['collision_rules_enabled']:
            return
        users = {o['id']: (o,v,s,m) for o,v,s,m in moving
                 if o['class'] in ROAD_USERS and m and self.in_scene(o['point'])
                 and o.get('confidence',1) >= self.c['hazard_confidence']}
        for ident,item in users.items():
            history = self.motion_history[ident]
            if history and t-history[-1][0] > self.c['event_gap_seconds']:
                history.clear()
            history.append((t,item[1]))
            while len(history)>1 and t-history[0][0]>.5:
                history.popleft()
        for ident,history in list(self.motion_history.items()):
            if t-history[-1][0]>self.c['event_gap_seconds']:
                del self.motion_history[ident]
        for key,state in list(self.pairs.items()):
            if t-state['last'] > self.c['event_gap_seconds']:
                if state.get('accident') and not state['completed']:
                    self.emit('accident',state['contact'],state['last'],key,'Contact, abrupt speed change; tracks lost')
                del self.pairs[key]
        seen = set()
        for a,b in combinations(users.values(),2):
            if a[0]['class'] == b[0]['class'] == 'person':
                continue
            key = tuple(sorted((a[0]['id'],b[0]['id'])))
            tau,miss,scale,distance,contact = pair_geometry(a,b)
            state = self.pairs.get(key)
            cosine = sum(x*y for x,y in zip(a[1],b[1]))/(a[2]*b[2]) if a[2]*b[2]>0 else -1
            co_moving = cosine>.85 and min(a[2],b[2])/max(a[2],b[2])>.5
            # Initial danger requires converging tracks, not existing overlap.
            if state is None and not co_moving and 0 < tau <= 1.5 and miss < scale*.65 and not contact:
                state = dict(last=t,armed=t,
                             contact=None,contact_last=None,accident=False,evasive=None,stopped=None,
                             touched=False,completed=False)
                self.pairs[key] = state
            if state is None:
                continue
            seen.add(key)
            state['last'] = t
            if state['completed']:
                if distance > scale*3:
                    del self.pairs[key]
                continue
            # A change must be abrupt over recent past motion, not a normal
            # multi-second turn compared against a stale entry velocity.
            abrupt,braking = False,False
            for obj,v,speed,_ in (a,b):
                history = self.motion_history[obj['id']]
                if t-history[0][0] < .2:
                    continue
                old = history[0][1]
                old_speed = math.hypot(*old)
                if old_speed >= self.c['stationary_speed']*4:
                    cosine = sum(x*y for x,y in zip(v,old))/(speed*old_speed) if speed>0 else 1
                    braking |= speed < old_speed*.4
                    abrupt |= speed < old_speed*.4 or (speed > self.c['stationary_speed']*2 and cosine < .5)
            if contact:
                state['touched'] = True
                if not state['accident'] and (state['contact_last'] is None or t-state['contact_last'] > self.c['event_gap_seconds']):
                    state['contact'] = t
                state['contact_last'] = t
                if braking and t-state['contact'] >= self.c['hazard_confirmation_seconds']:
                    state['accident'] = True
            if abrupt and state['evasive'] is None:
                state['evasive'] = t
            if state['accident']:
                if max(a[2],b[2]) < self.c['stationary_speed']:
                    if state['stopped'] is None:
                        state['stopped'] = t
                    if t-state['stopped'] >= .5:
                        self.emit('accident',state['contact'],state['stopped'],key,'Approach, contact proxy, abrupt motion change, then stop')
                        state['completed'] = True
                else:
                    state['stopped'] = None
            elif state['evasive'] is not None and not state['touched'] and distance > scale*2 and tau <= 0:
                self.emit('near_miss',state['evasive'],t,key,'Predicted conflict, evasive motion and subsequent clearance without contact proxy')
                state['completed'] = True
            elif t-state['armed'] > 5:
                # Unconfirmed dangers expire; do not turn high risk into an event.
                del self.pairs[key]
        # End a collision only when every participant leaves, not when one is occluded.
        for key,state in list(self.pairs.items()):
            if key not in seen and state['accident'] and not state['completed']:
                present = [users[i] for i in key if i in users]
                if present:
                    state['last'] = t

    def regions(self, moving, observations, t):
        active = set()
        motion = {o['id']:(speed,mature) for o,_,speed,mature in moving}
        users = [o for o in observations if o['class'] in ROAD_USERS]
        for obj in observations:
            name = obj['class']
            if obj.get('predicted') or obj.get('confidence',1) < self.c['hazard_confidence'] or not self.in_scene(obj['point']):
                continue
            if name in ('fire','smoke'):
                if not self.c['fire_smoke_enabled']:
                    continue
                label, threshold = 'fire_smoke', self.c['fire_confirmation_seconds']
            elif name in OBSTACLES:
                if not self.c['obstacle_detection_enabled']:
                    continue
                if any(overlap(obj['box'],u['box']) > .1 or
                       (u['box'][0] <= obj['point'][0] <= u['box'][2] and
                        u['box'][1] <= obj['point'][1] <= u['box'][3]) for u in users):
                    continue  # Carried objects / vehicle parts are not road debris.
                speed,mature = motion.get(obj['id'],(0,False))
                if name in ITEMS and (not mature or speed >= self.c['stationary_speed']):
                    continue
                label, threshold = 'road_obstacle', self.c['obstacle_confirmation_seconds']
            else:
                continue
            key = (label,obj['id'])
            # IDs returning after a gap must not reconnect an old interval.
            if key in self.visible and t-self.visible[key][1] > self.c['event_gap_seconds']:
                self.close_region(key,self.visible[key][1])
            start,_,_ = self.visible.get(key,(t,t,threshold))
            self.visible[key] = (start,t,threshold)
            active.add(key)
        observed_ids = {o['id'] for o in observations}
        for key,(start,last,threshold) in list(self.visible.items()):
            if key not in active and (key[1] in observed_ids or t-last>self.c['event_gap_seconds']):
                self.close_region(key,last)

    def close_region(self,key,end):
        start,last,threshold = self.visible.pop(key)
        if last-start >= threshold:
            self.emit(key[0],start,end,[key[1]],'Persistent learned class detection inside the road region')

    def finish(self,duration):
        for key,(start,last,threshold) in list(self.visible.items()):
            self.close_region(key,duration if duration-last<=1/self.c['sample_fps']+.05 else last)
        for key,state in self.pairs.items():
            if state['accident'] and not state['completed']:
                end = duration if duration-state['last']<=1/self.c['sample_fps']+.05 else state['last']
                self.emit('accident',state['contact'],end,key,'Confirmed contact proxy; video/track ended')
                state['completed'] = True
        return list(self.events)
