"""Unit tests for Antigravity local state & quota syncer."""
import sqlite3
from pathlib import Path

from desktop.antigravity_syncer import AntigravityQuotaSyncer, parse_protobuf_records


def test_parse_protobuf_records_primitive():
    # Test varint wire type (0)
    # Field 1, wire 0 (key = (1 << 3) | 0 = 8) -> value 42
    data = bytes([0x08, 0x2A])
    fields = parse_protobuf_records(data)
    assert len(fields) == 1
    assert fields[0] == (1, "varint", 42)


def test_parse_protobuf_records_length_delimited():
    # Field 2, wire 2 (key = (2 << 3) | 2 = 18) -> length 5 -> b"hello"
    payload = b"hello"
    data = bytes([0x12, len(payload)]) + payload
    fields = parse_protobuf_records(data)
    assert len(fields) == 1
    assert fields[0] == (2, "bytes", b"hello")


def test_antigravity_quota_syncer_mock(tmp_path: Path):
    syncer = AntigravityQuotaSyncer()
    syncer.ag_dir = tmp_path
    syncer.convo_dir = tmp_path / "conversations"
    syncer.convo_dir.mkdir(parents=True)
    syncer.summary_db = tmp_path / "conversation_summaries.db"

    # When no conversation DB exists, returns None
    assert syncer.get_session_status() is None

    # Create dummy conversation db
    db_path = syncer.convo_dir / "test-convo-123.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE gen_metadata (idx integer, data blob, size integer, PRIMARY KEY (idx))")
    conn.commit()
    conn.close()

    # Empty gen_metadata -> returns None
    assert syncer.get_session_status() is None
