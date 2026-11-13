#!/usr/bin/env python3
"""Extract the complete SpheraQA audio and check every file against its hash."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = (args.output_dir or args.repository).resolve()
    media = [json.loads(line) for line in (args.repository / "media.jsonl").open() if line.strip()]
    expected = {row["audio_path"]: row for row in media}
    if len(expected) != len(media): raise ValueError("duplicate media path")
    for name in expected:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 2 or path.parts[0] != "audio" or path.suffix != ".wav":
            raise ValueError("invalid media path")

    def check(row):
        path = root / row["audio_path"]
        if not path.is_file() or path.is_symlink() or path.stat().st_size != row["bytes"] or file_hash(path) != row["sha256"]:
            raise ValueError("audio missing or changed: " + row["audio_path"])

    if args.verify_only:
        with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(check, media))
    else:
        parts = json.loads((args.repository / "audio_parts.json").read_text())
        seen = set()
        (root / "audio").mkdir(parents=True, exist_ok=True)
        if (root / "audio").is_symlink(): raise ValueError("audio output must be a normal directory")
        for part in parts["parts"]:
            path = args.repository / part["file"]
            if not path.exists() or path.stat().st_size != part["bytes"]:
                raise ValueError(f"Download {part['file']} with git lfs pull before extracting audio.")
            if file_hash(path) != part["sha256"]: raise ValueError("archive hash mismatch: " + part["file"])
            with tarfile.open(path, mode="r|gz") as archive:
                for member in archive:
                    if not member.isfile() or member.name not in expected or member.name in seen:
                        raise ValueError("unexpected, duplicate, or non-file archive member")
                    row = expected[member.name]
                    if member.size != row["bytes"]: raise ValueError("archive member size mismatch")
                    target = root / member.name
                    write = not target.exists()
                    if not write: check(row)
                    temporary = target.with_suffix(".partial")
                    h = hashlib.sha256()
                    destination = temporary.open("wb") if write else None
                    try:
                        with archive.extractfile(member) as source:
                            for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
                                h.update(block)
                                if destination: destination.write(block)
                    finally:
                        if destination: destination.close()
                    if h.hexdigest() != row["sha256"]: raise ValueError("archive audio hash mismatch")
                    if write: temporary.replace(target)
                    seen.add(member.name)
            print(json.dumps({"part": part["file"], "audio_files": len(seen), "total": len(media)}), flush=True)
        if seen != set(expected): raise ValueError("archives do not cover the complete media list")
    print(json.dumps({"status": "verified", "audio_files": len(media), "audio_bytes": sum(m["bytes"] for m in media)}))


if __name__ == "__main__": main()
