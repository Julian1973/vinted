"""Deep-vet a candidate by reading its actual ad, not just the search result.

The search catalogue gives title, price, condition label and one thumbnail.
The ad itself holds what decides a buy: the full description (where damage
admissions live), every photo, and the seller's track record. This module
fetches the listing page, extracts those from the embedded JSON, and issues a
verdict:

  PROMISING — description claims good condition, no damage language, seller
              has history. Worth a buying decision today.
  CHECK     — something needs a human look or a message to the seller first:
              fixable flaws mentioned (stains, bobbling — cleaning is the
              play), a one-line description, or a seller with no feedback.
  AVOID     — the description admits damage a clean and reshoot can't fix,
              or the price is explained by something structural.

A verdict is a triage, not a guarantee: the guide's rule still stands —
read the ad yourself and message the seller before buying.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .api import VintedClient
from .photo import PhotoScore, score_photo

log = logging.getLogger(__name__)

# "smoke-free home" and "pet-free" are selling points, not warnings — remove
# them before scanning for the bad versions.
POSITIVE_PHRASES_RE = re.compile(
    r"\b(smoke[\s-]?free|pet[\s-]?free|non[\s-]?smok\w+|no\s+(?:smoke|pets?)|"
    r"no\s+(?:marks?|flaws?|holes?|stains?|damage|defects?|rips?|tears?))\b[^.,;]*",
    re.IGNORECASE,
)

# Damage a clean and a reshoot cannot fix.
FATAL_RE = re.compile(
    r"\b(holes?|holey|torn|tears?|rip(?:ped|s)?|broken|frayed|faulty|"
    r"doesn['’]?t\s+(?:work|close|zip)|zip\s+(?:stuck|sticks|broken|missing)|"
    r"missing\s+\w+|smoke|smoker|smoking|cigarette|odour|smell[sy]?|"
    r"cracked|peeling|shrunk|discolou?red|bleach)\b",
    re.IGNORECASE,
)

# Flaws the buy-clean-reshoot play exists to fix.
FIXABLE_RE = re.compile(
    r"\b(stain(?:s|ed)?|marks?|marked|bobbl\w+|pill(?:ed|ing)|creas\w+|"
    r"scuff\w*|dusty|dirty|needs?\s+(?:a\s+)?(?:wash|clean|iron|steam)|"
    r"mud(?:dy)?|lint|wrinkl\w+)\b",
    re.IGNORECASE,
)

# Explicit good-condition claims — the seller putting it in writing matters,
# because "significantly not as described" is refundable on Vinted.
POSITIVE_RE = re.compile(
    r"\b(excellent\s+condition|immaculate|pristine|like\s+new|as\s+new|"
    r"hardly\s+(?:ever\s+)?worn|barely\s+worn|never\s+worn|"
    r"worn\s+(?:once|twice|(?:only\s+)?a\s+(?:few|couple)\s+(?:of\s+)?times)|"
    r"great\s+condition|perfect\s+condition|very\s+good\s+condition|"
    r"no\s+(?:marks?|flaws?|damage|defects?))\b",
    re.IGNORECASE,
)


@dataclass
class ItemDetails:
    description: str
    photo_urls: list[str]
    feedback_count: Optional[int]
    feedback_reputation: Optional[float]  # 0..5


@dataclass
class VetReport:
    verdict: str  # PROMISING | CHECK | AVOID
    flags: list[str] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    description_excerpt: str = ""
    feedback_count: Optional[int] = None
    feedback_reputation: Optional[float] = None
    photo_scores: list[PhotoScore] = field(default_factory=list)

    @property
    def best_photo(self) -> Optional[float]:
        return max((p.total for p in self.photo_scores), default=None)

    @property
    def worst_photo(self) -> Optional[float]:
        return min((p.total for p in self.photo_scores), default=None)


def _unescape(html: str) -> str:
    """The listing page mixes plain and backslash-escaped JSON payloads;
    normalising the escapes lets one set of regexes see both."""
    return html.replace('\\"', '"').replace("\\/", "/").replace("\\n", "\n")


def fetch_item_details(client: VintedClient, url: str) -> Optional[ItemDetails]:
    try:
        client._throttle()
        resp = client.session.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("Could not fetch ad %s: %s", url, exc)
        return None
    html = _unescape(resp.text)

    descriptions = re.findall(r'"description":"([^"]{20,4000})"', html)
    description = max(descriptions, key=len) if descriptions else ""

    photo_urls = list(
        dict.fromkeys(
            re.findall(r'"full_size_url":"(https://images1\.vinted\.net/[^"]+?)"', html)
        )
    )

    fb_count = fb_rep = None
    m = re.search(
        r'"user_info_header".{0,600}?"feedback_count":(\d+),'
        r'"feedback_reputation":([\d.]+)',
        html,
        re.DOTALL,
    )
    if m:
        fb_count = int(m.group(1))
        fb_rep = round(float(m.group(2)) * 5, 1) if float(m.group(2)) <= 1 else float(m.group(2))

    return ItemDetails(
        description=description,
        photo_urls=photo_urls,
        feedback_count=fb_count,
        feedback_reputation=fb_rep,
    )


def vet_listing(
    client: VintedClient,
    url: str,
    max_photos: int = 4,
) -> Optional[VetReport]:
    details = fetch_item_details(client, url)
    if details is None:
        return None

    text = POSITIVE_PHRASES_RE.sub(" ", details.description)
    positives = sorted({m.group(0).lower() for m in POSITIVE_RE.finditer(details.description)})
    fatal = sorted({m.group(0).lower() for m in FATAL_RE.finditer(text)})
    fixable = sorted({m.group(0).lower() for m in FIXABLE_RE.finditer(text)})

    flags: list[str] = []
    if fatal:
        flags.append("description admits damage: " + ", ".join(fatal))
    if fixable:
        flags.append("fixable flaws mentioned: " + ", ".join(fixable))
    if len(details.description.strip()) < 40:
        flags.append("description is one line — ask the seller for details")
    if details.feedback_count == 0:
        flags.append("seller has no feedback history")
    elif (
        details.feedback_count is not None
        and details.feedback_reputation is not None
        and details.feedback_count >= 5
        and details.feedback_reputation < 4.0
    ):
        flags.append(
            f"seller rated {details.feedback_reputation}/5 "
            f"over {details.feedback_count} sales"
        )
    if len(details.photo_urls) == 1:
        flags.append("only one photo — ask for label and flaw shots")

    photo_scores = []
    for purl in details.photo_urls[:max_photos]:
        data = client.fetch_photo(purl)
        if data:
            try:
                photo_scores.append(score_photo(data))
            except Exception:
                pass

    if fatal:
        verdict = "AVOID"
    elif flags:
        verdict = "CHECK"
    elif positives:
        verdict = "PROMISING"
    else:
        verdict = "CHECK"
        flags.append("description makes no condition claims — ask the seller")

    excerpt = details.description.strip().replace("\n", " ")
    if len(excerpt) > 180:
        excerpt = excerpt[:177] + "…"

    return VetReport(
        verdict=verdict,
        flags=flags,
        positives=positives,
        description_excerpt=excerpt,
        feedback_count=details.feedback_count,
        feedback_reputation=details.feedback_reputation,
        photo_scores=photo_scores,
    )
