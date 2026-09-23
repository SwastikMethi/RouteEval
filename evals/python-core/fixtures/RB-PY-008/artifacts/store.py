from pathlib import Path
from .paths import normalize_path

class ArtifactStore:
    def __init__(self, root):
        self.root = Path(root)

    def path_for(self, name):
        return normalize_path(self.root, name)
