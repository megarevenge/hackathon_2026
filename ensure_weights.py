"""Verify bundled model files, downloading missing checkpoints during the build."""
import hashlib
import shutil
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = (
    (
        'yolo11n.pt',
        'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt',
        '0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1',
    ),
    (
        'fire_smoke_yolov8.pt',
        'https://huggingface.co/mfranzon/fire-smoke-yolov8/resolve/'
        'f1c6426b069c1849cbf13b1ef5d2a260289286db/fire_smoke_yolov8.pt',
        'ac0a10257b2bc1f20c9d957f8adeeb61dd6140322fc19d0b4a116cb491776d16',
    ),
)


def valid(path, expected):
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == expected


def ensure_weights(directory, models=MODELS):
    directory.mkdir(parents=True, exist_ok=True)
    for name, url, expected in models:
        target = directory / name
        if valid(target, expected):
            print(f'Verified {name}', flush=True)
            continue
        temporary = directory / (name + '.download')
        for attempt in range(3):
            try:
                print(f'Downloading {name} (attempt {attempt + 1}/3)', flush=True)
                request = urllib.request.Request(url, headers={'User-Agent': 'RoadLens-build/1.0'})
                with urllib.request.urlopen(request, timeout=120) as response:
                    with temporary.open('wb') as stream:
                        shutil.copyfileobj(response, stream)
                if not valid(temporary, expected):
                    raise ValueError(f'SHA256 mismatch for {name}')
                temporary.replace(target)
                print(f'Verified {name}', flush=True)
                break
            except (OSError, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError(
                        f'Cannot obtain verified {name}. Include the original weights/{name} '
                        'in your repository or allow access to its download host.'
                    ) from exc
                time.sleep(2 ** attempt)
            finally:
                temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    ensure_weights(ROOT / 'weights')
