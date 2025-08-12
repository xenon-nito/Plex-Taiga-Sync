# 📌 Plex → Taiga Sync 

This tool syncs anime playback from **Plex** server to **Taiga** using local files by running the file through a muted and small **MPV** window , matching your anime folder names automatically using AniList (primary) and TVDB (optional) metadata.  
It displays a live right-side panel with anime cover art, romaji + english titles, and a short synopsis — perfect for a Taiga-like experience.

<p align="center">
  <img src="https://i.imgur.com/2WQyyeV.jpeg" alt="App Preview" width="600">
</p>


I created this tool after noticing that, when streaming to my TV or phone, Taiga couldn’t detect files being played from the Plex server running on the same device.

Created using ChatGPT
---

## ✨ Features

- **Automatic folder detection** from Plex libraries (no hardcoded paths)  
- **AniList metadata lookup** (titles, synonyms, cover art, synopsis) this was added after I noticed that while Plex uses romaji for the series naming, my folders would use english names pulled from TVDB by **Sonarr**
- **Optional TVDB fallback** for extra title matching  
- **Local folder matching** works even if Plex uses different naming conventions  
- **Metadata panel** shows:
  - Cover art (cached in `thumbs/`)
  - Romaji + English title
  - Short description
- **Image caching** for instant reloads
- **JSON cache** of matched folders + metadata (`matches.json`)
- **Opens MPV** in a small, muted corner window for sync purposes  
- **Ignores non-anime libraries** (only syncs the ones you specify)  
- **Clean exit** — MPV closes when the GUI closes

---

## 📦 Requirements

- Python 3.8+
- Plex Media Server (local or remote access)
- MPV media player
- Plex token (see below)
- AniList API (no API key needed)
- Optional: TVDB API key

---

## 🛠 Installation

1. **Install dependencies:**
   ```bash
   pip install plexapi customtkinter requests pillow pywin32
   ```

2. **Download the script** (`plex_taiga_sync_GUI.py`) and place it in a folder.

3. **Create a config file**:  
   Copy `config.example.json` to `config.json` and fill in your settings:

   ```json
   {
       "PLEX_URL": "http://localhost:32400",
       "PLEX_TOKEN": "YOUR_PLEX_TOKEN",
       "USERNAME": "YourPlexUsername",
       "LIBRARY_NAMES": ["Anime", "Anime Seasonal"],
       "MPV_PATH": "C:\\Path\\To\\MPV\\mpv.exe",
       "POLL_INTERVAL": 3,
       "TVDB_API_KEY": ""
   }
   ```

   **How to get Plex Token:**  
   [Plex Support Article](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)

4. **(Optional) Get a TVDB API Key:**  
   - Create an account at [thetvdb.com](https://thetvdb.com/).  
   - Go to your account dashboard and request an API key.  
   - Add it to `config.json` under `"TVDB_API_KEY"`.

---

## ▶ Usage

### Run normally (console visible)
```bash
python plex_taiga_sync_GUI.py
```

### Run without console window (background mode)
1. Use `pythonw.exe` instead of `python.exe`:
   ```bash
   pythonw plex_taiga_sync_GUI.py
   ```
   This launches the GUI without a terminal window.

2. **Create a desktop shortcut**:
   - Right-click on your desktop → **New** → **Shortcut**.
   - For the location, enter:
     ```
     "C:\Path\To\Python\pythonw.exe" "C:\Path\To\plex_taiga_sync_GUI.py"
     ```
     *(Replace both paths with your actual Python and script locations.)*
   - Click **Next**, give it a name (e.g., `Plex Taiga Sync`), and click **Finish**.
   - Double-clicking this shortcut will run the script without a console window.

---

## 🔌 MPV IPC (Named Pipe)

This script launches MPV with an **IPC (inter-process communication) socket** using the `--input-ipc-server` flag.  
This allows the script to send commands to MPV (such as seeking, stopping, or checking playback status) while it’s running.  

- On Windows, this uses a **named pipe** (e.g., `\\.\pipe\mpv-taiga-sync`).  
- You normally don’t need to change this, but advanced users can modify the pipe name in the script if needed for other integrations.

---
## 🗂 Cache & Thumbnails

- **`matches.json`**: Stores matched folders + AniList metadata (ID, titles, synopsis, cached image filename).  
- **`thumbs/`**: Stores downloaded cover images (named by AniList ID) for instant reloads.  
- To reset matches, delete `matches.json`.  
- To clear cached images, delete the `thumbs/` folder (it will be recreated automatically).

---

## 💡 Notes
- If MPV is already playing the same file, the script will not relaunch it.  
- If an anime is not found, make sure its folder name matches at least one AniList or TVDB title/synonym.  
- Non-anime libraries are ignored completely — only the ones in `LIBRARY_NAMES` are monitored.

---

## 📜 License
You can share and modify this script freely, but please remove your own API keys before sharing.
