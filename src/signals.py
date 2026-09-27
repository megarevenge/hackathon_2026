"""Optional explicitly mapped lamp crops. Unknown never counts as red."""
from collections import defaultdict, deque


class SignalReader:
    def __init__(self, signals):
        self.signals = signals
        self.history = defaultdict(lambda: deque(maxlen=3))

    def step(self, frame):
        import cv2
        import numpy as np
        h,w = frame.shape[:2]
        result = {}
        for signal in self.signals:
            # Use separate tight lamp crops, not the whole signal assembly.
            strength = {}
            for color, roi in signal['lamps'].items():
                x1,y1,x2,y2 = roi
                crop = frame[int(y1*h):int(y2*h),int(x1*w):int(x2*w)]
                if crop.size == 0:
                    continue
                hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                hue,sat,val = cv2.split(hsv)
                hue_mask = ((hue < 12) | (hue > 165)) if color == 'red' else ((hue > 35) & (hue < 95))
                strength[color] = float(np.mean(hue_mask & (sat > 100) & (val > 140)))
            state = 'unknown'
            if strength:
                best = max(strength, key=strength.get)
                if strength[best] >= signal.get('min_fraction',.15) and all(strength[best] > 2*v for k,v in strength.items() if k != best):
                    state = best
            history = self.history[signal['id']]
            history.append(state)
            result[signal['id']] = state if len(history)==3 and len(set(history))==1 else 'unknown'
        return result
