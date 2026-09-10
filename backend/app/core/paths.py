from pathlib import Path

# backend/app/core/paths.py -> project-root/
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_TEMPLATES_DIR = FRONTEND_DIR / "templates"
FRONTEND_STATIC_DIR = FRONTEND_DIR / "static"
