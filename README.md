# RoboCap2MCAP

Convert [RoboCap](https://github.com/frodobots-org) sensor captures to [MCAP](https://mcap.dev/) format
for playback in [Foxglove Studio](https://foxglove.dev/), and convert back again.

| Tool | Direction |
|------|-----------|
| `convert.py` | RoboCap session → MCAP |
| `deconvert.py` | MCAP → RoboCap session |

## Channel mapping

Each RoboCap segment (3 SQLite databases + 6 H.265 videos) becomes one MCAP file with 11 channels:

| Source file | Table / stream | MCAP channel | Schema |
|-------------|---------------|--------------|--------|
| `*_imu_left.db` | `acc_data` | `/imu/left/acc` | `foxglove.Imu` |
| `*_imu_left.db` | `gyro_data` | `/imu/left/gyro` | `foxglove.Imu` |
| `*_imu_right.db` | `acc_data` | `/imu/right/acc` | `foxglove.Imu` |
| `*_imu_right.db` | `gyro_data` | `/imu/right/gyro` | `foxglove.Imu` |
| `*_mag_middle.db` | `mag_data` | `/mag/middle` | `foxglove.MagneticField` |
| `*_video_left.mp4` | video stream | `/video/left` | `foxglove.CompressedVideo` |
| `*_video_left_eye.mp4` | video stream | `/video/left/eye` | `foxglove.CompressedVideo` |
| `*_video_left_front.mp4` | video stream | `/video/left/front` | `foxglove.CompressedVideo` |
| `*_video_right.mp4` | video stream | `/video/right` | `foxglove.CompressedVideo` |
| `*_video_right_eye.mp4` | video stream | `/video/right/eye` | `foxglove.CompressedVideo` |
| `*_video_right_front.mp4` | video stream | `/video/right/front` | `foxglove.CompressedVideo` |

**IMU/mag values** are stored as raw LSB integers (ICM-42688-P / MMC5983MA counts, not converted to
physical units).

**Video** is stored as H.265 Annex B compressed packets — no re-encoding, original quality preserved.
Each `foxglove.CompressedVideo` message sets `format` (`"h265"` or `"h264"`, matching Foxglove's official
schema) so Foxglove Studio's video panel can pick the right decoder.

**Timestamps** in MCAP are UTC epoch nanoseconds, derived from the session folder name
(`YYYYMMDD_HHMMSS` parsed as UTC) plus each sensor sample's offset from the segment start.

## Requirements

- Python 3.10+
- FFmpeg — required by PyAV for video demuxing (`convert.py`) and by `deconvert.py` for writing MP4

```bash
pip install -r requirements.txt
```

## convert.py — RoboCap → MCAP

```bash
# Convert all segments in a session (H.265, default)
python convert.py robocap/20260707_074914_session13/

# Batch-convert every session under the robocap root
python convert.py robocap/

# Convert specific segments only
python convert.py robocap/20260707_074914_session13/ --segments 1 2

# Transcode video to H.264 (for Windows without HEVC extensions)
# GPU is auto-detected: NVENC → QuickSync → AMF → CPU (libx264)
python convert.py robocap/20260707_074914_session13/ --codec h264

# Skip video — IMU and magnetometer only, much faster
python convert.py robocap/20260707_074914_session13/ --no-video

# Show per-message progress bars
python convert.py robocap/20260707_074914_session13/ --verbose
```

Output files are written to `output/` by default:

```
output/
  20260707_074914_session13_segment1.mcap
  20260707_074914_session13_segment2.mcap
```

### Options

```
usage: robocap2mcap [-h] [--output-dir PATH] [--segments N [N ...]] [--tz HOURS]
                    [--codec {h265,h264}] [--no-video] [--verbose] path

positional arguments:
  path                  Session directory or robocap root to batch-convert

options:
  --output-dir, -o      Output directory (default: output/)
  --segments, -s        Only convert these segment numbers (e.g. -s 1 2)
  --tz HOURS            UTC offset of session timestamps (default: 0 for UTC)
                        e.g. --tz 8 for CST, --tz 9 for JST
  --codec {h265,h264}   Video codec in MCAP (default: h265)
  --no-video            Skip video tracks; write IMU and mag only
  --verbose, -v         Show per-message progress bars
```

### H.264 option

Windows 11 does not include an HEVC decoder by default. If video does not play in Foxglove Desktop:

- Install **"HEVC Video Extensions from Device Manufacturer"** (free, Microsoft Store), **or**
- Reconvert with `--codec h264` — universally compatible, but larger files and slower to produce

H.264 transcoding is lossless in quality terms (CRF 23 / QP 23) but H.264 files are ~5× larger than the
original H.265 at equivalent quality. GPU encoding (NVENC / QuickSync / AMF) is auto-detected and
significantly faster than CPU.

### Video not displaying at all (blank/black panel, no decode error)

If Foxglove shows no image whatsoever for `/video/*` topics — not even a codec/HEVC error — the MCAP was
likely produced before `CompressedVideo.proto` was fixed to match Foxglove's actual schema (see
[Changelog](#changelog)). Foxglove's video panel expects a `format` field to pick the decoder; older
files from this converter wrote `codec`/`keyframe_only` instead, which Foxglove silently ignores.
**Reconvert the session** with the current `convert.py` — old MCAP files cannot be fixed in place.

## deconvert.py — MCAP → RoboCap

Reconstructs the original RoboCap file structure from an MCAP produced by `convert.py`.

```bash
# Restore a single segment
python deconvert.py output/20260707_074914_session13_segment1.mcap

# Restore all segments in a directory
python deconvert.py output/

# SQLite DBs only (skip video)
python deconvert.py output/ --no-video

# Custom output directory
python deconvert.py output/ --output-dir robocap_restored/
```

Output is written under `robocap_restored/<session_name>/`:

```
robocap_restored/
  20260707_074914_session13/
    robocap_segment1_imu_left.db
    robocap_segment1_imu_right.db
    robocap_segment1_mag_middle.db
    robocap_segment1_video_left.mp4
    robocap_segment1_video_left_eye.mp4
    robocap_segment1_video_left_front.mp4
    robocap_segment1_video_right.mp4
    robocap_segment1_video_right_eye.mp4
    robocap_segment1_video_right_front.mp4
```

### Options

```
usage: deconvert [-h] [--output-dir DIR] [--tz HOURS] [--no-video] [--verbose] path

positional arguments:
  path                  MCAP file or directory containing MCAP files

options:
  --output-dir, -o      Root output directory (default: robocap_restored/)
  --tz HOURS            UTC offset — must match the value used in convert.py (default: 0)
  --no-video            Skip video reconstruction; write SQLite DBs only
  --verbose, -v         Show per-packet progress bars
```

### Reconstruction fidelity

| Item | Fidelity |
|------|----------|
| IMU / mag x, y, z values | Exact (stored and recovered as integers) |
| SQLite timestamps | Relative to session start in ns (≈ 0 for first sample); original device monotonic clock values cannot be recovered |
| Video | Lossless — packets copied from MCAP without re-encoding |
| metadata `deviceid`, `username` | Empty (not stored in MCAP) |

## Performance

Tested on `20260707_074914_session13` (1920×1080 H.265, 6 cameras):

### convert.py — H.265 (default)

| Segment | Duration | MCAP size | Time |
|---------|----------|-----------|------|
| segment1 | 599.9 s | 1.8 GB | ~15 s |
| segment2 | 80.9 s | 237 MB | ~3 s |

### convert.py — H.264 (CPU transcoding)

| Segment | Duration | MCAP size | Time |
|---------|----------|-----------|------|
| segment2 | 80.9 s | ~1.2 GB | ~8 min |
| segment1 | 599.9 s | ~9 GB (est.) | ~60 min (est.) |

### deconvert.py

| Segment | MCAP size | Output | Time |
|---------|-----------|--------|------|
| segment2 | 237 MB | 3 DBs + 6 MP4 (~233 MB) | ~5 s |
| segment1 | 1.8 GB | 3 DBs + 6 MP4 (~1.73 GB) | ~21 s |

## Project layout

```
RoboCap2MCAP/
├── convert.py              # RoboCap → MCAP entry point
├── deconvert.py            # MCAP → RoboCap entry point
├── requirements.txt
├── robocap2mcap/
│   ├── cli.py              # Argument parsing and session discovery
│   ├── converter.py        # Per-segment conversion logic
│   ├── sensors.py          # SQLite readers for IMU and magnetometer
│   ├── video.py            # H.265 packet extraction and H.264 transcoding via PyAV
│   ├── foxglove/           # Compiled Foxglove protobuf Python modules
│   │   ├── Imu_pb2.py
│   │   ├── MagneticField_pb2.py
│   │   ├── CompressedVideo_pb2.py
│   │   ├── Vector3_pb2.py
│   │   └── Quaternion_pb2.py
│   └── proto/foxglove/     # Source .proto files
├── robocap/                # Input capture data (not tracked in git)
│   └── <session>/
│       ├── robocap_segment1_imu_left.db
│       ├── robocap_segment1_imu_right.db
│       ├── robocap_segment1_mag_middle.db
│       ├── robocap_segment1_video_left.mp4
│       └── ...
├── output/                 # MCAP output files (not tracked in git)
└── robocap_restored/       # deconvert.py output (not tracked in git)
```

## Data notes

- **IMU sample rate:** ~200–250 Hz (ICM-42688-P). Values are raw LSB counts. To convert to physical units:
  accel ÷ 2048 = g (±16 g range); gyro ÷ 16.4 = dps (±2000 dps range).
- **Magnetometer sample rate:** ~100 Hz (MMC5983MA).
- **Video:** H.265/HEVC, 1920×1080, ~30 fps. Segment length is variable — do not assume fixed duration.
- **Segment timestamps:** IMU/mag `timestamp` columns are device monotonic clock values in nanoseconds
  (uptime since boot, not epoch). The MCAP writer aligns them to UTC using the session folder name as the
  epoch anchor, so Foxglove Studio displays wall-clock time correctly.

## Changelog

- **2026-08-10:** Fixed `CompressedVideo.proto` — it had drifted from Foxglove's real
  `foxglove.CompressedVideo` schema, carrying an extra `bool keyframe_only` field and naming the codec
  field `codec` instead of the official `format`. Foxglove Studio's built-in video panel keys off a field
  literally named `format` to choose a decoder, so video silently failed to render regardless of
  `keyframe_only`'s value. MCAP files converted before this fix need to be regenerated with
  `python convert.py <session>` — see [Video not displaying at all](#video-not-displaying-at-all-blankblack-panel-no-decode-error).
