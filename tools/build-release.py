"""Build and verify portable grandMA3 plugins and a reproducible USB release ZIP."""

import argparse
import base64
import hashlib
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO


ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.1.0-rc.1'
MA_VERSION = '2.4.2.2'
NAME = 'grandMA3 PRO DJ LINK'
ARCHIVE_NAME = f'grandma3-pro-dj-link-v{VERSION}.zip'
PLUGIN_FOLDER = 'grandMA3/gma3_library/datapools/plugins/'


def read_source(path):
    return (ROOT / path).read_text(encoding='utf-8').replace('\r\n', '\n')


def embed_plugin(name, code):
    tree = ET.Element('GMA3', DataVersion=MA_VERSION)
    plugin = ET.SubElement(tree, 'UserPlugin', Name=name,
                           Author='Harrison (Anon772000)', Version=VERSION.split('-')[0] + '.0')
    # No external FileName: import uses the embedded FileContent.
    component = ET.SubElement(plugin, 'ComponentLua', Name=name)
    chunks = [code[i:i + 1024] for i in range(0, len(code), 1024)]
    content = ET.SubElement(component, 'FileContent', Size=str(len(chunks)))
    for chunk in chunks:
        ET.SubElement(content, 'Block', Base64=base64.b64encode(chunk).decode('ascii'))
    ET.indent(tree, space='    ')
    xml = ET.tostring(tree, encoding='utf-8', xml_declaration=True) + b'\n'
    parsed = ET.fromstring(xml)
    stored = parsed.find('./UserPlugin/ComponentLua/FileContent')
    assert stored is not None
    assert int(stored.get('Size')) == len(stored.findall('Block'))
    recovered = b''.join(base64.b64decode(b.get('Base64'), validate=True)
                         for b in stored.findall('Block'))
    assert recovered == code, f'Embedded Lua does not match {name}'
    return xml


def build_files():
    lua = ('-- ' + NAME + ' v' + VERSION + ' | MIT License\n'
           + 'local Core = (function()\n' + read_source('src/cdj-link-core.lua')
           + '\nend)()\nlocal Network = (function()\n' + read_source('src/cdj-link-network.lua')
           + '\nend)()\n' + read_source('src/cdj-link-host.lua')).encode('utf-8')
    stop = read_source('src/cdj-stop-all.lua').encode('utf-8')
    artifacts = {
        'grandma3-pro-dj-link.xml': embed_plugin(NAME, lua),
        'grandma3-pro-dj-link.lua': lua,
        'cdj-stop-all.xml': embed_plugin('CDJ Stop All', stop),
        'cdj-stop-all.lua': stop,
    }
    payload = {PLUGIN_FOLDER + name: content for name, content in artifacts.items()}
    for name in ('README.md', 'LICENSE', 'CHANGELOG.md'):
        payload[name] = read_source(name).encode('utf-8')
    for path in sorted((ROOT / 'docs').rglob('*')):
        if path.is_file():
            relative = path.relative_to(ROOT).as_posix()
            payload[relative] = (read_source(relative).encode('utf-8')
                                 if path.suffix == '.md' else path.read_bytes())
    manifest = ''.join(f'{hashlib.sha256(content).hexdigest()}  {name}\n'
                       for name, content in sorted(payload.items()))
    payload['SHA256SUMS.txt'] = manifest.encode('ascii')
    archive = BytesIO()
    # Stored entries also avoid differences between platforms' zlib versions.
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as z:
        for name, content in sorted(payload.items()):
            # A stable timestamp and permissions make builds byte-for-byte reproducible.
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 21, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, content)
    zipped = archive.getvalue()
    with zipfile.ZipFile(BytesIO(zipped)) as z:
        assert z.testzip() is None, 'ZIP integrity check failed'
        assert set(z.namelist()) == set(payload), 'ZIP file list changed'
        for name, content in payload.items():
            assert z.read(name) == content, f'ZIP content mismatch: {name}'
    artifacts[ARCHIVE_NAME] = zipped
    artifacts['SHA256SUMS.txt'] = ''.join(
        f'{hashlib.sha256(content).hexdigest()}  {name}\n'
        for name, content in sorted(artifacts.items())
    ).encode('ascii')
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='Compare existing files to a fresh build without writing')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    artifacts = build_files()
    if args.check:
        bad = [name for name, content in artifacts.items()
               if not (args.output / name).is_file()
               or (args.output / name).read_bytes() != content]
        if bad:
            print('Distribution is missing or stale: ' + ', '.join(bad), file=sys.stderr)
            return 1
        print(f'Verified {len(artifacts)} distribution files, embedded Lua and ZIP integrity.')
        return 0
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        (args.output / name).write_bytes(content)
    print(f'Built {NAME} v{VERSION}: {args.output}')
    for name, content in artifacts.items():
        print(f'  {name} ({len(content):,} bytes)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
