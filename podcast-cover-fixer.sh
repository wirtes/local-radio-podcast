#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_path="$script_dir/config.toml"
mode=""

usage() {
  cat <<EOF
Usage:
  ./podcast-cover-fixer.sh --dry-run [config.toml]
  ./podcast-cover-fixer.sh --write [config.toml]

Flags:
  --dry-run  Show which podcast cover files would be written. No files are changed.
  --write    Write extracted artwork as artist.jpg in podcast folders missing a top-level JPG.

No files were changed. Choose --dry-run to preview or --write to create artwork files.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      if [[ -n "$mode" ]]; then
        echo "Choose only one mode: --dry-run or --write." >&2
        exit 2
      fi
      mode="dry-run"
      shift
      ;;
    --write)
      if [[ -n "$mode" ]]; then
        echo "Choose only one mode: --dry-run or --write." >&2
        exit 2
      fi
      mode="write"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown flag: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      config_path="$1"
      shift
      if [[ $# -gt 0 ]]; then
        echo "Unexpected extra argument: $1" >&2
        usage >&2
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$mode" ]]; then
  usage
  exit 0
fi

if [[ -x "$script_dir/.venv/bin/python" ]]; then
  python_bin="$script_dir/.venv/bin/python"
else
  python_bin="${PYTHON:-python3}"
fi

"$python_bin" - "$mode" "$config_path" <<'PY'
from __future__ import annotations

import io
import sys
import tomllib
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError


def main() -> int:
    mode = sys.argv[1]
    write = mode == "write"
    config_path = Path(sys.argv[2]).expanduser().resolve()
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    raw_root = config.get("feed", {}).get("root_directory")
    if not raw_root:
        print("Missing feed.root_directory in config.", file=sys.stderr)
        return 1

    root = Path(str(raw_root)).expanduser().resolve()
    if not root.is_dir():
        print(f"Configured root directory does not exist: {root}", file=sys.stderr)
        return 1

    podcast_dirs = sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name.lower())
    for podcast_dir in podcast_dirs:
        fix_podcast_cover(podcast_dir, write=write)

    return 0


def fix_podcast_cover(podcast_dir: Path, write: bool) -> None:
    if has_top_level_jpg(podcast_dir):
        print(f"SKIP existing JPG: {podcast_dir}")
        return

    for mp3_path in find_mp3_files(podcast_dir):
        artwork = read_first_artwork(mp3_path)
        if artwork is None:
            continue

        mime, data = artwork
        jpg_data = artwork_to_jpg(mime, data)
        if jpg_data is None:
            print(f"SKIP unsupported artwork ({mime}): {mp3_path}")
            continue

        output_path = podcast_dir / "artist.jpg"
        if write:
            output_path.write_bytes(jpg_data)
            print(f"WROTE {output_path} from {mp3_path}")
        else:
            print(f"WOULD WRITE {output_path} from {mp3_path}")
        return

    print(f"SKIP no embedded JPG artwork found: {podcast_dir}")


def has_top_level_jpg(podcast_dir: Path) -> bool:
    return any(path.is_file() and path.suffix.lower() == ".jpg" for path in podcast_dir.iterdir())


def find_mp3_files(podcast_dir: Path) -> list[Path]:
    return sorted(
        (path for path in podcast_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".mp3"),
        key=lambda path: str(path).lower(),
    )


def read_first_artwork(mp3_path: Path) -> tuple[str, bytes] | None:
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        return None

    pictures = [frame for frame in tags.values() if isinstance(frame, APIC)]
    if not pictures:
        return None

    pictures.sort(key=lambda frame: 0 if frame.type == 3 else 1)
    picture = pictures[0]
    return (picture.mime or "").lower(), bytes(picture.data)


def artwork_to_jpg(mime: str, data: bytes) -> bytes | None:
    if mime in {"image/jpeg", "image/jpg"} or data.startswith(b"\xff\xd8\xff"):
        return data

    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=95)
    return output.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
PY
