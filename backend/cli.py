"""Burst command-line helpers."""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List
from urllib.parse import unquote, urlparse

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from downloader import DownloadManager
from interfaces import get_active_interfaces_dict

app = typer.Typer(
    add_completion=False,
    help="Burst CLI commands.",
)
console = Console()


def _attach_console() -> None:
    """Attach to the parent console or allocate a new one for windowed build."""
    if os.name != "nt":
        return
    try:
        # Try to attach to the calling terminal first (cmd, powershell)
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="ignore")
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="ignore")
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="ignore")
            console.file = sys.stdout
            return
            
        # Fallback: Allocate a new console if run as windowed without a parent terminal
        ctypes.windll.kernel32.AllocConsole()
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="ignore")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="ignore")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="ignore")
        console.file = sys.stdout
    except Exception:
        pass


def _pip_command() -> List[str]:
    """Return the pip command for source and packaged execution."""
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "pip"]
    py_launcher = shutil.which("py")
    if py_launcher:
        return [py_launcher, "-m", "pip"]
    python = shutil.which("python")
    if python:
        return [python, "-m", "pip"]
    return ["pip"]


def _run_command(command: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )


def _extract_json_report(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("pip did not return a JSON report")
    return json.loads(stdout[start:end + 1])


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _resolve_with_report(pip_args: List[str]) -> List[str]:
    command = (
        _pip_command()
        + ["install", "--dry-run", "--ignore-installed", "--report", "-", "--no-input", "--disable-pip-version-check", "--no-cache-dir"]
        + pip_args
    )
    result = _run_command(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "pip resolution failed")

    report = _extract_json_report(result.stdout)
    urls = []
    for entry in report.get("install", []):
        download_info = entry.get("download_info") or {}
        url = download_info.get("url")
        if url:
            urls.append(url)
    return _dedupe(urls)


def _resolve_with_verbose_download(pip_args: List[str], tmp_dir: Path) -> List[str]:
    command = (
        _pip_command()
        + ["download", "--dest", str(tmp_dir), "--verbose", "--no-input", "--disable-pip-version-check", "--no-cache-dir"]
        + pip_args
    )
    result = _run_command(command)
    combined = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        raise RuntimeError(combined.strip() or "pip fallback resolution failed")

    urls = []
    pattern = re.compile(r"https?://\S+?(?:\.whl|\.tar\.gz)(?=[\s'\"<>)]|$)")
    for line in combined.splitlines():
        if not line.lstrip().startswith("Downloading"):
            continue
        urls.extend(match.rstrip(".,") for match in pattern.findall(line))
    return _dedupe(urls)


def _resolve_package_urls(pip_args: List[str], tmp_dir: Path) -> List[str]:
    try:
        urls = _resolve_with_report(pip_args)
    except Exception as primary_error:
        console.print(f"[yellow]pip report resolver failed; trying verbose resolver.[/yellow] {primary_error}")
        urls = _resolve_with_verbose_download(pip_args, tmp_dir)

    artifact_urls = [
        url for url in urls
        if urlparse(url).path.endswith((".whl", ".tar.gz"))
    ]
    if not artifact_urls:
        raise RuntimeError("No wheel or source archive URLs were resolved by pip")
    return artifact_urls


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name)
    if not name:
        raise ValueError(f"Could not determine filename for {url}")
    return name


def _active_interfaces() -> List[dict]:
    interfaces = [
        iface for iface in get_active_interfaces_dict()
        if iface.get("ip_address")
    ]
    if not interfaces:
        raise RuntimeError("No active network interfaces found")
    return interfaces


async def download_all(urls: List[str], tmp_dir: Path, interfaces: List[dict]) -> None:
    manager = DownloadManager()
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        for url in urls:
            filename = _filename_from_url(url)
            output_path = tmp_dir / filename
            task_id = progress.add_task(filename, total=None)
            await _download_one(manager, url, output_path, interfaces, progress, task_id)


async def _download_one(
    manager: DownloadManager,
    url: str,
    output_path: Path,
    interfaces: List[dict],
    progress: Progress,
    task_id: TaskID,
) -> None:
    job = await manager.create_job(url, str(output_path), interfaces)

    terminal_states = ("completed", "failed", "cancelled")
    while job.status not in terminal_states:
        total = job.expected_size if job.expected_size > 0 else None
        progress.update(task_id, total=total, completed=job.total_downloaded)
        await asyncio.sleep(0.2)

    progress.update(
        task_id,
        total=job.expected_size or job.total_downloaded,
        completed=job.total_downloaded,
    )
    if job.status != "completed":
        raise RuntimeError(f"{_filename_from_url(url)} failed: {job.error or job.status}")


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Resolve packages with pip, download artifacts with Burst, then install from the local cache.",
)
def install(ctx: typer.Context) -> None:
    pip_args = list(ctx.args)
    if not pip_args:
        console.print("[red]Error:[/red] missing pip install arguments")
        raise typer.Exit(1)

    tmp_dir = Path(tempfile.mkdtemp(prefix="burst-pip-"))
    try:
        console.print("[bold]Resolving packages with pip...[/bold]")
        urls = _resolve_package_urls(pip_args, tmp_dir)
        interfaces = _active_interfaces()

        console.print(f"[bold]Downloading {len(urls)} artifact(s) with Burst...[/bold]")
        asyncio.run(download_all(urls, tmp_dir, interfaces))

        artifacts = sorted(
            path for path in tmp_dir.iterdir()
            if path.is_file() and path.name.endswith((".whl", ".tar.gz"))
        )
        if not artifacts:
            raise RuntimeError("Burst completed, but no installable artifacts were found")

        console.print("[bold]Installing from Burst cache...[/bold]")
        install_command = (
            _pip_command()
            + ["install", "--find-links", str(tmp_dir), "--no-index", "--no-input"]
            + [str(path) for path in artifacts]
        )
        result = subprocess.run(install_command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"pip install failed with exit code {result.returncode}")

        console.print("[green]Done.[/green]")
    except Exception as exc:
        console.print(f"[red]Burst pip failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_pip_cli() -> None:
    _attach_console()
    if len(sys.argv) < 3 or sys.argv[2] != "install":
        console.print("[red]Error:[/red] expected 'burst pip install <packages>'")
        raise typer.Exit(1)
    app(args=sys.argv[3:], prog_name="burst pip install")
