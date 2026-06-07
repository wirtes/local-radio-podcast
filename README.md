# Local Radio Podcast

A small Python app that turns a root folder of MP3 directories into private podcast RSS feeds for your local network.

## Setup

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
```

Edit `config.toml`:

- Set `server.base_url` to this computer's LAN URL, such as `http://192.168.1.25:8000`.
- Set `feed.root_directory` to the folder that contains your podcast folders.
- Adjust the library title, description, and author.

Every immediate directory inside `feed.root_directory` becomes a separate podcast. For example:

```text
/Users/you/Music/Local Podcasts/
  Morning Show/
    cover.jpg
    Morning Show 2026/
      2026-03-04 episode-1.mp3
  Interviews/
    artwork.jpg
    Interviews 2026/
      2026-04-12 guest-a.mp3
```

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
http://YOUR_LAN_IP:8000/
```

## Subscribe in Apple Podcasts

On the same local network, open Apple Podcasts and choose:

```text
File -> Follow a Show by URL...
```

Enter:

```text
http://YOUR_LAN_IP:8000/podcasts/PODCAST_ID/feed.xml
```

The homepage lists the feed URL for each podcast folder.

For Apple Podcasts and iPod syncing, subscribe to the specific podcast feed URL, not the homepage URL.
Each episode includes a stable non-permalink GUID, an item link, and an MP3 enclosure URL for better compatibility with older sync paths.

## Run on Debian Startup

These commands assume the code lives at:

```text
/home/YOUR_USER/code/local-radio-podcast
```

Install Python tooling:

```sh
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

Set up the app:

```sh
cd /home/YOUR_USER/code/local-radio-podcast
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml
```

Edit `config.toml`:

```sh
nano config.toml
```

Set `server.base_url` to the Debian machine's LAN URL and `feed.root_directory` to your podcast root:

```toml
[server]
base_url = "http://YOUR_DEBIAN_LAN_IP:8000"
host = "0.0.0.0"
port = 8000

[feed]
root_directory = "/path/to/your/podcast/root"
```

Test it manually:

```sh
cd /home/YOUR_USER/code/local-radio-podcast
.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
```

Then open:

```text
http://YOUR_DEBIAN_LAN_IP:8000/
```

Stop the manual server with `Ctrl+C`.

Create the systemd service:

```sh
sudo nano /etc/systemd/system/local-radio-podcast.service
```

Paste:

```ini
[Unit]
Description=Local Radio Podcast Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
Group=YOUR_USER
WorkingDirectory=/home/YOUR_USER/code/local-radio-podcast
ExecStart=/home/YOUR_USER/code/local-radio-podcast/.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```sh
sudo systemctl daemon-reload
sudo systemctl enable local-radio-podcast
sudo systemctl start local-radio-podcast
```

Check status:

```sh
sudo systemctl status local-radio-podcast
```

Follow logs:

```sh
journalctl -u local-radio-podcast -f
```

If a firewall is enabled, allow the app port:

```sh
sudo ufw allow 8000/tcp
```

## Behavior

- Every immediate directory inside `feed.root_directory` is exposed as a separate podcast.
- Hidden folders, virtualenv/cache folders, and folders without any `.mp3` files are ignored.
- Each podcast recursively scans its own directory and year subdirectories for `.mp3` files.
- The first `.jpg` file in each podcast directory's top level is used as that podcast's cover image.
- Episode dates are read first from filenames that start with `YYYY-MM-DD`, such as `2026-03-04 Modern Jetset.mp3`.
- RSS items are sorted newest-first by filename date, then ID3 date metadata, then file modification time.
- Episode title, artist, album, date, comment/description, duration, and file size are read from MP3 metadata.
- When MP3 title metadata is missing or only repeats the filename, the filename becomes the episode title.
- MP3 files are served only when they were found inside the requested podcast directory.
