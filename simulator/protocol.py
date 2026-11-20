"""Original packet encoders and dbserver fields, based on Deep Symmetry's wire docs."""
import math
import struct

MAGIC = b'Qspt1WmJOL'
MODEL = 'CDJ-3000 SIM'
MAX_FIELD = 2 * 1024 * 1024


def header(kind, size, player, offset=12):
    data = bytearray(size)
    data[:10] = MAGIC
    data[10] = kind
    data[offset:offset + len(MODEL)] = MODEL.encode('ascii')
    data[0x21] = player
    return data


def presence(player, ip, mac):
    import socket
    data = header(6, 54, player)
    data[0x20:0x26] = bytes([1, 2, 0, 54, player, 1])
    data[0x26:0x2c] = mac
    data[0x2c:0x30] = socket.inet_aton(ip)
    data[0x30] = 2
    data[0x34] = 1
    return bytes(data)


def status(player, state, counter):
    data = header(0x0a, 0x200, player)
    data[0x20] = 3
    struct.pack_into('>H', data, 0x22, len(data) - 0x24)
    data[0x24] = player
    data[0x28:0x2c] = bytes([player if state['loaded'] else 0, 3, 1, 0])
    struct.pack_into('>I', data, 0x2c, state['track_id'] if state['loaded'] else 0)
    data[0x7b] = 3 if state['playing'] else 5 if state['loaded'] else 0
    data[0x7c:0x80] = b'3.20'
    data[0x89] = (0x40 if state['playing'] else 0) | (0x20 if state['master'] else 0)
    struct.pack_into('>I', data, 0x8c, round(0x100000 * state['rate']))
    struct.pack_into('>H', data, 0x92, round(state['bpm'] * 100))
    struct.pack_into('>I', data, 0xa0, max(0, state['beat_index'] + 1))
    data[0xa6] = state['beat_index'] % 4 + 1
    struct.pack_into('>I', data, 0xc8, counter)
    return bytes(data)


def beat(player, state):
    data = header(0x28, 96, player, 11)
    data[0x1f] = 1
    struct.pack_into('>H', data, 0x22, 60)
    bar_beat = state['beat_index'] % 4 + 1
    for offset, count in [(0x24, 1), (0x28, 2), (0x2c, 5 - bar_beat),
                          (0x30, 4), (0x34, 9 - bar_beat), (0x38, 8)]:
        struct.pack_into('>I', data, offset, round(60000 / state['bpm'] * count))
    struct.pack_into('>I', data, 0x54, round(0x100000 * state['rate']))
    struct.pack_into('>H', data, 0x5a, round(state['bpm'] * 100))
    data[0x5c] = bar_beat
    data[0x5f] = player
    return bytes(data)


def position(player, state):
    data = header(0x0b, 60, player, 11)
    data[0x1f] = 1
    struct.pack_into('>H', data, 0x22, 24)
    struct.pack_into('>IIi', data, 0x24, max(1, int(state['duration'])),
                     round(state['position'] * 1000), round((state['rate'] - 1) * 10000))
    struct.pack_into('>I', data, 0x38, round(state['bpm'] * state['rate'] * 10))
    return bytes(data)


def number(value, size=4):
    return bytes([{1: 15, 2: 16, 4: 17}[size]]) + int(value).to_bytes(size, 'big')


def blob(value):
    return b'\x14' + len(value).to_bytes(4, 'big') + value if value else b''


def text(value):
    value = ''.join(c if ord(c) >= 32 else ' ' for c in str(value))[:256]
    raw = value.encode('utf-16-be', errors='replace') + b'\0\0'
    return b'\x26' + (len(raw) // 2).to_bytes(4, 'big') + raw


def message(tx, kind, arguments=()):
    if len(arguments) > 12:
        raise ValueError('Too many arguments')
    tags = bytes(3 if item[:1] == b'\x14' or not item else
                 2 if item[:1] == b'\x26' else 6 for item in arguments)
    return (number(0x872349ae) + number(tx) + number(kind, 2) + number(len(arguments), 1) +
            blob(tags.ljust(12, b'\0')) + b''.join(arguments))


def menu_item(tx, kind, label='', value=0):
    return message(tx, 0x4101, [number(0), number(value), number(0), text(label),
                               number(0), text(''), number(kind), number(0), number(0),
                               number(0), number(0), number(0)])


def receive_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise EOFError('Connection closed')
        result.extend(chunk)
    return bytes(result)


def receive_message(sock):
    header_data = receive_exact(sock, 32)
    if (header_data[:5] != number(0x872349ae) or header_data[5] != 17 or
            header_data[10] != 16 or header_data[13] != 15 or
            header_data[15:20] != b'\x14\0\0\0\x0c' or header_data[14] > 12):
        raise ValueError('Invalid dbserver header')
    arguments = []
    for tag in header_data[20:20 + header_data[14]]:
        if tag == 3 and arguments and arguments[-1] == 0:
            arguments.append(b'')
            continue
        field_tag = receive_exact(sock, 1)[0]
        if field_tag in (15, 16, 17) and tag == 6:
            arguments.append(int.from_bytes(receive_exact(sock, {15: 1, 16: 2, 17: 4}[field_tag]), 'big'))
        elif (field_tag, tag) in ((20, 3), (38, 2)):
            length = int.from_bytes(receive_exact(sock, 4), 'big') * (2 if tag == 2 else 1)
            if length > MAX_FIELD:
                raise ValueError('Oversized dbserver field')
            arguments.append(receive_exact(sock, length))
        else:
            raise ValueError('Unexpected dbserver field')
    return int.from_bytes(header_data[6:10], 'big'), int.from_bytes(header_data[11:13], 'big'), arguments


def beat_index(position_seconds, bpm, origin):
    return math.floor((position_seconds - origin) * bpm / 60 + 1e-7)
