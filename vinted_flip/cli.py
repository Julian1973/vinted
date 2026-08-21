"""Command-line entry point.

Usage:
    python -m vinted_flip --searches "carhartt jacket" "nike vintage hoodie" \\
        --max-ratio 0.7 --out report

Each search term defines a comparable group: the tool pulls the current market
for that term, computes the median price and demand, then hunts the cheap end
of the same market for underpriced listings with poor photos.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .analysis import FlipCandidate, GroupStats, evaluate, is_kids_size
from .api import VintedClient
from .photo import score_photo
from .report import write_csv, write_html

log = logging.getLogger("vinted_flip")

DEFAULT_SEARCHES = [
    "carhartt jacket",
    "nike vintage sweatshirt",
    "the north face fleece",
    "levis 501",
    "ralph lauren shirt",
]


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vinted_flip", description="Find undervalued Vinted listings with poor photos."
    )
    parser.add_argument(
        "--searches", nargs="+", default=DEFAULT_SEARCHES,
        help="Search terms; each defines a comparable market group.",
    )
    parser.add_argument("--domain", default="www.vinted.co.uk",
                        help="Vinted domain (default UK).")
    parser.add_argument("--pages", type=int, default=2,
                        help="Pages of comparables to sample per search (96/page).")
    parser.add_argument("--max-ratio", type=float, default=0.70,
                        help="Max price as a fraction of group median (default 0.70).")
    parser.add_argument("--photo-threshold", type=float, default=45.0,
                        help="Photo scores below this count as 'poor' (default 45).")
    parser.add_argument("--require-poor-photo", action="store_true",
                        help="Only keep candidates whose photo scores as poor.")
    parser.add_argument("--min-profit", type=float, default=5.0,
                        help="Minimum estimated profit in GBP (default 5).")
    parser.add_argument(
        "--conditions", nargs="+", default=None, metavar="CONDITION",
        help='Only keep these condition labels, e.g. --conditions '
             '"New with tags" "New without tags" "Very good".',
    )
    parser.add_argument("--vet", type=int, default=10, metavar="N",
                        help="Deep-vet the top N candidates by opening their "
                             "ads: description damage scan, all photos, "
                             "seller history (default 10; 0 disables).")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds between requests (be polite; default 2).")
    parser.add_argument("--out", default="report",
                        help="Output basename; writes <out>.html and <out>.csv.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    client = VintedClient(domain=args.domain, delay_seconds=args.delay)
    candidates: list[FlipCandidate] = []

    for term in args.searches:
        log.info("Sampling market for %r ...", term)
        listings = []
        for page in range(1, args.pages + 1):
            batch = client.search(text=term, order="relevance", page=page)
            listings.extend(batch)
            if len(batch) < 96:
                break
        listings = [l for l in listings if not is_kids_size(l)]
        group = GroupStats.from_listings(term, listings)
        if group is None:
            log.warning("Not enough comparables for %r; skipping.", term)
            continue
        log.info(
            "  %d comparables, median £%.2f, avg %.1f favourites",
            group.count, group.median_price, group.mean_favourites,
        )

        # Hunt the cheap end of the same market explicitly.
        cheap = client.search(
            text=term, order="price_low_to_high",
            price_to=round(group.median_price * args.max_ratio, 2),
        )
        seen = {l.id for l in cheap}
        cheap.extend(l for l in listings if l.id not in seen
                     and l.price <= group.median_price * args.max_ratio)

        allowed_conditions = (
            {c.lower() for c in args.conditions} if args.conditions else None
        )
        # The search term's last word names the item type ("...jacket",
        # "...samba"); a candidate whose title never mentions it is usually a
        # different garment or an accessory priced in a different market.
        item_word = term.split()[-1].lower()
        for listing in cheap:
            if not group.matches(listing):
                continue  # off-brand result from the cheapest-first search
            if item_word not in listing.title.lower():
                continue  # different item type than the market we priced
            if allowed_conditions and listing.status.lower() not in allowed_conditions:
                continue
            photo = None
            if listing.photo_url:
                data = client.fetch_photo(listing.photo_url)
                if data:
                    try:
                        photo = score_photo(data)
                    except Exception as exc:  # corrupt image etc.
                        log.debug("Photo scoring failed for %s: %s", listing.id, exc)
            cand = evaluate(
                listing, group, photo,
                max_price_ratio=args.max_ratio,
                poor_photo_threshold=args.photo_threshold,
            )
            if cand is None or cand.estimated_profit < args.min_profit:
                continue
            if args.require_poor_photo and not (
                photo is not None and photo.total < args.photo_threshold
            ):
                continue
            candidates.append(cand)

    candidates.sort(key=lambda c: c.flip_score, reverse=True)

    if args.vet > 0 and candidates:
        from .vet import vet_listing

        to_vet = candidates[: args.vet]
        log.info("Deep-vetting top %d candidates (reading full ads)...", len(to_vet))
        for cand in to_vet:
            cand.vet = vet_listing(client, cand.listing.url)
            if cand.vet:
                log.info("  %s — %s", cand.vet.verdict, cand.listing.title[:60])
        # Damage-admitting ads sink to the bottom; PROMISING floats up.
        rank = {"PROMISING": 0, "CHECK": 1, None: 2, "AVOID": 3}
        candidates.sort(
            key=lambda c: (
                rank.get(c.vet.verdict if c.vet else None, 2),
                -c.flip_score,
            )
        )
    out_html = Path(f"{args.out}.html")
    out_csv = Path(f"{args.out}.csv")
    write_html(candidates, out_html)
    write_csv(candidates, out_csv)
    log.info("Done: %d candidates → %s / %s", len(candidates), out_html, out_csv)
    return 0


if __name__ == "__main__":
    sys.exit(run())
