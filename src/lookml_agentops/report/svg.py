"""Inline SVG trend chart (no JS, no external assets) — renders offline."""

from __future__ import annotations

from html import escape

from lookml_agentops.report.data import TrendPoint

W, H = 640, 220
PAD_L, PAD_R, PAD_T, PAD_B = 44, 150, 16, 36


def trend_svg(points: list[TrendPoint], series: list[str]) -> str:
    """Pass-rate per agent across runs. Series colors come from CSS vars --series-N."""
    if not points:
        return ""
    n = len(points)
    iw, ih = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(i: int) -> float:
        return PAD_L + (iw * i / (n - 1) if n > 1 else iw / 2)

    def y(v: float) -> float:
        return PAD_T + ih * (1 - v / 100.0)

    out = [
        f'<svg class="trend" viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Pass rate per agent over the last {n} runs">'
    ]
    for v in (0, 25, 50, 75, 100):
        out.append(
            f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
        )
        out.append(
            f'<text class="tick" x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v}%</text>'
        )
    step = max(1, n // 8)
    for i, p in enumerate(points):
        if i % step == 0 or i == n - 1:
            out.append(
                f'<text class="tick" x="{x(i):.1f}" y="{H - PAD_B + 16}" text-anchor="middle">'
                f"{escape(p.run_id.removeprefix('run-'))}</text>"
            )
    ends: list[tuple[float, float, str]] = []
    for si, agent in enumerate(series):
        cls = f"s{si % 3 + 1}"
        pts = [(x(i), y(p.rates[agent]), p) for i, p in enumerate(points) if agent in p.rates]
        if not pts:
            continue
        path = " ".join(
            f"{'M' if j == 0 else 'L'}{px:.1f},{py:.1f}" for j, (px, py, _) in enumerate(pts)
        )
        out.append(f'<path class="line {cls}" d="{path}"/>')
        for px, py, p in pts:
            tip = f"{agent} · {p.run_id} ({p.runner}{' · ' + p.label if p.label else ''}): {p.rates[agent]:.1f}%"
            out.append(
                f'<g class="pt"><circle class="hit" cx="{px:.1f}" cy="{py:.1f}" r="10"/>'
                f'<circle class="dot {cls}" cx="{px:.1f}" cy="{py:.1f}" r="4"/>'
                f"<title>{escape(tip)}</title></g>"
            )
        lx, ly, lp = pts[-1]
        ends.append((ly, lx, f"{agent} {lp.rates[agent]:.0f}%"))
    # direct labels at line ends, pushed apart so they never overlap
    placed: list[float] = []
    for ly, lx, text in sorted(ends):
        ty = max(ly + 4, placed[-1] + 14) if placed else ly + 4
        placed.append(ty)
        out.append(f'<text class="direct" x="{lx + 10:.1f}" y="{ty:.1f}">{escape(text)}</text>')
    out.append("</svg>")
    return "".join(out)
