from __future__ import annotations

import os
import tempfile
from pathlib import Path

import sqlite_vec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlcipher3 import dbapi2 as sqlcipher


path = Path(tempfile.gettempdir()) / "daon-r1-m5-03-feasibility.db"
path.unlink(missing_ok=True)
key = os.urandom(32)
connection = sqlcipher.connect(path)
connection.execute(f"PRAGMA key=\"x'{key.hex()}'\"")
cipher_version = connection.execute("PRAGMA cipher_version").fetchone()[0]
connection.enable_load_extension(True)
sqlite_vec.load(connection)
connection.enable_load_extension(False)
vector_version = connection.execute("SELECT vec_version()").fetchone()[0]
connection.execute("CREATE VIRTUAL TABLE vectors USING vec0(embedding float[2])")
connection.execute(
    "INSERT INTO vectors(rowid, embedding) VALUES(1, ?)",
    (sqlite_vec.serialize_float32([1.0, 0.0]),),
)
connection.commit()
connection.close()
raw = path.read_bytes()
print(f"cipher_version={cipher_version}")
print(f"vector_version={vector_version}")
print(f"sqlite_plain_header={raw.startswith(b'SQLite format 3')}")
print(f"aesgcm_key_bytes={len(AESGCM.generate_key(bit_length=256))}")
path.unlink()
