# Nyota Player 🎵

A sleek desktop music player for your local audio files, built with Python + VLC.

## Features
- **Load a whole folder** of music at once (scans subfolders too), or add individual files
- **Play / pause / next / previous / shuffle / repeat / stop**
- Click any track in the **Queue**, **Library**, **Favorites**, or **Recently Played** tab to play it
- **Seekable progress bar** — drag to any point in the track
- **Volume control + mute button**
- **10-band equalizer** with preamp (VLC-powered)
- **Favorites** — click the ♡ on any track to save it; persists between sessions
- **Recently played** — automatically tracked, persists between sessions
- **Save/load playlists** as `.m3u` files
- **Mini / always-on-top mode** — a small floating window with cover art + transport controls
- **Keyboard shortcuts** (window focused):
  - `Space` — play/pause
  - `←` / `→` — seek back/forward 5s
  - `↑` / `↓` — volume up/down
  - `M` — mute
  - `N` / `P` — next/previous track
- **Global media keys** (system-wide, works even when the app isn't focused) — via the `keyboard` package
- Reads embedded metadata (title, artist, album, cover art)

## Setup

1. Install **VLC media player** itself (not just the Python package) from [videolan.org](https://www.videolan.org/) — `python-vlc` is just bindings that call into your VLC install.
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run:
   ```bash
   python main.py
   ```

## Notes
- **Global media keys** need the `keyboard` package to hook your keyboard system-wide. On Windows this usually works out of the box; on Linux/macOS it may require running with elevated permissions (`sudo`). If it can't register, the app still runs fine — you'll just see a message in the terminal and can still use the in-window shortcuts.
- **Equalizer**: uses VLC's built-in equalizer API. If your installed `python-vlc`/VLC version doesn't expose it the same way, you'll get a clear error dialog instead of a crash — let me know the error text if that happens and I can adjust for your version.
- **Favorites and recently played** are stored in `nyota_state.json`, created next to `main.py`. Delete that file to reset both.
- **Mini mode** opens a small always-on-top window and hides the main one; click "⤢ Expand" or close the mini window to bring the full player back.
