#!/usr/bin/env python3
"""
deconvert.py  —  Reconstruct RoboCap files from MCAP.

Reads MCAP files produced by convert.py and recreates:
  robocap_segmentN_imu_left.db
  robocap_segmentN_imu_right.db
  robocap_segmentN_mag_middle.db
  robocap_segmentN_video_left.mp4
  robocap_segmentN_video_left_eye.mp4
  robocap_segmentN_video_left_front.mp4
  robocap_segmentN_video_right.mp4
  robocap_segmentN_video_right_eye.mp4
  robocap_segmentN_video_right_front.mp4

Timestamps in reconstructed SQLite files are relative to session start (ns),
not the original device monotonic clock values (those cannot be recovered).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcap_protobuf.reader import read_protobuf_messages
from tqdm import tqdm


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _parse_mcap_filename(mcap_path: Path) -> tuple[str, int]:
    """Return (session_name, segment_num) from e.g. '20260707_074914_session13_segment1.mcap'."""
    m = re.match(r"(.+)_segment(\d+)$", mcap_path.stem)
    if not m:
        raise ValueError(
            f"Cannot parse session/segment from '{mcap_path.name}'. "
            "Expected: <session>_segment<N>.mcap"
        )
    return m.group(1), int(m.group(2))


def _session_start_utc_ns(session_name: str, tz_offset_hours: int = 0) -> int:
    """Parse 'YYYYMMDD_HHMMSS...' session name to UTC epoch nanoseconds."""
    m = re.match(r"(\d{8})_(\d{6})", session_name)
    if not m:
        return 0
    dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    tz = timezone(timedelta(hours=tz_offset_hours))
    return int(dt.replace(tzinfo=tz).timestamp() * 1_000_000_000)


# --------------------------------------------------------------------------- #
# SQLite reconstruction                                                        #
# --------------------------------------------------------------------------- #

_METADATA_DEFAULTS = [
    ("product",    "robocap"),
    ("author",     "frodobots"),
    ("version",    "1.2.8"),
    ("imu",        "icm42688p"),
    ("camera",     "sc233hgs"),
    ("mag",        "mmc5983ma"),
    ("deviceid",   ""),
    ("username",   ""),
    ("subdevices", "NONE"),
    ("host",       "NONE"),
]

IMUID_IMU_LEFT  = 2
IMUID_IMU_RIGHT = 1
IMUID_MAG       = 3

SENSOR_TOPICS = [
    "/imu/left/acc",
    "/imu/left/gyro",
    "/imu/right/acc",
    "/imu/right/gyro",
    "/mag/middle",
]


def _open_imu_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS acc_data (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            imuid_    INTEGER,
            x         INTEGER,
            y         INTEGER,
            z         INTEGER,
            timestamp INTEGER
        );
        CREATE TABLE IF NOT EXISTS gyro_data (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            imuid_    INTEGER,
            x         INTEGER,
            y         INTEGER,
            z         INTEGER,
            timestamp INTEGER
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.executemany("INSERT OR IGNORE INTO metadata VALUES (?,?)", _METADATA_DEFAULTS)
    conn.commit()
    return conn


def _open_mag_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mag_data (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            imuid_    INTEGER,
            mag_x     INTEGER,
            mag_y     INTEGER,
            mag_z     INTEGER,
            timestamp INTEGER
        );
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.executemany("INSERT OR IGNORE INTO metadata VALUES (?,?)", _METADATA_DEFAULTS)
    conn.commit()
    return conn


def _flush(rows: list, conn: sqlite3.Connection, table: str, cols: str) -> None:
    if rows:
        placeholders = ",".join(["?"] * len(cols.split(",")))
        conn.executemany(
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", rows
        )
        conn.commit()
        rows.clear()


def write_sensor_dbs(
    mcap_path: Path,
    prefix: str,
    out_dir: Path,
    t0_epoch: int,
    verbose: bool,
) -> tuple[Path, Path, Path]:
    """Read sensor channels from MCAP → write three SQLite DBs."""
    imu_left_path  = out_dir / f"{prefix}_imu_left.db"
    imu_right_path = out_dir / f"{prefix}_imu_right.db"
    mag_path       = out_dir / f"{prefix}_mag_middle.db"

    # Remove existing files so re-runs don't append duplicate rows.
    for p in (imu_left_path, imu_right_path, mag_path):
        p.unlink(missing_ok=True)

    conn_l   = _open_imu_db(imu_left_path)
    conn_r   = _open_imu_db(imu_right_path)
    conn_mag = _open_mag_db(mag_path)

    BATCH = 5000
    acc_l, gyro_l = [], []
    acc_r, gyro_r = [], []
    mag_buf = []

    imu_row_cols = "imuid_,x,y,z,timestamp"
    mag_row_cols = "imuid_,mag_x,mag_y,mag_z,timestamp"

    with open(mcap_path, "rb") as f:
        with tqdm(desc="  sensors", unit="msg", leave=False, disable=not verbose) as pbar:
            for item in read_protobuf_messages(f, topics=SENSOR_TOPICS):
                topic = item.topic
                msg   = item.proto_msg
                rel   = item.log_time_ns - t0_epoch  # ns since session start

                if topic == "/imu/left/acc":
                    a = msg.linear_acceleration
                    acc_l.append((IMUID_IMU_LEFT, round(a.x), round(a.y), round(a.z), rel))
                    if len(acc_l) >= BATCH:
                        _flush(acc_l, conn_l, "acc_data", imu_row_cols)

                elif topic == "/imu/left/gyro":
                    g = msg.angular_velocity
                    gyro_l.append((IMUID_IMU_LEFT, round(g.x), round(g.y), round(g.z), rel))
                    if len(gyro_l) >= BATCH:
                        _flush(gyro_l, conn_l, "gyro_data", imu_row_cols)

                elif topic == "/imu/right/acc":
                    a = msg.linear_acceleration
                    acc_r.append((IMUID_IMU_RIGHT, round(a.x), round(a.y), round(a.z), rel))
                    if len(acc_r) >= BATCH:
                        _flush(acc_r, conn_r, "acc_data", imu_row_cols)

                elif topic == "/imu/right/gyro":
                    g = msg.angular_velocity
                    gyro_r.append((IMUID_IMU_RIGHT, round(g.x), round(g.y), round(g.z), rel))
                    if len(gyro_r) >= BATCH:
                        _flush(gyro_r, conn_r, "gyro_data", imu_row_cols)

                elif topic == "/mag/middle":
                    v = msg.magnetic_field
                    mag_buf.append((IMUID_MAG, round(v.x), round(v.y), round(v.z), rel))
                    if len(mag_buf) >= BATCH:
                        _flush(mag_buf, conn_mag, "mag_data", mag_row_cols)

                pbar.update(1)

    _flush(acc_l,    conn_l,   "acc_data",  imu_row_cols)
    _flush(gyro_l,   conn_l,   "gyro_data", imu_row_cols)
    _flush(acc_r,    conn_r,   "acc_data",  imu_row_cols)
    _flush(gyro_r,   conn_r,   "gyro_data", imu_row_cols)
    _flush(mag_buf,  conn_mag, "mag_data",  mag_row_cols)

    conn_l.close()
    conn_r.close()
    conn_mag.close()

    return imu_left_path, imu_right_path, mag_path


# --------------------------------------------------------------------------- #
# Video reconstruction                                                         #
# --------------------------------------------------------------------------- #

VIDEO_TOPICS: dict[str, str] = {
    "/video/left":         "video_left",
    "/video/left/eye":     "video_left_eye",
    "/video/left/front":   "video_left_front",
    "/video/right":        "video_right",
    "/video/right/eye":    "video_right_eye",
    "/video/right/front":  "video_right_front",
}


def _detect_codec(mcap_path: Path, topic: str) -> str:
    """Return codec name ('h265' or 'h264') from the first video message."""
    with open(mcap_path, "rb") as f:
        for item in read_protobuf_messages(f, topics=[topic]):
            return getattr(item.proto_msg, "codec", "h265") or "h265"
    return "h265"


def write_video_mp4(
    mcap_path: Path,
    topic: str,
    out_path: Path,
    verbose: bool,
) -> bool:
    """Pipe compressed video data from MCAP → MP4 via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("  [skip video] ffmpeg not found in PATH — install FFmpeg to reconstruct video.")
        return False

    codec = _detect_codec(mcap_path, topic)
    fmt   = "hevc" if codec == "h265" else "h264"

    cmd = [
        ffmpeg, "-y",
        "-f", fmt,
        "-i", "pipe:0",
        "-c:v", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        with open(mcap_path, "rb") as f:
            with tqdm(
                desc=f"  {out_path.name}",
                unit="pkt",
                leave=False,
                disable=not verbose,
            ) as pbar:
                for item in read_protobuf_messages(f, topics=[topic]):
                    proc.stdin.write(item.proto_msg.data)
                    pbar.update(1)
        proc.stdin.close()
        proc.wait()
    except BrokenPipeError:
        proc.stdin.close()
        proc.wait()

    if proc.returncode != 0:
        print(f"  [warn] ffmpeg returned {proc.returncode} for {out_path.name}")
        return False
    return True


# --------------------------------------------------------------------------- #
# Per-file entry                                                               #
# --------------------------------------------------------------------------- #

def deconvert_file(
    mcap_path: Path,
    output_dir: Path,
    tz_offset_hours: int = 0,
    no_video: bool = False,
    verbose: bool = False,
) -> None:
    session_name, seg_num = _parse_mcap_filename(mcap_path)
    t0_epoch = _session_start_utc_ns(session_name, tz_offset_hours)

    seg_dir = output_dir / session_name
    seg_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"robocap_segment{seg_num}"
    print(f"\n[segment{seg_num}]  {mcap_path.name}  ->  {seg_dir}/")

    # ---- sensors ---------------------------------------------------------- #
    t0 = time.perf_counter()
    imu_l, imu_r, mag = write_sensor_dbs(mcap_path, prefix, seg_dir, t0_epoch, verbose)
    dt = time.perf_counter() - t0
    rows_l = sqlite3.connect(imu_l).execute(
        "SELECT (SELECT COUNT(*) FROM acc_data) + (SELECT COUNT(*) FROM gyro_data)"
    ).fetchone()[0]
    rows_r = sqlite3.connect(imu_r).execute(
        "SELECT (SELECT COUNT(*) FROM acc_data) + (SELECT COUNT(*) FROM gyro_data)"
    ).fetchone()[0]
    rows_m = sqlite3.connect(mag).execute("SELECT COUNT(*) FROM mag_data").fetchone()[0]
    print(
        f"  sensors  ({dt:.1f}s):\n"
        f"    {imu_l.name}   [{rows_l:,} rows]\n"
        f"    {imu_r.name}  [{rows_r:,} rows]\n"
        f"    {mag.name}  [{rows_m:,} rows]"
    )

    # ---- video ------------------------------------------------------------ #
    if no_video:
        return

    for topic, suffix in VIDEO_TOPICS.items():
        mp4_path = seg_dir / f"{prefix}_{suffix}.mp4"
        t0 = time.perf_counter()
        ok = write_video_mp4(mcap_path, topic, mp4_path, verbose)
        dt = time.perf_counter() - t0
        if ok and mp4_path.exists():
            size_mb = mp4_path.stat().st_size / 1024 / 1024
            print(f"  {mp4_path.name}  ({size_mb:.1f} MB, {dt:.1f}s)")
        elif not ok:
            print(f"  {mp4_path.name}  [FAILED, {dt:.1f}s]")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="deconvert",
        description="Reconstruct RoboCap files (SQLite DBs + MP4) from MCAP.",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="MCAP file or directory containing MCAP files.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("robocap_restored"),
        metavar="DIR",
        help="Root output directory (default: robocap_restored/).",
    )
    parser.add_argument(
        "--tz",
        type=int,
        default=0,
        metavar="HOURS",
        help="UTC offset of session timestamps (default: 0 for UTC). "
             "Must match the --tz value used when running convert.py.",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="Skip video reconstruction; write SQLite DBs only.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-packet progress bars.",
    )

    args = parser.parse_args(argv)

    if args.path.is_file() and args.path.suffix == ".mcap":
        mcap_files = [args.path]
    elif args.path.is_dir():
        mcap_files = sorted(args.path.glob("*.mcap"))
        if not mcap_files:
            print(f"[error] No .mcap files found in {args.path}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"[error] '{args.path}' is not a .mcap file or directory.", file=sys.stderr)
        sys.exit(1)

    t_all = time.perf_counter()
    for mcap_file in mcap_files:
        deconvert_file(
            mcap_path=mcap_file,
            output_dir=args.output_dir,
            tz_offset_hours=args.tz,
            no_video=args.no_video,
            verbose=args.verbose,
        )

    elapsed = time.perf_counter() - t_all
    print(f"\nDone. {len(mcap_files)} file(s) processed in {elapsed:.1f}s.")


if __name__ == "__main__":
    main()
