from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.exists():
        raise RuntimeError(f"alembic.ini not found at: {alembic_ini}")

    cfg = Config(str(alembic_ini))
    command.upgrade(cfg, "head")
    print("DB migrated (alembic upgrade head).")


if __name__ == "__main__":
    main()
