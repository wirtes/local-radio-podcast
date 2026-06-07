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
    episode-1.mp3
  Interviews/
    artwork.jpg
    guest-a.mp3
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

## Run on Debian Startup

These commands assume the code lives at:

```text
/home/alw/code/local-radio-podcast
```

Install Python tooling:

```sh
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

Set up the app:

```sh
cd /home/alw/code/local-radio-podcast
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
cd /home/alw/code/local-radio-podcast
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
User=alw
Group=alw
WorkingDirectory=/home/alw/code/local-radio-podcast
ExecStart=/home/alw/code/local-radio-podcast/.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
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
- Each podcast recursively scans its own directory for `.mp3` files.
- The first `.jpg` file in each podcast directory's top level is used as that podcast's cover image.
- RSS items are sorted newest-first by ID3 date metadata when available, otherwise by file modification time.
- Episode title, artist, album, date, comment/description, duration, and file size are read from MP3 metadata.
- MP3 files are served only when they were found inside the requested podcast directory.
