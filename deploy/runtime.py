"""Container maintenance using SQLite's online backup, never a live file copy."""
import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from floorhand import db, seed


def backup(path: Path, keep: int = 7) -> Path:
    """Publish a complete snapshot before rotating older snapshots."""
    if keep < 1:
        raise ValueError("Keep at least one backup. / Храните хотя бы одну копию.")
    folder = path.parent / "backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = folder / f"floorhand-{stamp}.db"
    temporary = dest.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=10)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target, pages=256)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup check failed. / Ошибка проверки копии.")
        temporary.chmod(0o600)
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    for old in sorted(folder.glob("floorhand-*.db"), reverse=True)[keep:]:
        old.unlink()
    return dest


def rebuild(path: Path, *, preserve_calls: bool = False) -> None:
    """Build beside the old DB; the caller must stop the app before replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".seed-", suffix=".db", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as target:
            target.row_factory = sqlite3.Row
            target.execute("PRAGMA foreign_keys = ON")
            seed.build(target)
            if preserve_calls:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw",
                                             uri=True)) as source:
                    columns = [r[1] for r in source.execute("PRAGMA table_info(model_calls)")]
                    rows = source.execute("SELECT * FROM model_calls").fetchall()
                    names = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
                    placeholders = ",".join("?" for _ in columns)
                    target.executemany(f"INSERT INTO model_calls ({names}) "
                                       f"VALUES ({placeholders})", rows)
                    # No live reader/writer may retain an old WAL across replacement.
                    if source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                        raise RuntimeError("Stop the app first. / Сначала остановите приложение.")
            target.commit()
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Seed check failed. / Ошибка проверки демобазы.")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize(path: Path) -> None:
    if not path.exists():
        if os.environ.get("FLOORHAND_DEMO") != "1":
            raise RuntimeError("Set FLOORHAND_DEMO=1 to seed. / Для демобазы задайте FLOORHAND_DEMO=1.")
        rebuild(path)


def reset(path: Path) -> None:
    if os.environ.get("FLOORHAND_DEMO") != "1":
        raise RuntimeError("Reset requires FLOORHAND_DEMO=1. / Сброс требует FLOORHAND_DEMO=1.")
    # Fail closed if the old ledger cannot be read; never reset the budget.
    backup(path)
    rebuild(path, preserve_calls=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "backup", "reset"))
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    path = db.db_path()
    if args.command == "init":
        initialize(path)
    elif args.command == "backup":
        dest = backup(path, args.keep)
        print(f"Backup saved / Копия сохранена: {dest}")
    else:
        reset(path)
        print("Demo refreshed; model ledger kept. / Демобаза обновлена; журнал модели сохранён.")


if __name__ == "__main__":
    main()
