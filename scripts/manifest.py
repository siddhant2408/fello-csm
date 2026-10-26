import hashlib
import json
from pathlib import Path

paths = [Path("data/dataset.json"), Path("data/models.json"), Path("data/evaluation.json")]
manifest = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
Path("data/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
