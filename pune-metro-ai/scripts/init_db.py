"""Apply all database migrations."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    """Upgrade the database schema to the latest Alembic revision."""
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(project_root / "alembic.ini")
    command.upgrade(alembic_config, "head")


if __name__ == "__main__":
    main()
