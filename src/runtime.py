"""Local-only runtime setup shared by the website and organizer interface."""
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from .config import ROOT


@lru_cache(maxsize=8)
def verify_weights(path, expected):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'Missing bundled weights: {path}. Obtain the complete repository/package; inference is offline.')
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'Checkpoint checksum mismatch: {path.name}. Restore the original bundled file.')


def configure_runtime(config):
    # Set before importing torch/Ultralytics; no network or package installation.
    os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
    settings = ROOT / '.cache/ultralytics'
    settings.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('YOLO_CONFIG_DIR', str(settings))
    import torch
    result = dict(config)
    if str(result.get('device', 'auto')).lower() == 'auto':
        result['device'] = '0' if torch.cuda.is_available() else 'cpu'
    return result
