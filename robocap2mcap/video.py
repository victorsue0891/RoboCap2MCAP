"""Extracts / transcodes video packets from MP4 using PyAV."""

import fractions
from pathlib import Path
from typing import Iterator, Tuple
import av


def _hvcC_to_annexb(data: bytes) -> bytes:
    """Convert hvcC-format NAL units (4-byte length prefix) to Annex B (start codes).

    MP4 containers store H.265 in hvcC format; Foxglove Studio expects Annex B.
    """
    result = bytearray()
    offset = 0
    while offset + 4 <= len(data):
        nal_len = int.from_bytes(data[offset : offset + 4], "big")
        if nal_len == 0 or offset + 4 + nal_len > len(data):
            break
        result += b"\x00\x00\x00\x01"
        result += data[offset + 4 : offset + 4 + nal_len]
        offset += 4 + nal_len
    return bytes(result)


def iter_video_packets(video_path: Path) -> Iterator[Tuple[int, bytes, bool]]:
    """Yield (pts_ns, annexb_bytes, is_keyframe) — H.265 packets, no decode."""
    container = av.open(str(video_path))
    try:
        stream = container.streams.video[0]
        time_base = stream.time_base
        for packet in container.demux(stream):
            if packet.size == 0:
                continue
            pts = packet.pts if packet.pts is not None else packet.dts
            if pts is None:
                continue
            pts_ns = int(float(pts * time_base) * 1_000_000_000)
            yield pts_ns, _hvcC_to_annexb(bytes(packet)), bool(packet.is_keyframe)
    finally:
        container.close()


# GPU-accelerated encoders tried in order; libx264 is the CPU fallback.
_H264_ENCODER_CANDIDATES = [
    ("h264_nvenc", "NVIDIA NVENC",   {"preset": "p1", "rc": "constqp", "qp": "23"}),
    ("h264_qsv",   "Intel QuickSync",{"preset": "veryfast", "global_quality": "23"}),
    ("h264_amf",   "AMD AMF",        {}),
    ("libx264",    "CPU (libx264)",  {"preset": "ultrafast", "tune": "zerolatency", "crf": "23"}),
]


def detect_h264_encoder() -> Tuple[str, str]:
    """Return (encoder_name, label) for the best available H.264 encoder.

    Actually tries to open each encoder to confirm hardware/driver availability.
    """
    for name, label, opts in _H264_ENCODER_CANDIDATES:
        try:
            probe = av.CodecContext.create(name, "w")
            probe.width     = 64
            probe.height    = 64
            probe.pix_fmt   = "yuv420p"
            probe.framerate = fractions.Fraction(30)
            probe.time_base = fractions.Fraction(1, 90000)
            if opts:
                probe.options = opts
            probe.open()
            return name, label
        except Exception:
            continue
    return "libx264", "CPU (libx264)"


def _encoder_options(encoder_name: str) -> dict:
    for name, _, opts in _H264_ENCODER_CANDIDATES:
        if name == encoder_name:
            return opts
    return {}


def iter_video_packets_h264(
    video_path: Path,
    encoder_name: str = "libx264",
) -> Iterator[Tuple[int, bytes, bool]]:
    """Decode H.265 and re-encode as H.264 Annex B.

    Yields (pts_ns, h264_bytes, is_keyframe).
    pts_ns is taken from the original decoded frame PTS so timeline alignment
    is preserved.  With zerolatency/low-latency settings there are no B-frames,
    so each input frame produces exactly one output packet.
    """
    container = av.open(str(video_path))
    try:
        in_stream = container.streams.video[0]
        time_base = in_stream.time_base
        fps = in_stream.average_rate or fractions.Fraction(30)

        enc = av.CodecContext.create(encoder_name, "w")
        enc.width      = in_stream.codec_context.width
        enc.height     = in_stream.codec_context.height
        enc.pix_fmt    = "yuv420p"
        enc.framerate  = fps
        enc.time_base  = fractions.Fraction(1, 90000)
        opts = _encoder_options(encoder_name)
        if opts:
            enc.options = opts
        enc.open()

        for frame in container.decode(in_stream):
            pts_ns = int(float(frame.pts * time_base) * 1_000_000_000)
            if frame.format.name != "yuv420p":
                frame = frame.reformat(format="yuv420p")
            for pkt in enc.encode(frame):
                yield pts_ns, bytes(pkt), bool(pkt.is_keyframe)

        # Flush encoder buffer (empty with zerolatency, but required for correctness)
        for pkt in enc.encode(None):
            yield 0, bytes(pkt), bool(pkt.is_keyframe)
    finally:
        container.close()


def estimate_packet_count(video_path: Path) -> int:
    """Rough frame count estimate for tqdm; uses duration × fps."""
    container = av.open(str(video_path))
    try:
        stream = container.streams.video[0]
        duration_s = float(stream.duration * stream.time_base) if stream.duration else 0.0
        fps = float(stream.average_rate) if stream.average_rate else 30.0
        return max(1, int(duration_s * fps))
    finally:
        container.close()
