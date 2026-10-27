"""Cross-feature role boundaries on the complete invented demo seed."""
import random

from floorhand import db, seed
from fix1_smoke import check_get_routes


def test_merged_feature_gets_are_complete_and_role_filtered(tmp_path, monkeypatch):
    monkeypatch.setenv('FLOORHAND_DB', str(tmp_path / 'fix1.db'))
    monkeypatch.setenv('FLOORHAND_META_LIVE', '0')
    monkeypatch.setenv('FLOORHAND_SIMULATE', '0')
    conn = db.connect()
    try:
        seed.build(conn, random.Random(7))
    finally:
        conn.close()
    check_get_routes()
