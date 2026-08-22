"""Market discovery: find WHERE the demand is before hunting in it.

"Best-selling" isn't directly visible on Vinted, but demand velocity is: each
listing's photo carries its upload timestamp, so favourites-per-day can be
computed from a single search page. Sweeping a universe of candidate markets
and ranking them by velocity, liquidity and price level tells us which markets
are worth deep-scanning today — instead of guessing search terms.

Opportunity score per market = median favourites/day (how wanted the typical
item is) × median price (how much margin pool a flip has) × a liquidity
damper (thin markets are hard to price and hard to resell into).
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass

from .api import Listing, VintedClient

log = logging.getLogger(__name__)

# Proven UK resale staples. Deliberately excludes heavy-counterfeit brands
# (Moncler, Supreme, LV...) — not worth the risk for a cleaning-and-reshoot
# operation. Edit freely; discovery re-ranks whatever it is given.
MARKET_UNIVERSE = [
    "carhartt jacket", "carhartt hoodie", "carhartt trousers",
    "the north face puffer", "the north face fleece",
    "patagonia fleece", "berghaus jacket", "columbia fleece",
    "arcteryx jacket", "rab jacket", "montane jacket",
    "nike vintage sweatshirt", "nike tech fleece", "nike hoodie",
    "adidas samba", "adidas gazelle", "adidas vintage jacket",
    "new balance 550", "new balance 990", "salomon trainers",
    "dr martens boots", "ugg boots", "birkenstock",
    "levis 501", "levis vintage jeans", "dickies trousers",
    "ralph lauren jumper", "ralph lauren shirt", "ralph lauren quarter zip",
    "tommy hilfiger jumper", "lacoste polo", "fred perry polo",
    "barbour wax jacket", "barbour quilted jacket",
    "stone island jumper", "cp company overshirt",
    "lululemon leggings", "gymshark leggings", "sweaty betty leggings",
    "free people dress", "reformation dress", "ganni dress",
]


def fav_per_day(listing: Listing, now: float | None = None) -> float | None:
    """Favourites per day since upload; None when age is unknowable."""
    ts = ((listing.raw.get("photo") or {}).get("high_resolution") or {}).get(
        "timestamp"
    )
    if not ts:
        return None
    age_days = max(((now or time.time()) - ts) / 86400, 0.5)  # damp day-0 spikes
    return listing.favourite_count / age_days


@dataclass
class MarketStats:
    term: str
    count: int
    median_price: float
    median_velocity: float  # favourites/day for the typical listing
    hot_share: float  # fraction of listings gaining >= 1 fav/day
    opportunity: float

    @classmethod
    def from_listings(cls, term: str, listings: list[Listing]) -> "MarketStats | None":
        now = time.time()
        prices = [l.price for l in listings if l.price > 0]
        velocities = [v for l in listings if (v := fav_per_day(l, now)) is not None]
        if len(prices) < 10 or len(velocities) < 10:
            return None
        median_price = statistics.median(prices)
        median_velocity = statistics.median(velocities)
        hot_share = sum(1 for v in velocities if v >= 1.0) / len(velocities)
        # Liquidity damper: a full page (~96) scores 1.0, thinner markets less.
        liquidity = min(len(prices) / 96.0, 1.0)
        opportunity = median_velocity * median_price * (0.5 + 0.5 * liquidity)
        return cls(
            term=term,
            count=len(prices),
            median_price=round(median_price, 2),
            median_velocity=round(median_velocity, 2),
            hot_share=round(hot_share, 2),
            opportunity=round(opportunity, 1),
        )


def discover(
    client: VintedClient,
    universe: list[str] | None = None,
    per_page: int = 96,
) -> list[MarketStats]:
    """Sample every candidate market once and rank by opportunity."""
    results: list[MarketStats] = []
    for term in universe or MARKET_UNIVERSE:
        try:
            listings = client.search(text=term, order="relevance", per_page=per_page)
        except Exception as exc:
            log.warning("Discovery search failed for %r: %s", term, exc)
            continue
        stats = MarketStats.from_listings(term, listings)
        if stats:
            results.append(stats)
            log.info(
                "  %-32s median £%-8.2f velocity %.2f fav/day  hot %d%%  score %.0f",
                stats.term, stats.median_price, stats.median_velocity,
                int(stats.hot_share * 100), stats.opportunity,
            )
        else:
            log.info("  %-32s too thin to rank", term)
    results.sort(key=lambda s: s.opportunity, reverse=True)
    return results
