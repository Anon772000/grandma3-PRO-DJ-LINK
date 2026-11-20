"""Independent Pro DJ Link packet sender for the plugin's loopback Test mode."""
import argparse
import json
from pathlib import Path
import socket
import struct
import time


def header(kind, size, player):
    data = bytearray(size)
    data[:10] = b'Qspt1WmJOL'
    data[10] = kind
    offset = 11 if kind == 0x28 else 12
    name = f'CDJLINK TEST {player}'.encode()
    data[offset:offset + len(name)] = name
    data[0x21] = player
    return data


def announce(player):
    data = header(6, 54, player)
    data[0x20:0x26] = bytes([1, 2, 0, 54, player, 1])
    data[0x26:0x2c] = bytes([2, 0, 0, 0, 0, player])
    data[0x2c:0x30] = bytes([127, 0, 0, 1])
    data[0x30] = 2
    data[0x34] = 1
    return data


def status(player, bpm, playing, master, beat, counter):
    data = header(0x0a, 0xd4, player)
    data[0x20] = 3
    struct.pack_into('>H', data, 0x22, 0xb0)
    data[0x24] = player
    data[0x7b] = 3 if playing else 5
    data[0x89] = (0x40 if playing else 0) | (0x20 if master else 0)
    struct.pack_into('>I', data, 0x8c, 0x100000)
    struct.pack_into('>H', data, 0x92, round(bpm * 100))
    data[0xa6] = beat
    struct.pack_into('>I', data, 0xc8, counter)
    return data


def beat_packet(player, bpm, beat):
    data = header(0x28, 96, player)
    data[0x1f] = 1
    struct.pack_into('>H', data, 0x22, 60)
    period = round(60000 / bpm)
    for offset, beats in [(0x24, 1), (0x28, 2), (0x2c, 5 - beat), (0x30, 4), (0x34, 9 - beat), (0x38, 8)]:
        struct.pack_into('>I', data, offset, period * beats)
    struct.pack_into('>I', data, 0x54, 0x100000)
    struct.pack_into('>H', data, 0x5a, round(bpm * 100))
    data[0x5c] = beat
    data[0x5f] = player
    return data


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--scenario', action='store_true', help='A -> B master handoff, pause, disconnect; 15 seconds')
    args.add_argument('--bpm', type=float, default=128)
    args.add_argument('--player', type=int, choices=range(1, 7), default=1)
    args.add_argument('--seconds', type=float, default=30)
    options = args.parse_args()
    if not 20 <= options.bpm <= 300:
        args.error('BPM must be 20-300')
    duration = 15 if options.scenario else options.seconds
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.bind(('127.0.0.1', 0))
    start = time.monotonic()
    next_status = next_presence = 0
    next_beats = {1: 0, 2: 3}
    beats = {1: 1, 2: 1}
    counter = 0
    log = []
    while (elapsed := time.monotonic() - start) < duration:
        active = 1 if elapsed < 3 else 2 if elapsed < 6 else None
        connected = elapsed < 8 if options.scenario else True
        players = [1, 2] if options.scenario else [options.player]
        if connected:
            if elapsed >= next_presence:
                for player in players:
                    sender.sendto(announce(player), ('127.0.0.1', 50000))
                next_presence = elapsed + 1
            if elapsed >= next_status:
                counter += 1
                for player in players:
                    bpm = (128 if player == 1 else 132) if options.scenario else options.bpm
                    playing = player == active if options.scenario else True
                    sender.sendto(status(player, bpm, playing, playing, beats.get(player, 1), counter), ('127.0.0.1', 50002))
                next_status = elapsed + .2
            for player in players:
                playing = player == active if options.scenario else True
                bpm = (128 if player == 1 else 132) if options.scenario else options.bpm
                if playing and elapsed >= next_beats.get(player, 0):
                    beat = beats.get(player, 1)
                    data = beat_packet(player, bpm, beat)
                    sender.sendto(data, ('127.0.0.1', 50001))
                    if options.scenario:
                        sender.sendto(data, ('127.0.0.1', 50001))  # duplicate datagram
                    log.append(dict(player=player, beat=beat, bpm=bpm, time=time.time(), elapsed=elapsed))
                    beats[player] = beat % 4 + 1
                    next_beats[player] = elapsed + 60 / bpm
        time.sleep(.002)
    sender.close()
    (Path(__file__).parent / 'simulator-packets.json').write_text(json.dumps(log, indent=2))
    print(f'Sent {len(log)} independent beat packets over loopback UDP; scenario={options.scenario}')


if __name__ == '__main__':
    main()
