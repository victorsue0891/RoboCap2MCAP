# RoboCap2MCAP

Convert [RoboCap](https://github.com/frodobots-org) sensor captures to [MCAP](https://mcap.dev/) format for playback in [Foxglove Studio](https://foxglove.dev/).

## What it does

Each RoboCap segment contains 3 SQLite sensor databases and 6 H.265 video streams. This tool packages them into a single MCAP file per segment with standard Foxglove protobuf schemas, ready to open in Foxglove Studio.

| Source | MCAP Channel | Schema |
|--------|-------------|--------|
| `*_imu_left.db` → `acc_data` | `/imu/left/acc` | `foxglove.Imu` |
| `*_imu_left.db` → `gyro_data` | `/imu/left/gyro` | `foxglove.Imu` |
| `*_imu_right.db` → `acc_data` | `/imu/right/acc` | `foxglove.Imu` |
| `*_imu_right.db` → `gyro_data` | `/imu/right/gyro` | `foxglove.Imu` |
| `*_mag_middle.db` → `mag_data` | `/mag/middle` | `foxglove.MagneticField` |
| `*_video_left.mp4` | `/video/left` | `foxglove.CompressedVideo` |
| `*_video_left_eye.mp4` | `/video/left/eye` | `foxglove.CompressedVideo` |
| `*_video_left_front.mp4` | `/video/left/front` | `foxglove.CompressedVideo` |
| `*_video_right.mp4` | `/video/right` | `foxglove.CompressedVideo` |
| `*_video_right_eye.mp4` | `/video/right/eye` | `foxglove.CompressedVideo` |
| `*_video_right_front.mp4` | `/video/right/front` | `foxglove.CompressedVideo` |

**IMU values** are stored as raw LSB integers (ICM-42688-P counts, not converted to m/s² or rad/s).  
**Video** is embedded as H.265 compressed packets — no re-encoding, original quality preserved.  
**Timestamps** are relative to the first sensor sample in each segment (device monotonic clock → segment-start = 0).

## Requirements

- Python 3.10+
- FFmpeg (required by PyAV for video demuxing)

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Convert all segments in a session (H.265, default)
python convert.py robocap/20260707_074914_session13/

# Convert all sessions under the robocap root
python convert.py robocap/

# Convert specific segments only
python convert.py robocap/20260707_074914_session13/ --segments 1 2

# Transcode video to H.264 (for Windows without HEVC extensions)
# GPU acceleration is auto-detected (NVENC / QuickSync / AMF → CPU fallback)
python convert.py robocap/20260707_074914_session13/ --codec h264

# Custom output directory with progress bars
python convert.py robocap/20260707_074914_session13/ --output-dir output/ --verbose

# Skip video (IMU and magnetometer only — much faster)
python convert.py robocap/20260707_074914_session13/ --no-video

# Override timezone if session timestamps are not UTC
python convert.py robocap/20260707_074914_session13/ --tz 8   # CST/UTC+8
python convert.py robocap/20260707_074914_session13/ --tz 9   # JST
```

Output files are written to `output/` by default:

```
output/
  20260707_074914_session13_segment1.mcap
  20260707_074914_session13_segment2.mcap
```

### All options

```
usage: robocap2mcap [-h] [--output-dir PATH] [--segments N [N ...]] [--tz HOURS]
                    [--codec {h265,h264}] [--no-video] [--verbose] path

positional arguments:
  path                  Session directory or robocap root to batch-convert

options:
  --output-dir, -o      Output directory (default: output/)
  --segments, -s        Only convert these segment numbers (e.g. -s 1 2)
  --tz HOURS            UTC offset of session timestamps (default: 0 for UTC)
  --codec {h265,h264}   Video codec in MCAP (default: h265). Use h264 for
                        Windows without HEVC Video Extensions.
  --no-video            Skip video tracks; write IMU and mag only
  --verbose, -v         Show per-message progress bars
```

## Project layout

```
RoboCap2MCAP/
├── convert.py              # Entry point: python convert.py <args>
├── requirements.txt
├── robocap2mcap/
│   ├── cli.py              # Argument parsing and session discovery
│   ├── converter.py        # Segment conversion logic
│   ├── sensors.py          # SQLite readers for IMU and magnetometer
│   ├── video.py            # H.265 packet extraction via PyAV (no decode)
│   ├── foxglove/           # Compiled Foxglove protobuf Python modules
│   │   ├── Imu_pb2.py
│   │   ├── MagneticField_pb2.py
│   │   ├── CompressedVideo_pb2.py
│   │   ├── Vector3_pb2.py
│   │   └── Quaternion_pb2.py
│   └── proto/foxglove/     # Source .proto files (Foxglove schemas)
├── robocap/                # Input capture data
│   └── <session>/
│       ├── robocap_segment1_imu_left.db
│       ├── robocap_segment1_imu_right.db
│       ├── robocap_segment1_mag_middle.db
│       ├── robocap_segment1_video_left.mp4
│       └── ...
└── output/                 # MCAP output files
```

## Performance

Tested on `20260707_074914_session13` (two segments, 1920×1080 H.265, 6 cameras):

### `--codec h265` (default, no transcoding)

| Segment | Duration | MCAP size | Time |
|---------|----------|-----------|------|
| segment1 | 599.9 s | 1.8 GB | ~15 s |
| segment2 | 80.9 s | 237 MB | ~3 s |

### `--codec h264` (transcode H.265 → H.264)

| Segment | Duration | MCAP size | Time (CPU) |
|---------|----------|-----------|------------|
| segment2 | 80.9 s | ~1.2 GB | ~8 min |
| segment1 | 599.9 s | ~9 GB (est.) | ~60 min (est.) |

> H.264 output is larger than H.265 at equivalent quality. GPU encoding (NVENC/QSV/AMF)
> is auto-detected and significantly faster if available.

### When to use H.264

Windows 11 does not include an HEVC decoder by default. If Foxglove Desktop video
does not display, either:
- Install **"HEVC Video Extensions from Device Manufacturer"** (free, Microsoft Store), or
- Reconvert with `--codec h264` (universally compatible, larger files, slower conversion)

## Data notes

- IMU sample rate: ~200–250 Hz (ICM-42688-P). Values are raw counts; to convert to physical units use the ICM-42688-P datasheet scale factors (default: ±16g / 2048 LSB per g for accel, ±2000 dps / 16.4 LSB per dps for gyro).
- Magnetometer sample rate: ~100 Hz (MMC5983MA).
- Video codec: H.265/HEVC, 1920×1080, 30 fps. Foxglove Studio supports H.265 playback.
- Segment length is variable; do not assume fixed duration.
- There is no shared wall-clock epoch across sensors. Timestamps are relative to each segment's first sensor sample.
