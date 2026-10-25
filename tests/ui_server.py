"""Browser acceptance only: actual backend/tools with an explicitly scripted model.

Run: python -m uvicorn tests.ui_server:app --host 127.0.0.1 --port 8001
Never use this entry point for live-model demos or production.
"""
from dataclasses import replace

from backend.api.main import create_app
from backend.config import ROOT, Settings
from tests.scripted_llm import ScriptedLLM

app = create_app(replace(Settings.from_env(), workspace_root=ROOT / ".cache/ui/workspaces",
                         database_path=ROOT / ".cache/ui/experiences.db"), llm_factory=ScriptedLLM)
