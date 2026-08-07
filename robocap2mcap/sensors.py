"""Reads IMU and magnetometer data from RoboCap SQLite databases."""

import sqlite3
from pathlib import Path
from typing import Iterator, Tuple


def _min_ts(db_path: Path, table: str, col: str = "timestamp") -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"SELECT MIN({col}) FROM {table}").fetchone()
        return row[0]
    finally:
        conn.close()


def segment_t0(imu_left: Path, imu_right: Path, mag: Path) -> int:
    """Return the earliest timestamp (ns) across all three sensor DBs."""
    candidates = [
        _min_ts(imu_left, "acc_data"),
        _min_ts(imu_left, "gyro_data"),
        _min_ts(imu_right, "acc_data"),
        _min_ts(imu_right, "gyro_data"),
        _min_ts(mag, "mag_data"),
    ]
    return min(c for c in candidates if c is not None)


def iter_acc(db_path: Path) -> Iterator[Tuple[int, int, int, int]]:
    """Yields (x, y, z, timestamp_ns) from acc_data ordered by timestamp."""
    conn = sqlite3.connect(db_path)
    try:
        yield from conn.execute(
            "SELECT x, y, z, timestamp FROM acc_data ORDER BY timestamp"
        )
    finally:
        conn.close()


def iter_gyro(db_path: Path) -> Iterator[Tuple[int, int, int, int]]:
    """Yields (x, y, z, timestamp_ns) from gyro_data ordered by timestamp."""
    conn = sqlite3.connect(db_path)
    try:
        yield from conn.execute(
            "SELECT x, y, z, timestamp FROM gyro_data ORDER BY timestamp"
        )
    finally:
        conn.close()


def iter_mag(db_path: Path) -> Iterator[Tuple[int, int, int, int]]:
    """Yields (mag_x, mag_y, mag_z, timestamp_ns) from mag_data ordered by timestamp."""
    conn = sqlite3.connect(db_path)
    try:
        yield from conn.execute(
            "SELECT mag_x, mag_y, mag_z, timestamp FROM mag_data ORDER BY timestamp"
        )
    finally:
        conn.close()


def row_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()
