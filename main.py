import os
import io
import json
import threading
import time
import random
from tkinter import filedialog, messagebox

import customtkinter as ctk
import vlc
from mutagen import File as MutagenFile
from PIL import Image

try:
    import keyboard  # global (system-wide) media key hooking
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6a49ee"
BG_MAIN = "#0e0e12"
BG_PANEL = "#17171d"
BG_CARD = "#1f1f28"
BG_ROW_ACTIVE = "#232330"
TEXT_MUTED = "#8a8a97"

SUPPORTED_EXT = (".mp3", ".flac", ".wav", ".ogg", ".m4a")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nyota_state.json")


def format_time(seconds):
    if seconds is None:
        seconds = 0
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def get_track_metadata(path):
    meta = {"title": os.path.basename(path), "artist": "Unknown Artist", "album": "Unknown Album",
            "duration": 0, "cover": None, "path": path}
    try:
        audio = MutagenFile(path)
        if audio and audio.info:
            meta["duration"] = int(audio.info.length)
        if audio and audio.tags:
            tags = audio.tags
            for k in ("TIT2", "title", "\xa9nam"):
                if k in tags:
                    meta["title"] = str(tags[k][0]); break
            for k in ("TPE1", "artist", "\xa9ART"):
                if k in tags:
                    meta["artist"] = str(tags[k][0]); break
            for k in ("TALB", "album", "\xa9alb"):
                if k in tags:
                    meta["album"] = str(tags[k][0]); break
            for key in ("APIC:", "APIC", "covr"):
                if key in tags and hasattr(tags[key], "data"):
                    meta["cover"] = tags[key].data
                    break
    except Exception:
        pass
    return meta


class NyotaPlayer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Nyota Player")
        self.geometry("1120x760")
        self.minsize(1000, 660)
        self.configure(fg_color=BG_MAIN)

        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()
        self.equalizer = None

        self.library = []
        self.queue = []
        self.current_queue_idx = None

        self.favorite_paths = set()
        self.recent_tracks = []  # list of track dicts, most-recent-first

        self.is_playing = False
        self.shuffle_on = False
        self.repeat_on = False
        self.is_muted = False
        self.pre_mute_volume = 80
        self.track_length = 0
        self._cover_photo = None
        self.seek_lock = False
        self.mini_win = None

        self._build_ui()
        self._load_state()
        self._start_progress_watcher()
        self._start_visualizer()
        self._setup_local_shortcuts()
        self._setup_global_hotkeys()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ==================================================================
    # UI
    # ==================================================================
    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=260, fg_color=BG_PANEL, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nswe")
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(self.sidebar, text="✦ Nyota", font=ctk.CTkFont(size=26, weight="bold")).pack(pady=(28, 4), padx=24, anchor="w")
        ctk.CTkLabel(self.sidebar, text="Local Music Player", text_color=TEXT_MUTED).pack(padx=24, anchor="w")

        ctk.CTkButton(self.sidebar, text="＋ Load Folder", command=self.load_folder, height=44,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(pady=(24, 6), padx=20, fill="x")
        ctk.CTkButton(self.sidebar, text="＋ Add Files", command=self.load_files, height=40,
                      fg_color=BG_CARD, hover_color="#2a2a36").pack(pady=6, padx=20, fill="x")

        ctk.CTkLabel(self.sidebar, text="Playlist", text_color=TEXT_MUTED).pack(pady=(20, 4), padx=24, anchor="w")
        pl_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        pl_row.pack(padx=20, fill="x")
        ctk.CTkButton(pl_row, text="Save", command=self.save_playlist, height=36, width=100,
                      fg_color=BG_CARD, hover_color="#2a2a36").pack(side="left", padx=(0, 6), expand=True, fill="x")
        ctk.CTkButton(pl_row, text="Load", command=self.load_playlist, height=36, width=100,
                      fg_color=BG_CARD, hover_color="#2a2a36").pack(side="left", expand=True, fill="x")

        ctk.CTkButton(self.sidebar, text="🎛  Equalizer", command=self.open_equalizer, height=38,
                      fg_color=BG_CARD, hover_color="#2a2a36").pack(pady=(16, 6), padx=20, fill="x")
        ctk.CTkButton(self.sidebar, text="🗗  Mini Mode", command=self.toggle_mini_mode, height=38,
                      fg_color=BG_CARD, hover_color="#2a2a36").pack(pady=6, padx=20, fill="x")

        vol_label_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        vol_label_row.pack(pady=(24, 4), padx=24, fill="x")
        ctk.CTkLabel(vol_label_row, text="Volume", text_color=TEXT_MUTED).pack(side="left")
        self.mute_btn = ctk.CTkButton(vol_label_row, text="🔊", width=28, height=24, fg_color="transparent",
                                       hover_color="#2a2a36", command=self.toggle_mute)
        self.mute_btn.pack(side="right")

        self.volume_slider = ctk.CTkSlider(self.sidebar, from_=0, to=100, command=self.on_volume,
                                            fg_color=BG_CARD, progress_color=ACCENT,
                                            button_color=ACCENT, button_hover_color=ACCENT_HOVER)
        self.volume_slider.set(80)
        self.volume_slider.pack(padx=24, fill="x")
        self.player.audio_set_volume(80)

        shortcuts_hint = ("Space play/pause · ←/→ seek\n↑/↓ volume · M mute · N/P next/prev"
                           + ("" if KEYBOARD_AVAILABLE else "\n(global media keys unavailable)"))
        ctk.CTkLabel(self.sidebar, text=shortcuts_hint, text_color="#55555f", font=ctk.CTkFont(size=10),
                     justify="left").pack(side="bottom", padx=20, pady=16, anchor="w")

        main = ctk.CTkFrame(self, fg_color=BG_MAIN)
        main.grid(row=0, column=1, sticky="nswe")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self._build_now_playing(main)
        self._build_tabs(main)

    def _build_now_playing(self, parent):
        np = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=20, height=310)
        np.grid(row=0, column=0, sticky="ew", padx=24, pady=24)
        np.grid_propagate(False)
        self.np_frame = np

        self.cover_label = ctk.CTkLabel(np, text="♪", font=ctk.CTkFont(size=80), fg_color=BG_PANEL,
                                         corner_radius=16, width=190, height=190)
        self.cover_label.place(x=40, y=50)

        self.visualizer_canvas = ctk.CTkCanvas(np, width=380, height=110, bg="#1a1a22", highlightthickness=0)
        self.visualizer_canvas.place(x=270, y=80)

        self.now_title = ctk.CTkLabel(np, text="Nothing playing", font=ctk.CTkFont(size=20, weight="bold"), anchor="w")
        self.now_title.place(x=270, y=20)

        self.now_artist = ctk.CTkLabel(np, text="", text_color=TEXT_MUTED, anchor="w")
        self.now_artist.place(x=270, y=52)

        self.fav_btn = ctk.CTkButton(np, text="♡", width=32, height=32, fg_color="transparent",
                                      hover_color="#2a2a36", font=ctk.CTkFont(size=16),
                                      command=self.toggle_current_favorite)
        self.fav_btn.place(relx=0.97, y=18, anchor="ne")

        self.time_current = ctk.CTkLabel(np, text="00:00", text_color=TEXT_MUTED)
        self.time_current.place(x=270, y=210)

        self.progress_slider = ctk.CTkSlider(np, from_=0, to=100, command=self.on_seek,
                                              fg_color=BG_PANEL, progress_color=ACCENT,
                                              button_color=ACCENT, button_hover_color=ACCENT_HOVER)
        self.progress_slider.place(x=330, y=210, relwidth=0.55)
        self.progress_slider.bind("<ButtonPress-1>", lambda e: setattr(self, "seek_lock", True))
        self.progress_slider.bind("<ButtonRelease-1>", self.on_seek_release)

        self.time_total = ctk.CTkLabel(np, text="00:00", text_color=TEXT_MUTED)
        self.time_total.place(relx=0.92, y=210)

        ctrl = ctk.CTkFrame(np, fg_color="transparent")
        ctrl.place(x=270, y=245)

        self.shuffle_btn = ctk.CTkButton(ctrl, text="🔀", width=42, height=42, fg_color=BG_PANEL,
                                          hover_color="#2a2a36", command=self.toggle_shuffle)
        self.shuffle_btn.grid(row=0, column=0, padx=5)
        ctk.CTkButton(ctrl, text="⏮", width=46, height=46, fg_color=BG_PANEL, hover_color="#2a2a36",
                      command=self.play_previous).grid(row=0, column=1, padx=5)

        self.play_btn = ctk.CTkButton(ctrl, text="▶", width=60, height=60, corner_radius=30,
                                       fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.toggle_play)
        self.play_btn.grid(row=0, column=2, padx=10)

        ctk.CTkButton(ctrl, text="⏭", width=46, height=46, fg_color=BG_PANEL, hover_color="#2a2a36",
                      command=self.play_next).grid(row=0, column=3, padx=5)
        ctk.CTkButton(ctrl, text="⏹", width=42, height=42, fg_color=BG_PANEL, hover_color="#2a2a36",
                      command=self.stop_playback).grid(row=0, column=4, padx=5)
        self.repeat_btn = ctk.CTkButton(ctrl, text="🔁", width=42, height=42, fg_color=BG_PANEL,
                                         hover_color="#2a2a36", command=self.toggle_repeat)
        self.repeat_btn.grid(row=0, column=5, padx=5)

    def _build_tabs(self, parent):
        self.tabview = ctk.CTkTabview(parent)
        self.tabview.grid(row=1, column=0, sticky="nswe", padx=24, pady=(0, 24))
        for name in ("Queue", "Library", "Favorites", "Recently Played"):
            self.tabview.add(name)

        self.queue_scroll = ctk.CTkScrollableFrame(self.tabview.tab("Queue"), fg_color="transparent")
        self.queue_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self.lib_scroll = ctk.CTkScrollableFrame(self.tabview.tab("Library"), fg_color="transparent")
        self.lib_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self.fav_scroll = ctk.CTkScrollableFrame(self.tabview.tab("Favorites"), fg_color="transparent")
        self.fav_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self.recent_scroll = ctk.CTkScrollableFrame(self.tabview.tab("Recently Played"), fg_color="transparent")
        self.recent_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self.queue_row_widgets = []

    # ==================================================================
    # Loading library / queue
    # ==================================================================
    def load_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            threading.Thread(target=self._scan_folder, args=(folder,), daemon=True).start()

    def load_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Audio", " ".join(f"*{e}" for e in SUPPORTED_EXT))])
        if files:
            self._add_to_library(list(files))

    def _scan_folder(self, folder):
        paths = [os.path.join(root, f) for root, _, fs in os.walk(folder)
                 for f in fs if f.lower().endswith(SUPPORTED_EXT)]
        self.after(0, lambda: self._add_to_library(paths))

    def _add_to_library(self, paths, add_to_queue=True):
        def worker():
            new_tracks = [get_track_metadata(p) for p in paths]
            self.after(0, lambda: self._on_tracks_ready(new_tracks, add_to_queue))
        threading.Thread(target=worker, daemon=True).start()

    def _on_tracks_ready(self, new_tracks, add_to_queue):
        # avoid duplicate library entries for the same path
        existing_paths = {t["path"] for t in self.library}
        for t in new_tracks:
            if t["path"] not in existing_paths:
                self.library.append(t)
                existing_paths.add(t["path"])
        if add_to_queue:
            self.queue.extend(new_tracks)
        self.refresh_library()
        self.refresh_queue()
        self.refresh_favorites()
        if self.current_queue_idx is None and self.queue:
            self.play_queue_index(0)

    # ==================================================================
    # Playlist save / load (.m3u)
    # ==================================================================
    def save_playlist(self):
        if not self.queue:
            messagebox.showinfo("Nothing to save", "Your queue is empty.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".m3u",
                                             filetypes=[("M3U Playlist", "*.m3u")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in self.queue:
                    f.write(f"#EXTINF:{int(t['duration'])},{t['artist']} - {t['title']}\n")
                    f.write(t["path"] + "\n")
            messagebox.showinfo("Saved", f"Playlist saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Error saving playlist", str(e))

    def load_playlist(self):
        path = filedialog.askopenfilename(filetypes=[("M3U Playlist", "*.m3u")])
        if not path:
            return
        try:
            paths = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if os.path.exists(line):
                            paths.append(line)
            if not paths:
                messagebox.showwarning("Empty playlist", "No valid tracks found in that file.")
                return
            self.queue = []
            self.current_queue_idx = None
            self._add_to_library(paths, add_to_queue=True)
        except Exception as e:
            messagebox.showerror("Error loading playlist", str(e))

    # ==================================================================
    # Rendering
    # ==================================================================
    def refresh_library(self):
        for w in self.lib_scroll.winfo_children():
            w.destroy()
        for i, t in enumerate(self.library):
            self._create_track_row(self.lib_scroll, t, i, mode="library")

    def refresh_queue(self):
        for w in self.queue_scroll.winfo_children():
            w.destroy()
        self.queue_row_widgets = []
        for i, t in enumerate(self.queue):
            row, num = self._create_track_row(self.queue_scroll, t, i, mode="queue")
            self.queue_row_widgets.append((i, row, num))
        self._refresh_queue_highlight()

    def refresh_favorites(self):
        for w in self.fav_scroll.winfo_children():
            w.destroy()
        favs = [t for t in self.library if t["path"] in self.favorite_paths]
        if not favs:
            ctk.CTkLabel(self.fav_scroll, text="No favorites yet — click the ♡ on a track to add one.",
                         text_color=TEXT_MUTED).pack(pady=20)
            return
        for i, t in enumerate(favs):
            self._create_track_row(self.fav_scroll, t, i, mode="adhoc")

    def refresh_recent(self):
        for w in self.recent_scroll.winfo_children():
            w.destroy()
        if not self.recent_tracks:
            ctk.CTkLabel(self.recent_scroll, text="Nothing played yet.", text_color=TEXT_MUTED).pack(pady=20)
            return
        for i, t in enumerate(self.recent_tracks):
            self._create_track_row(self.recent_scroll, t, i, mode="adhoc")

    def _create_track_row(self, parent, track, idx, mode):
        row = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=8, height=56)
        row.pack(fill="x", pady=3, padx=6)
        row.grid_columnconfigure(1, weight=1)
        row.grid_propagate(False)

        num = ctk.CTkLabel(row, text=str(idx + 1), width=30, text_color=TEXT_MUTED)
        num.grid(row=0, column=0, rowspan=2, padx=12)
        title = ctk.CTkLabel(row, text=track["title"], anchor="w", font=ctk.CTkFont(weight="bold"))
        title.grid(row=0, column=1, sticky="ew")
        artist = ctk.CTkLabel(row, text=track["artist"], text_color=TEXT_MUTED, anchor="w")
        artist.grid(row=1, column=1, sticky="ew")
        dur = ctk.CTkLabel(row, text=format_time(track["duration"]), text_color=TEXT_MUTED)
        dur.grid(row=0, column=2, rowspan=2, padx=(0, 8))

        is_fav = track["path"] in self.favorite_paths
        heart_btn = ctk.CTkButton(row, text=("♥" if is_fav else "♡"), width=30, height=30,
                                   fg_color="transparent", hover_color="#2a2a36",
                                   text_color=(ACCENT if is_fav else TEXT_MUTED),
                                   command=lambda t=track: self.toggle_favorite(t))
        heart_btn.grid(row=0, column=3, rowspan=2, padx=(0, 12))

        def click(e=None, i=idx, t=track, m=mode):
            if m == "library":
                self.add_to_queue_by_track(t)
            elif m == "queue":
                self.play_queue_index(i)
            else:  # adhoc: favorites / recently played
                self.play_ad_hoc(t)

        for w in (row, num, title, artist, dur):
            w.bind("<Button-1>", click)
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
        return row, num

    def _refresh_queue_highlight(self):
        for idx, row, num in self.queue_row_widgets:
            active = idx == self.current_queue_idx
            row.configure(fg_color=BG_ROW_ACTIVE if active else BG_PANEL)
            num.configure(text="♫" if (active and self.is_playing) else str(idx + 1),
                          text_color=ACCENT if active else TEXT_MUTED)

    def add_to_queue_by_track(self, track):
        self.queue.append(track)
        self.refresh_queue()

    def play_ad_hoc(self, track):
        """Play a track from Favorites/Recently Played, adding it to the queue if needed."""
        for i, t in enumerate(self.queue):
            if t["path"] == track["path"]:
                self.play_queue_index(i)
                return
        self.queue.append(track)
        self.refresh_queue()
        self.play_queue_index(len(self.queue) - 1)

    # ==================================================================
    # Favorites
    # ==================================================================
    def toggle_favorite(self, track):
        path = track["path"]
        if path in self.favorite_paths:
            self.favorite_paths.discard(path)
        else:
            self.favorite_paths.add(path)
        self.refresh_favorites()
        self.refresh_library()
        self.refresh_queue()
        self._sync_current_favorite_icon()

    def toggle_current_favorite(self):
        if self.current_queue_idx is None:
            return
        track = self.queue[self.current_queue_idx]
        self.toggle_favorite(track)

    def _sync_current_favorite_icon(self):
        if self.current_queue_idx is None:
            self.fav_btn.configure(text="♡", text_color=TEXT_MUTED)
            return
        track = self.queue[self.current_queue_idx]
        is_fav = track["path"] in self.favorite_paths
        self.fav_btn.configure(text=("♥" if is_fav else "♡"),
                                text_color=(ACCENT if is_fav else "white"))

    # ==================================================================
    # Playback
    # ==================================================================
    def play_queue_index(self, q_idx):
        if not (0 <= q_idx < len(self.queue)):
            return
        track = self.queue[q_idx]
        try:
            media = self.instance.media_new(track["path"])
            self.player.set_media(media)
            self.player.play()
            if self.equalizer is not None:
                try:
                    self.player.set_equalizer(self.equalizer)
                except Exception:
                    pass

            self.current_queue_idx = q_idx
            self.is_playing = True
            self.play_btn.configure(text="⏸")
            self._update_now_playing(track)
            self._refresh_queue_highlight()
            self._record_recent(track)
            self._sync_mini_ui()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def toggle_play(self):
        if self.current_queue_idx is None:
            if self.queue:
                self.play_queue_index(0)
            return
        if self.player.is_playing():
            self.player.pause()
            self.is_playing = False
            self.play_btn.configure(text="▶")
        else:
            self.player.play()
            self.is_playing = True
            self.play_btn.configure(text="⏸")
        self._refresh_queue_highlight()
        self._sync_mini_ui()

    def play_next(self):
        if not self.queue:
            return
        idx = random.randint(0, len(self.queue) - 1) if self.shuffle_on else \
            ((self.current_queue_idx if self.current_queue_idx is not None else -1) + 1) % len(self.queue)
        self.play_queue_index(idx)

    def play_previous(self):
        if not self.queue:
            return
        cur = self.current_queue_idx if self.current_queue_idx is not None else 0
        idx = (cur - 1) % len(self.queue)
        self.play_queue_index(idx)

    def stop_playback(self):
        self.player.stop()
        self.is_playing = False
        self.play_btn.configure(text="▶")
        self._refresh_queue_highlight()
        self._sync_mini_ui()

    def toggle_shuffle(self):
        self.shuffle_on = not self.shuffle_on
        self.shuffle_btn.configure(fg_color=ACCENT if self.shuffle_on else BG_PANEL)

    def toggle_repeat(self):
        self.repeat_on = not self.repeat_on
        self.repeat_btn.configure(fg_color=ACCENT if self.repeat_on else BG_PANEL)

    # ==================================================================
    # Volume / Mute
    # ==================================================================
    def on_volume(self, val):
        self.player.audio_set_volume(int(val))
        if int(val) > 0:
            self.is_muted = False
            self.mute_btn.configure(text="🔊")
        else:
            self.mute_btn.configure(text="🔇")

    def toggle_mute(self):
        if self.is_muted:
            self.player.audio_set_volume(self.pre_mute_volume)
            self.volume_slider.set(self.pre_mute_volume)
            self.is_muted = False
            self.mute_btn.configure(text="🔊")
        else:
            self.pre_mute_volume = self.player.audio_get_volume() or 80
            self.player.audio_set_volume(0)
            self.volume_slider.set(0)
            self.is_muted = True
            self.mute_btn.configure(text="🔇")

    def change_volume(self, delta):
        current = self.player.audio_get_volume()
        new_val = max(0, min(100, current + delta))
        self.player.audio_set_volume(new_val)
        self.volume_slider.set(new_val)
        self.is_muted = (new_val == 0)
        self.mute_btn.configure(text="🔇" if self.is_muted else "🔊")

    # ==================================================================
    # Now playing / cover art
    # ==================================================================
    def _update_now_playing(self, track):
        self.now_title.configure(text=track["title"])
        self.now_artist.configure(text=f"{track['artist']} • {track['album']}")
        self.track_length = track["duration"] or 0
        self.time_total.configure(text=format_time(self.track_length))
        self.progress_slider.set(0)
        self.time_current.configure(text="00:00")
        self._sync_current_favorite_icon()

        if track.get("cover"):
            try:
                img = Image.open(io.BytesIO(track["cover"])).resize((190, 190))
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(190, 190))
                self._cover_photo = ctk_img
                self.cover_label.configure(image=ctk_img, text="")
            except Exception:
                self._cover_photo = None
                self.cover_label.configure(image=None, text="♪")
        else:
            self._cover_photo = None
            self.cover_label.configure(image=None, text="♪")

    # ==================================================================
    # Recently played
    # ==================================================================
    def _record_recent(self, track):
        self.recent_tracks = [t for t in self.recent_tracks if t["path"] != track["path"]]
        self.recent_tracks.insert(0, track)
        self.recent_tracks = self.recent_tracks[:50]
        self.refresh_recent()

    # ==================================================================
    # Seeking
    # ==================================================================
    def on_seek(self, val):
        if self.track_length:
            self.time_current.configure(text=format_time(float(val) / 100 * self.track_length))

    def on_seek_release(self, event=None):
        if self.track_length == 0:
            self.seek_lock = False
            return
        target_ms = int(self.progress_slider.get() / 100 * self.track_length * 1000)
        self.player.set_time(target_ms)
        self.seek_lock = False

    def seek_relative(self, delta_seconds):
        if not self.track_length or self.current_queue_idx is None:
            return
        current_ms = self.player.get_time()
        new_ms = max(0, min(int(self.track_length * 1000), current_ms + delta_seconds * 1000))
        self.player.set_time(new_ms)

    # ==================================================================
    # Progress watcher / visualizer
    # ==================================================================
    def _start_progress_watcher(self):
        def loop():
            while True:
                time.sleep(0.3)
                try:
                    self.after(0, self._tick)
                except Exception:
                    pass
        threading.Thread(target=loop, daemon=True).start()

    def _tick(self):
        if self.seek_lock:
            return
        if self.player.is_playing():
            current_ms = self.player.get_time()
            if self.track_length > 0 and current_ms >= 0:
                pct = (current_ms / 1000) / self.track_length * 100
                self.progress_slider.set(min(100, pct))
                self.time_current.configure(text=format_time(current_ms / 1000))
            if self.player.get_state() == vlc.State.Ended:
                self._handle_track_end()

    def _handle_track_end(self):
        if self.repeat_on and self.current_queue_idx is not None:
            self.play_queue_index(self.current_queue_idx)
        else:
            self.play_next()

    def _start_visualizer(self):
        def animate():
            while True:
                time.sleep(0.08)
                try:
                    self.after(0, self._draw_visualizer)
                except Exception:
                    pass
        threading.Thread(target=animate, daemon=True).start()

    def _draw_visualizer(self):
        self.visualizer_canvas.delete("all")
        if not self.player.is_playing():
            return
        for i in range(28):
            h = random.randint(15, 85)
            x = i * 14
            self.visualizer_canvas.create_rectangle(x, 110 - h, x + 8, 110, fill=ACCENT, outline="")

    # ==================================================================
    # Equalizer
    # ==================================================================
    def open_equalizer(self):
        try:
            band_count = vlc.libvlc_audio_equalizer_get_band_count()
        except Exception as e:
            messagebox.showerror("Equalizer unavailable",
                                  f"Your python-vlc/libvlc version doesn't expose the equalizer API:\n{e}")
            return

        if self.equalizer is None:
            try:
                self.equalizer = vlc.AudioEqualizer()
            except Exception as e:
                messagebox.showerror("Equalizer unavailable", str(e))
                return

        win = ctk.CTkToplevel(self)
        win.title("Equalizer")
        win.geometry("620x340")
        win.configure(fg_color=BG_MAIN)
        win.attributes("-topmost", True)

        ctk.CTkLabel(win, text="Preamp", text_color=TEXT_MUTED).pack(pady=(16, 0))
        preamp_slider = ctk.CTkSlider(win, from_=-20, to=20, width=300,
                                      command=lambda v: self._set_preamp(float(v)))
        try:
            preamp_slider.set(self.equalizer.preamp())
        except Exception:
            preamp_slider.set(0)
        preamp_slider.pack(pady=(4, 20))

        bands_frame = ctk.CTkFrame(win, fg_color="transparent")
        bands_frame.pack(expand=True, fill="both", padx=20)

        for i in range(band_count):
            try:
                freq = vlc.libvlc_audio_equalizer_get_band_frequency(i)
            except Exception:
                freq = None
            col = ctk.CTkFrame(bands_frame, fg_color="transparent")
            col.grid(row=0, column=i, padx=8, sticky="ns")
            bands_frame.grid_columnconfigure(i, weight=1)

            slider = ctk.CTkSlider(col, from_=20, to=-20, orientation="vertical", height=160,
                                    command=lambda v, idx=i: self._set_band(idx, float(v)))
            try:
                slider.set(self.equalizer.amp_at_index(i))
            except Exception:
                slider.set(0)
            slider.pack()
            label_text = f"{int(freq)}Hz" if freq else f"Band {i+1}"
            ctk.CTkLabel(col, text=label_text, text_color=TEXT_MUTED, font=ctk.CTkFont(size=10)).pack(pady=(6, 0))

        ctk.CTkButton(win, text="Reset", fg_color=BG_CARD, hover_color="#2a2a36",
                      command=lambda: self._reset_equalizer(win)).pack(pady=16)

    def _set_preamp(self, value):
        try:
            self.equalizer.set_preamp(value)
            self.player.set_equalizer(self.equalizer)
        except Exception:
            pass

    def _set_band(self, index, value):
        try:
            self.equalizer.set_amp_at_index(value, index)
            self.player.set_equalizer(self.equalizer)
        except Exception:
            pass

    def _reset_equalizer(self, win):
        try:
            self.equalizer = vlc.AudioEqualizer()
            self.player.set_equalizer(self.equalizer)
        except Exception:
            pass
        win.destroy()
        self.open_equalizer()

    # ==================================================================
    # Mini / always-on-top mode
    # ==================================================================
    def toggle_mini_mode(self):
        if self.mini_win is not None and self.mini_win.winfo_exists():
            self.exit_mini_mode()
            return

        self.withdraw()
        self.mini_win = ctk.CTkToplevel()
        self.mini_win.title("Nyota Mini")
        self.mini_win.geometry("300x360")
        self.mini_win.attributes("-topmost", True)
        self.mini_win.configure(fg_color=BG_MAIN)
        self.mini_win.protocol("WM_DELETE_WINDOW", self.exit_mini_mode)

        self.mini_cover = ctk.CTkLabel(self.mini_win, text="♪", width=150, height=150,
                                        fg_color=BG_PANEL, corner_radius=14, font=ctk.CTkFont(size=52))
        self.mini_cover.pack(pady=(20, 10))

        self.mini_title = ctk.CTkLabel(self.mini_win, text=self.now_title.cget("text"),
                                        font=ctk.CTkFont(size=14, weight="bold"))
        self.mini_title.pack(padx=10)
        self.mini_artist = ctk.CTkLabel(self.mini_win, text=self.now_artist.cget("text"), text_color=TEXT_MUTED)
        self.mini_artist.pack()

        ctrl = ctk.CTkFrame(self.mini_win, fg_color="transparent")
        ctrl.pack(pady=18)
        ctk.CTkButton(ctrl, text="⏮", width=40, height=40, fg_color=BG_PANEL,
                      hover_color="#2a2a36", command=self.play_previous).grid(row=0, column=0, padx=6)
        self.mini_play_btn = ctk.CTkButton(ctrl, text=self.play_btn.cget("text"), width=52, height=52,
                                            corner_radius=26, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                            command=self.toggle_play)
        self.mini_play_btn.grid(row=0, column=1, padx=8)
        ctk.CTkButton(ctrl, text="⏭", width=40, height=40, fg_color=BG_PANEL,
                      hover_color="#2a2a36", command=self.play_next).grid(row=0, column=2, padx=6)

        ctk.CTkButton(self.mini_win, text="⤢ Expand", fg_color=BG_CARD, hover_color="#2a2a36",
                      command=self.exit_mini_mode).pack(pady=(6, 16))

    def exit_mini_mode(self):
        if self.mini_win is not None:
            try:
                self.mini_win.destroy()
            except Exception:
                pass
            self.mini_win = None
        self.deiconify()

    def _sync_mini_ui(self):
        if self.mini_win is None or not self.mini_win.winfo_exists():
            return
        self.mini_title.configure(text=self.now_title.cget("text"))
        self.mini_artist.configure(text=self.now_artist.cget("text"))
        self.mini_play_btn.configure(text=self.play_btn.cget("text"))
        if self._cover_photo is not None:
            self.mini_cover.configure(image=self._cover_photo, text="")
        else:
            self.mini_cover.configure(image=None, text="♪")

    # ==================================================================
    # Keyboard shortcuts (local, window-focused) + global media keys
    # ==================================================================
    def _setup_local_shortcuts(self):
        self.bind_all("<space>", lambda e: self.toggle_play())
        self.bind_all("<Right>", lambda e: self.seek_relative(5))
        self.bind_all("<Left>", lambda e: self.seek_relative(-5))
        self.bind_all("<Up>", lambda e: self.change_volume(5))
        self.bind_all("<Down>", lambda e: self.change_volume(-5))
        self.bind_all("m", lambda e: self.toggle_mute())
        self.bind_all("M", lambda e: self.toggle_mute())
        self.bind_all("n", lambda e: self.play_next())
        self.bind_all("N", lambda e: self.play_next())
        self.bind_all("p", lambda e: self.play_previous())
        self.bind_all("P", lambda e: self.play_previous())

    def _setup_global_hotkeys(self):
        if not KEYBOARD_AVAILABLE:
            print("Global media keys unavailable: install the 'keyboard' package "
                  "(pip install keyboard). Local in-window shortcuts still work.")
            return
        try:
            keyboard.add_hotkey("play/pause media", lambda: self.after(0, self.toggle_play))
            keyboard.add_hotkey("next track", lambda: self.after(0, self.play_next))
            keyboard.add_hotkey("previous track", lambda: self.after(0, self.play_previous))
            keyboard.add_hotkey("volume mute", lambda: self.after(0, self.toggle_mute))
            keyboard.add_hotkey("stop media", lambda: self.after(0, self.stop_playback))
        except Exception as e:
            # Common on Linux/macOS without elevated permissions — degrade gracefully.
            print(f"Global media keys could not be registered: {e}")

    # ==================================================================
    # Persistence (favorites + recently played)
    # ==================================================================
    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            fav_paths = [p for p in data.get("favorites", []) if os.path.exists(p)]
            recent_paths = [p for p in data.get("recent", []) if os.path.exists(p)]

            self.favorite_paths = set(fav_paths)

            all_paths = list(dict.fromkeys(fav_paths + recent_paths))  # de-duped, order preserved
            if all_paths:
                new_tracks = [get_track_metadata(p) for p in all_paths]
                existing = {t["path"] for t in self.library}
                for t in new_tracks:
                    if t["path"] not in existing:
                        self.library.append(t)
                        existing.add(t["path"])

            path_to_track = {t["path"]: t for t in self.library}
            self.recent_tracks = [path_to_track[p] for p in recent_paths if p in path_to_track]

            self.refresh_library()
            self.refresh_favorites()
            self.refresh_recent()
        except Exception as e:
            print(f"Could not load saved state: {e}")

    def _save_state(self):
        try:
            data = {
                "favorites": list(self.favorite_paths),
                "recent": [t["path"] for t in self.recent_tracks],
            }
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Could not save state: {e}")

    def on_close(self):
        self._save_state()
        if KEYBOARD_AVAILABLE:
            try:
                keyboard.unhook_all()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = NyotaPlayer()
    app.mainloop()