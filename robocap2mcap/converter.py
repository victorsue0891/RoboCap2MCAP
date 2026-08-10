"""Converts one RoboCap segment to an MCAP file."""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from google.protobuf.timestamp_pb2 import Timestamp
from mcap_protobuf.writer import Writer
from tqdm import tqdm

from robocap2mcap import sensors, video as vid
from foxglove.CompressedVideo_pb2 import CompressedVideo
from foxglove.Imu_pb2 import Imu
from foxglove.MagneticField_pb2 import MagneticField
from foxglove.Vector3_pb2 import Vector3


def _ts(ns: int) -> Timestamp:
    return Timestamp(seconds=ns // 1_000_000_000, nanos=int(ns % 1_000_000_000))


def _session_start_utc_ns(session_name: str, tz_offset_hours: int = 8) -> int:
    """Parse '20260707_074914_session13' → UTC epoch nanoseconds.

    The timestamp in the session folder name is treated as local time
    at tz_offset_hours UTC offset (default 8 = CST/UTC+8).
    """
    m = re.match(r"(\d{8})_(\d{6})", session_name)
    if not m:
        return 0
    dt_naive = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    local_tz = timezone(timedelta(hours=tz_offset_hours))
    dt_aware = dt_naive.replace(tzinfo=local_tz)
    return int(dt_aware.timestamp() * 1_000_000_000)


def _write_acc(writer, db_path, channel, frame_id, t0_sensor, t0_epoch, pbar):
    for x, y, z, ts in sensors.iter_acc(db_path):
        log_time = t0_epoch + (ts - t0_sensor)
        msg = Imu(
            timestamp=_ts(log_time),
            frame_id=frame_id,
            linear_acceleration=Vector3(x=float(x), y=float(y), z=float(z)),
        )
        writer.write_message(channel, msg, log_time=log_time, publish_time=log_time)
        if pbar:
            pbar.update(1)


def _write_gyro(writer, db_path, channel, frame_id, t0_sensor, t0_epoch, pbar):
    for x, y, z, ts in sensors.iter_gyro(db_path):
        log_time = t0_epoch + (ts - t0_sensor)
        msg = Imu(
            timestamp=_ts(log_time),
            frame_id=frame_id,
            angular_velocity=Vector3(x=float(x), y=float(y), z=float(z)),
        )
        writer.write_message(channel, msg, log_time=log_time, publish_time=log_time)
        if pbar:
            pbar.update(1)


def _write_mag(writer, db_path, channel, frame_id, t0_sensor, t0_epoch, pbar):
    for mx, my, mz, ts in sensors.iter_mag(db_path):
        log_time = t0_epoch + (ts - t0_sensor)
        msg = MagneticField(
            timestamp=_ts(log_time),
            frame_id=frame_id,
            magnetic_field=Vector3(x=float(mx), y=float(my), z=float(mz)),
        )
        writer.write_message(channel, msg, log_time=log_time, publish_time=log_time)
        if pbar:
            pbar.update(1)


def _write_video(writer, video_path, channel, frame_id, t0_epoch, pbar, video_codec="h265", h264_encoder="libx264"):
    if video_codec == "h264":
        packet_iter = vid.iter_video_packets_h264(video_path, encoder_name=h264_encoder)
    else:
        packet_iter = vid.iter_video_packets(video_path)

    for pts_ns, data, keyframe in packet_iter:
        log_time = t0_epoch + pts_ns
        msg = CompressedVideo(
            timestamp=_ts(log_time),
            frame_id=frame_id,
            data=data,
            format=video_codec,
        )
        writer.write_message(channel, msg, log_time=log_time, publish_time=log_time)
        if pbar:
            pbar.update(1)


def convert_segment(
    session_dir: Path,
    segment_num: int,
    output_dir: Path,
    tz_offset_hours: int = 0,
    video_codec: str = "h265",
    h264_encoder: str = "libx264",
    verbose: bool = False,
) -> Path:
    """Convert one segment to MCAP. Returns the output file path."""
    prefix = f"robocap_segment{segment_num}"

    imu_left_db = session_dir / f"{prefix}_imu_left.db"
    imu_right_db = session_dir / f"{prefix}_imu_right.db"
    mag_db = session_dir / f"{prefix}_mag_middle.db"

    video_map = {
        "/video/left":        (session_dir / f"{prefix}_video_left.mp4",        "video_left"),
        "/video/left/eye":    (session_dir / f"{prefix}_video_left_eye.mp4",    "video_left_eye"),
        "/video/left/front":  (session_dir / f"{prefix}_video_left_front.mp4",  "video_left_front"),
        "/video/right":       (session_dir / f"{prefix}_video_right.mp4",       "video_right"),
        "/video/right/eye":   (session_dir / f"{prefix}_video_right_eye.mp4",   "video_right_eye"),
        "/video/right/front": (session_dir / f"{prefix}_video_right_front.mp4", "video_right_front"),
    }

    t0_sensor = sensors.segment_t0(imu_left_db, imu_right_db, mag_db)
    t0_epoch = _session_start_utc_ns(session_dir.name, tz_offset_hours)

    output_path = output_dir / f"{session_dir.name}_segment{segment_num}.mcap"
    output_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(t0_epoch / 1e9, tz=timezone.utc)
        print(f"  session start (UTC): {dt.isoformat()}  ->  {output_path.name}")

    sensor_tasks = [
        (imu_left_db,  "acc_data"),
        (imu_left_db,  "gyro_data"),
        (imu_right_db, "acc_data"),
        (imu_right_db, "gyro_data"),
        (mag_db,       "mag_data"),
    ]

    total_sensor_rows = sum(sensors.row_count(db, tbl) for db, tbl in sensor_tasks)
    total_video_pkts = sum(
        vid.estimate_packet_count(vp) for vp, _ in video_map.values() if vp.exists()
    )

    with open(output_path, "wb") as f:
        with Writer(f) as writer:
            with tqdm(
                total=total_sensor_rows + total_video_pkts,
                desc=f"segment{segment_num}",
                unit="msg",
                disable=not verbose,
            ) as pbar:
                _write_acc( writer, imu_left_db,  "/imu/left/acc",   "left_imu",  t0_sensor, t0_epoch, pbar)
                _write_gyro(writer, imu_left_db,  "/imu/left/gyro",  "left_imu",  t0_sensor, t0_epoch, pbar)
                _write_acc( writer, imu_right_db, "/imu/right/acc",  "right_imu", t0_sensor, t0_epoch, pbar)
                _write_gyro(writer, imu_right_db, "/imu/right/gyro", "right_imu", t0_sensor, t0_epoch, pbar)
                _write_mag( writer, mag_db,       "/mag/middle",     "mag_middle",t0_sensor, t0_epoch, pbar)
                for channel, (vpath, fid) in video_map.items():
                    if vpath.exists():
                        _write_video(writer, vpath, channel, fid, t0_epoch, pbar,
                                     video_codec=video_codec, h264_encoder=h264_encoder)
                    elif verbose:
                        print(f"    [skip] {vpath.name} not found")

    return output_path


def discover_segments(session_dir: Path) -> list[int]:
    """Return sorted list of segment numbers present in session_dir."""
    nums = set()
    for f in session_dir.iterdir():
        m = re.match(r"robocap_segment(\d+)_", f.name)
        if m:
            nums.add(int(m.group(1)))
    return sorted(nums)
