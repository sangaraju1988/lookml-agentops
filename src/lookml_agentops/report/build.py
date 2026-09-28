"""Render reports with Jinja2 templates shipped in the package."""

from __future__ import annotations

from pathlib import Path

import jinja2

from lookml_agentops import __version__
from lookml_agentops._util.io import write_text
from lookml_agentops.report.data import ReportData
from lookml_agentops.report.svg import trend_svg

_ENV = jinja2.Environment(
    loader=jinja2.PackageLoader("lookml_agentops.report", "templates"),
    autoescape=jinja2.select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)


def render_markdown(data: ReportData, *, max_rows: int = 15) -> str:
    return _ENV.get_template("report.md.j2").render(d=data, max_rows=max_rows, version=__version__)


def render_html(data: ReportData) -> str:
    spokes = [s.spoke for s in data.spokes]
    return _ENV.get_template("report.html.j2").render(
        d=data, chart=trend_svg(data.trend, spokes), version=__version__
    )


def write_reports(data: ReportData, out_dir: Path) -> list[Path]:
    md = out_dir / "report.md"
    html = out_dir / "report.html"
    write_text(md, render_markdown(data))
    write_text(html, render_html(data))
    return [md, html]
