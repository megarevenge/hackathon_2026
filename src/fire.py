"""Pinned local fire/smoke checkpoint. Inference never downloads models."""
import hashlib
import os
from .config import ROOT
from .tracking import overlap

FIRE_SHA256 = 'ac0a10257b2bc1f20c9d957f8adeeb61dd6140322fc19d0b4a116cb491776d16'
FIRE_URL = ('https://huggingface.co/mfranzon/fire-smoke-yolov8/resolve/'
            'f1c6426b069c1849cbf13b1ef5d2a260289286db/fire_smoke_yolov8.pt')
FIRE_PATH = ROOT/'weights/fire_smoke_yolov8.pt'


class FireDetector:
    def __init__(self,config):
        if not FIRE_PATH.is_file():
            raise FileNotFoundError('Fire/smoke weights missing. Run python scripts/download_weights.py --fire-smoke, '
                                    'or explicitly set fire_smoke_enabled=false for a reduced-coverage run.')
        if hashlib.sha256(FIRE_PATH.read_bytes()).hexdigest() != FIRE_SHA256:
            raise ValueError('Fire/smoke checkpoint checksum mismatch.')
        os.environ.setdefault('YOLO_AUTOINSTALL','false')
        settings = ROOT/'.cache/ultralytics'
        settings.mkdir(parents=True,exist_ok=True)
        os.environ.setdefault('YOLO_CONFIG_DIR',str(settings))
        from ultralytics import YOLO
        self.model = YOLO(str(FIRE_PATH))
        if set(self.model.names.values()) != {'fire','smoke'}:
            raise ValueError('Fire/smoke checkpoint has unexpected class names.')
        self.c = config
        self.previous = {}
        self.next_id = -1
        self.tick = 0

    def step(self,frame):
        self.tick += 1
        result = self.model.predict(frame,conf=self.c['hazard_confidence'],imgsz=640,
                                    device=self.c['device'],verbose=False)[0]
        height,width = frame.shape[:2]
        out,used = [],set()
        if result.boxes is not None:
            for box,cls,confidence in zip(result.boxes.xyxy.cpu().tolist(),result.boxes.cls.cpu().tolist(),result.boxes.conf.cpu().tolist()):
                box = [box[0]/width,box[1]/height,box[2]/width,box[3]/height]
                name = result.names[int(cls)]
                candidates = [(overlap(box,old['box']),ident) for ident,(last,old) in self.previous.items()
                              if ident not in used and self.tick-last<=self.c['sample_fps']*self.c['event_gap_seconds']]
                score,ident = max(candidates,default=(0,0))
                if score < .2:
                    ident = self.next_id
                    self.next_id -= 1
                used.add(ident)
                # Negative IDs keep the specialist namespace separate from ByteTrack.
                obj = dict(id=ident,box=box,point=[(box[0]+box[2])/2,box[3]],confidence=confidence,
                           **{'class':name})
                self.previous[ident] = (self.tick,obj)
                out.append(obj)
        self.previous = {i:v for i,v in self.previous.items() if self.tick-v[0]<=self.c['sample_fps']*self.c['event_gap_seconds']}
        return out
