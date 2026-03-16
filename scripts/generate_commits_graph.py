#!/usr/bin/env python3
"""Fetch all-time GitHub commits and generate a cumulative commits SVG graph."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

USERNAME = os.environ.get("GITHUB_USERNAME", "epicexcelsior")
OUTPUT = os.environ.get("OUTPUT_PATH", "commits-graph.svg")


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
    """Fetch daily contribution counts for every year since account creation."""
    resp = graphql(f'{{ user(login: "{USERNAME}") {{ createdAt }} }}', token)
    created = datetime.fromisoformat(resp["data"]["user"]["createdAt"].replace("Z", "+00:00"))
    start_year = created.year
    now = datetime.now(timezone.utc)
    end_year = now.year

    daily = {}

    for year in range(start_year, end_year + 1):
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
    """Generate a wide, interactive SVG line chart with hover tooltips."""
    if not cumulative:
        return "<svg></svg>"

    # Chart dimensions — wide to match contribution heatmap
    w, h = 840, 180
    pad_l, pad_r, pad_t, pad_b = 52, 20, 30, 32
    cw = w - pad_l - pad_r
    ch = h - pad_t - pad_b

    # Data bounds
    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in cumulative]
    values = [v for _, v in cumulative]
    d_min, d_max = dates[0], dates[-1]
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
        return pad_l + ((date - d_min).total_seconds() / d_range) * cw

    def ypos(val):
        return pad_t + ch - (val / v_ceil) * ch

    # Build polyline points
    points = " ".join(f"{xpos(d):.1f},{ypos(v):.1f}" for d, v in zip(dates, values))

    # Area path
    area_pts = " ".join(f"L{xpos(d):.1f},{ypos(v):.1f}" for d, v in zip(dates, values))
    area = (
        f"M{xpos(dates[0]):.1f},{ypos(0):.1f} "
        f"L{xpos(dates[0]):.1f},{ypos(values[0]):.1f} "
        f"{area_pts[1:]} "
        f"L{xpos(dates[-1]):.1f},{ypos(0):.1f} Z"
    )

    # Y-axis grid lines and labels (4 divisions)
    grid_lines = ""
    for i in range(5):
        val = int(v_ceil * i / 4)
        yy = ypos(val)
        dash = ' stroke-dasharray="3,3"' if i > 0 else ""
        grid_lines += f'  <line x1="{pad_l}" y1="{yy:.1f}" x2="{w - pad_r}" y2="{yy:.1f}" class="grid"{dash}/>\n'
        grid_lines += f'  <text x="{pad_l - 8}" y="{yy + 3:.1f}" class="label" text-anchor="end">{val:,}</text>\n'

    # X-axis labels — year markers
    year_labels = ""
    all_years = sorted(set(d.year for d in dates))
    if len(all_years) <= 8:
        show_years = all_years
    else:
        step = max(1, len(all_years) // 6)
        show_years = all_years[::step]
        if all_years[-1] not in show_years:
            show_years.append(all_years[-1])

    for yr in show_years:
        dt = datetime(yr, 1, 1)
        if dt < d_min:
            dt = d_min
        if dt > d_max:
            continue
        xx = xpos(dt)
        year_labels += f'  <text x="{xx:.1f}" y="{h - 8}" class="label" text-anchor="middle">{yr}</text>\n'

    # Interactive hover dots — sample ~30-50 points along the timeline
    # Use monthly snapshots for clean hover targets
    hover_dots = ""
    monthly = {}
    for d_str, val in cumulative:
        key = d_str[:7]  # YYYY-MM
        monthly[key] = (d_str, val)  # last entry for each month wins

    # Always include first and last
    samples = [(cumulative[0][0], cumulative[0][1])]
    for key in sorted(monthly.keys()):
        d_str, val = monthly[key]
        samples.append((d_str, val))
    samples.append((cumulative[-1][0], cumulative[-1][1]))

    # Deduplicate
    seen = set()
    unique_samples = []
    for s in samples:
        if s[0] not in seen:
            seen.add(s[0])
            unique_samples.append(s)

    for d_str, val in unique_samples:
        dt = datetime.strptime(d_str, "%Y-%m-%d")
        cx = xpos(dt)
        cy = ypos(val)
        # Format date nicely
        date_label = dt.strftime("%b %d, %Y")
        hover_dots += (
            f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="10" class="hit"/>\n'
            f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" class="hover-dot">\n'
            f'    <title>{date_label} — {val:,} commits</title>\n'
            f'  </circle>\n'
        )

    # Last data point — always visible with total label
    last_x, last_y = xpos(dates[-1]), ypos(values[-1])
    # Position label to the left if near right edge
    label_x = last_x - 10
    label_y = last_y - 10
    total_label = (
        f'  <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" class="dot"/>\n'
        f'  <text x="{label_x:.1f}" y="{label_y:.1f}" class="total">{values[-1]:,}</text>\n'
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none">
  <style>
    .label {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 10px; fill: #8b949e; }}
    .title {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 11px; fill: #8b949e; font-weight: 600; }}
    .total {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 11px; fill: #58a6ff; font-weight: 600; }}
    .grid {{ stroke: #21262d; stroke-width: 1; }}
    .line {{ stroke: #58a6ff; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; fill: none; }}
    .dot {{ fill: #58a6ff; }}
    .hover-dot {{ fill: #58a6ff; opacity: 0; cursor: pointer; transition: opacity 0.15s; }}
    .hover-dot:hover {{ opacity: 1; }}
    .hit {{ fill: transparent; cursor: pointer; }}
    .hit:hover + .hover-dot {{ opacity: 1; }}
    .area {{ fill: url(#areaGrad); }}
  </style>
  <defs>
    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#58a6ff" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="#58a6ff" stop-opacity="0.01"/>
    </linearGradient>
  </defs>

  <text x="{pad_l}" y="18" class="title">Cumulative Commits</text>

{grid_lines}
{year_labels}

  <path class="area" d="{area}"/>
  <polyline class="line" points="{points}"/>

{hover_dots}
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
