# Local Radio Podcast

A small Python app that turns one or more local MP3 directories into a private podcast RSS feed for your local network.

## Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
```

Edit `config.toml`:

- Set `server.base_url` to this computer's LAN URL, such as `http://192.168.1.25:8000`.
- Add every MP3 folder you want scanned to `feed.directories`.
- Adjust the feed title, description, and author.

Find your LAN IP on macOS with:

```sh
ipconfig getifaddr en0
```

## Run

```sh
.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
```

Then open:

```text
http://YOUR_LAN_IP:8000/feed.xml
```

## Subscribe in Apple Podcasts

On the same local network, open Apple Podcasts and choose:

```text
File -> Follow a Show by URL...
```

Enter:

```text
http://YOUR_LAN_IP:8000/feed.xml
```

## Behavior

- The app recursively scans all configured directories for `.mp3` files.
- RSS items are sorted newest-first by ID3 date metadata when available, otherwise by file modification time.
- Episode title, artist, album, date, comment/description, duration, and file size are read from MP3 metadata.
- MP3 files are served only when they were found inside the configured directories.
