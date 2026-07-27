from __future__ import annotations

import shutil
import uuid
from pathlib import Path


class ProjectTempDir:
    def __init__(self) -> None:
        self.path = Path(__file__).resolve().parents[1] / "test-work" / uuid.uuid4().hex

    def __enter__(self) -> str:
        self.path.mkdir(parents=True, exist_ok=False)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
