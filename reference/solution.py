"""Organizer interface. Both paths are offline; Part B has independent causal state."""
from src.config import CLASSES, load_config
from src.risk import bounded_score, smooth_score


def detect_events(video_path: str) -> list[list]:
    from src.pipeline import analyze
    return analyze(video_path)['events']


class RiskEstimator:
    def reset(self, meta: dict) -> None:
        self.config = load_config()
        self.detector = None
        self.engine = None
        self.last_sample = float('-inf')
        self.last_time = float('-inf')
        self.score = 0.0
        self.stride = max(1,round(meta.get('fps',25)/self.config['sample_fps']))
        self.config['sample_fps'] = meta.get('fps',25)/self.stride
        self.frame_index = -1

    def step(self, frame, t_sec: float) -> float:
        if t_sec <= self.last_time:
            raise ValueError('RiskEstimator requires increasing timestamps.')
        self.last_time = t_sec
        self.frame_index += 1
        if not self.config['risk_enabled']:
            return 0.0
        if self.frame_index % self.stride:
            return bounded_score(self.score)
        if self.detector is None:
            from src.pipeline import Detector
            from src.events import RuleEngine
            self.detector = Detector(self.config)
            self.engine = RuleEngine(self.config)
        raw = self.engine.step(self.detector.step(frame), t_sec)
        dt = t_sec-self.last_sample if self.last_sample != float('-inf') else 1/self.config['sample_fps']
        self.score = smooth_score(self.score,raw,dt)
        self.last_sample = t_sec
        return self.score
