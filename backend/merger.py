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
    if expected_size > 0 and written_bytes < expected_size * 0.99:
        raise ValueError(
            f"Merged file appears truncated: expected ~{expected_size} bytes, got {written_bytes}"
        )


async def cleanup_chunks(chunk_paths: List[Path]) -> None:
    for chunk in chunk_paths:
        try:
            await asyncio.to_thread(chunk.unlink, missing_ok=True)
        except Exception:
            # Best-effort cleanup.
            continue
