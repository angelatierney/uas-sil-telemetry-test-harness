"""
Test report generator — emits machine-readable JSON and a styled HTML
flight-test execution summary from an ``EvaluationResult``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .assertion_engine import EvaluationResult

logger = logging.getLogger(__name__)


class TestReportGenerator:
    """
    Produce ``test_report.json`` and ``test_report.html`` artefacts
    summarising a SIL assertion run.
    """

    # Prevent pytest from treating this production class as a test suite
    # when imported from tests/ (class name starts with "Test").
    __test__ = False

    def __init__(
        self,
        output_dir: Union[str, Path] = ".",
        suite_name: str = "UAS SIL Telemetry Verification",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.suite_name = suite_name
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        result: EvaluationResult,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Path]:
        """
        Write JSON and HTML reports for the given evaluation result.

        Returns
        -------
        dict
            Mapping of ``{"json": Path, "html": Path}`` to the written files.
        """
        report = self._build_report_dict(result, metadata or {})
        json_path = self.write_json(report)
        html_path = self.write_html(report)
        logger.info("Reports written → %s , %s", json_path, html_path)
        return {"json": json_path, "html": html_path}

    def write_json(self, report: Dict[str, Any]) -> Path:
        """Serialize the report dictionary to ``test_report.json``."""
        path = self.output_dir / "test_report.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=False)
        return path

    def write_html(self, report: Dict[str, Any]) -> Path:
        """Render a styled HTML summary to ``test_report.html``."""
        path = self.output_dir / "test_report.html"
        html = self._render_html(report)
        with path.open("w", encoding="utf-8") as fh:
            fh.write(html)
        return path

    # ------------------------------------------------------------------
    # Report assembly
    # ------------------------------------------------------------------

    def _build_report_dict(
        self,
        result: EvaluationResult,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat()
        critical = sum(1 for i in result.issues if i.severity == "CRITICAL")
        high = sum(1 for i in result.issues if i.severity == "HIGH")

        return {
            "suite": self.suite_name,
            "generated_at_utc": generated_at,
            "status": "PASS" if result.passed else "FAIL",
            "frames_evaluated": result.frames_evaluated,
            "issue_count": len(result.issues),
            "severity_summary": {
                "CRITICAL": critical,
                "HIGH": high,
            },
            "metadata": metadata,
            "issues": [issue.to_dict() for issue in result.issues],
        }

    # ------------------------------------------------------------------
    # HTML rendering
    # ------------------------------------------------------------------

    def _render_html(self, report: Dict[str, Any]) -> str:
        status = report["status"]
        status_color = "#1a7f37" if status == "PASS" else "#cf222e"
        status_bg = "#dafbe1" if status == "PASS" else "#ffebe9"

        rows = []
        for issue in report["issues"]:
            sev = issue["severity"]
            sev_color = "#cf222e" if sev == "CRITICAL" else "#9a6700"
            measured = (
                f"{issue['measured_value']:.2f}"
                if issue.get("measured_value") is not None
                else "—"
            )
            limit = (
                f"{issue['limit_value']:.2f}"
                if issue.get("limit_value") is not None
                else "—"
            )
            rows.append(
                f"""
                <tr>
                  <td>{issue['timestamp']:.3f}</td>
                  <td style="color:{sev_color};font-weight:600;">{sev}</td>
                  <td><code>{issue['rule']}</code></td>
                  <td>{issue['description']}</td>
                  <td>{measured}</td>
                  <td>{limit}</td>
                </tr>
                """
            )

        issues_table = (
            "".join(rows)
            if rows
            else '<tr><td colspan="6" style="text-align:center;color:#57606a;">'
            "No issues detected — all frames within envelope.</td></tr>"
        )

        meta_items = "".join(
            f"<li><strong>{k}:</strong> {v}</li>"
            for k, v in report.get("metadata", {}).items()
        ) or "<li>None</li>"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{report['suite']} — Flight Test Report</title>
  <style>
    :root {{
      --bg: #f6f8fa;
      --card: #ffffff;
      --border: #d0d7de;
      --text: #1f2328;
      --muted: #57606a;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 2rem;
      font-family: var(--sans);
      background: var(--bg); color: var(--text);
      line-height: 1.5;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    header {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem 2rem;
      margin-bottom: 1.5rem;
    }}
    h1 {{ margin: 0 0 0.25rem; font-size: 1.5rem; }}
    .subtitle {{ color: var(--muted); font-size: 0.9rem; }}
    .badge {{
      display: inline-block;
      padding: 0.35rem 0.85rem;
      border-radius: 999px;
      font-weight: 700;
      font-size: 0.85rem;
      letter-spacing: 0.04em;
      background: {status_bg};
      color: {status_color};
      border: 1px solid {status_color}33;
      margin-top: 0.75rem;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
    }}
    .card .label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 0.15rem; }}
    section {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1.5rem;
    }}
    h2 {{ margin: 0 0 1rem; font-size: 1.1rem; }}
    table {{
      width: 100%; border-collapse: collapse; font-size: 0.875rem;
    }}
    th, td {{
      text-align: left; padding: 0.6rem 0.75rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{
      background: #f6f8fa; color: var(--muted);
      font-weight: 600; font-size: 0.75rem;
      text-transform: uppercase; letter-spacing: 0.04em;
    }}
    code {{ font-family: var(--mono); font-size: 0.8rem; }}
    ul {{ margin: 0; padding-left: 1.25rem; }}
    footer {{
      text-align: center; color: var(--muted); font-size: 0.8rem;
      margin-top: 2rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{report['suite']}</h1>
      <div class="subtitle">Generated {report['generated_at_utc']}</div>
      <div class="badge">{status}</div>
    </header>

    <div class="cards">
      <div class="card">
        <div class="label">Frames Evaluated</div>
        <div class="value">{report['frames_evaluated']}</div>
      </div>
      <div class="card">
        <div class="label">Total Issues</div>
        <div class="value">{report['issue_count']}</div>
      </div>
      <div class="card">
        <div class="label">Critical</div>
        <div class="value" style="color:#cf222e;">{report['severity_summary']['CRITICAL']}</div>
      </div>
      <div class="card">
        <div class="label">High</div>
        <div class="value" style="color:#9a6700;">{report['severity_summary']['HIGH']}</div>
      </div>
    </div>

    <section>
      <h2>Run Metadata</h2>
      <ul>{meta_items}</ul>
    </section>

    <section>
      <h2>Timestamped Issue Log</h2>
      <table>
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Severity</th>
            <th>Rule</th>
            <th>Description</th>
            <th>Measured</th>
            <th>Limit</th>
          </tr>
        </thead>
        <tbody>
          {issues_table}
        </tbody>
      </table>
    </section>

    <footer>
      UAS SIL Telemetry Test Harness &mdash; Software-in-the-Loop Flight Test Automation
    </footer>
  </div>
</body>
</html>
"""
