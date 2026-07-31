from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent / "suivi-heures-ot"
APP_MAIN = APP_DIR / "main.py"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

spec = importlib.util.spec_from_file_location("suivi_heures_ot_main", APP_MAIN)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Impossible de charger {APP_MAIN}")

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

app = module.app


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
