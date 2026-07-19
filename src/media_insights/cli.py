"""Typer CLI entry point."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from media_insights.api import configure, create_app
from media_insights.classify import LABELS
from media_insights.config import AppConfig, load_config, resolve_config_path
from media_insights.db import session_scope
from media_insights.models import MediaItem
from media_insights.scanner import manual_rescan_path, scan_all, scan_library

app = typer.Typer(add_completion=False)

_cfg: AppConfig | None = None
_path: Path | None = None

log = logging.getLogger("media_insights")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), "INFO"),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


class HealthCheckFilter(logging.Filter):
    """Drop /healthz from the uvicorn access log.

    Docker's healthcheck polls it every few seconds forever, which buries
    every log line that's actually worth reading. Uvicorn puts the request
    path in record.args (client, method, path, http_version, status).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            return "/healthz" not in str(args[2])
        return True


def _load_cfg(config: str | None) -> AppConfig:
    global _cfg, _path
    if _cfg is None or config:
        _path = resolve_config_path(config)
        _cfg = load_config(_path)
    return _cfg


def _config_path() -> Path:
    assert _path is not None  # set by _load_cfg, always called first
    return _path


@app.callback()
def _root(
    ctx: typer.Context,
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """media-insights command-line."""
    # DB initialization is deferred to the commands that need it, so
    # `version`, `config`, and `--help` work without a writable config_dir.
    cfg = _load_cfg(config)
    _setup_logging("DEBUG" if verbose else cfg.log_level)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command("scan")
def cmd_scan(
    library: str | None = typer.Option(None, "--library", "-l", help="Scan only one library"),
    force: bool = typer.Option(False, "--force", help="Re-probe every file"),
) -> None:
    """Run a one-shot scan."""
    cfg = _load_cfg(None)
    if library:
        for lib in cfg.libraries:
            if lib.name == library:
                typer.echo(json.dumps(scan_library(cfg, lib, force=force, trigger="cli"), indent=2))
                return
        raise typer.BadParameter(f"no such library: {library}")
    summaries = scan_all(cfg, force=force, trigger="cli")
    typer.echo(json.dumps(summaries, indent=2))


@app.command("serve")
def cmd_serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Run the API + Web UI."""
    import uvicorn

    cfg = _load_cfg(None)
    configure(cfg, _config_path())
    bind_host = host or cfg.server.host
    bind_port = port or cfg.server.port
    logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())
    api_app = create_app()
    uvicorn.run(api_app, host=bind_host, port=bind_port, log_level=cfg.log_level.lower())


@app.command("search")
def cmd_search(query: str) -> None:
    """Search titles and file paths."""
    cfg = _load_cfg(None)
    configure(cfg, _config_path())
    with session_scope() as session:
        like = f"%{query}%"
        items = session.query(MediaItem).filter(MediaItem.title.ilike(like)).limit(50).all()
        typer.echo(f"{len(items)} item(s):")
        for it in items:
            typer.echo(f"  [{it.id}] {it.title} ({it.year or '?'}) — {it.match_status} — {it.classification_label or '?'}")
            typer.echo(f"      /items/{it.id}")


@app.command("unmatched")
def cmd_unmatched() -> None:
    """List items still waiting for manual identification."""
    cfg = _load_cfg(None)
    configure(cfg, _config_path())
    with session_scope() as session:
        rows = (
            session.query(MediaItem)
            .filter(MediaItem.match_status.in_(["unmatched", "unresolved"]))
            .order_by(MediaItem.title)
            .all()
        )
        if not rows:
            typer.echo("All matched. Nothing to resolve.")
            return
        typer.echo(f"{len(rows)} unmatched item(s):")
        for it in rows:
            typer.echo(f"  [{it.id}] {it.title} ({it.year or '?'}) in {it.library.name} [{it.match_status}]")


@app.command("resolve")
def cmd_resolve(
    item_id: int = typer.Option(..., "--id"),
    imdb: str | None = typer.Option(None, "--imdb"),
    tmdb: int | None = typer.Option(None, "--tmdb"),
    tvdb: int | None = typer.Option(None, "--tvdb"),
    anidb: int | None = typer.Option(None, "--anidb"),
    classify: str | None = typer.Option(None, "--classify", help="anime|tv|movie|anime_movie"),
) -> None:
    """Attach IDs to a previously-unmatched item."""
    from media_insights.models import ChangeEvent

    cfg = _load_cfg(None)
    configure(cfg, _config_path())
    with session_scope() as session:
        item = session.get(MediaItem, item_id)
        if not item:
            raise typer.BadParameter(f"no such item: {item_id}")
        old = {"match_status": item.match_status, "ids": {
            "imdb": item.imdb_id, "tmdb": item.tmdb_id,
            "tvdb": item.tvdb_id, "anidb": item.anidb_id,
        }}
        if imdb:
            item.imdb_id = imdb
        if tmdb is not None:
            item.tmdb_id = tmdb
        if tvdb is not None:
            item.tvdb_id = tvdb
        if anidb is not None:
            item.anidb_id = anidb
        if classify in LABELS:
            item.classification_label = classify
            item.classification_override = True
        if item.imdb_id or item.tmdb_id or item.tvdb_id or item.anidb_id:
            item.match_status = "matched"
        elif item.match_status == "unmatched":
            # Leave as unmatched when no IDs were attached
            pass
        new = {"match_status": item.match_status, "ids": {
            "imdb": item.imdb_id, "tmdb": item.tmdb_id,
            "tvdb": item.tvdb_id, "anidb": item.anidb_id,
        }}
        session.add(ChangeEvent(
            type="item.identified",
            subject_id=item.id,
            subject_path=None,
            old_payload=old,
            new_payload=new,
            delivery_status="pending",
        ))
    typer.echo(f"Resolved {item_id}: {item.title} -> {item.match_status}")


@app.command("rescan")
def cmd_rescan(path: str) -> None:
    """Force-rescan a single path."""
    cfg = _load_cfg(None)
    configure(cfg, _config_path())
    outcome = manual_rescan_path(cfg, path, trigger="cli")
    typer.echo(outcome)


@app.command("subtitle-coverage")
def cmd_subtitle_coverage(
    language: str | None = typer.Option(
        None, "--language", "-L",
        help="Language token (pt, pt-BR, por, portuguese, ...); "
             "defaults to subtitles.coverage_language in config.yaml",
    ),
    library: str | None = typer.Option(None, "--library", "-l", help="Scope to one library by name"),
    complete_only: bool = typer.Option(False, "--complete-only", help="Only list shows with full coverage"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a formatted report"),
) -> None:
    """Which shows have <language> subtitles in every episode, and which episodes are missing them."""
    import dataclasses

    from media_insights.db import session_scope
    from media_insights.models import Library
    from media_insights.subtitle_coverage import compute_coverage, resolve_language

    cfg = _load_cfg(None)
    configure(cfg, _config_path())
    token = language or cfg.subtitles.coverage_language
    resolved = resolve_language(token)
    if resolved is None:
        raise typer.BadParameter(f"unrecognized language: {token!r}")
    code, display = resolved

    with session_scope() as session:
        library_id = None
        if library:
            row = session.query(Library).filter(Library.name == library).one_or_none()
            if row is None:
                raise typer.BadParameter(f"no such library: {library}")
            library_id = row.id
        items = compute_coverage(session, code, library_id=library_id)

    if complete_only:
        items = [it for it in items if it.complete]

    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(it) for it in items], indent=2))
        return

    complete_items = [it for it in items if it.complete]
    incomplete_items = [it for it in items if not it.complete]

    typer.echo(f"{display} ({code}) subtitle coverage -- {len(items)} show(s)")
    typer.echo("")
    typer.echo(f"Complete ({display} in every episode): {len(complete_items)}")
    for it in complete_items:
        typer.echo(f"  {it.title} ({it.year or '?'}) -- {it.episodes_total}/{it.episodes_total}")

    if complete_only:
        return

    typer.echo("")
    typer.echo(f"Incomplete: {len(incomplete_items)}")
    for it in incomplete_items:
        typer.echo(
            f"  {it.title} ({it.year or '?'}) -- {it.episodes_with}/{it.episodes_total}, "
            f"missing {it.episodes_missing}"
        )
        for ep in it.episodes:
            if not ep.has_language:
                typer.echo(f"      missing: {ep.path}")


@app.command("config")
def cmd_config() -> None:
    """Print the resolved configuration as JSON."""
    cfg = _load_cfg(None)
    typer.echo(json.dumps(cfg.model_dump(mode="json"), indent=2, default=str))


@app.command("version")
def cmd_version() -> None:
    from media_insights import __version__

    typer.echo(f"media-insights {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
