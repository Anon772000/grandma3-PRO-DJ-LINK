"""Audio-backed playhead, analysis and transport. No network operations in the audio callback."""
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time

import numpy as np

from .protocol import beat_index


@dataclass(frozen=True)
class Track:
    path: str
    title: str
    artist: str
    samples: np.ndarray
    sample_rate: int
    bpm: float
    estimated: bool
    preview: bytes
    detail: bytes

    @property
    def duration(self):
        return len(self.samples) / self.sample_rate


def peaks(samples, count):
    mono = np.max(np.abs(samples), axis=1)
    edges = np.linspace(0, len(mono), count + 1, dtype=np.int64)
    return np.array([float(np.max(mono[a:max(a + 1, b)])) if a < len(mono) else 0
                     for a, b in zip(edges[:-1], edges[1:])], dtype=np.float32)


def estimate_bpm(samples, sample_rate):
    # Energy onsets: useful starting point, not a rekordbox beat-grid replacement.
    mono = np.mean(samples[:sample_rate * 90], axis=1)
    hop = max(1, sample_rate // 100)
    count = len(mono) // hop
    if count < 300:
        return 128.0
    energy = np.sqrt(np.mean(mono[:count * hop].reshape(count, hop) ** 2, axis=1))
    onset = np.maximum(0, np.diff(energy))
    onset -= onset.mean()
    if np.max(np.abs(onset)) < 1e-5:
        return 128.0
    rate = sample_rate / hop
    low, high = round(rate * 60 / 180), round(rate * 60 / 70)
    correlations = [float(np.dot(onset[:-lag], onset[lag:])) for lag in range(low, high + 1)]
    lag = low + int(np.argmax(correlations))
    return round(60 * rate / lag, 2)


def load_track(path):
    import soundfile as sf
    from tinytag import TinyTag
    path = Path(path)
    info = sf.info(str(path))
    if info.frames < info.samplerate or info.frames * info.channels * 4 > 512 * 1024**2:
        raise ValueError('Choose a track at least 1 second long and under 512 MB when decoded.')
    if info.channels < 1 or info.channels > 8 or info.samplerate > 192000:
        raise ValueError('Unsupported channel count or sample rate.')
    samples, sample_rate = sf.read(str(path), dtype='float32', always_2d=True)
    samples = np.nan_to_num(samples, nan=0, posinf=1, neginf=-1)
    if samples.shape[1] == 1:
        samples = np.repeat(samples, 2, axis=1)
    elif samples.shape[1] > 2:
        samples = samples[:, :2].copy()
    np.clip(samples, -1, 1, out=samples)
    tags = None
    try:
        tags = TinyTag.get(str(path))
    except Exception:
        pass

    def tag(name, fallback):
        value = getattr(tags, name, None) if tags is not None else None
        if value is None and tags is not None:
            values = tags.other.get(name, [])
            value = values[0] if values else None
        return str(value)[:256] if value else fallback

    title, artist = tag('title', path.stem), tag('artist', 'Unknown artist')
    try:
        bpm = float(tag('bpm', '0'))
    except ValueError:
        bpm = 0
    estimated = not 20 <= bpm <= 240
    if estimated:
        bpm = estimate_bpm(samples, sample_rate)
    count = max(1, math.ceil(len(samples) / sample_rate * 150))
    detail_peaks = peaks(samples, count)
    normalizer = max(float(np.max(detail_peaks)), .001)
    detail = np.clip(np.rint(detail_peaks / normalizer * 31), 0, 31).astype(np.uint8).tobytes()
    overview = peaks(samples, 400)
    preview = b''.join(bytes([int(min(31, round(float(v) / normalizer * 31))), 5]) for v in overview)
    return Track(str(path), title, artist, samples, sample_rate, bpm, estimated, preview, detail)


class Player:
    def __init__(self):
        self.lock = threading.RLock()
        self.track = None
        self.track_id = 0
        self.cursor = 0.0
        self.anchor_frame = 0.0
        self.anchor_time = time.monotonic()
        self.playing = False
        self.rate = 1.0
        self.bpm = 128.0
        self.origin = 0.0
        self.master = True
        self.stream = None
        self.output_rate = 48000
        self.volume = .5
        self.audio_error = ''
        self.underflows = 0
        self.revision = 0

    def load(self, track):
        with self.lock:
            self.playing = False
            self.track = track
            self.track_id = self.track_id % 0xfffffffe + 1
            self.bpm = track.bpm
            self.origin = 0
            self.rate = 1
            self.seek(0)

    def snapshot(self):
        with self.lock:
            duration = self.track.duration if self.track else 0
            cursor = self.cursor
            if self.playing and self.track and self.stream is not None:
                advance = (time.monotonic() - self.anchor_time) * self.track.sample_rate * self.rate
                cursor = min(self.cursor, max(0, self.anchor_frame + advance))
            position = cursor / self.track.sample_rate if self.track else 0
            return dict(loaded=self.track is not None, playing=self.playing, position=position,
                        duration=duration, bpm=self.bpm, rate=self.rate, master=self.master,
                        track_id=self.track_id, beat_index=beat_index(position, self.bpm, self.origin),
                        revision=self.revision, origin=self.origin, track=self.track)

    def seek(self, seconds):
        with self.lock:
            self.cursor = max(0, min(seconds, self.track.duration)) * self.track.sample_rate if self.track else 0
            self.anchor_frame = self.cursor
            self.anchor_time = time.monotonic()
            self.revision += 1

    def configure_grid(self, bpm, origin):
        if not math.isfinite(bpm) or not 20 <= bpm <= 240 or not math.isfinite(origin):
            raise ValueError('BPM must be 20–240 and downbeat offset must be a finite number.')
        with self.lock:
            if not self.track or not 0 <= origin <= self.track.duration:
                raise ValueError('The downbeat offset must be within the loaded track.')
            self.bpm = bpm
            self.origin = origin
            self.revision += 1

    def set_rate(self, value):
        value = float(value)
        if not math.isfinite(value) or not .84 <= value <= 1.16:
            raise ValueError('Tempo must be within ±16%.')
        with self.lock:
            self.rate = value
            self.revision += 1

    def render(self, frames, delay=0):
        with self.lock:
            output = np.zeros((frames, 2), dtype=np.float32)
            if not self.playing or not self.track:
                return output
            step = self.track.sample_rate / self.output_rate * self.rate
            positions = self.cursor + np.arange(frames) * step
            valid = positions < len(self.track.samples) - 1
            positions = positions[valid]
            indices = positions.astype(np.int64)
            fractions = (positions - indices)[:, None]
            output[valid] = (self.track.samples[indices] * (1 - fractions) +
                             self.track.samples[indices + 1] * fractions) * self.volume
            self.anchor_frame = self.cursor
            self.anchor_time = time.monotonic() + max(0, delay)
            self.cursor = min(len(self.track.samples), self.cursor + frames * step)
            if self.cursor >= len(self.track.samples):
                self.playing = False
            return output

    def open_audio(self, device=None):
        import sounddevice as sd
        self.close_audio()
        output = sd.query_devices(device, 'output')
        self.output_rate = round(output['default_samplerate'])

        def callback(outdata, frames, timing, status):
            try:
                if status:
                    self.underflows += 1
                outdata[:] = self.render(frames, timing.outputBufferDacTime - timing.currentTime)
            except Exception as exc:
                outdata.fill(0)
                self.audio_error = str(exc)
                self.playing = False

        self.stream = sd.OutputStream(device=device, samplerate=self.output_rate, channels=2,
                                      dtype='float32', blocksize=512, latency='low', callback=callback)
        self.stream.start()
        self.audio_error = ''

    def close_audio(self):
        stream, self.stream = self.stream, None
        self.playing = False
        if stream is not None:
            stream.stop()
            stream.close()

    def toggle_play(self):
        with self.lock:
            if not self.track:
                raise ValueError('Choose a track first.')
            if self.stream is None:
                raise ValueError('Choose a working audio output first.')
            if self.cursor >= len(self.track.samples):
                self.seek(0)
            self.playing = not self.playing
            self.revision += 1

    def metadata(self, track_id):
        with self.lock:
            return self.track if self.track_id == track_id else None
