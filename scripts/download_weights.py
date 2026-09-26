"""Online preparation only. Inference never downloads weights."""
import hashlib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'weights/yolo11n.pt'
URL = 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt'

if __name__ == '__main__':
    TARGET.parent.mkdir(exist_ok=True)
    if not TARGET.exists():
        temporary = TARGET.with_suffix('.download')
        try:
            urllib.request.urlretrieve(URL, temporary)
            if temporary.stat().st_size < 1_000_000:
                raise ValueError('Downloaded weights are unexpectedly small.')
            temporary.replace(TARGET)
        finally:
            temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    expected = (TARGET.parent/'SHA256SUMS').read_text().split()[0]
    if digest != expected:
        raise ValueError('Weights checksum mismatch. Remove the incorrect checkpoint and retry from the official source.')
    print(f'{TARGET}\nSHA256 {digest}')
