from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat


_MAX_APK_BYTES = 100 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024


class UpdateStoreError(Exception):
    pass


class UpdateStore:
    def __init__(self, apk_path: Path, *, version_code: int, version_name: str):
        # Keep the configured path opaque to clients and do not resolve a final
        # symlink before opening it; the open operation rejects symlink swaps.
        self.apk_path = Path(os.path.abspath(os.fspath(apk_path)))
        self.version_code = version_code
        self.version_name = version_name

    @property
    def filename(self) -> str:
        return f"omp-remote-v{self.version_name}.apk"


    def metadata(self) -> dict[str, object]:
        details, digest, _ = self._snapshot(include_content=False)
        return {
            "version_code": self.version_code,
            "version_name": self.version_name,
            "filename": self.filename,
            "size_bytes": details.st_size,
            "sha256": digest,
        }

    def read(self) -> bytes:
        _, _, content = self._snapshot(include_content=True)
        return content

    def _snapshot(self, *, include_content: bool) -> tuple[os.stat_result, str, bytes]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd: int | None = None
        try:
            fd = os.open(self.apk_path, flags)
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode):
                raise UpdateStoreError("update unavailable")
            if details.st_size > _MAX_APK_BYTES:
                raise UpdateStoreError("update is too large")

            digest = hashlib.sha256()
            content_parts: list[bytes] = []
            total_bytes = 0
            while True:
                chunk = os.read(fd, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                total_bytes += len(chunk)
                if include_content:
                    content_parts.append(chunk)
            final_details = os.fstat(fd)
            if final_details.st_size != details.st_size or total_bytes != details.st_size:
                raise UpdateStoreError("update changed during read")
            return details, digest.hexdigest(), b"".join(content_parts)
        except UpdateStoreError:
            raise
        except OSError as exc:
            raise UpdateStoreError("update unavailable") from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
