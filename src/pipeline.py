import os
os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
import random
import time
from pathlib import Path
import numpy as np
from .config import ROOT, load_config
from .events import RuleEngine
from .risk import bounded_score, smooth_score
from .tracking import DisplayTracker
from .signals import SignalReader


class Detector:
    def __init__(self, config):
        weights = Path(os.environ.get('TRAFFIC_WEIGHTS', ROOT / 'weights/yolo11n.pt'))
        if not weights.is_file():
            raise FileNotFoundError(f'Missing local weights: {weights}. Run python scripts/download_weights.py once with internet.')
        settings = ROOT / '.cache/ultralytics'
        settings.mkdir(parents=True,exist_ok=True)
        os.environ.setdefault('YOLO_CONFIG_DIR',str(settings))
        import torch
        from ultralytics import YOLO
        random.seed(42)
        np.random.seed(42)
        torch.manual_seed(42)
        torch.set_num_threads(4)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.use_deterministic_algorithms(True, warn_only=True)
        self.model = YOLO(str(weights))
        self.c = config

    def step(self, frame):
        result = self.model.track(frame, persist=True, tracker=str(ROOT/'configs/bytetrack.yaml'),
                                 classes=[0, 1, 2, 3, 5, 7], conf=self.c['confidence'],
                                 imgsz=self.c['image_size'], device=self.c['device'], verbose=False)[0]
        # Ultralytics constructs ByteTrack at 30 FPS even on sampled frames.
        # Convert the requested lifetime in seconds to actual update counts.
        for tracker in self.model.predictor.trackers:
            tracker.max_time_lost = max(1,round(self.c['track_buffer_seconds']*self.c['sample_fps']))
        boxes = result.boxes
        if boxes is None or boxes.id is None:
            return []
        h, w = frame.shape[:2]
        output = []
        for box, ident, cls in zip(boxes.xyxy.cpu().tolist(), boxes.id.cpu().tolist(), boxes.cls.cpu().tolist()):
            x1, y1, x2, y2 = box
            output.append(dict(id=int(ident), **{'class': result.names[int(cls)]},
                               point=[(x1+x2)/(2*w), y2/h], box=[x1/w, y1/h, x2/w, y2/h]))
        return output


def analyze(video_path, config=None, progress=None):
    import cv2
    c = config or load_config()
    cap = cv2.VideoCapture(str(video_path))
    started = time.perf_counter()
    try:
        fps, n = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or not np.isfinite(fps) or fps <= 0 or n <= 0:
            raise ValueError('Video cannot be decoded or has invalid metadata.')
        width, height = int(cap.get(3)), int(cap.get(4))
        stride = max(1, round(fps/c['sample_fps']))
        c = dict(c, sample_fps=fps/stride)
        detector, engine = Detector(c), RuleEngine(c)
        display = DisplayTracker(c['display_hold_seconds'])
        signal_reader = SignalReader(c['signals'])
        tracks, counts, risk = [], [], []
        last_risk = 0.0
        last_sample_t = -stride/fps
        i = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = i/fps
            if i % stride == 0:
                objects = detector.step(frame)
                signals = signal_reader.step(frame)
                score = engine.step(objects, t, signals)
                last_risk = smooth_score(last_risk, score, t-last_sample_t)
                last_sample_t = t
                tracks.append({'t': t, 'objects': objects, 'display_objects':display.update(objects,t), 'signals':signals})
                row = {'time': t}
                for name in ('person', 'bicycle', 'car', 'motorcycle', 'bus', 'truck'):
                    row[name] = sum(o['class'] == name for o in objects)
                counts.append(row)
                if progress:
                    progress(min(0.99, (i+1)/n))
            risk.append([round(t, 6), round(bounded_score(last_risk), 6)])
            i += 1
        if i == 0 or i < n-2:
            raise ValueError(f'Video decoding stopped early ({i}/{n} frames).')
        duration = i/fps
        elapsed = time.perf_counter()-started
        if progress:
            progress(1.0)
        return dict(events=engine.finish(duration), risk=risk, tracks=tracks, counts=counts,
                    meta=dict(fps=fps, duration=duration, width=width, height=height, frames=i,
                              elapsed=elapsed, realtime_factor=elapsed/duration),
                    calibrated=bool(c.get('calibrated')), risk_enabled=bool(c.get('risk_enabled')),
                    risk_columns=['timestamp_seconds','score_0_to_1'],config=c)
    finally:
        cap.release()
