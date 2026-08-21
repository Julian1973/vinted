"""Market analysis: what sells, what's underpriced, what's worth flipping.

Approach
--------
Vinted doesn't expose sold-price history to anonymous users, so we estimate the
going rate from the live market: for each comparable group (brand + search
term), the median asking price of currently listed items is our resale
benchmark. Favourite counts are the demand signal — items people are actively
watching are items that sell.

A listing becomes a flip candidate when:
  1. its brand/search group shows real demand (favourites across the group),
  2. it is priced well below the group's median, and
  3. its photo scores poorly (see photo.py) — meaning the low price is likely
     presentation, not a fault, and a clean reshoot can close the gap.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .api import Listing
from .photo import PhotoScore

# Buyer-side costs on Vinted UK: buyer protection fee is roughly 5% + £0.70,
# and you'll usually pay a few pounds shipping when you buy the item in.
BUYER_PROTECTION_RATE = 0.05
BUYER_PROTECTION_FLAT = 0.70
DEFAULT_SHIPPING_IN = 2.50
DEFAULT_CLEANING_COST = 2.00

# Kids'/baby items aren't comparable with an adult-priced market group. The
# size field catches "9-12 months / 74 cm"; the title catches "boys XL puffer"
# where the size field alone looks adult.
KIDS_SIZE_RE = re.compile(
    r"\b(months?|years?|\d+\s*cm|child|kids)\b", re.IGNORECASE
)
KIDS_TITLE_RE = re.compile(
    r"\b(kids?|boys?|girls?|junior|youth|toddler|baby|infant)\b", re.IGNORECASE
)

# Likely fakes or admitted lookalikes — never worth the reputation risk.
FAKE_RE = re.compile(
    r"\b(rip[\s-]?off|replica|fake|copy|inspired|style of|dupe|"
    r"not\s+(?:sure\s+if\s+)?(?:it['’]?s\s+)?authentic|unverified|"
    r"not\s+verified)\b",
    re.IGNORECASE,
)

# A cheap listing whose title admits damage is discounted for a reason a
# reshoot won't fix. (Stains are deliberately absent: cleaning is the play.)
DAMAGE_RE = re.compile(
    r"\b(broken|faulty|damaged|ripped|torn|hole[sy]?|missing|repair|"
    r"doesn'?t work|not working|defect)\b",
    re.IGNORECASE,
)


def is_kids_size(listing: Listing) -> bool:
    return bool(
        KIDS_SIZE_RE.search(listing.size) or KIDS_TITLE_RE.search(listing.title)
    )


def looks_damaged(listing: Listing) -> bool:
    return bool(DAMAGE_RE.search(listing.title))


def looks_fake(listing: Listing) -> bool:
    return bool(FAKE_RE.search(listing.title))


# Brand accessories ride along in brand searches at pocket-money prices —
# laces, keyrings, pins — and aren't comparable with the garment market.
ACCESSORY_RE = re.compile(
    r"\b(laces?|shoelaces?|keyrings?|key\s?rings?|pins?|badges?|stickers?|"
    r"patch(es)?|swing\s?tags?|dust\s?bags?|box\s+only|hangers?|"
    r"buttons?|wax\s+tin|care\s+kit)\b",
    re.IGNORECASE,
)


def is_accessory(listing: Listing) -> bool:
    return bool(ACCESSORY_RE.search(listing.title))


@dataclass
class GroupStats:
    """Price/demand statistics for one comparable group of listings."""

    key: str
    count: int
    median_price: float
    p25_price: float
    mean_favourites: float
    total_favourites: int
    dominant_brand: str = ""

    @classmethod
    def from_listings(cls, key: str, listings: list[Listing]) -> Optional["GroupStats"]:
        prices = sorted(l.price for l in listings if l.price > 0)
        if len(prices) < 5:  # too few comparables to trust a median
            return None
        favs = [l.favourite_count for l in listings]
        brands = Counter(l.brand for l in listings if l.brand)
        return cls(
            key=key,
            count=len(prices),
            median_price=statistics.median(prices),
            p25_price=prices[len(prices) // 4],
            mean_favourites=sum(favs) / len(favs),
            total_favourites=sum(favs),
            dominant_brand=brands.most_common(1)[0][0] if brands else "",
        )

    def matches(self, listing: Listing) -> bool:
        """Is this listing genuinely comparable to the group? Cheapest-first
        search results drift off-brand — and a mere title mention isn't enough
        when the item carries a *different* brand tag (e.g. CHAPS "Ralph
        Lauren" diffusion pieces aren't priced like mainline Ralph Lauren)."""
        if not self.dominant_brand:
            return True
        brand = self.dominant_brand.lower()
        listing_brand = listing.brand.lower().strip()
        if listing_brand and listing_brand not in ("not verified",):
            return listing_brand == brand
        return brand in listing.title.lower()


@dataclass
class FlipCandidate:
    listing: Listing
    group: GroupStats
    photo: Optional[PhotoScore]
    undervalue_ratio: float  # price / group median, lower = cheaper
    estimated_resale: float
    total_cost: float
    estimated_profit: float
    flip_score: float
    reasons: list[str] = field(default_factory=list)


def buy_in_cost(price: float, shipping: float = DEFAULT_SHIPPING_IN,
                cleaning: float = DEFAULT_CLEANING_COST) -> float:
    """What it actually costs you to get the item in hand, cleaned."""
    protection = price * BUYER_PROTECTION_RATE + BUYER_PROTECTION_FLAT
    return price + protection + shipping + cleaning


def evaluate(
    listing: Listing,
    group: GroupStats,
    photo: Optional[PhotoScore],
    max_price_ratio: float = 0.70,
    poor_photo_threshold: float = 45.0,
    min_group_favourites: float = 1.0,
) -> Optional[FlipCandidate]:
    """Return a FlipCandidate if this listing fits the buy-clean-reshoot play."""
    if listing.price <= 0 or group.median_price <= 0:
        return None

    if (
        is_kids_size(listing)
        or looks_damaged(listing)
        or looks_fake(listing)
        or is_accessory(listing)
    ):
        return None  # not comparable / discounted for a reason we can't fix

    ratio = listing.price / group.median_price
    if ratio > max_price_ratio:
        return None  # not underpriced enough to leave margin

    if group.mean_favourites < min_group_favourites:
        return None  # nobody wants this group; cheap but unsellable

    reasons = [f"priced at {ratio:.0%} of group median £{group.median_price:.2f}"]

    poor_photo = photo is not None and photo.total < poor_photo_threshold
    if photo is not None:
        if poor_photo:
            problems = ", ".join(photo.problems) or "low overall quality"
            reasons.append(f"poor photo ({photo.total:.0f}/100: {problems})")
        else:
            reasons.append(f"photo already decent ({photo.total:.0f}/100)")

    if listing.favourite_count > 0:
        reasons.append(f"{listing.favourite_count} people already watching")

    # Reshot on a clean background, price it just under median to move fast.
    estimated_resale = group.median_price * 0.95
    cost = buy_in_cost(listing.price)
    profit = estimated_resale - cost
    if profit <= 0:
        return None

    margin = profit / cost  # return on cash in
    # Score blends margin, demand, and how fixable the listing looks.
    photo_gap = (poor_photo_threshold - photo.total) if poor_photo else 0.0
    demand = min(group.mean_favourites / 10.0, 1.0)
    flip_score = round(
        min(margin, 2.0) / 2.0 * 50  # up to 50 pts for margin
        + demand * 25  # up to 25 pts for demand
        + min(photo_gap, 25.0),  # up to 25 pts for a fixable bad photo
        1,
    )

    return FlipCandidate(
        listing=listing,
        group=group,
        photo=photo,
        undervalue_ratio=round(ratio, 3),
        estimated_resale=round(estimated_resale, 2),
        total_cost=round(cost, 2),
        estimated_profit=round(profit, 2),
        flip_score=flip_score,
        reasons=reasons,
    )
