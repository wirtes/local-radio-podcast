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
- Optionally set `server.main_page_password` to require a shared password for the homepage only.

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

The homepage shows podcast cards. Click a podcast title to open a pretty HTML episode page with audio players, or copy the raw `feed.xml` URL from the card.

If `server.main_page_password` is set, the homepage prompts for that password. Podcast detail pages, RSS feeds, audio files, and cover images stay reachable so podcast clients continue to work.

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

## Fix Podcast Covers

If podcast folders do not already have cover images, extract embedded MP3 artwork into each podcast root:

```sh
./podcast-cover-fixer.sh --dry-run
```

Write the artwork files:

```sh
./podcast-cover-fixer.sh --write
```

The script reads `config.toml`, scans each immediate podcast directory under `feed.root_directory`, and writes:

```text
artist.jpg
```

It skips any podcast directory that already has a top-level `.jpg` file. It searches MP3s recursively, so artwork inside year subdirectories is fine.
If no flag is specified, it prints usage and does not change files.

## Create Show Info Stubs

Create missing `_info.yaml` files for podcast folders. Run this through the project virtualenv so the script can import the app dependencies:

```sh
.venv/bin/python scripts/create_info_yaml.py --config config.toml
```

Preview without writing:

```sh
.venv/bin/python scripts/create_info_yaml.py --config config.toml --dry-run
```

Existing `_info.yaml` files are left unchanged.

The generated file looks like this:

```yaml
show: Morning Show
station: TBD

tags:
  - To Be Cataloged

notes: |
  Free text.
```

Tags from `_info.yaml` appear on the homepage and can be used to filter the podcast list.

## Repair MP3 Tags

Apple Podcasts may display the RSS feed correctly while iPod sync still reads stale embedded MP3 tags.
If sync shows repeated or wrong episode names, write filename-derived ID3 tags into the files.

Diagnose one podcast before changing files:

```sh
.venv/bin/python app.py diagnose --config config.toml --podcast "Gracyn Your Eardrums" --limit 3
```

This reports duplicate feed titles, GUIDs, enclosure URLs, and the current-vs-target ID3 tag status for the first few episodes.

Preview the changes:

```sh
.venv/bin/python app.py repair-tags --config config.toml
```

Preview one podcast only, using either the podcast title, slug, or full podcast ID:

```sh
.venv/bin/python app.py repair-tags --config config.toml --podcast "Modern Jetset"
.venv/bin/python app.py repair-tags --config config.toml --podcast modern-jetset
```

The preview reports each tag as `ADD`, `CHANGE`, or `OK`:

```text
DRY /path/to/Modern Jetset 2026/2026-03-04 Modern Jetset.mp3
  CHANGE Title (TIT2): old title -> 2026-03-04 Modern Jetset
  OK     Artist (TPE1): Modern Jetset
  ADD    Album (TALB): Modern Jetset 2026
  ADD    Date (TDRC): 2026-03-04
  ADD    Comment (COMM): 2026-03-04 Modern Jetset
```

Write the tags:

```sh
.venv/bin/python app.py repair-tags --config config.toml --write
```

Write tags for one podcast only:

```sh
.venv/bin/python app.py repair-tags --config config.toml --podcast "Modern Jetset" --write
```

Files that do not start with `YYYY-MM-DD` are reported as `SKIP no filename date` and are not modified, even when `--write` is used.

For files named like `2026-03-04 Modern Jetset.mp3`, this writes:

- Title: `2026-03-04 Modern Jetset`
- Date: `2026-03-04`
- Legacy date frames for older iPod/iTunes sync: year `2026`, day/month `0403`, track `20260304`
- Artist: the podcast folder name
- Album: the immediate parent folder name, such as `Modern Jetset 2026`

Tags are saved as ID3v2.3 for better compatibility with older iPods.
After tags are written, the feed's MP3 enclosure URLs include an updated file-version query string so podcast clients are encouraged to download the repaired file instead of reusing a cached copy.
Feed enclosure URLs also end with a readable episode filename, such as `2026-03-04-Modern-Jetset.mp3`, for better compatibility with older iPod/iTunes import paths.

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

Create missing show metadata stubs:

```sh
.venv/bin/python scripts/create_info_yaml.py --config config.toml
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
- Podcast folders can include `_info.yaml` metadata. Homepage tags are read from its `tags` section.
- Parsed `_info.yaml` tags are cached by the file's modified time and refreshed when the file changes.
- If `server.main_page_password` is set, only the homepage is password-protected.
- `/robots.txt` returns `User-agent: *` and `Disallow: /`.
- Each podcast recursively scans its own directory and year subdirectories for `.mp3` files.
- The first `.jpg` file in each podcast directory's top level is used as that podcast's cover image.
- Episode dates are read first from filenames that start with `YYYY-MM-DD`, such as `2026-03-04 Modern Jetset.mp3`.
- RSS items are sorted newest-first by filename date, then ID3 date metadata, then file modification time.
- Episode title, artist, album, date, comment/description, duration, and file size are read from MP3 metadata.
- When MP3 title metadata is missing or only repeats the filename, the filename becomes the episode title.
- MP3 files are served only when they were found inside the requested podcast directory.
- Feed and MP3 responses include no-store cache headers, and feeds are rebuilt from the podcast directories on every request.
- Artwork URLs include a file modified-time version query string and are cacheable for clients/CDNs.
