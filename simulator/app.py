"""Native Windows-friendly test player. Audio files and analysis stay on this PC."""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .network import Network
from .player import Player, load_track

BG = '#10161e'
CARD = '#192330'
TEXT = '#f0f5fa'
MUTED = '#9daec1'
TEAL = '#42d5b4'


def clock(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f'{minutes}:{seconds:02d}'


def interfaces():
    result = [('This PC / onPC — 127.0.0.2', '127.0.0.2')]
    try:
        if sys.platform == 'win32':
            command = ('Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" } | '
                       'Select-Object InterfaceAlias,IPAddress | ConvertTo-Json -Compress')
            output = subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                             creationflags=subprocess.CREATE_NO_WINDOW, timeout=8)
            rows = json.loads(output.decode('utf-8', errors='replace'))
            for row in rows if isinstance(rows, list) else [rows]:
                result.append((f"{row['InterfaceAlias']} / {row['IPAddress']}", row['IPAddress']))
        else:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if not ip.startswith('127.'):
                    result.append((ip, ip))
    except Exception:
        pass
    return result


class App:
    def __init__(self, root, initial_file=None):
        self.root = root
        self.player = Player()
        self.network = Network(self.player)
        self.events = queue.Queue()
        self.loading = False
        self.dragging = False
        self.tap_times = deque(maxlen=8)
        self.adapters = [('This PC / onPC — 127.0.0.2', '127.0.0.2')]
        self.output_devices = [('System default', None)]
        root.title('PRO DJ LINK Test Player')
        root.geometry('1080x900')
        root.minsize(860, 730)
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 11), background=BG, foreground=TEXT)
        style.configure('TFrame', background=BG)
        style.configure('Card.TFrame', background=CARD)
        style.configure('TLabel', background=BG, foreground=TEXT)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('Title.TLabel', font=('Segoe UI', 24, 'bold'))
        style.configure('Track.TLabel', font=('Segoe UI', 20, 'bold'))
        style.configure('Time.TLabel', font=('Consolas', 20))
        style.configure('TButton', background='#28394c', foreground=TEXT, padding=(12, 9), borderwidth=0)
        style.map('TButton', background=[('active', '#3c536b'), ('disabled', '#202b36')])
        style.configure('Play.TButton', background=TEAL, foreground='#08291f', font=('Segoe UI', 12, 'bold'))
        style.map('Play.TButton', background=[('active', '#7be8d0')])
        style.configure('TEntry', fieldbackground='#243244', foreground=TEXT, padding=5)
        style.configure('TCombobox', fieldbackground='#243244', foreground=TEXT, padding=5)
        style.map('TCombobox', fieldbackground=[('readonly', '#243244')], selectbackground=[('readonly', '#243244')],
                  selectforeground=[('readonly', TEXT)])
        root.option_add('*TCombobox*Listbox.background', CARD)
        root.option_add('*TCombobox*Listbox.foreground', TEXT)

        viewport = tk.Canvas(root, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(root, orient='vertical', command=viewport.yview)
        scrollbar.pack(side='right', fill='y')
        viewport.pack(side='left', fill='both', expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(viewport, padding=24)
        content = viewport.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda _: viewport.configure(scrollregion=viewport.bbox('all')))
        viewport.bind('<Configure>', lambda e: viewport.itemconfigure(content, width=e.width))
        # Keep every setting reachable on smaller displays and at larger Windows scaling.
        root.bind('<MouseWheel>', lambda e: viewport.yview_scroll(-int(e.delta / 120), 'units')
                  if e.widget.winfo_class() not in ('TCombobox', 'TScale') else None)
        header = ttk.Frame(body); header.pack(fill='x')
        ttk.Label(header, text='PRO DJ LINK', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='  TEST PLAYER', foreground=TEAL).pack(side='left', pady=(8, 0))
        self.open_button = ttk.Button(header, text='Choose track…', command=self.choose_file)
        self.open_button.pack(side='right')
        ttk.Label(body, text='Play local audio. Send tempo, beats, playhead, track details and waveform analysis.',
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 18))

        self.title = ttk.Label(body, text='Choose a track to get started', style='Track.TLabel')
        self.title.pack(anchor='w')
        self.artist = ttk.Label(body, text='MP3 · WAV · FLAC · OGG · AIFF', style='Muted.TLabel')
        self.artist.pack(anchor='w', pady=(3, 8))
        self.canvas = tk.Canvas(body, height=128, bg=CARD, highlightthickness=0, cursor='hand2')
        self.canvas.pack(fill='x', pady=(6, 6))
        self.canvas.bind('<Button-1>', self.wave_seek)
        self.time_label = ttk.Label(body, text='0:00  /  0:00      REMAIN 0:00', style='Time.TLabel')
        self.time_label.pack(anchor='w')
        self.progress = ttk.Scale(body, from_=0, to=1000)
        self.progress.pack(fill='x', pady=(4, 10))
        self.progress.bind('<ButtonPress-1>', lambda _: setattr(self, 'dragging', True))
        self.progress.bind('<ButtonRelease-1>', self.finish_seek)

        controls = ttk.Frame(body); controls.pack(fill='x')
        self.play_button = ttk.Button(controls, text='▶  PLAY', style='Play.TButton', command=self.play)
        self.play_button.pack(side='left')
        ttk.Button(controls, text='Cue / start', command=lambda: self.cue()).pack(side='left', padx=6)
        ttk.Button(controls, text='−15 sec', command=lambda: self.seek_delta(-15)).pack(side='left')
        ttk.Button(controls, text='+15 sec', command=lambda: self.seek_delta(15)).pack(side='left', padx=6)
        self.beat_label = ttk.Label(controls, text='1   2   3   4', font=('Consolas', 18), foreground=TEAL)
        self.beat_label.pack(side='right')

        grid = ttk.Frame(body); grid.pack(fill='x', pady=(16, 0))
        ttk.Label(grid, text='Track BPM').grid(row=0, column=0, sticky='w')
        self.bpm = tk.StringVar(value='128.00')
        ttk.Entry(grid, textvariable=self.bpm, width=9).grid(row=1, column=0, sticky='w', padx=(0, 12))
        ttk.Label(grid, text='Beat 1 offset / sec').grid(row=0, column=1, sticky='w')
        self.origin = tk.StringVar(value='0.000')
        ttk.Entry(grid, textvariable=self.origin, width=11).grid(row=1, column=1, sticky='w', padx=(0, 12))
        ttk.Button(grid, text='Apply grid', command=self.apply_grid).grid(row=1, column=2, padx=(0, 6))
        ttk.Button(grid, text='Tap BPM', command=self.tap).grid(row=1, column=3, padx=(0, 6))
        ttk.Button(grid, text='Beat 1 here', command=self.downbeat).grid(row=1, column=4)
        self.analysis = ttk.Label(body, text='BPM estimates need checking. Set beat 1 to match the music.', style='Muted.TLabel')
        self.analysis.pack(anchor='w', pady=(6, 10))

        tempo_row = ttk.Frame(body); tempo_row.pack(fill='x')
        self.pitch_label = ttk.Label(tempo_row, text='Tempo +0.0%', width=16); self.pitch_label.pack(side='left')
        self.pitch = ttk.Scale(tempo_row, from_=-16, to=16, command=self.change_pitch)
        self.pitch.pack(side='left', fill='x', expand=True, padx=(0, 12))
        ttk.Button(tempo_row, text='Reset', command=lambda: self.pitch.set(0)).pack(side='left')
        ttk.Label(tempo_row, text='  Volume').pack(side='left')
        self.volume = ttk.Scale(tempo_row, from_=0, to=100, command=self.change_volume, length=120)
        self.volume.set(30); self.volume.pack(side='left')

        audio_row = ttk.Frame(body); audio_row.pack(fill='x', pady=(10, 14))
        ttk.Label(audio_row, text='Audio output  ').pack(side='left')
        self.output = ttk.Combobox(audio_row, state='readonly', values=['System default'])
        self.output.current(0); self.output.pack(side='left', fill='x', expand=True)
        self.output.bind('<<ComboboxSelected>>', lambda _: self.select_audio())
        ttk.Separator(body).pack(fill='x', pady=(0, 12))

        net = ttk.Frame(body); net.pack(fill='x')
        net.columnconfigure(0, weight=3); net.columnconfigure(1, weight=2)
        ttk.Label(net, text='Send from').grid(row=0, column=0, sticky='w')
        ttk.Label(net, text='Receiver / console IPv4').grid(row=0, column=1, sticky='w', padx=10)
        ttk.Label(net, text='Player').grid(row=0, column=2, sticky='w')
        self.interface = ttk.Combobox(net, state='readonly', values=[self.adapters[0][0]])
        self.interface.current(0); self.interface.grid(row=1, column=0, sticky='ew')
        self.interface.bind('<<ComboboxSelected>>', self.select_interface)
        self.receiver = tk.StringVar(value='127.0.0.1')
        ttk.Entry(net, textvariable=self.receiver).grid(row=1, column=1, sticky='ew', padx=10)
        self.player_id = ttk.Combobox(net, state='readonly', values=list('123456'), width=4)
        self.player_id.current(0); self.player_id.grid(row=1, column=2)
        self.send_button = ttk.Button(net, text='Start sending', command=self.toggle_network)
        self.send_button.grid(row=1, column=3, padx=(12, 0))
        ttk.Label(body, text='For onPC: open Track view → Connect PC player in the preview plugin, then start sending here.',
                  style='Muted.TLabel').pack(anchor='w', pady=(8, 2))
        ttk.Label(body, text='For a console: select your DJ adapter, enter the console IP and use a free player number.',
                  style='Muted.TLabel').pack(anchor='w')
        self.status = ttk.Label(body, text='NETWORK OFF  •  No audio loaded', foreground=TEAL, wraplength=970)
        self.status.pack(anchor='w', pady=(12, 0))
        root.bind('<space>', self.space)
        self.root.after(40, self.tick)
        self.root.after(100, self.initialize_audio)
        threading.Thread(target=lambda: self.events.put(('interfaces', interfaces())), daemon=True).start()
        if initial_file:
            self.root.after(150, lambda: self.load(initial_file))

    def initialize_audio(self):
        try:
            import sounddevice as sd
            self.output_devices += [(f"{d['name']} / {sd.query_hostapis(d['hostapi'])['name']}", i)
                                    for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] >= 2]
            self.output.configure(values=[label for label, _ in self.output_devices])
            self.player.open_audio()
        except Exception as exc:
            self.player.audio_error = str(exc)

    def select_audio(self):
        try:
            self.player.open_audio(self.output_devices[self.output.current()][1])
        except Exception as exc:
            self.player.audio_error = str(exc)
            messagebox.showerror('Audio output', str(exc), parent=self.root)

    def choose_file(self):
        path = filedialog.askopenfilename(title='Choose a track', filetypes=[('Audio tracks', '*.mp3 *.wav *.flac *.ogg *.aif *.aiff'), ('All files', '*.*')])
        if path:
            self.load(path)

    def load(self, path):
        if self.loading:
            return
        self.loading = True
        self.player.playing = False
        self.open_button.configure(state='disabled')
        self.analysis.configure(text='Loading audio and creating waveform analysis…')

        def work():
            try:
                self.events.put(('track', load_track(path)))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def play(self):
        try:
            self.player.toggle_play()
        except Exception as exc:
            messagebox.showerror('Playback', str(exc), parent=self.root)

    def space(self, event):
        if event.widget.winfo_class() not in ('TEntry', 'Entry', 'TCombobox'):
            self.play(); return 'break'

    def cue(self):
        self.player.playing = False
        self.player.seek(0)

    def seek_delta(self, delta):
        self.player.seek(self.player.snapshot()['position'] + delta)

    def finish_seek(self, _):
        self.player.seek(self.progress.get() / 1000 * self.player.snapshot()['duration'])
        self.dragging = False

    def wave_seek(self, event):
        self.player.seek(event.x / max(1, self.canvas.winfo_width()) * self.player.snapshot()['duration'])

    def apply_grid(self):
        try:
            self.player.configure_grid(float(self.bpm.get()), float(self.origin.get()))
            self.analysis.configure(text='Manual grid applied. Beat 1 follows the offset above.')
        except Exception as exc:
            messagebox.showerror('Beat grid', str(exc), parent=self.root)

    def downbeat(self):
        self.origin.set(f"{self.player.snapshot()['position']:.3f}")
        self.apply_grid()

    def tap(self):
        now = time.monotonic()
        if self.tap_times and now - self.tap_times[-1] > 2:
            self.tap_times.clear()
        self.tap_times.append(now)
        if len(self.tap_times) > 1:
            intervals = [b - a for a, b in zip(self.tap_times, list(self.tap_times)[1:])]
            self.bpm.set(f'{60 / sorted(intervals)[len(intervals) // 2]:.2f}')
            self.apply_grid()

    def change_pitch(self, value):
        self.player.set_rate(1 + float(value) / 100)
        self.pitch_label.configure(text=f'Tempo {float(value):+.1f}%')

    def change_volume(self, value):
        self.player.volume = float(value) / 100

    def select_interface(self, _):
        if self.network.running:
            self.network.stop()
        if self.interface.current() == 0:
            self.receiver.set('127.0.0.1')
        elif self.receiver.get().startswith('127.'):
            self.receiver.set('')

    def toggle_network(self):
        if self.network.running:
            self.network.stop(); return
        try:
            self.network.start(self.adapters[self.interface.current()][1], self.receiver.get().strip(), int(self.player_id.get()))
        except Exception as exc:
            messagebox.showerror('Network not started', str(exc), parent=self.root)

    def draw(self, state):
        self.canvas.delete('all')
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        track = state['track']
        if not track:
            self.canvas.create_text(width / 2, height / 2, text='YOUR TRACK · YOUR WAVEFORM', fill=MUTED, font=('Segoe UI', 12))
            return
        fraction = state['position'] / max(.001, state['duration'])
        for index, amplitude in enumerate(track.preview[::2]):
            x = index * width / 400
            size = max(1, amplitude / 31 * (height / 2 - 12))
            self.canvas.create_line(x, height / 2 - size, x, height / 2 + size,
                                    fill=TEAL if index / 400 < fraction else '#648aa5', width=max(1, width / 400))
        x = fraction * width
        self.canvas.create_line(x, 0, x, height, fill=TEXT, width=2)

    def tick(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'interfaces':
                self.adapters = value
                self.interface.configure(values=[label for label, _ in value])
            else:
                self.loading = False
                self.open_button.configure(state='normal')
                if kind == 'error':
                    self.analysis.configure(text='Could not decode this file. Try MP3, WAV, FLAC, OGG or AIFF.')
                    messagebox.showerror('Track not loaded', value, parent=self.root)
                else:
                    self.player.load(value)
                    self.title.configure(text=value.title)
                    self.artist.configure(text=value.artist)
                    self.bpm.set(f'{value.bpm:.2f}')
                    self.origin.set('0.000'); self.pitch.set(0)
                    self.analysis.configure(text='Estimated BPM — check it, then set beat 1 to the musical downbeat.' if value.estimated else 'BPM loaded from file tags — check the downbeat offset.')
        state = self.player.snapshot()
        self.play_button.configure(text='Ⅱ  PAUSE' if state['playing'] else '▶  PLAY')
        if not self.dragging:
            self.progress.set(state['position'] / max(.001, state['duration']) * 1000)
        self.time_label.configure(text=f"{clock(state['position'])}  /  {clock(state['duration'])}      REMAIN {clock(state['duration'] - state['position'])}")
        active = state['beat_index'] % 4 + 1
        self.beat_label.configure(text=' '.join(f'[{i}]' if i == active else f' {i} ' for i in range(1, 5)))
        self.send_button.configure(text='Stop sending' if self.network.running else 'Start sending')
        if self.network.running:
            net = f"SENDING CDJ {self.network.player_id} · {self.network.bind_ip} → {self.network.receiver_ip} · UDP {self.network.packets} · metadata {self.network.queries}"
        else:
            net = 'NETWORK OFF'
        detail = self.network.error or self.player.audio_error or f"Effective tempo {state['bpm'] * state['rate']:.2f} BPM · audio underruns {self.player.underflows}"
        self.status.configure(text=net + '\n' + detail)
        self.draw(state)
        self.root.after(50, self.tick)

    def close(self):
        self.network.stop()
        self.player.close_audio()
        self.root.destroy()


def main():
    args = argparse.ArgumentParser(description='Standalone audio-backed PRO DJ LINK test source.')
    args.add_argument('--track', help='Load a local MP3, WAV, FLAC, OGG or AIFF file at startup.')
    options = args.parse_args()
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root, options.track)
    root.mainloop()


if __name__ == '__main__':
    main()
