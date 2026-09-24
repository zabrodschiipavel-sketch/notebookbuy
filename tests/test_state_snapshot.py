"""Tests for packing/restoring pipeline state (local files only)."""
import json
import sqlite3
import tarfile

import pytest

import state_snapshot
from db import _initialized_dbs, init_database


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_db(path, ads: int):
    _initialized_dbs.discard(str(path))
    init_database(str(path))
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO ads (id, title, price) VALUES (?, 't', 1000)", [(i,) for i in range(ads)]
        )


def test_pack_then_unpack_round_trips(workdir):
    db = workdir / "laptops.db"
    _make_db(db, ads=10)
    (workdir / "digest_history.json").write_text('{"1": {}}', encoding="utf-8")

    meta = state_snapshot.pack(str(workdir / "out"), db_path=str(db))
    assert meta["ads"] == 10

    restore = workdir / "restore"
    restore.mkdir()
    names = state_snapshot.unpack(str(workdir / "out" / "state.tar.gz"), dest=str(restore),
                                  db_name="laptops.db")
    assert set(names) == {"laptops.db", "digest_history.json"}
    with sqlite3.connect(restore / "laptops.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM ads").fetchone()[0] == 10


def test_pack_refuses_a_database_that_shrank(workdir):
    """A failed restore leaves an empty-ish DB; publishing it would destroy
    the only good copy."""
    db = workdir / "laptops.db"
    _make_db(db, ads=10)
    prev = workdir / "prev.json"
    prev.write_text(json.dumps({"ads": 100}), encoding="utf-8")
    with pytest.raises(ValueError, match="refusing"):
        state_snapshot.pack(str(workdir / "out"), previous_meta=str(prev), db_path=str(db))


def test_pack_refuses_an_empty_database(workdir):
    db = workdir / "laptops.db"
    _make_db(db, ads=0)
    with pytest.raises(ValueError, match="no ads"):
        state_snapshot.pack(str(workdir / "out"), db_path=str(db))


def test_unpack_ignores_unexpected_members(workdir):
    evil = workdir / "evil.tar.gz"
    payload = workdir / "payload.txt"
    payload.write_text("x", encoding="utf-8")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../../escape.txt")
        tar.add(payload, arcname="digest_history.json")
    names = state_snapshot.unpack(str(evil), dest=str(workdir / "r"), db_name="laptops.db")
    assert names == ["digest_history.json"]
    assert not (workdir.parent / "escape.txt").exists()


def test_cli_exit_code_signals_a_refused_snapshot(workdir):
    assert state_snapshot.main(["pack", str(workdir / "out")]) == 1  # no database at all


def test_unreadable_previous_meta_counts_as_no_snapshot(workdir):
    """First run: `git show` fails and leaves an empty file behind."""
    db = workdir / "laptops.db"
    _make_db(db, ads=3)
    empty = workdir / "prev.json"
    empty.write_text("", encoding="utf-8")
    assert state_snapshot.pack(str(workdir / "out"), previous_meta=str(empty), db_path=str(db))["ads"] == 3
