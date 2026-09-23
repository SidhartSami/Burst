from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import List, Optional


async def merge_chunks(
    chunk_paths: List[Path],
    output_path: Path,
    expected_size: int,
    expected_chunk_sizes: Optional[List[int]] = None,
) -> None:
    """
    Merge chunk files into final output file with pre-merge chunk validation,
    atomic merge via temporary file, fsync, and atomic rename.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ponytail: ceiling: requires 2x file size free disk space during assembly; upgrade: Milestone 4 preflight disk space check
    temp_output = output_path.with_suffix(output_path.suffix + f".merge_tmp_{uuid.uuid4().hex[:8]}")

    def _merge() -> int:
        # Step 1: Pre-verify all chunk files exist and match sizes
        for idx, chunk in enumerate(chunk_paths):
            if not chunk.exists():
                raise FileNotFoundError(f"Missing chunk file before merge: {chunk}")
            actual_size = chunk.stat().st_size
            if actual_size == 0 and expected_size > 0:
                raise ValueError(f"Chunk file is unexpectedly empty: {chunk.name}")
            if expected_chunk_sizes and idx < len(expected_chunk_sizes):
                exp = expected_chunk_sizes[idx]
                if actual_size != exp:
                    raise ValueError(
                        f"Chunk size mismatch for {chunk.name}: expected {exp} bytes, got {actual_size} bytes"
                    )

        # Step 2: Merge into temp file
        written = 0
        try:
            with temp_output.open("wb") as out_file:
                for chunk in chunk_paths:
                    with chunk.open("rb") as chunk_file:
                        while True:
                            part = chunk_file.read(1024 * 1024)
                            if not part:
                                break
                            out_file.write(part)
                            written += len(part)
                out_file.flush()
                try:
                    os.fsync(out_file.fileno())
                except Exception:
                    pass

            if expected_size > 0 and written != expected_size:
                raise ValueError(
                    f"Merged file size mismatch: expected {expected_size} bytes, got {written} bytes"
                )

            # Step 3: Atomic replace to final output
            os.replace(temp_output, output_path)
            return written
        finally:
            if temp_output.exists():
                try:
                    temp_output.unlink(missing_ok=True)
                except Exception:
                    pass

    await asyncio.to_thread(_merge)


async def cleanup_chunks(chunk_paths: List[Path]) -> None:
    for chunk in chunk_paths:
        try:
            await asyncio.to_thread(chunk.unlink, missing_ok=True)
        except Exception:
            # Best-effort cleanup.
            continue
