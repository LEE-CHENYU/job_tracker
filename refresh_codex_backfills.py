#!/usr/bin/env python3
"""Sync the edgar-crawler market-backfill supervisor's jobs into the tracker.

The Codex CLI runs a launchd supervisor (com.edgar-crawler.market-filings-
supervisor) that maintains backfill workers for DART / TWSE / ASX / HKEX /
EDINET inside GNU screen sessions, and writes a snapshot to
``/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/monitoring/current_status.json``.

This script reads that snapshot and registers one job per market in
~/job_tracker/state/. The menubar then surfaces them alongside Claude's
jobs (Scenario B etc.).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home()))
from job_tracker import register_job, update_job, _state_path

MONITORING_JSON = Path(
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/monitoring/current_status.json"
)

# Static rich-context per market (used as "purpose"). The supervisor itself
# doesn't store these, so we hand-code them so the menubar has the WHY.
MARKET_PURPOSE = {
    "DART": (
        "Backfill Korean DART (Korea Financial Supervisory Service) "
        "regulatory filings 2008→present in 7-day chunks. Output: PDF/XML "
        "filings + metadata rows into the canonical MARKET_FILINGS corpus. "
        "Supervised by com.edgar-crawler.market-filings-supervisor (launchd) "
        "in screen session market_backfill_dart."
    ),
    "TWSE_REPORTS": (
        "Backfill Taiwan Stock Exchange (TWSE) company annual reports "
        "2008→present, processed in 3-company batches. Output: filings + "
        "metadata. Screen session market_backfill_twse_reports."
    ),
    "ASX": (
        "Backfill Australian Stock Exchange (ASX) annual reports via "
        "asx_annual_discovery.py, iterating by year then by symbol. Screen "
        "session market_backfill_asx_annual."
    ),
    "HKEX": (
        "Hong Kong Stock Exchange annual reports backfill 2008→present. "
        "Complete as of 2026-05; not auto-restarted by supervisor. "
        "Total: ~26,500 filings."
    ),
    "EDINET": (
        "Japanese EDINET (Electronic Disclosure for Investors' NETwork) "
        "filings — earlier corpus exists, current backfill paused."
    ),
}


def _parse_progress(progress_str: str | None) -> dict:
    """Try to parse strings like 'year=2023 company_index=756/1965' into structured dict."""
    if not progress_str:
        return {}
    out = {}
    for tok in re.findall(r"(\w+)=(\S+)", progress_str):
        k, v = tok
        # Convert numeric "k/total" → percentage info
        m = re.match(r"^(\d+)/(\d+)$", v)
        if m:
            cur, tot = int(m.group(1)), int(m.group(2))
            out[k] = {"current": cur, "total": tot}
        else:
            out[k] = v
    return out


def main() -> int:
    if not MONITORING_JSON.exists():
        print(f"monitoring snapshot not found at {MONITORING_JSON}",
              file=sys.stderr)
        return 1

    snap = json.loads(MONITORING_JSON.read_text())
    counts = snap.get("counts", {})
    metadata_counts = counts.get("metadata", {}).get("by_market", {})
    raw_counts = counts.get("raw_files", {}).get("by_market", {})
    captured_at = snap.get("captured_at")

    for job in snap.get("jobs", []):
        key = job.get("key", "?")
        market = job.get("market", key.upper())
        job_id = f"codex_backfill_{key.lower()}"

        running = bool(job.get("running"))
        complete = bool(job.get("complete"))
        status = "done" if complete else ("running" if running else "paused")
        progress_str = job.get("progress", "")
        parsed = _parse_progress(progress_str)

        # Try to pick a sensible (current/total) pair: the deepest one (e.g.
        # company_index=756/1965 for TWSE, symbol_index=57/1809 for ASX).
        cur, tot, unit = None, None, "items"
        for k in ("symbol_index", "company_index"):
            if k in parsed and isinstance(parsed[k], dict):
                cur = parsed[k]["current"]
                tot = parsed[k]["total"]
                unit = k.replace("_index", "s")
                break

        meta_rows = metadata_counts.get(market, 0)
        raw_files = raw_counts.get(market, 0)

        details = []
        if progress_str:
            details.append(f"progress: {progress_str}")
        if meta_rows:
            details.append(f"metadata rows: {meta_rows:,}")
        if raw_files:
            details.append(f"raw files: {raw_files:,}")
        recent = job.get("recent_log", []) or []
        if recent:
            tail = recent[-1].strip()
            if tail:
                details.append(f"last log: {tail[:140]}")

        telemetry = {
            "market": market,
            "screen": job.get("screen"),
            "state_file": job.get("state_file"),
            "metadata_rows": meta_rows,
            "raw_files": raw_files,
            "snapshot_captured_at": captured_at,
        }
        for k, v in parsed.items():
            telemetry[f"progress_{k}"] = v

        # Register or update
        if not _state_path(job_id).exists():
            register_job(
                id=job_id,
                title=f"Codex backfill — {market}",
                purpose=MARKET_PURPOSE.get(market, f"Backfill for {market}"),
                agent="codex-cli/edgar-crawler-supervisor",
                log_path=job.get("log_file"),
                cancel_cmd=["screen", "-S", job.get("screen", ""), "-X", "quit"]
                            if job.get("screen") else None,
                tags=["edgar-crawler", "backfill", market.lower()],
                progress_total=tot,
                progress_unit=unit,
            )
        update_job(
            id=job_id,
            current=cur, total=tot, status=status,
            details=details, telemetry=telemetry,
        )
        print(f"updated {job_id}: {status} {cur}/{tot} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
