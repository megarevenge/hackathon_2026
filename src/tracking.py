"""Short display-only prediction; inferred boxes never enter event rules."""
from copy import deepcopy


def overlap(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union>0 else 0.0


class DisplayTracker:
    def __init__(self, hold_seconds=0.5):
        self.hold = hold_seconds
        self.states = {}

    def update(self, objects, t):
        visible = set()
        output = []
        for obj in objects:
            ident = obj['id']
            visible.add(ident)
            velocity = [0.0]*4
            previous = self.states.get(ident)
            if previous and 0 < t-previous[0] <= self.hold:
                dt = t-previous[0]
                velocity = [max(-.5, min(.5, (a-b)/dt)) for a,b in zip(obj['box'],previous[1]['box'])]
            record = deepcopy(obj)
            record['predicted'] = False
            self.states[ident] = (t, record, velocity)
            output.append(record)
        for ident, (last, obj, velocity) in list(self.states.items()):
            if t-last > self.hold:
                del self.states[ident]
            elif ident not in visible:
                record = deepcopy(obj)
                record['box'] = [max(0.0,min(1.0,b+v*(t-last))) for b,v in zip(obj['box'],velocity)]
                record['point'] = [(record['box'][0]+record['box'][2])/2,record['box'][3]]
                record['predicted'] = True
                record['expires_at'] = last+self.hold
                if not any(o['class']==record['class'] and overlap(o['box'],record['box'])>.25 for o in objects):
                    output.append(record)
                else:
                    # A newly assigned ID likely covers the old object's position.
                    # Never show two boxes solely because the old one was extrapolated.
                    del self.states[ident]
        return output
