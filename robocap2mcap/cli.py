"""Command-line interface for RoboCap → MCAP converter."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import robocap2mcap  # noqa: F401 — side-effect: sets up sys.path for foxglove protos

from robocap2mcap.converter import convert_segment, discover_segments
from robocap2mcap.video import detect_h264_encoder


def _find_sessions(path: Path) -> list[Path]:
    """
    Accept either:
      - a session directory (contains robocap_segment*.db files directly), or
      - a robocap root dir (contains session sub-directories).
    """
    # Direct session dir?
    if any(path.glob("robocap_segment*_imu_left.db")):
        return [path]
    # Root dir containing session sub-dirs?
    sessions = [d for d in sorted(path.iterdir()) if d.is_dir() and any(d.glob("robocap_segment*_imu_left.db"))]
    if sessions:
        return sessions
    print(f"[error] No RoboCap session data found in: {path}", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="robocap2mcap",
        description="Convert RoboCap sensor captures to MCAP format.",
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Session directory (e.g. robocap/20260707_074914_session13/) "
             "or robocap root directory to batch-convert all sessions.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("output"),
        help="Output directory for MCAP files (default: output/).",
    )
    parser.add_argument(
        "--segments", "-s",
        nargs="+",
        type=int,
        metavar="N",
        help="Only convert specific segment numbers (e.g. --segments 1 2).",
    )
    parser.add_argument(
        "--tz",
        type=int,
        default=0,
        metavar="HOURS",
        help="UTC offset of session timestamps (default: 0 for UTC). "
             "E.g. --tz 8 for CST/UTC+8, --tz 9 for JST.",
    )
    parser.add_argument(
        "--codec",
        choices=["h265", "h264"],
        default="h265",
        help="Video codec in MCAP (default: h265). Use h264 for Windows without "
             "HEVC extensions — transcodes on-the-fly, slower but universally compatible.",
    )
    parser.add_argument(
        "--no-video",
        action="store_true",
        help="Skip video tracks; write only IMU and magnetometer data.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-message progress bars.",
    )

    args = parser.parse_args(argv)

    h264_encoder_name = "libx264"
    if args.codec == "h264" and not args.no_video:
        h264_encoder_name, label = detect_h264_encoder()
        print(f"H.264 transcoding enabled — encoder: {label}")

    if args.no_video:
        # Monkey-patch video writing to be a no-op
        import robocap2mcap.converter as _conv
        _conv._write_video = lambda *a, **kw: None

    sessions = _find_sessions(args.path)

    total_files = 0
    t_start = time.perf_counter()

    for session_dir in sessions:
        segs = discover_segments(session_dir)
        if not segs:
            print(f"[warn] No segments found in {session_dir.name}")
            continue

        if args.segments:
            segs = [s for s in segs if s in args.segments]
            if not segs:
                print(f"[warn] Requested segments not found in {session_dir.name}")
                continue

        print(f"\nSession: {session_dir.name}  ({len(segs)} segment(s): {segs})")

        for seg_num in segs:
            t0_seg = time.perf_counter()
            out = convert_segment(
                session_dir=session_dir,
                segment_num=seg_num,
                output_dir=args.output_dir,
                tz_offset_hours=args.tz,
                video_codec=args.codec,
                h264_encoder=h264_encoder_name,
                verbose=args.verbose,
            )
            elapsed = time.perf_counter() - t0_seg
            size_mb = out.stat().st_size / 1024 / 1024
            print(f"  [OK] segment{seg_num}  ->  {out.name}  ({size_mb:.1f} MB, {elapsed:.1f}s)")
            total_files += 1

    total_elapsed = time.perf_counter() - t_start
    print(f"\nDone. {total_files} file(s) written in {total_elapsed:.1f}s.")


if __name__ == "__main__":
    main()
