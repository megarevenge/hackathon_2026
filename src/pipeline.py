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
from .output import validate_prediction, risk_summary


class Detector:
    def __init__(self, config):
        from .runtime import configure_runtime, verify_weights
        config = configure_runtime(config)
        weights = Path(os.environ.get('TRAFFIC_WEIGHTS', ROOT / 'weights/yolo11n.pt'))
        if not weights.is_file():
            raise FileNotFoundError(f'Missing bundled weights: {weights}. Obtain the complete repository/package; inference is offline.')
        if weights.resolve() == (ROOT / 'weights/yolo11n.pt').resolve():
            verify_weights(weights, '0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1')
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
        self.fire = None
        if config.get('calibrated') and config['fire_smoke_enabled']:
            from .fire import FireDetector
            self.fire = FireDetector(config)

    def step(self, frame):
        classes = [0,1,2,3,5,7]
        if self.c['obstacle_detection_enabled']:
            classes += [14,15,16,17,18,19,20,21,22,23,24,26,28,39,56]
        result = self.model.track(frame, persist=True, tracker=str(ROOT/'configs/bytetrack.yaml'),
                                 classes=classes, conf=self.c['confidence'],
                                 imgsz=self.c['image_size'], device=self.c['device'], verbose=False)[0]
        # Ultralytics constructs ByteTrack at 30 FPS even on sampled frames.
        # Convert the requested lifetime in seconds to actual update counts.
        for tracker in self.model.predictor.trackers:
            tracker.max_time_lost = max(1,round(self.c['track_buffer_seconds']*self.c['sample_fps']))
        boxes = result.boxes
        if boxes is None or boxes.id is None:
            return self.fire.step(frame) if self.fire else []
        h, w = frame.shape[:2]
        output = []
        for box, ident, cls, confidence in zip(boxes.xyxy.cpu().tolist(), boxes.id.cpu().tolist(), boxes.cls.cpu().tolist(), boxes.conf.cpu().tolist()):
            x1, y1, x2, y2 = box
            output.append(dict(id=int(ident), **{'class': result.names[int(cls)]},
                               point=[(x1+x2)/(2*w), y2/h], box=[x1/w, y1/h, x2/w, y2/h],confidence=confidence))
        if self.fire:
            output.extend(self.fire.step(frame))
        return output


def analyze(video_path, config=None, progress=None):
    import cv2
    from .config import validate_config
    c = validate_config(config) if config is not None else load_config()
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
        events = engine.finish(duration)
        validate_prediction(events,risk,duration)
        return dict(analysis_version=3,events=events, risk=risk, tracks=tracks, counts=counts,
                    raw_events=engine.raw_events,event_evidence=engine.yield_rule.evidence+engine.hazards.evidence,
                    risk_summary=risk_summary(risk),
                    model_status=dict(collision_rules='experimental image-plane rules',
                                      obstacle_detection=c['obstacle_detection_enabled'],
                                      fire_smoke=c['fire_smoke_enabled'] and c.get('calibrated',False)),
                    meta=dict(fps=fps, duration=duration, width=width, height=height, frames=i,
                              elapsed=elapsed, realtime_factor=elapsed/duration),
                    calibrated=bool(c.get('calibrated')), risk_enabled=bool(c.get('risk_enabled')),
                    risk_columns=['timestamp_seconds','score_0_to_1'],config=c)
    finally:
        cap.release()
