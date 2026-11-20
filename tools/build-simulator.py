"""Package the Windows test player, its original demo and dependency notices."""
import argparse
import hashlib
from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import sys
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'PRO DJ LINK Test Player'


def demo(path):
    import numpy as np
    rate, bpm = 48000, 128
    audio = np.zeros((round(32 * 60 / bpm * rate), 2), dtype=np.float32)
    length = round(.07 * rate)
    t = np.arange(length) / rate
    for beat in range(32):
        start = round(beat * 60 / bpm * rate)
        pulse = .35 * np.sin(2 * np.pi * (880 if beat % 4 == 0 else 440) * t) * np.exp(-t * 70)
        audio[start:start + length] += pulse[:, None]
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes((audio * 32767).astype('<i2').tobytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Output directory outside the repository.')
    parser.add_argument('--package-only', action='store_true', help='Refresh docs and ZIP for an existing built application.')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Build this Windows application on Windows.')
    target = args.output.resolve()
    if target == ROOT or ROOT in target.parents:
        parser.error('Use an output directory outside the source repository.')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir',
                    '--name', NAME, '--distpath', str(target / 'application'),
                    '--workpath', str(target / 'build'), '--specpath', str(target / 'build'),
                    '--paths', str(ROOT), '--collect-all', '_soundfile_data',
               '--collect-all', '_sounddevice_data', str(ROOT / 'tools/cdj-simulator.py')]
    if not args.package_only:
        subprocess.run(command, cwd=ROOT, check=True)
    app = target / 'application' / NAME
    if not (app / (NAME + '.exe')).is_file():
        parser.error('Build the executable before using --package-only.')
    shutil.copyfile(ROOT / 'docs/standalone-player.md', app / 'START-HERE.md')
    shutil.copyfile(ROOT / 'docs/track-display.md', app / 'track-display.md')
    shutil.copyfile(ROOT / 'dist/grandma3-pro-dj-link.xml', app / 'grandma3-pro-dj-link-track-preview.xml')
    shutil.copyfile(ROOT / 'LICENSE', app / 'LICENSE')
    demo(app / 'Demo-128-BPM.wav')
    notices = app / 'third-party-licenses'
    notices.mkdir(exist_ok=True)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, notices / 'Python-LICENSE.txt')
    for component in ('tcl8.6', 'tk8.6'):
        license_file = Path(sys.base_prefix) / 'tcl' / component / 'license.terms'
        if license_file.is_file():
            shutil.copyfile(license_file, notices / (component + '-license.terms'))
    for filename in ('standalone-player.png', 'track-display-local-player.png'):
        destination = app / 'images' / filename
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'docs/images' / filename, destination)
    for name in ('numpy', 'sounddevice', 'soundfile', 'tinytag', 'cffi', 'pycparser', 'pyinstaller'):
        dist = metadata.distribution(name)
        for file in dist.files or ():
            if file.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'AUTHORS', 'NOTICE')):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = notices / name / str(file).replace('..', '_')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    # Include notices shipped alongside the bundled audio DLLs as well.
    for name in ('_soundfile_data', '_sounddevice_data'):
        module = __import__(name)
        base = Path(next(iter(module.__path__)))
        for file in base.rglob('*'):
            if file.is_file() and file.suffix.lower() not in ('.dll', '.py', '.pyc'):
                destination = notices / name / file.relative_to(base)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, destination)
    (notices / 'SOURCES.txt').write_text(
        'Dependencies retain their own licenses. The generated demo is original project audio.\n'
        'No commercial music or vendor firmware is included.\n'
        'Python/Tcl/Tk: https://www.python.org/ and https://www.tcl.tk/\n'
        'NumPy: https://github.com/numpy/numpy\n'
        'SoundDevice and PortAudio: https://github.com/spatialaudio/python-sounddevice and https://github.com/PortAudio/portaudio\n'
        'SoundFile and libsndfile: https://github.com/bastibe/python-soundfile and https://github.com/libsndfile/libsndfile\n'
        'TinyTag: https://github.com/tinytag/tinytag\n'
        'CFFI: https://cffi.readthedocs.io/\n'
        'PyInstaller: https://github.com/pyinstaller/pyinstaller (bootloader distribution exception)\n', encoding='utf-8')
    # Do not package arbitrary tracks placed in the output directory by a user.
    archive = target / 'pro-dj-link-test-player-windows-alpha.2.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for file in sorted(app.rglob('*')):
            if file.is_file():
                output.write(file, file.relative_to(app.parent))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    print(f'Application: {app / (NAME + ".exe")}\nArchive: {archive}\nSHA256: {digest}')


if __name__ == '__main__':
    main()
