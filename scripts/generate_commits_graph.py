#!/usr/bin/env python3
"""Fetch all-time GitHub commits and generate a cumulative commits SVG graph."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

USERNAME = os.environ.get("GITHUB_USERNAME", "epicexcelsior")
OUTPUT = os.environ.get("OUTPUT_PATH", "commits-graph.svg")
START_YEAR = int(os.environ.get("START_YEAR", "2021"))


def graphql(query, token):
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True, text=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    if result.returncode != 0:
        print(f"GraphQL error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def get_contributions(token):
    """Fetch daily contribution counts for every year since START_YEAR."""
    now = datetime.now(timezone.utc)
    end_year = now.year

    daily = {}

    for year in range(START_YEAR, end_year + 1):
        fr = f"{year}-01-01T00:00:00Z"
        to = f"{year}-12-31T23:59:59Z"
        if year == end_year:
            to = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        query = f"""{{
          user(login: "{USERNAME}") {{
            contributionsCollection(from: "{fr}", to: "{to}") {{
              totalCommitContributions
              contributionCalendar {{
                weeks {{
                  contributionDays {{
                    date
                    contributionCount
                  }}
                }}
              }}
            }}
          }}
        }}"""
        resp = graphql(query, token)
        cal = resp["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        for week in cal["weeks"]:
            for day in week["contributionDays"]:
                if day["contributionCount"] > 0:
                    daily[day["date"]] = day["contributionCount"]

    return daily


def build_cumulative(daily):
    """Turn daily counts into sorted cumulative (date, total) pairs."""
    if not daily:
        return []
    dates = sorted(daily.keys())
    cumulative = []
    total = 0
    for d in dates:
        total += daily[d]
        cumulative.append((d, total))
    return cumulative


def generate_svg(cumulative):
    """Generate a wide SVG line chart with hover tooltips."""
    if not cumulative:
        return "<svg></svg>"

    # Accent color — pastel green
    accent = "#7ee787"
    accent_dim = "#3fb950"

    # Chart dimensions — wide to match contribution heatmap
    w, h = 840, 196
    pad_l, pad_r, pad_t, pad_b = 60, 20, 30, 36
    cw = w - pad_l - pad_r
    ch = h - pad_t - pad_b

    # Data bounds — x-axis always starts at Jan 1 of START_YEAR
    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in cumulative]
    values = [v for _, v in cumulative]
    d_min = datetime(START_YEAR, 1, 1)
    d_max = dates[-1]
    v_max = values[-1]
    d_range = (d_max - d_min).total_seconds() or 1

    # Round v_max up to a nice number
    def nice_ceil(n):
        if n <= 10:
            return 10
        if n <= 50:
            return (int(n / 10) + 1) * 10
        mag = 10 ** (len(str(int(n))) - 1)
        return int(((n / mag) + 0.9999) // 1) * mag

    v_ceil = nice_ceil(v_max)

    def xpos(date):
        return pad_l + max(0, ((date - d_min).total_seconds() / d_range)) * cw

    def ypos(val):
        return pad_t + ch - (val / v_ceil) * ch

    # Build polyline points — include origin at start
    pts = []
    # Start at (d_min, 0) if first data point is after d_min
    if dates[0] > d_min:
        pts.append((d_min, 0))
    for d, v in zip(dates, values):
        pts.append((d, v))

    points = " ".join(f"{xpos(d):.1f},{ypos(v):.1f}" for d, v in pts)

    # Area path
    area_moves = " ".join(f"L{xpos(d):.1f},{ypos(v):.1f}" for d, v in pts)
    area = (
        f"M{xpos(pts[0][0]):.1f},{ypos(0):.1f} "
        f"L{xpos(pts[0][0]):.1f},{ypos(pts[0][1]):.1f} "
        f"{area_moves[1:]} "
        f"L{xpos(pts[-1][0]):.1f},{ypos(0):.1f} Z"
    )

    # Y-axis grid lines and labels (4 divisions)
    grid_lines = ""
    for i in range(5):
        val = int(v_ceil * i / 4)
        yy = ypos(val)
        dash = ' stroke-dasharray="3,3"' if i > 0 else ""
        grid_lines += f'  <line x1="{pad_l}" y1="{yy:.1f}" x2="{w - pad_r}" y2="{yy:.1f}" class="grid"{dash}/>\n'
        grid_lines += f'  <text x="{pad_l - 10}" y="{yy + 3:.1f}" class="label" text-anchor="end">{val:,}</text>\n'

    # X-axis labels — year markers, offset right so they don't overlap y-axis
    year_labels = ""
    now_year = datetime.now().year
    for yr in range(START_YEAR, now_year + 1):
        dt = datetime(yr, 1, 1)
        if dt > d_max:
            continue
        xx = xpos(dt)
        # Skip if too close to left edge (overlaps y-axis labels)
        if xx < pad_l + 10 and yr == START_YEAR:
            xx = pad_l + 10
        year_labels += f'  <text x="{xx:.1f}" y="{h - 10}" class="label" text-anchor="middle">{yr}</text>\n'

    # Monthly hover groups — each is a vertical strip with tooltip
    hover_groups = ""
    monthly = {}
    for d_str, val in cumulative:
        key = d_str[:7]  # YYYY-MM
        monthly[key] = (d_str, val)

    sorted_months = sorted(monthly.keys())
    for i, key in enumerate(sorted_months):
        d_str, val = monthly[key]
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        cx = xpos(dt)
        cy = ypos(val)
        date_label = dt.strftime("%b %Y")

        # Compute monthly delta
        if i > 0:
            prev_val = monthly[sorted_months[i - 1]][1]
            delta = val - prev_val
        else:
            delta = val

        tooltip = f"{date_label}: {val:,} total (+{delta:,} this month)"

        # Invisible wide hit area + visible dot on hover + vertical guide line
        hover_groups += f'  <g class="hoverpoint">\n'
        hover_groups += f'    <line x1="{cx:.1f}" y1="{pad_t}" x2="{cx:.1f}" y2="{pad_t + ch:.1f}" class="guide"/>\n'
        hover_groups += f'    <circle cx="{cx:.1f}" cy="{cy:.1f}" r="16" class="hit"/>\n'
        hover_groups += f'    <circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" class="hover-dot"><title>{tooltip}</title></circle>\n'
        hover_groups += f'    <rect x="{cx - 70:.1f}" y="{cy - 26:.1f}" width="140" height="20" rx="4" class="tooltip-bg"/>\n'
        hover_groups += f'    <text x="{cx:.1f}" y="{cy - 12:.1f}" class="tooltip-text" text-anchor="middle">{tooltip}</text>\n'
        hover_groups += f'  </g>\n'

    # Last data point — always visible
    last_x, last_y = xpos(dates[-1]), ypos(values[-1])
    label_x = last_x - 10
    label_y = last_y - 10
    total_label = (
        f'  <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" class="dot"/>\n'
        f'  <text x="{label_x:.1f}" y="{label_y:.1f}" class="total">{values[-1]:,}</text>\n'
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none">
  <style>
    .label {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 10px; fill: #8b949e; }}
    .title {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 11px; fill: #8b949e; font-weight: 600; }}
    .total {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 11px; fill: {accent}; font-weight: 600; }}
    .grid {{ stroke: #21262d; stroke-width: 1; }}
    .line {{ stroke: {accent}; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; fill: none; }}
    .dot {{ fill: {accent}; }}
    .area {{ fill: url(#areaGrad); }}
    /* Hover interaction */
    .hoverpoint .guide {{ stroke: {accent}; stroke-width: 1; stroke-dasharray: 2,2; opacity: 0; }}
    .hoverpoint .hover-dot {{ fill: {accent}; opacity: 0; }}
    .hoverpoint .hit {{ fill: transparent; cursor: pointer; }}
    .hoverpoint .tooltip-bg {{ fill: #161b22; stroke: #30363d; stroke-width: 1; opacity: 0; }}
    .hoverpoint .tooltip-text {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 9px; fill: #c9d1d9; opacity: 0; }}
    .hoverpoint:hover .guide {{ opacity: 0.5; }}
    .hoverpoint:hover .hover-dot {{ opacity: 1; }}
    .hoverpoint:hover .tooltip-bg {{ opacity: 0.95; }}
    .hoverpoint:hover .tooltip-text {{ opacity: 1; }}
  </style>
  <defs>
    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0.01"/>
    </linearGradient>
  </defs>

  <text x="{pad_l}" y="18" class="title">Cumulative Commits</text>

{grid_lines}
{year_labels}

  <path class="area" d="{area}"/>
  <polyline class="line" points="{points}"/>

{hover_groups}
{total_label}
</svg>"""
    return svg


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("No GH_TOKEN or GITHUB_TOKEN set, trying gh auth", file=sys.stderr)
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        token = r.stdout.strip()
    if not token:
        print("Error: could not find a GitHub token", file=sys.stderr)
        sys.exit(1)

    daily = get_contributions(token)
    cumulative = build_cumulative(daily)
    svg = generate_svg(cumulative)

    with open(OUTPUT, "w") as f:
        f.write(svg)

    total = cumulative[-1][1] if cumulative else 0
    print(f"Generated {OUTPUT} — {total} total commits, {len(cumulative)} active days")


if __name__ == "__main__":
    main()
