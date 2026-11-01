"""Local browser acceptance server: scripted responses, production API/SQLite/tools.

No live model calls. Start Vue with VITE_USE_MOCK=false and API_PROXY_TARGET=http://127.0.0.1:8003.
The same --data-dir can be reused to demonstrate restart persistence. Never point it at production data.
"""
import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn
from backend.api.main import create_app
from backend.config import Settings
from tests.scripted_multi_agent import ScriptedRoleFactory


def acceptance_app(directory):
    base = Settings.from_env()
    definitions = {key: base.project(key) for key in ("java-checkout-complex", "python-checkout-complex",
                                                       "java-booking-transfer", "python-booking-transfer")}
    settings = replace(base, database_path=directory / "experiences.db", workspace_root=directory / "workspaces",
                       projects={key: item.source for key, item in definitions.items()}, project_definitions=definitions)
    return create_app(settings, llm_factory=ScriptedRoleFactory())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".cache/frontend-v3-acceptance")
    args = parser.parse_args()
    print(f"SCRIPTED MODEL ONLY; real tools and SQLite. Data: {args.data_dir.resolve()}", flush=True)
    uvicorn.run(acceptance_app(args.data_dir.resolve()), host="127.0.0.1", port=args.port)
