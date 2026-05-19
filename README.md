# Telegram Drive Downloader Bot

A practical MVP Telegram bot for one Linux server.

It accepts:

- yt-dlp supported video/stream links
- direct HTTP/HTTPS download links
- Telegram files

Downloads are saved into a whitelisted user's rclone-mounted Google Drive folder.

## Stack

- Python 3.11+
- Kurigram, Pyrogram-compatible Telegram client
- yt-dlp
- wget
- SQLite
- asyncio queue
- no Redis, no Celery, no Docker required

## Project structure

```text
main.py
config.py
db.py
models.py
queue_manager.py
handlers.py
downloaders.py
file_manager.py
utils.py
requirements.txt
.env.example
README.md
```

## Install

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv wget ffmpeg

cd telegram_drive_bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

`ffmpeg` is needed by yt-dlp when merging separate audio/video streams.

## rclone mount

This bot assumes Google Drive is already mounted, for example:

```bash
rclone mount gdrive: /mnt/gdrive --vfs-cache-mode writes
```

Each user gets a folder under:

```text
/mnt/gdrive/{gmail}/
```

Local temporary downloads go to:

```text
/downloads/{gmail}/
```

## Environment

```env
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:ABCDEF
ADMIN_IDS=415176
DB_PATH=bot.sqlite3
LOCAL_DOWNLOAD_ROOT=/downloads
DRIVE_ROOT=/mnt/gdrive
MAX_CONCURRENT_DOWNLOADS=1
MAX_FILE_SIZE_MB=2048
```

Get `API_ID` and `API_HASH` from Telegram's app portal, and `BOT_TOKEN` from BotFather.

## Run

```bash
source .venv/bin/activate
python main.py
```

## Add a user

Only admins listed in `ADMIN_IDS` can add users.

```text
/adduser 415176 ali ali@gmail.com
```

This stores:

- Telegram ID
- username
- Gmail
- Drive folder path: `/mnt/gdrive/ali@gmail.com`

## User commands

```text
/start
/help
/queue
/myfiles
/serverfiles
/cancel
```

## Admin commands

```text
/adduser telegram_id username gmail
/removeuser telegram_id
/users
```

## Download flow

### yt-dlp links

1. User sends a video/stream link.
2. Bot probes with yt-dlp.
3. Bot shows inline buttons:
   - best video
   - best audio
   - 1080p
   - 720p
   - 480p
4. Selected download is queued.
5. File downloads to `/downloads/{gmail}/`.
6. File moves to `/mnt/gdrive/{gmail}/`.

### Direct links

1. User sends direct HTTP/HTTPS URL.
2. Bot tries a HEAD request for filename and size.
3. User clicks `Download`.
4. `wget --continue` saves directly to the user's Drive folder.

### Telegram files

1. User sends a document/video/audio.
2. Bot shows filename and size.
3. User clicks `Save to Drive`.
4. File downloads to local folder, then moves to Drive.

## Queue behavior

- Uses `asyncio.Queue`.
- `MAX_CONCURRENT_DOWNLOADS=1` by default.
- Tasks are stored in SQLite.
- On restart, queued/running tasks are marked failed rather than resumed.
- `/cancel` cancels the user's newest queued/running task.

## Security notes

The MVP includes these basics:

- rejects non-whitelisted users
- admin-only user management
- validates HTTP/HTTPS URLs
- never uses `shell=True`
- sanitizes filenames
- prevents path traversal with resolved path checks
- keeps delete operations inside the user's own folders
- enforces `MAX_FILE_SIZE_MB` where size is known and again after downloads
- limits concurrency
- catches common errors and reports them to Telegram

## Production tips

Run it with systemd:

```ini
[Unit]
Description=Telegram Drive Downloader Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/telegram_drive_bot
EnvironmentFile=/opt/telegram_drive_bot/.env
ExecStart=/opt/telegram_drive_bot/.venv/bin/python /opt/telegram_drive_bot/main.py
Restart=always
RestartSec=5
User=botuser
Group=botuser

[Install]
WantedBy=multi-user.target
```

Make sure the service user can write to `LOCAL_DOWNLOAD_ROOT`, `DRIVE_ROOT`, and `DB_PATH`.

## Limitations

This is intentionally simple:

- no distributed workers
- no Redis
- no automatic resume of yt-dlp or Telegram tasks after restart
- no advanced format menus
- no dashboard
- no per-user quota system

Those can be added later without changing the basic structure.
