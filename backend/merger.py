from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List


async def merge_chunks(chunk_paths: List[Path], output_path: Path, expected_size: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _merge() -> int:
        written = 0
        with output_path.open("wb") as out_file:
            for chunk in chunk_paths:
                with chunk.open("rb") as chunk_file:
                    while True:
                        part = chunk_file.read(1024 * 1024)
                        if not part:
                            break
                        out_file.write(part)
                        written += len(part)
        return written

    written_bytes = await asyncio.to_thread(_merge)
    if written_bytes != expected_size:
        raise ValueError(
            f"Merged file size mismatch: expected {expected_size}, got {written_bytes}"
        )


async def cleanup_chunks(chunk_paths: List[Path]) -> None:
    for chunk in chunk_paths:
        try:
            await asyncio.to_thread(chunk.unlink, missing_ok=True)
        except Exception:
            # Best-effort cleanup.
            continue
