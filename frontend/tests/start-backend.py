"""Playwright-only backend; every database/workspace is temporary and mocked."""
import os
import sys
import tempfile
from pathlib import Path

import yaml
from routebench.main import main

root = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="routebench-playwright-") as directory:
    storage = Path(directory)
    config = yaml.safe_load((root / "routebench.example.yaml").read_text())
    config["server"] = {"host": "127.0.0.1", "port": 8766}
    config["storage"] = {
        "database": str(storage / "runs.sqlite"),
        "artifacts": str(storage / "artifacts"),
        "workspaces": str(storage / "workspaces"),
        "keep_workspaces": False,
        "retention_days": 1,
    }
    config["suites"] = {
        "python-core": str(root / "evals/python-core/suite.yaml"),
        "python-smoke": str(root / "evals/python-core/suite.yaml") + "#smoke",
        "python-planning": str(root / "evals/python-core/suite.yaml") + "#planning",
    }
    path = storage / "mock.yaml"
    path.write_text(yaml.safe_dump(config))
    os.environ["ROUTEBENCH_MOCK"] = "1"
    sys.argv = ["routebench", "--config", str(path), "serve"]
    main()
