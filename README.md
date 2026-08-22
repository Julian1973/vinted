# Vinted Flip Finder

Finds Vinted listings that fit the **buy → clean → reshoot → relist** play:
items from in-demand markets that are priced well below the going rate *and*
let down by a poor photograph (dark, blurry, cluttered background). The theory:
when the only thing wrong with a listing is presentation, a clean-up and a good
shot on a plain background can close most of the gap to the market median.

## How it works

**Finding the best markets first** (`--discover`): before hunting, the tool
can sweep a universe of ~40 proven UK resale markets and rank them by real
demand — each listing's upload timestamp lets it compute **favourites per
day**, so "best-selling" becomes measurable. Markets are scored by demand
velocity × price level × liquidity and written to a league table
(`<out>-markets.csv`). Add `--auto 5` to deep-scan the top 5 automatically:

```bash
python -m vinted_flip --discover --auto 5 --conditions "Very good" \
    "New with tags" "New without tags"
```

For each search term (given via `--searches` or chosen by `--auto`):

1. **Samples the market** — pulls a couple of pages of current listings and
   computes the median asking price and average favourite count (the demand
   signal: watched items are items that sell).
2. **Hunts the cheap end** — re-searches the same term sorted cheapest-first,
   capped at your undervalue threshold (default: 70% of median).
3. **Scores every photo** 0–100 on brightness, contrast, sharpness, and
   background clutter (pure Pillow, no heavy CV stack). Under ~45 is the
   "bedroom-floor shot" a reshoot visibly fixes.
4. **Estimates profit** — resale at 95% of group median, minus the buy price,
   buyer-protection fee (~5% + £0.70), inbound shipping, and a cleaning
   allowance. On Vinted UK selling is fee-free, so what's left is yours.
5. **Deep-vets the top candidates** — opens each ad itself (not just the
   search result): scans the full description for damage the discount is
   really about (holes, broken zips, smells → **AVOID**) versus flaws the
   play exists to fix (stains, bobbling → still in), checks the seller's
   feedback score and history, counts photos, and issues a verdict —
   **PROMISING / CHECK / AVOID** — with the reasons and a description
   excerpt shown in the report. `--vet N` controls how many (default 10).
6. **Ranks and reports** — PROMISING first, AVOID last; within a verdict, a
   flip score blends margin, demand, and how fixable the photo is. Results
   land in `report.html` (browseable, with thumbnails) and `report.csv`.

## Usage

```bash
pip install -r requirements.txt

# Default scan (Carhartt, Nike vintage, TNF, Levi's 501, Ralph Lauren):
python -m vinted_flip

# Your own hunting grounds:
python -m vinted_flip --searches "adidas gazelle" "barbour wax jacket" \
    --max-ratio 0.65 --min-profit 8 --require-poor-photo

# Other markets:
python -m vinted_flip --domain www.vinted.fr --searches "lacoste polo"
```

Key flags:

| Flag | Default | Meaning |
|---|---|---|
| `--max-ratio` | 0.70 | Only flag items at ≤ this fraction of the group median |
| `--photo-threshold` | 45 | Photo scores below this count as "poor" |
| `--require-poor-photo` | off | Drop candidates whose photos are already decent |
| `--min-profit` | £5 | Minimum estimated profit to report |
| `--pages` | 2 | Pages of comparables sampled per search (96/page) |
| `--delay` | 2.0s | Politeness delay between requests |

## Linking your Vinted account (optional)

With your account linked, the scanner **hearts its PROMISING finds** so they
land in the Favourites tab of the Vinted app — one tap from the ad, one more
from buying. Vinted has no official third-party login, so linking means
lending the tool your browser session:

1. Log in at vinted.co.uk in your browser.
2. Open dev tools (F12) → Network tab → click any request to vinted.co.uk →
   Request Headers → copy the entire value of the `Cookie` header.
3. Paste it into a file, e.g. `~/.vinted_cookies` (one line). Treat this file
   like a password — anyone holding it is logged in as you.

```bash
python -m vinted_flip --discover --auto 5 \
    --cookies ~/.vinted_cookies --favourite promising
```

The run logs `Linked to Vinted account: <name>` when the session is
recognised; if the cookie has expired it says so and continues anonymously.

Safety rails, on purpose: favouriting is capped per run (15 for
`promising`, 25 for `all`), every action is human-paced by the request
delay, and the tool will never buy, bid, message, or list on your behalf —
automating those is against Vinted's terms and risks the account the whole
operation depends on. Sessions expire after a while; re-copy the cookie
when the link stops being recognised.

## Vinted MCP server (chat-driven research)

`.mcp.json` configures the [`vinted-mcp-server`](https://www.npmjs.com/package/vinted-mcp-server)
so Claude Code sessions on this repo can also search Vinted, compare prices,
and analyse sellers interactively in chat — useful for vetting individual
candidates the scanner surfaces.

## Honest caveats

- Vinted has **no official public API**; this uses the same JSON endpoints the
  website calls, anonymously. Keep volume low (the built-in delay helps),
  cache results, and respect Vinted's terms of service. This is a personal
  research tool, not a bulk scraper.
- The resale benchmark is the median **asking** price of live listings, not
  sold prices (Vinted doesn't expose those anonymously). Asking prices run a
  bit above sold prices, so treat profit estimates as optimistic ceilings and
  sanity-check candidates by eye before buying.
- A very cheap listing can also mean stains, fake, or damage the photo hides.
  The photo score can't tell "badly photographed gem" from "honestly awful
  item" — always read the description and message the seller.
