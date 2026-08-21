"""Output: ranked HTML report and CSV of flip candidates."""

from __future__ import annotations

import csv
import html
from pathlib import Path

from .analysis import FlipCandidate


def write_csv(candidates: list[FlipCandidate], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "flip_score", "title", "brand", "size", "condition", "price",
                "group_median", "undervalue_ratio", "photo_score",
                "photo_problems", "est_total_cost", "est_resale",
                "est_profit", "watchers", "url",
            ]
        )
        for c in candidates:
            writer.writerow(
                [
                    c.flip_score, c.listing.title, c.listing.brand,
                    c.listing.size, c.listing.status, f"{c.listing.price:.2f}",
                    f"{c.group.median_price:.2f}", c.undervalue_ratio,
                    c.photo.total if c.photo else "",
                    "; ".join(c.photo.problems) if c.photo else "",
                    f"{c.total_cost:.2f}", f"{c.estimated_resale:.2f}",
                    f"{c.estimated_profit:.2f}", c.listing.favourite_count,
                    c.listing.url,
                ]
            )


def write_html(candidates: list[FlipCandidate], path: Path) -> None:
    rows = []
    for c in candidates:
        photo_cell = ""
        if c.listing.photo_url:
            photo_cell = (
                f'<img src="{html.escape(c.listing.photo_url)}" alt="" '
                'loading="lazy" style="width:90px;height:90px;'
                'object-fit:cover;border-radius:8px">'
            )
        photo_score = f"{c.photo.total:.0f}/100" if c.photo else "n/a"
        problems = ", ".join(c.photo.problems) if c.photo else ""
        rows.append(
            f"""<tr>
  <td class="score">{c.flip_score:.0f}</td>
  <td>{photo_cell}</td>
  <td><a href="{html.escape(c.listing.url)}" target="_blank" rel="noopener">
      {html.escape(c.listing.title)}</a><br>
      <small>{html.escape(c.listing.brand)} · {html.escape(c.listing.size)}
      · {html.escape(c.listing.status)}</small></td>
  <td>£{c.listing.price:.2f}<br><small>median £{c.group.median_price:.2f}
      ({c.undervalue_ratio:.0%})</small></td>
  <td>{photo_score}<br><small>{html.escape(problems)}</small></td>
  <td class="profit">£{c.estimated_profit:.2f}<br>
      <small>cost £{c.total_cost:.2f} → sell £{c.estimated_resale:.2f}</small></td>
  <td>{c.listing.favourite_count}</td>
</tr>"""
        )

    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>Vinted Flip Finder</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a2e;
         background: #f7f7fb; }}
  h1 {{ font-size: 1.4rem; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); border-radius: 10px;
           overflow: hidden; }}
  th, td {{ padding: .6rem .8rem; text-align: left; vertical-align: top;
            border-bottom: 1px solid #eee; }}
  th {{ background: #1a1a2e; color: #fff; font-weight: 600; }}
  td.score {{ font-size: 1.3rem; font-weight: 700; color: #6246ea; }}
  td.profit {{ font-weight: 700; color: #0a7d33; }}
  small {{ color: #666; }}
  a {{ color: #1a1a2e; }}
</style>
<h1>Vinted Flip Finder — {len(candidates)} candidates</h1>
<p>Underpriced listings from in-demand groups with weak photos: buy, clean,
reshoot on a plain background, relist just under the group median.</p>
<table>
<tr><th>Score</th><th>Photo</th><th>Listing</th><th>Price</th>
<th>Photo quality</th><th>Est. profit</th><th>Watchers</th></tr>
{''.join(rows)}
</table>
"""
    path.write_text(doc, encoding="utf-8")
