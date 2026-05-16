#!/usr/bin/env python3
"""Recompute Scenario B (HK dual-source rescue) state from its parquet output.

Designed to be called periodically (e.g. cron or just on demand) to keep
the menubar in sync with the actual rescue progress, since the rescue
script doesn't currently call update_job() itself. Computes:
  - filings done / total
  - field rows + fields per filing
  - throughput on the last contiguous window (skips dead gaps)
  - rough ETA
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home()))
from job_tracker import register_job, update_job, mark_failed, _state_path

PARQUET = Path(
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/HKEX_FINANCIALS"
    "/three_statement_parquet/llm_corrections_v5_dualsource_global.parquet"
)
TOTAL = 19274
JOB_ID = "scenario_b_hk_dualsource"


def _latest_log() -> str | None:
    from glob import glob
    matches = sorted(glob("/tmp/hk_rescue_logs/scenario_b_dualsource*"), reverse=True)
    return matches[0] if matches else None


def main() -> int:
    import pandas as pd

    if not _state_path(JOB_ID).exists():
        register_job(
            id=JOB_ID,
            title="HK Scenario B — dual-source enrichment",
            purpose=(
                "Re-extract ALL 19,274 HK annual-report filings whose canonical "
                "aggregator output is missing >=1 critical metric (revenue / "
                "net_income / total_assets). Uses Codex gpt-5.4-mini fed BOTH "
                "the regex-parsed facts JSON AND raw PDF text from the 25,692-"
                "filing text corpus, in a single LLM call per filing. Resolves "
                "70% null rate in canonical_metrics_wide. "
                "Outputs to llm_corrections_v5_dualsource_global.parquet; "
                "downstream adjudicator + aggregator picks it up after completion. "
                "Resumable via --auto-resume-on-cap (sleeps until Codex weekly "
                "reset if cap is hit)."
            ),
            agent="claude-opus-4-7@2026-05-15",
            log_path=_latest_log(),
            cancel_cmd=["pkill", "-f", "hk_codex_dual_source_rescue"],
            tags=["hk-corpus", "long-running", "codex"],
            progress_total=TOTAL,
            progress_unit="filings",
        )

    if not PARQUET.exists():
        update_job(JOB_ID, status="error",
                    details=[f"parquet missing: {PARQUET}"])
        return 1

    df = pd.read_parquet(PARQUET)
    df["extracted_at"] = pd.to_datetime(df["extracted_at"], format="ISO8601")
    n_filings = df["filing_id"].nunique()
    n_rows = len(df)

    # Throughput: last contiguous run (no >5-min gaps)
    times = df.groupby("filing_id")["extracted_at"].max().sort_values()
    diffs = times.diff().dt.total_seconds()
    GAP_SEC = 5 * 60
    contiguous = times if not (diffs > GAP_SEC).any() else times.loc[diffs[diffs > GAP_SEC].index[-1]:]
    if len(contiguous) >= 5:
        span_sec = (contiguous.iloc[-1] - contiguous.iloc[0]).total_seconds()
        sec_per_filing = span_sec / max(1, len(contiguous) - 1)
        rate_per_sec = 1.0 / sec_per_filing if sec_per_filing > 0 else None
    else:
        sec_per_filing = None
        rate_per_sec = None

    # Process running?
    ps = subprocess.run(["pgrep", "-f", "hk_codex_dual_source_rescue"],
                        capture_output=True, text=True, timeout=2)
    running = ps.returncode == 0
    status = "running" if running else "paused"

    fields_per_filing = n_rows / max(1, n_filings)

    update_job(
        JOB_ID,
        current=n_filings, total=TOTAL,
        rate_per_sec=rate_per_sec, status=status,
        details=[
            f"{n_rows:,} field rows",
            f"{fields_per_filing:.1f} fields/filing avg",
            (f"window: last {len(contiguous)} filings, {sec_per_filing:.1f}s each"
             if sec_per_filing else "window: warming up"),
        ],
        telemetry={
            "field_rows": n_rows,
            "fields_per_filing": round(fields_per_filing, 1),
            "sec_per_filing": round(sec_per_filing, 1) if sec_per_filing else None,
            "process_running": running,
        },
    )
    print(f"updated {JOB_ID}: {n_filings:,}/{TOTAL:,} ({n_filings/TOTAL*100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
