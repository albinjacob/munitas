"""Read and write WAV files in 16-bit samples, with nothing but the standard library and numpy.

The platform takes recordings only as `.wav`. This replaces `soundfile`, whose Windows and Linux packages carry libsndfile, a library under the LGPL: the
licence check (verify/v176_licences.py) keeps it out. Every kind of WAV the redaction step meets is read: whole-number samples of 8, 16, 24 or 32 bits and
floating-point samples of 32 or 64 bits, one channel or several. A file that is not a WAV, or is a kind of WAV not listed here (for example a compressed one),
is refused with a plain sentence rather than read wrongly.
"""
import io
import struct
import wave

import numpy as np

_PCM, _FLOAT, _EXTENSIBLE = 1, 3, 0xFFFE


def _read_all(source) -> bytes:
    """The bytes of a path, a file object or bytes."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "read"):
        return source.read()
    with open(source, "rb") as handle:
        return handle.read()


def _chunks(data: bytes):
    """Yield (name, body) for each chunk of a RIFF/WAVE file."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("this is not a WAV file: it does not start with the RIFF and WAVE marks")
    at = 12
    while at + 8 <= len(data):
        name = data[at:at + 4]
        size = struct.unpack("<I", data[at + 4:at + 8])[0]
        body = data[at + 8:at + 8 + size]
        yield name, body
        at += 8 + size + (size & 1)


def read_int16(source) -> tuple[np.ndarray, int]:
    """Return (samples as int16, sample rate). One channel gives shape (n,), several give (n, channels)."""
    fmt = None
    pcm = None
    for name, body in _chunks(_read_all(source)):
        if name == b"fmt " and fmt is None:
            if len(body) < 16:
                raise ValueError("this WAV file has a damaged format section")
            tag, channels, rate, _bytes_rate, _align, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == _EXTENSIBLE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, channels, rate, bits)
        elif name == b"data" and pcm is None:
            pcm = body
    if fmt is None or pcm is None:
        raise ValueError("this WAV file has no format section or no sound data")
    tag, channels, rate, bits = fmt
    if channels < 1:
        raise ValueError("this WAV file declares no channels")
    width = bits // 8
    frames = len(pcm) // (width * channels) if width else 0
    pcm = pcm[:frames * width * channels]
    if tag == _PCM and bits == 8:
        out = (np.frombuffer(pcm, dtype=np.uint8).astype(np.int16) - 128) << 8
    elif tag == _PCM and bits == 16:
        out = np.frombuffer(pcm, dtype="<i2").astype(np.int16)
    elif tag == _PCM and bits == 24:
        raw = np.frombuffer(pcm, dtype=np.uint8).reshape(-1, 3)
        out = ((raw[:, 2].astype(np.int32) << 24 | raw[:, 1].astype(np.int32) << 16 | raw[:, 0].astype(np.int32) << 8) >> 16).astype(np.int16)
    elif tag == _PCM and bits == 32:
        out = (np.frombuffer(pcm, dtype="<i4") >> 16).astype(np.int16)
    elif tag == _FLOAT and bits in (32, 64):
        floats = np.frombuffer(pcm, dtype="<f4" if bits == 32 else "<f8").astype(np.float64)
        out = np.clip(np.round(floats * 32768.0), -32768, 32767).astype(np.int16)
    else:
        raise ValueError(f"this kind of WAV file cannot be read (format {tag}, {bits} bits): send whole-number samples of 8, 16, 24 or 32 bits, or floating-point samples")
    return (out.reshape(-1, channels) if channels > 1 else out), rate


def write_int16(samples, rate: int) -> bytes:
    """A WAV file, 16-bit whole-number samples, as bytes. `samples` has shape (n,) or (n, channels).

    Whole-number samples are written as they are; floating-point samples are taken to lie between -1 and 1 and are scaled to the 16-bit range.
    """
    data = np.asarray(samples)
    if data.dtype.kind == "f":
        data = np.clip(np.round(data.astype(np.float64) * 32767.0), -32768, 32767).astype(np.int16)
    elif data.dtype != np.int16:
        data = np.clip(data.astype(np.int64), -32768, 32767).astype(np.int16)
    channels = 1 if data.ndim == 1 else data.shape[1]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(int(rate))
        out.writeframes(data.astype("<i2").tobytes())
    return buffer.getvalue()
