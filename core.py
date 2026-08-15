from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".mts", ".m2ts", ".mpeg", ".mpg", ".3gp",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".jfif",
}

MAGIC = b"VHASHMASK\x01"
TOKEN_SIZE = 32
FOOTER_SIZE = len(MAGIC) + TOKEN_SIZE + 8
CHUNK_SIZE = 8 * 1024 * 1024


class CancelledError(Exception):
    pass


@dataclass(frozen=True)
class MediaFile:
    source: Path
    relative: Path
    size: int


def scan_media(folder: Path, output_folder: Path | None = None) -> list[MediaFile]:
    folder = folder.resolve()
    excluded = output_folder.resolve() if output_folder else None
    found: list[MediaFile] = []
    for root, dirs, names in os.walk(folder):
        root_path = Path(root)
        if excluded:
            dirs[:] = [d for d in dirs if (root_path / d).resolve() != excluded]
        for name in names:
            path = root_path / name
            if path.suffix.lower() in MEDIA_EXTENSIONS:
                try:
                    found.append(MediaFile(path, path.relative_to(folder), path.stat().st_size))
                except OSError:
                    continue
    return found


def _existing_footer_size(path: Path) -> int:
    try:
        if path.stat().st_size < FOOTER_SIZE:
            return 0
        with path.open("rb") as stream:
            stream.seek(-FOOTER_SIZE, os.SEEK_END)
            footer = stream.read(FOOTER_SIZE)
        if footer[: len(MAGIC)] != MAGIC:
            return 0
        declared = struct.unpack("<Q", footer[-8:])[0]
        return FOOTER_SIZE if declared == FOOTER_SIZE else 0
    except OSError:
        return 0


def _footer() -> bytes:
    return MAGIC + secrets.token_bytes(TOKEN_SIZE) + struct.pack("<Q", FOOTER_SIZE)


def process_file(
    source: Path,
    destination: Path,
    *,
    in_place: bool = False,
    cancel: threading.Event | None = None,
    on_bytes: Callable[[int], None] | None = None,
) -> None:
    cancel = cancel or threading.Event()
    old_footer = _existing_footer_size(source)
    content_size = source.stat().st_size - old_footer

    if in_place:
        if cancel.is_set():
            raise CancelledError()
        with source.open("r+b") as stream:
            stream.truncate(content_size)
            stream.seek(0, os.SEEK_END)
            stream.write(_footer())
            stream.flush()
            os.fsync(stream.fileno())
        if on_bytes:
            on_bytes(source.stat().st_size)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".vhm-part")
    try:
        copied = 0
        with source.open("rb") as src, temp.open("wb") as dst:
            while copied < content_size:
                if cancel.is_set():
                    raise CancelledError()
                block = src.read(min(CHUNK_SIZE, content_size - copied))
                if not block:
                    break
                dst.write(block)
                copied += len(block)
                if on_bytes:
                    on_bytes(len(block))
            dst.write(_footer())
            dst.flush()
        shutil.copystat(source, temp)
        os.replace(temp, destination)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()

