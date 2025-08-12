# plex_taiga_sync_final.py
"""
Full final script:
- Config-driven (config.json)
- AniList metadata + title matching
- Optional TVDB fallback (if TVDB_API_KEY provided)
- Automatic Plex library -> local folder detection
- matches.json caching (match path + metadata)
- thumbs/ image cache
- GUI: left = console log, right = anime info panel
- MPV launched muted, small in corner (user supplied command)
"""

import os
import re
import json
import time
import threading
import requests
import subprocess
import logging
import html
from io import BytesIO
from pathlib import Path

# UI libs
import customtkinter as ctk
import tkinter as tk
from PIL import Image, ImageTk

# Windows IPC for MPV
import win32file
import pywintypes

# Plex
from plexapi.server import PlexServer

# ---------- Load config ----------
BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError(
        f"Config file not found: {CONFIG_FILE}\n"
        "Copy config.example.json to config.json and fill in your values."
    )

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

PLEX_URL = config.get("PLEX_URL", "").strip()
PLEX_TOKEN = config.get("PLEX_TOKEN", "").strip()
USERNAME = config.get("USERNAME", "").strip()
LIBRARY_NAMES = config.get("LIBRARY_NAMES", [])
MPV_PATH = config.get("MPV_PATH", "").strip()
POLL_INTERVAL = int(config.get("POLL_INTERVAL", 3))
TVDB_API_KEY = config.get("TVDB_API_KEY", "").strip() or None
PIPE_NAME = config.get("PIPE_NAME", r'\\.\pipe\mpvsocket')  # can be overridden

required = [PLEX_URL, PLEX_TOKEN, USERNAME, LIBRARY_NAMES, MPV_PATH]
if not all(required):
    raise ValueError("Missing required config values. Please fill config.json (PLEX_URL, PLEX_TOKEN, USERNAME, LIBRARY_NAMES, MPV_PATH).")

# ---------- Logging ----------
logging.basicConfig(format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S', level=logging.INFO)
logger = logging.getLogger("plex-taiga-sync")

# ---------- Files & caches ----------
CACHE_FILE = os.path.join(BASE_DIR, "matches.json")
THUMBS_DIR = os.path.join(BASE_DIR, "thumbs")
os.makedirs(THUMBS_DIR, exist_ok=True)

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read cache file: {e}")
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Could not write cache file: {e}")

matches_cache = load_cache()  # structure: keys like "guid:<guid>" and "title:<normalized>" with values { "path":..., "anilist_id":..., "meta": {...} }

# ---------- Plex init and anime folders detection ----------
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

ANIME_FOLDERS = []
for lib_name in LIBRARY_NAMES:
    try:
        section = plex.library.section(lib_name)
        locs = getattr(section, "locations", None) or []
        ANIME_FOLDERS.extend(locs)
        logger.info(f"Library '{lib_name}' locations: {locs}")
    except Exception as e:
        logger.warning(f"Could not load library '{lib_name}': {e}")

# deduplicate and ensure valid
ANIME_FOLDERS = [p for p in dict.fromkeys(ANIME_FOLDERS) if os.path.exists(p)]
if not ANIME_FOLDERS:
    raise RuntimeError("No valid anime folders found from the Plex libraries specified in config.json.")

logger.info(f"Using anime folders: {ANIME_FOLDERS}")

# ---------- UI setup ----------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")
app = ctk.CTk()
app.title("Plex → Taiga Sync")
app.geometry("1100x640")

# Left frame: console/log (wide)
left_frame = ctk.CTkFrame(app, width=760)
left_frame.pack(side="left", fill="both", expand=True, padx=(12,6), pady=12)

status_label = ctk.CTkLabel(left_frame, text="● Status: Idle", font=("Segoe UI", 14))
status_label.pack(anchor="nw", pady=(6,4))

log_text = ctk.CTkTextbox(left_frame, width=720, height=480, corner_radius=8)
log_text.tag_config("green", foreground="#4CAF50")
log_text.tag_config("red", foreground="#F44336")
log_text.tag_config("yellow", foreground="#FFC107")
log_text.tag_config("blue", foreground="#03A9F4")
log_text.tag_config("gray", foreground="#9E9E9E")
log_text.pack(fill="both", expand=True, padx=6, pady=6)

button_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
button_frame.pack(pady=(6,12))

start_button = ctk.CTkButton(button_frame, text="▶ Start Sync", width=160)
stop_button = ctk.CTkButton(button_frame, text="■ Stop Sync", width=160)
start_button.grid(row=0, column=0, padx=8)
stop_button.grid(row=0, column=1, padx=8)

# Right frame: anime info panel (narrow)
right_frame = ctk.CTkFrame(app, width=320)
right_frame.pack(side="right", fill="y", padx=(6,12), pady=12)

# cover image label (placeholder)
cover_label = tk.Label(right_frame, bg="#222222")
cover_label.pack(padx=12, pady=(12,6))

title_romaji_label = ctk.CTkLabel(right_frame, text="", font=("Segoe UI", 16, "bold"), wraplength=300, justify="left")
title_romaji_label.pack(anchor="nw", padx=8, pady=(6,0))

title_english_label = ctk.CTkLabel(right_frame, text="", font=("Segoe UI", 12), wraplength=300, justify="left")
title_english_label.pack(anchor="nw", padx=8, pady=(2,6))

desc_text = tk.Text(right_frame, width=38, height=10, wrap="word", bg="#1f1f1f", fg="#eaeaea", bd=0)
desc_text.pack(padx=8, pady=(6,12))
desc_text.configure(state="disabled")

# store current Tk PhotoImage to prevent GC
app._current_cover_image = None

# ---------- GUI logging helper ----------
def gui_log(msg):
    timestamp = time.strftime("%H:%M:%S")
    entry = f"[{timestamp}] {msg}\n"
    start_index = log_text.index("end-1c")
    log_text.insert("end", entry)
    if "✔" in msg:
        log_text.tag_add("green", start_index, f"{start_index} lineend")
    elif "✖" in msg or "‼" in msg:
        log_text.tag_add("red", start_index, f"{start_index} lineend")
    elif "⚠" in msg:
        log_text.tag_add("yellow", start_index, f"{start_index} lineend")
    elif "▶" in msg or "⏵" in msg:
        log_text.tag_add("blue", start_index, f"{start_index} lineend")
    elif "■" in msg:
        log_text.tag_add("gray", start_index, f"{start_index} lineend")
    log_text.see("end")
    logger.info(msg)

# ---------- Normalization helpers ----------
def normalize_title(name):
    if not name:
        return ""
    return re.sub(r'[^a-z0-9]', '', name.lower())

def clean_title(name):
    if not name:
        return ""
    base = re.split(r'[\(\[]', name)[0].strip()
    return normalize_title(base)

# ---------- AniList lookup (returns metadata and name set) ----------
ANILIST_GRAPHQL = "https://graphql.anilist.co"

def strip_html_tags(s):
    if not s:
        return ""
    # Unescape HTML entities first
    s = html.unescape(s)
    # Remove tags
    return re.sub(r'<[^>]+>', '', s)

def get_anilist_metadata(title, timeout=6):
    """
    Returns (names_set, metadata_dict or None)
    metadata_dict: { 'id', 'romaji', 'english', 'native', 'synopsis', 'cover_image_url' }
    """
    names = set()
    try:
        query = """
        query ($search: String) {
          Media(search: $search, type: ANIME) {
            id
            title { romaji english native }
            synonyms
            description(asHtml: false)
            coverImage { extraLarge large medium color }
          }
        }
        """
        r = requests.post(ANILIST_GRAPHQL, json={"query": query, "variables": {"search": title}}, timeout=timeout)
        if r.status_code == 200:
            data = r.json().get("data", {}).get("Media", None)
            if data:
                # names
                t = data.get("title", {}) or {}
                for v in (t.get("romaji"), t.get("english"), t.get("native")):
                    if v:
                        names.add(clean_title(v))
                for syn in data.get("synonyms", []) or []:
                    if syn:
                        names.add(clean_title(syn))
                # default include cleaned input title
                names.add(clean_title(title))

                # metadata
                meta = {
                    "id": data.get("id"),
                    "romaji": t.get("romaji") or "",
                    "english": t.get("english") or "",
                    "native": t.get("native") or "",
                    "synopsis": strip_html_tags(data.get("description") or ""),
                    "cover_image_url": (data.get("coverImage") or {}).get("extraLarge") or (data.get("coverImage") or {}).get("large")
                }
                gui_log(f"✔ AniList metadata fetched for '{title}' (id={meta['id']})")
                return names, meta
        else:
            gui_log(f"⚠ AniList returned {r.status_code} for search '{title}'")
    except Exception as e:
        gui_log(f"⚠ AniList lookup failed for '{title}': {e}")
    # fallback: include cleaned title
    names.add(clean_title(title))
    return names, None

# ---------- TVDB optional lookup (only for names) ----------
def get_tvdb_titles(title, timeout=6):
    if not TVDB_API_KEY:
        return set()
    names = set()
    try:
        auth_r = requests.post("https://api4.thetvdb.com/v4/login", json={"apikey": TVDB_API_KEY}, timeout=timeout)
        token = auth_r.json().get("data", {}).get("token")
        if not token:
            return names
        r = requests.get(f"https://api4.thetvdb.com/v4/search?query={requests.utils.quote(title)}", headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
        if r.status_code == 200:
            data = r.json().get("data", []) or []
            for item in data:
                name = item.get("name")
                if name:
                    names.add(clean_title(name))
            gui_log(f"✔ TVDB search returned {len(names)} candidates for '{title}'")
    except Exception as e:
        gui_log(f"⚠ TVDB lookup error for '{title}': {e}")
    return names

# ---------- Cover image caching ----------
def get_cover_filename_for_anilist_id(aid):
    return os.path.join(THUMBS_DIR, f"anilist_{aid}.jpg")

def download_and_cache_cover(aid, url):
    if not url or not aid:
        return None
    dest = get_cover_filename_for_anilist_id(aid)
    if os.path.exists(dest):
        return dest
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            img = Image.open(BytesIO(r.content)).convert("RGB")
            img.save(dest, format="JPEG", quality=85)
            return dest
    except Exception as e:
        gui_log(f"⚠ Could not download cover for id {aid}: {e}")
    return None

def load_cover_image_for_display(filepath, width=300):
    try:
        img = Image.open(filepath)
        w, h = img.size
        if w > width:
            new_h = int((width / w) * h)
            img = img.resize((width, new_h), Image.LANCZOS)
        tkimg = ImageTk.PhotoImage(img)
        return tkimg
    except Exception as e:
        gui_log(f"⚠ Could not load cover image: {e}")
        return None

# ---------- Update info panel ----------
def update_info_panel_from_meta(meta):
    """
    meta: dict with fields id, romaji, english, synopsis, cover_image_url
    """
    if not meta:
        # clear
        title_romaji_label.configure(text="")
        title_english_label.configure(text="")
        desc_text.configure(state="normal")
        desc_text.delete("1.0", "end")
        desc_text.configure(state="disabled")
        cover_label.config(image="", text="")
        app._current_cover_image = None
        return

    # titles
    title_romaji_label.configure(text=meta.get("romaji") or "")
    title_english_label.configure(text=meta.get("english") or "")

    # description (trim)
    synopsis = (meta.get("synopsis") or "").strip()
    max_chars = 600  # about ~4-6 lines depending on width
    if len(synopsis) > max_chars:
        synopsis = synopsis[:max_chars].rsplit(" ", 1)[0] + "…"
    desc_text.configure(state="normal")
    desc_text.delete("1.0", "end")
    desc_text.insert("1.0", synopsis)
    desc_text.configure(state="disabled")

    # cover: either cached or download
    aid = meta.get("id")
    cover_path = get_cover_filename_for_anilist_id(aid) if aid else None
    if cover_path and os.path.exists(cover_path):
        tkimg = load_cover_image_for_display(cover_path, width=300)
        if tkimg:
            app._current_cover_image = tkimg
            cover_label.config(image=tkimg, text="")
            return

    # otherwise download
    url = meta.get("cover_image_url")
    if url and aid:
        got = download_and_cache_cover(aid, url)
        if got:
            tkimg = load_cover_image_for_display(got, width=300)
            if tkimg:
                app._current_cover_image = tkimg
                cover_label.config(image=tkimg, text="")
                return

    # fallback: clear image
    cover_label.config(image="", text="")
    app._current_cover_image = None

# ---------- Folder matching helpers ----------
def folder_name_matches(entry_clean, candidate_clean):
    if not entry_clean or not candidate_clean:
        return False
    if entry_clean == candidate_clean: return True
    if candidate_clean in entry_clean: return True
    if entry_clean in candidate_clean: return True
    if entry_clean.startswith(candidate_clean) or candidate_clean.startswith(entry_clean): return True
    return False

def find_series_folder(title, plex_guid=None):
    """
    Try cache by guid -> title. If not found, query AniList & TVDB for candidate names,
    scan ANIME_FOLDERS for a match. Cache result and metadata (if found).
    """
    title_key = clean_title(title)
    guid_key = f"guid:{plex_guid}" if plex_guid else None

    # 1) guid cache
    if guid_key and guid_key in matches_cache:
        data = matches_cache[guid_key]
        path = data.get("path")
        if path and os.path.exists(path):
            gui_log(f"✔ Using cached folder for GUID {plex_guid}: {path}")
            return path
        else:
            matches_cache.pop(guid_key, None)
            save_cache(matches_cache)

    # 2) title cache
    tk = f"title:{title_key}"
    if tk in matches_cache:
        data = matches_cache[tk]
        path = data.get("path")
        if path and os.path.exists(path):
            gui_log(f"✔ Using cached folder for title '{title}': {path}")
            return path
        else:
            matches_cache.pop(tk, None)
            save_cache(matches_cache)

    # 3) name candidates from AniList (and TVDB if available)
    names_al, meta = get_anilist_metadata(title)
    names = set(names_al)
    # add cleaned raw title
    names.add(title_key)
    # tvdb fallback (names only)
    names_tvdb = get_tvdb_titles(title) if TVDB_API_KEY else set()
    names.update(names_tvdb)

    gui_log(f"⏳ Searching local folders for '{title}' (candidates: {len(names)})...")
    for base in ANIME_FOLDERS:
        try:
            for entry in os.listdir(base):
                full = os.path.join(base, entry)
                if not os.path.isdir(full):
                    continue
                entry_clean = clean_title(entry)
                for candidate in names:
                    if folder_name_matches(entry_clean, candidate):
                        gui_log(f"✔ Matched '{entry}' <-> '{title}' via candidate '{candidate}'")
                        # cache
                        if guid_key:
                            matches_cache[guid_key] = {"path": full}
                            # also store metadata if available
                            if meta:
                                matches_cache[guid_key].update({"anilist_id": meta.get("id"), "meta": meta})
                        matches_cache[tk] = {"path": full}
                        if meta:
                            matches_cache[tk].update({"anilist_id": meta.get("id"), "meta": meta})
                        save_cache(matches_cache)
                        # ensure cover downloaded for meta
                        if meta and meta.get("id"):
                            download_and_cache_cover(meta.get("id"), meta.get("cover_image_url"))
                        return full
        except Exception as e:
            gui_log(f"⚠ Error listing '{base}': {e}")

    gui_log(f"✖ Series folder for '{title}' not found locally.")
    # still cache negative? skip
    return None

# ---------- Local episode finder ----------
def find_local_episode(title, season_num, episode_num, plex_guid=None):
    s = int(season_num or 1)
    e = int(episode_num or 1)
    pattern = f"s{s:02}e{e:02}"
    folder = find_series_folder(title, plex_guid)
    if not folder:
        return None
    gui_log(f"Searching series folder: {folder}")
    for root, dirs, files in os.walk(folder):
        for file in files:
            low = file.lower()
            if low.endswith(('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.webm')):
                if pattern in low:
                    full = os.path.join(root, file)
                    gui_log(f"✔ Matched file: {full}")
                    return full
                # alt pattern e.g., "1x02"
                alt = f"{s}x{e}"
                if alt in low:
                    full = os.path.join(root, file)
                    gui_log(f"✔ Matched alt file: {full}")
                    return full
    return None

# ---------- MPV control (user requested variant) ----------
current_process = None

def play_with_mpv(file_path):
    global current_process
    gui_log(f"▶ Launching MPV: {file_path}")
    try:
        current_process = subprocess.Popen([
            MPV_PATH,
            '--input-ipc-server=' + PIPE_NAME,
            '--vo=gpu',
            '--gpu-context=win',
            '--mute=yes',
            '--osc=no',
            '--hwdec=no',
            '--scale=nearest',
            '--cscale=nearest',
            '--no-sub',
            '--geometry=1x1+0+0',
            '--force-window=yes',
            file_path
        ])
    except Exception as e:
        gui_log(f"✖ Error launching MPV: {e}")

def stop_mpv():
    global current_process
    if current_process and current_process.poll() is None:
        gui_log("■ Stopping MPV playback")
        try:
            current_process.terminate()
        except Exception as e:
            gui_log(f"⚠ Error terminating MPV: {e}")
        current_process = None

def is_mpv_running():
    return current_process and current_process.poll() is None

# MPV IPC helpers (for time queries/seeks)
def send_mpv_command(command):
    try:
        handle = win32file.CreateFile(PIPE_NAME, win32file.GENERIC_WRITE, 0, None, win32file.OPEN_EXISTING, 0, None)
        win32file.WriteFile(handle, (command + "\n").encode('utf-8'))
        win32file.CloseHandle(handle)
    except pywintypes.error as e:
        logger.debug(f"MPV pipe write error: {e}")
    except Exception as e:
        logger.debug(f"MPV pipe unexpected error: {e}")

def get_mpv_playback_time():
    try:
        handle = win32file.CreateFile(PIPE_NAME, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None, win32file.OPEN_EXISTING, 0, None)
        win32file.WriteFile(handle, b'{"command": ["get_property", "time-pos"]}\n')
        result = win32file.ReadFile(handle, 4096)[1].decode('utf-8')
        win32file.CloseHandle(handle)
        j = json.loads(result)
        return float(j.get("data") or 0.0)
    except Exception as e:
        raise RuntimeError(f"Failed to query mpv time: {e}")

# ---------- Plex session helpers & sync loop ----------
def get_user_session():
    for session in plex.sessions():
        try:
            # Check if session belongs to target user
            if getattr(session, "usernames", None) and session.usernames[0] != USERNAME:
                continue

            # Make sure the library section matches our configured anime libraries
            lib_section = getattr(session, "librarySectionTitle", None)
            if not lib_section or lib_section not in LIBRARY_NAMES:
                continue

            return session
        except Exception:
            continue
    return None
sync_running = False
last_played_guid = None

def sync_loop():
    global sync_running, last_played_guid
    was_paused = False
    while sync_running:
        try:
            session = get_user_session()
            if session:
                guid = getattr(session, "guid", None)
                title = session.grandparentTitle or session.title
                season = getattr(session, "parentIndex", 1) or 1
                episode = getattr(session, "index", 1) or 1
                gui_log(f"Looking for: Title={title}, Season={season}, Episode={episode}")
                is_paused = getattr(session.player, "state", "") == "paused"
                plex_pos = (getattr(session, "viewOffset", 0) or 0) / 1000.0

                # show metadata in right panel if available in cache or fetch new
                # prefer guid cache
                cached_meta = None
                if guid and f"guid:{guid}" in matches_cache:
                    cached_meta = matches_cache[f"guid:{guid}"].get("meta")
                elif f"title:{clean_title(title)}" in matches_cache:
                    cached_meta = matches_cache[f"title:{clean_title(title)}"].get("meta")

                if cached_meta:
                    update_info_panel_from_meta(cached_meta)
                else:
                    # try to fetch metadata (but don't block too long)
                    try:
                        _, meta = get_anilist_metadata(title)
                        if meta:
                            update_info_panel_from_meta(meta)
                            # store into cache entry even before we find local folder
                            tk = f"title:{clean_title(title)}"
                            entry = matches_cache.get(tk, {})
                            entry.update({"meta": meta, "anilist_id": meta.get("id")})
                            matches_cache[tk] = entry
                            save_cache(matches_cache)
                    except Exception as e:
                        logger.debug(f"AniList metadata background fetch error: {e}")

                # sync mpv time if running
                if is_mpv_running() and not is_paused:
                    try:
                        mpv_time = get_mpv_playback_time()
                        delta = abs(mpv_time - plex_pos)
                        if delta > 5:
                            send_mpv_command(f'{{"command": ["seek", {int(plex_pos)}, "absolute"]}}')
                            gui_log(f"↔ Synced MPV to Plex time (Δ {int(delta)}s)")
                    except Exception as e:
                        gui_log(f"⚠ Could not query MPV time: {e}")

                if guid != last_played_guid or not is_mpv_running():
                    stop_mpv()
                    local_file = find_local_episode(title, season, episode, plex_guid=guid)
                    if local_file:
                        play_with_mpv(local_file)
                        last_played_guid = guid
                    else:
                        gui_log("⚠ Local file not found.")

                # pause/resume handling
                if is_mpv_running():
                    if is_paused and not was_paused:
                        send_mpv_command('{"command": ["set_property", "pause", true]}')
                        gui_log("⏸ Paused MPV to match Plex")
                        was_paused = True
                    elif not is_paused and was_paused:
                        send_mpv_command('{"command": ["set_property", "pause", false]}')
                        gui_log("▶ Resumed MPV to match Plex")
                        was_paused = False

            else:
                if is_mpv_running():
                    stop_mpv()
                last_played_guid = None
        except Exception as e:
            gui_log(f"‼ Unexpected error in sync loop: {e}")
        time.sleep(POLL_INTERVAL)

# ---------- Controls ----------
def start_sync():
    global sync_running
    if not sync_running:
        sync_running = True
        status_label.configure(text="● Status: Syncing", text_color="#4CAF50")
        gui_log("✔ Sync started.")
        threading.Thread(target=sync_loop, daemon=True).start()

def stop_sync():
    global sync_running
    sync_running = False
    stop_mpv()
    status_label.configure(text="● Status: Stopped", text_color="#F44336")
    gui_log("■ Sync stopped.")

start_button.configure(command=start_sync)
stop_button.configure(command=stop_sync)

# ---------- Close handling ----------
def on_close():
    global sync_running
    sync_running = False
    try:
        stop_mpv()
    except Exception as e:
        gui_log(f"⚠ Error stopping MPV on close: {e}")
    app.destroy()

app.protocol("WM_DELETE_WINDOW", on_close)

# ---------- Start GUI loop ----------
# If we want to start automatically:
start_sync()
app.mainloop()
try:
    stop_mpv()
except Exception as e:
    print(f"⚠ Error stopping MPV after exit: {e}")