# StockAssist Design System (`components/ds/`)

The shared component layer. Everything that makes a screen *look like
StockAssist* comes from here.

---

## The rule

**A page never hand-builds a card, a page header, a tab row, a status pill, a
signed change value or an empty state.** If a page needs one of those, it
imports it. If the system is missing something, it gets added here — not
invented locally.

That rule is not stylistic fussiness. Before this layer existed the page header
was hand-written on 27 pages and the copies had already drifted (`mt-1` vs
`mt-0.5`, `items-center` vs `items-start`, some wrapping on mobile and some
overflowing). Five pages each had their own tab row. The signed-change fragment
— `+1.28% (+36.20)` — was rebuilt in eight places with different arrow sizes,
decimal counts and sign logic. None of those differences was a decision anyone
made; they are what happens when there is nowhere for the decision to live.

---

## How this relates to the other component folders

| Folder | Owns | Example |
| --- | --- | --- |
| `components/ui/` | Generic widget behaviour and accessibility (shadcn/Radix primitives) | `dialog`, `dropdown-menu`, `tooltip` |
| `components/ds/` | **What StockAssist looks like** — product decisions made once | `MetricCard`, `InsightCard`, `StatusBadge` |
| `components/market/`, `stock/`, `ai/` … | Feature components that *consume* `ds/` | `MarketScanner`, `OrderTicket` |

A component belongs in `ds/` when it encodes a **product decision**: how a
metric is presented, what an AI insight must contain, which green means "profit"
and which means "this will execute a trade".

---

## Components

| Component | Use it for |
| --- | --- |
| `Card`, `CardHeader` | Every panel. `Card` renders the platform `.glass-card` surface, so a migrated page and an unmigrated one look identical. |
| `PageHeader` | The top of every authenticated page: serif title, subtitle, right-hand actions slot. |
| `MetricCard` | A headline number — Portfolio Value, Today's P&L, NIFTY 50. Optional sparkline and click-through. |
| `InsightCard` | An AI insight. `variant="featured"` is the dark high-contrast treatment for the single top insight on a page. |
| `StatusBadge` | Connection, market, broker, order and freshness state. Semantic `tone`, never a colour name. |
| `DeltaValue` | A signed market change with its arrow and colour. |
| `SegmentedTabs`, `TabPanel` | Tab rows, in both product shapes (`pill` and `underline`), with the full WAI-ARIA keyboard pattern. |
| `EmptyState` | Empty / no-results / not-connected, always with an explanation and a way forward. |

---

## Decisions encoded here (don't quietly undo these)

**Zero is flat, not positive.** `change >= 0 ? green : red` paints an unchanged
instrument bright green with an up-arrow, which is a false claim about the
market. `DeltaValue` renders exact zero neutral, with no arrow.

**`null` is unavailable, not zero.** A missing metric renders the shared
`Unavailable` em-dash (`components/ui/Unavailable`), never `0` — `₹0` says we
looked and measured nothing, which is a different claim from having no data.
`0` itself is a real value and renders normally.

**Only interactive cards react to the pointer.** A card that merely *contains* a
button must not lift on hover, or the page reads as though everything is
clickable. Pass `interactive` only when the whole card is a click target.

**The active state is a tint plus a marker, never a saturated fill.** This
applies to the sidebar, the tab rows and the segmented control. A loud fill
makes navigation the most colourful thing on screen, competing with the market
data that is supposed to hold attention.

**`--gain` is a market-state colour and is never a button fill.** A green that
means "the price went up" and a green that means "this will execute a trade"
cannot be the same green. Execution actions use `--confirm` / `.btn-confirm`.

**An AI insight carries its reasoning.** `InsightCard` takes `summary` (what
happened), `rationale` (why it matters) and `watch` (what to watch) as separate
slots, so a caller with only a headline produces a card that *visibly* lacks its
reasoning — obvious in review rather than invisible in production.

---

## Tokens

All colour, spacing, radius and elevation values live in `src/index.css` as CSS
custom properties, defined for both themes. Components reference them by name;
they never hardcode a hex value.

Key token groups:

- **Surfaces** — `--bg`, `--bg-surface`, `--bg-elevated`, `--bg-card-glass`
- **Text** — `--text-primary`, `--text-secondary`, `--text-muted`
- **Market state** — `--gain`, `--gain-bg`, `--loss`, `--loss-bg`
- **Actions** — `--brand-accent` (primary), `--confirm` (execution)
- **Status** — `--warning`, `--info`, `--ai-accent`
- **Navigation** — `--nav-active-bg`, `--nav-active-fg`, `--nav-active-marker`
- **Elevation** — `--shadow-sm` / `-md` / `-lg`

`src/__tests__/designTokens.test.js` enforces that every `var()` in the codebase
resolves to a defined token, that the shadcn HSL-component tokens are only ever
used inside `hsl()`, and that every themeable token is defined in **both** the
light and dark blocks. A `var(--typo)` is silently invalid CSS — the declaration
is dropped and the element renders with no background at all — so this is
checked mechanically rather than by review.

---

## Testing these components

`src/components/__tests__/designSystem.test.jsx` asserts the *decisions* above,
not the markup.

One trap worth knowing: **jsdom discards `var()` values entirely.** An element
styled `color: var(--gain)` reports no style attribute at all, so
`toHaveStyle({ color: "var(--gain)" })` matches anything — including an element
painted red. Such an assertion can never fail. That is why `DeltaValue` and
`StatusBadge` expose `data-direction` and `data-tone`: the decision is readable
from the DOM in a form a test can actually falsify.
