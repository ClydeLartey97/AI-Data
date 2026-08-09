"""
Allocation path map — where the work goes, and when.

This is the one picture that shows what the system actually does. A job enters,
splits across heterogeneous devices in proportion to what each can really
deliver, and each split lands in a time window chosen against the grid signal.
Three stages, one diagram:

    JOB  ──▶  DEVICES  ──▶  WINDOWS

Drawn as a flow with proportional widths, because the proportions *are* the
decision. Split a job evenly across unequal hardware and the slowest device
sets the finish time while the fastest sit part-idle burning power; splitting
by throughput is the whole point of hardware-aware allocation, and a diagram
with equal-width ribbons would hide exactly the thing worth seeing.

Deliberately not a generic Sankey library: the middle stage needs to carry
per-device power and efficiency, and the right stage needs grid price and
carbon per window. A ribbon whose width is share and whose colour is carbon
says more than either channel alone.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field


@dataclass
class Leg:
    """One device group's share of a job, and the window it runs in."""

    device: str
    count: int
    share: float           # 0-1 of total work
    throughput: float      # effective TFLOPS or tokens/s
    power_kw: float
    hours: float
    window: str = ""       # e.g. "02:00–06:00"
    price: float | None = None      # £/MWh in that window
    carbon: float | None = None     # gCO2/kWh in that window
    fits: bool = True


@dataclass
class FlowSpec:
    job: str
    detail: str
    legs: list[Leg] = field(default_factory=list)
    unit: str = "TFLOPS"
    currency: str = "£"


def _fmt(v: float, dp: int = 1) -> str:
    return f"{v:,.{dp}f}"


def path_map(spec: FlowSpec, *, width: int = 1000, height: int = 320) -> str:
    """Render the allocation as an SVG flow."""
    legs = [l for l in spec.legs if l.share > 0.0005]
    if not legs:
        return '<p class="empty">Nothing allocated.</p>'

    # Columns: job node, device nodes, window nodes.
    x_job, w_job = 8, 132
    x_dev, w_dev = 330, 210
    x_win, w_win = 700, 292
    pad, gap = 18, 8
    usable = height - pad * 2 - gap * (len(legs) - 1)

    # Node heights are proportional to share, with a floor so a 1% leg stays
    # clickable and legible rather than collapsing to a hairline.
    floor = 22.0
    raw = [max(floor, l.share * usable) for l in legs]
    scale = usable / sum(raw) if sum(raw) > usable else 1.0
    heights = [r * scale for r in raw]

    job_h = sum(heights) + gap * (len(legs) - 1)
    job_y = pad

    carbons = [l.carbon for l in legs if l.carbon is not None]
    c_lo, c_hi = (min(carbons), max(carbons)) if carbons else (0, 1)

    def carbon_class(c: float | None) -> str:
        if c is None or c_hi == c_lo:
            return "f-mid"
        t = (c - c_lo) / (c_hi - c_lo)
        return "f-lo" if t < 0.34 else ("f-mid" if t < 0.67 else "f-hi")

    ribbons, devices, windows, labels = [], [], [], []
    y = pad
    src_y = job_y
    for leg, h in zip(legs, heights):
        cls = carbon_class(leg.carbon)
        bad = "" if leg.fits else " f-nofit"

        # job -> device ribbon
        ribbons.append(
            f'<path class="f-ribbon {cls}{bad}" d="'
            f'M{x_job + w_job},{src_y:.1f} '
            f'C{(x_job + w_job + x_dev) / 2},{src_y:.1f} '
            f'{(x_job + w_job + x_dev) / 2},{y:.1f} {x_dev},{y:.1f} '
            f'L{x_dev},{y + h:.1f} '
            f'C{(x_job + w_job + x_dev) / 2},{y + h:.1f} '
            f'{(x_job + w_job + x_dev) / 2},{src_y + h:.1f} '
            f'{x_job + w_job},{src_y + h:.1f} Z"/>')

        # device -> window ribbon
        ribbons.append(
            f'<path class="f-ribbon {cls}{bad}" d="'
            f'M{x_dev + w_dev},{y:.1f} '
            f'C{(x_dev + w_dev + x_win) / 2},{y:.1f} '
            f'{(x_dev + w_dev + x_win) / 2},{y:.1f} {x_win},{y:.1f} '
            f'L{x_win},{y + h:.1f} '
            f'C{(x_dev + w_dev + x_win) / 2},{y + h:.1f} '
            f'{(x_dev + w_dev + x_win) / 2},{y + h:.1f} '
            f'{x_dev + w_dev},{y + h:.1f} Z"/>')

        devices.append(
            f'<rect class="f-node f-dev{bad}" x="{x_dev}" y="{y:.1f}" '
            f'width="{w_dev}" height="{h:.1f}" rx="4"/>')
        windows.append(
            f'<rect class="f-node {cls}" x="{x_win}" y="{y:.1f}" '
            f'width="{w_win}" height="{h:.1f}" rx="4"/>')

        mid = y + h / 2
        small = h < 34
        labels.append(
            f'<text class="f-lab" x="{x_dev + 10}" y="{mid - (5 if not small else 0):.1f}">'
            f'{leg.count}× {html.escape(leg.device)}</text>')
        if not small:
            labels.append(
                f'<text class="f-sub" x="{x_dev + 10}" y="{mid + 10:.1f}">'
                f'{leg.share * 100:.1f}% · {_fmt(leg.throughput)} {html.escape(spec.unit)}'
                f' · {_fmt(leg.power_kw)} kW</text>')
        labels.append(
            f'<text class="f-lab" x="{x_win + 10}" y="{mid - (5 if not small else 0):.1f}">'
            f'{html.escape(leg.window or "immediate")}</text>')
        if not small:
            bits = [f'{_fmt(leg.hours, 2)} h']
            if leg.price is not None:
                bits.append(f'{spec.currency}{_fmt(leg.price)}/MWh')
            if leg.carbon is not None:
                bits.append(f'{_fmt(leg.carbon, 0)} gCO₂')
            labels.append(
                f'<text class="f-sub" x="{x_win + 10}" y="{mid + 10:.1f}">'
                f'{html.escape(" · ".join(bits))}</text>')
        if not leg.fits:
            labels.append(
                f'<text class="f-warn" x="{x_dev + w_dev - 8}" y="{mid + 3:.1f}" '
                f'text-anchor="end">!</text>')

        y += h + gap
        src_y += h + gap

    return f"""
<figure class="flow">
  <svg viewBox="0 0 {width} {height}" role="img"
       aria-label="Allocation of {html.escape(spec.job)} across devices and time windows">
    <text class="f-col" x="{x_job}" y="12">JOB</text>
    <text class="f-col" x="{x_dev}" y="12">DEVICES</text>
    <text class="f-col" x="{x_win}" y="12">WINDOW</text>
    {''.join(ribbons)}
    <rect class="f-node f-job" x="{x_job}" y="{job_y}" width="{w_job}"
          height="{job_h:.1f}" rx="4"/>
    <text class="f-lab" x="{x_job + 10}" y="{job_y + job_h / 2 - 5:.1f}">
      {html.escape(spec.job)}</text>
    <text class="f-sub" x="{x_job + 10}" y="{job_y + job_h / 2 + 10:.1f}">
      {html.escape(spec.detail)}</text>
    {''.join(devices)}{''.join(windows)}{''.join(labels)}
  </svg>
</figure>"""


FLOW_CSS = """
.flow { margin: 0; }
.flow svg { width: 100%; height: auto; display: block; }
.f-col { fill: var(--text-3); font-size: 9px; font-weight: 700; letter-spacing: .12em;
  font-family: inherit; }
.f-node { stroke: none; }
.f-job { fill: var(--text); opacity: .82; }
.f-dev { fill: color-mix(in srgb, var(--blue) 62%, transparent); }
.f-ribbon { opacity: .30; }
.f-lo  { fill: var(--green); }
.f-mid { fill: var(--blue); }
.f-hi  { fill: var(--orange); }
.f-nofit { opacity: .16; }
.f-lab { fill: var(--text); font-size: 11.5px; font-weight: 600; font-family: inherit;
  letter-spacing: -.01em; }
.f-sub { fill: var(--text-2); font-size: 10px; font-family: inherit;
  font-variant-numeric: tabular-nums; }
.f-warn { fill: var(--red); font-size: 13px; font-weight: 800; font-family: inherit; }
.f-job + .f-lab, .f-job ~ .f-lab { fill: var(--bg); }
"""
