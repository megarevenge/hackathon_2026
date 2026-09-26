"""Real weights/decoder smoke test on synthetic footage, not an accuracy benchmark."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from src.pipeline import analyze

with tempfile.TemporaryDirectory(prefix='roadlens-smoke-') as directory:
    path = Path(directory)/'blank.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (320,240))
    if not writer.isOpened(): raise RuntimeError('Cannot encode test video')
    for i in range(20): writer.write(np.full((240,320,3), 70, dtype=np.uint8))
    writer.release()
    result = analyze(path)
    assert result['events'] == []
    assert len(result['risk']) == 20
    print('Real YOLO inference smoke passed:', result['meta'])
