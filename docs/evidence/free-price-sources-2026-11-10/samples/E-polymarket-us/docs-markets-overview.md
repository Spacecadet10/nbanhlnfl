> ## Documentation Index
> Fetch the complete documentation index at: https://docs.polymarket.us/llms.txt
> Use this file to discover all available pages before exploring further.

# Markets API Overview

> Query market data and information

# Markets API

The Market API provides access to market information, pricing, and settlement data.

## Endpoints

| Method | Endpoint | Description |
| - | - | - |
| `GET` | `/v1/markets` | Get all markets with filtering |
| `GET` | `/v1/market/id/{id}` | Get market by ID |
| `GET` | `/v1/market/slug/{slug}` | Get market by slug |
| `GET` | `/v1/markets/{slug}/book` | Get full market order book and stats |
| `GET` | `/v1/markets/{slug}/bbo` | Get best bid/offer (lightweight) |
| `GET` | `/v1/markets/{slug}/settlement` | Get market settlement price |
| `GET` | `/v1/price-history` | Get historical Yes and No display prices |

## Key Market Fields

| Field | Description |
| - | - |
| `id` | Unique market identifier |
| `slug` | URL-friendly identifier |
| `question` | Market question |
| `description` | Detailed market description |
| `category` | Market category |
| `subcategory` | Market subcategory |
| `active` | Whether market is accepting orders |
| `closed` | Whether market has closed |
| `archived` | Whether market is archived |

### Pricing Fields

| Field | Description |
| - | - |
| `orderPriceMinTickSize` | Minimum valid price increment for orders on this market |
| `minimumTradeQty` | Minimum order quantity in contracts |
| `lastTradePrice` | Most recent trade price |
| `bestBid` | Best bid price |
| `bestAsk` | Best ask price |
| `spread` | Current bid-ask spread |
| `oneDayPriceChange` | 24-hour price change |
| `oneWeekPriceChange` | 7-day price change |

Use `orderPriceMinTickSize` and `minimumTradeQty` from the market response before submitting orders. Do not infer price tick size or minimum quantity from product type, symbol, or slug. For example, `minimumTradeQty: 0.01` means the market supports 1% contract increments, and `orderPriceMinTickSize: 0.005` means valid order prices move in half-cent increments.

### Volume & Liquidity

| Field | Description |
| - | - |
| `liquidity` | Current market liquidity |
| `liquidityNum` | Liquidity as number |
| `volume` | Total trading volume |
| `volumeNum` | Volume as number |
| `volume24hr` | 24-hour volume |
| `volume1wk` | 7-day volume |
| `volume1mo` | 30-day volume |

### Sports Market Fields

| Field | Type | Description |
| - | - | - |
| `sportsMarketType` | string | Fine-grained sports market type, for example `football_team_full_game_spread`. Same inventory as the Institutional `market_sport_type` field; see [Sports Schema](https://docs.polymarket.us/trader-guide/sports-schema.md) |
| `sportsMarketTypeV2` | string (enum) | Type: MONEYLINE, SPREAD, TOTAL, or PROP |
| `gameId` | string | Sports provider game ID |
| `line` | number | Line value for spread/total markets |

### Asset Price Market Fields

Markets that settle against a reference price carry a typed `assetPriceTerms` object: crypto price markets (`ASSET_CLASS_CRYPTO`) and gold and silver price markets (`ASSET_CLASS_COMMODITY`). It is `null` on every other market, including the hand-listed year-end Bitcoin markets, which show `marketType: "futures"`. Automated crypto, gold and silver markets have an empty `marketType` and `sportsMarketType`; identify them from `assetPriceTerms.marketType`. See [Crypto Schema](https://docs.polymarket.us/trader-guide/crypto-schema.md) and [Metals Schema](https://docs.polymarket.us/trader-guide/metals-schema.md) for the Institutional counterpart of every field. `priceDecimals`, `assetName` and `priceUnit` are Retail only and have no Institutional counterpart. `nextWindowStart` maps to `metals_next_window_start` on gold and silver, and to `interval_end` on crypto, which trades around the clock.

| Field | Type | Description |
| - | - | - |
| `assetPriceTerms.marketType` | string (enum) | `ASSET_PRICE_MARKET_TYPE_UP_DOWN`, `ASSET_PRICE_MARKET_TYPE_ABOVE_BELOW`, `ASSET_PRICE_MARKET_TYPE_RANGE`, or `ASSET_PRICE_MARKET_TYPE_ONE_TOUCH`. `ASSET_PRICE_MARKET_TYPE_UNSPECIFIED` means a newer type: render the terms that are present |
| `assetPriceTerms.asset` | object | `assetClass` and `symbol`, e.g. `ASSET_CLASS_CRYPTO` / `btc` or `ASSET_CLASS_COMMODITY` / `gold`. Its canonical form (`crypto:btc`, `commodity:gold`, `commodity:silver`) is the `assets` parameter of the [Asset Prices API](https://docs.polymarket.us/api-reference/asset-prices/overview.md) |
| `assetPriceTerms.indexSymbol` | string | Reference index the market settles on, e.g. `BRTI`. Empty on gold and silver markets, which settle on Chainlink spot streams; see [Metals Schema](https://docs.polymarket.us/trader-guide/metals-schema.md) |
| `assetPriceTerms.horizon` | string | Cadence or window length, e.g. `15m`, `1h`, `1d`, `hourly`, `daily`, `weekly`, `monthly`. Display and grouping only |
| `assetPriceTerms.windowStart` | timestamp | Start of the measurement window. Set on UP\_DOWN and ONE\_TOUCH; `null` on ladders |
| `assetPriceTerms.windowEnd` | timestamp | End of the measurement window, i.e. the settlement instant. Use this rather than `endDate` for the settlement time |
| `assetPriceTerms.priceToBeat` | Amount | UP\_DOWN reference (open) price. `null` until stamped: a few seconds after `windowStart` on crypto, about a minute after it on gold and silver. While `null` and `windowStart` has passed, re-fetch at most every 2 seconds and stop after 60 seconds on crypto, or after 2 minutes on gold and silver, then back off |
| `assetPriceTerms.strike` | Amount | ABOVE\_BELOW threshold; ONE\_TOUCH target |
| `assetPriceTerms.rangeLower` / `rangeUpper` | Amount | RANGE bounds, inclusive. The open side of a tail is `null` |
| `assetPriceTerms.rangeKind` | string (enum) | `ASSET_PRICE_RANGE_KIND_INTERIOR`, `_BELOW` (bottom tail) or `_ABOVE` (top tail) |
| `assetPriceTerms.direction` | string (enum) | ONE\_TOUCH: `ASSET_PRICE_DIRECTION_UP` (reaches the target from below) or `_DOWN` (dips to it from above) |
| `assetPriceTerms.settlementPrice` | Amount | Reference price the market settled on. `null` until resolved, and stays `null` on a ONE\_TOUCH market that resolved No: no price was touched, so none is recorded. Do not wait on this field for completion; use the market `status` or `GET /v1/markets/{slug}/settlement` |
| `assetPriceTerms.priceDecimals` | integer | Decimal places every price of the market carries, e.g. `2` for BTC and gold, `3` for silver. Always read this field: it can differ between assets, and between market types of one asset, for example `4` for SOL Up/Down. Show prices at exactly this many places |
| `assetPriceTerms.assetName` | string | Display name of the asset, e.g. `Gold`, `Silver`. Empty when the asset has none (crypto): show the symbol |
| `assetPriceTerms.priceUnit` | string | Unit one price is quoted per, e.g. `oz` for gold and silver. Empty for crypto |
| `assetPriceTerms.nextWindowStart` | timestamp | Start of the next window of this market's length that the asset's trading hours allow. Equals `windowEnd` on crypto windows. On gold and silver it is later than `windowEnd` when trading closes after the window (the daily break, the weekend or a holiday): show a closed state until then. `null` when unknown |

Amounts are `{ "value": "81650.14", "currency": "USD" }` with the value as a decimal string exactly as stamped. On UP\_DOWN markets the long side (`Yes`) pays when the close value is at or above `priceToBeat`. Filter crypto markets with `categories=crypto`.

Gold and silver markets are in the `finance` category (`categories=finance`); select them with `assetPriceTerms.asset.assetClass = ASSET_CLASS_COMMODITY`. Their `priceToBeat` and `settlementPrice` are single Chainlink spot values at `windowStart` and `windowEnd`, not 60-second averages, and `priceToBeat` is usually stamped about a minute after `windowStart`. See [Metals Schema](https://docs.polymarket.us/trader-guide/metals-schema.md) and [Gold and Silver FAQs](https://docs.polymarket.us/faqs/metals-faqs.md).

## Filtering Markets

Query markets with various filters:

```bash theme={null}
GET /v1/markets?active=true&categories=sports&limit=50
```

### Pagination & Ordering

| Parameter | Type | Description |
| - | - | - |
| `limit` | integer | Maximum number of markets to return per page. Default varies by endpoint. Example: `50` |
| `offset` | integer | Number of markets to skip for pagination. Use with `limit` to page through results. Example: `100` to skip the first 100 |
| `orderBy` | string\[] | Fields to sort results by. Supports multiple fields for multi-level sorting. Example: `["volumeNum", "createdAt"]` |
| `orderDirection` | string | Sort direction for the `orderBy` fields. Values: `asc` (ascending) or `desc` (descending). Default: `desc` |

### Status Filters

| Parameter | Type | Description |
| - | - | - |
| `active` | boolean | Filter markets by active trading status. `true` returns only markets currently accepting orders, `false` returns inactive markets |
| `closed` | boolean | Filter markets by closed status. `true` returns only markets that have closed (resolved or expired), `false` returns open markets |
| `archived` | boolean | Filter markets by archived status. `true` returns only archived/hidden markets, `false` excludes archived markets from results |

### Category & Type Filters

| Parameter | Type | Description |
| - | - | - |
| `categories` | string\[] | Filter by market categories. Example: `["sports", "politics", "crypto"]` |
| `marketTypes` | string\[] | Filter by market format types. Example: `["binary", "scalar"]` |
| `sportsMarketTypes` | enum\[] | Filter by sports market type. Values: `SPORTS_MARKET_TYPE_MONEYLINE` (winner), `SPORTS_MARKET_TYPE_SPREAD` (point spread), `SPORTS_MARKET_TYPE_TOTAL` (over/under), `SPORTS_MARKET_TYPE_PROP` (player/game props) |
| `tagId` | integer | Filter markets associated with a specific tag ID. Returns markets that have this tag applied |
| `relatedTags` | boolean | When `true` and `tagId` is provided, also includes markets with tags related to the specified tag |
| `includeTag` | boolean | When `true`, includes full tag information in the response for each market |
| `cyom` | boolean | Filter "Create Your Own Market" submissions. `true` returns only user-submitted markets, `false` excludes them |

### ID Filters

| Parameter | Type | Description |
| - | - | - |
| `id` | integer\[] | Filter by specific market IDs. Returns only markets matching these numeric IDs. Example: `[123, 456, 789]` |
| `slug` | string\[] | Filter by market URL slugs. Returns only markets matching these slug identifiers. Example: `["will-team-a-win", "super-bowl-winner"]` |
| `questionIds` | string\[] | Filter by question IDs (UUIDs). Returns markets associated with these question identifiers |
| `gameId` | string | Filter by sports game ID from the data provider. Returns all markets associated with a specific sporting event |

### Volume & Liquidity Filters

| Parameter | Type | Description |
| - | - | - |
| `volumeNumMin` | number | Minimum total trading volume in USD. Only returns markets with `volumeNum` >= this value. Example: `1000.00` |
| `volumeNumMax` | number | Maximum total trading volume in USD. Only returns markets with `volumeNum` \<= this value. Example: `100000.00` |
| `liquidityNumMin` | number | Minimum available liquidity in USD. Only returns markets with `liquidityNum` >= this value. Example: `500.00` |
| `liquidityNumMax` | number | Maximum available liquidity in USD. Only returns markets with `liquidityNum` \<= this value. Example: `50000.00` |
| `rewardsMinSize` | number | Minimum order size eligible for liquidity rewards. Filters to markets where reward-eligible orders must be at least this size |

### Date Filters

All date parameters accept ISO 8601 format strings (e.g., `2025-01-20T00:00:00Z`).

| Parameter | Type | Description |
| - | - | - |
| `startDateMin` | string (ISO 8601) | Filter markets with a start date on or after this timestamp. Example: `2025-01-01T00:00:00Z` |
| `startDateMax` | string (ISO 8601) | Filter markets with a start date on or before this timestamp. Example: `2025-12-31T23:59:59Z` |
| `endDateMin` | string (ISO 8601) | Filter markets with an end/expiration date on or after this timestamp |
| `endDateMax` | string (ISO 8601) | Filter markets with an end/expiration date on or before this timestamp |

## Market Sides

Each market has sides representing the possible outcomes. Sides are returned nested on the market object as `marketSides` from:

* `GET /v1/markets`
* `GET /v1/market/id/{id}`
* `GET /v1/market/slug/{slug}`

There is no separate REST endpoint to fetch a side by ID or to list sides by market ID.

### Market Side Fields

| Field | Description |
| - | - |
| `id` | Market side ID |
| `marketSideType` | Type (ERC1155 or INSTRUMENT) |
| `identifier` | Market side identifier |
| `description` | Side description |
| `long` | Whether this is the long side |
| `participantId` | Associated participant ID |

> [!TIP]
>
> **Real-Time Market Data**
>
> For real-time price updates and order book data, use the [WebSocket Markets Stream](https://docs.polymarket.us/api-reference/websocket/markets.md) instead of polling the REST API.

## Market Book (Full)

Get real-time order book data and market statistics for a specific market:

```bash theme={null}
GET /v1/markets/{slug}/book
```

### Path Parameters

| Parameter | Type | Required | Description |
| - | - | - | - |
| `slug` | string | Yes | Market slug identifier |

### Response

```json theme={null}
{
  "marketData": {
    "marketSlug": "will-team-a-win",
    "bids": [
      { "px": { "value": "0.55", "currency": "USD" }, "qty": "1000" },
      { "px": { "value": "0.54", "currency": "USD" }, "qty": "500" }
    ],
    "offers": [
      { "px": { "value": "0.56", "currency": "USD" }, "qty": "750" },
      { "px": { "value": "0.57", "currency": "USD" }, "qty": "1200" }
    ],
    "state": "MARKET_STATE_OPEN",
    "stats": {
      "lastTradePx": { "value": "0.55", "currency": "USD" },
      "openPx": { "value": "0.50", "currency": "USD" },
      "highPx": { "value": "0.58", "currency": "USD" },
      "lowPx": { "value": "0.48", "currency": "USD" },
      "sharesTraded": "50000",
      "openInterest": "125000",
      "notionalTraded": { "value": "27500.00", "currency": "USD" }
    },
    "transactTime": "2025-01-20T12:30:45.123Z"
  }
}
```

### Market Data Fields

| Field | Type | Description |
| - | - | - |
| `marketSlug` | string | Market identifier |
| `bids` | array | Buy orders (highest price first) |
| `offers` | array | Sell orders (lowest price first) |
| `state` | string | Current market state |
| `stats` | object | Market statistics |
| `transactTime` | string | Timestamp of data |

### Book Entry

| Field | Type | Description |
| - | - | - |
| `px` | Amount | Price level |
| `qty` | string | Quantity available at this price. May contain decimals for partial-contract markets. |

### Market States

| State | Description |
| - | - |
| `MARKET_STATE_OPEN` | Market is open for trading |
| `MARKET_STATE_PREOPEN` | Market is in pre-open phase |
| `MARKET_STATE_SUSPENDED` | Trading temporarily suspended |
| `MARKET_STATE_HALTED` | Trading halted |
| `MARKET_STATE_EXPIRED` | Market has expired |
| `MARKET_STATE_TERMINATED` | Market terminated |
| `MARKET_STATE_MATCH_AND_CLOSE_AUCTION` | Market in closing auction |

### Market Stats

| Field | Type | Description |
| - | - | - |
| `openPx` | Amount | Opening price |
| `closePx` | Amount | Closing price |
| `highPx` | Amount | High price |
| `lowPx` | Amount | Low price |
| `lastTradePx` | Amount | Last trade price |
| `indicativeOpenPx` | Amount | Indicative opening price (pre-open) |
| `settlementPx` | Amount | Settlement price |
| `sharesTraded` | string | Total shares traded |
| `notionalTraded` | Amount | Total notional value traded |
| `lastTradeQty` | string | Last trade quantity |
| `openInterest` | string | Current open interest |
| `currentPx` | Amount | Current market price |

***

## Market BBO (Lightweight)

Get best bid/offer and basic market statistics in a lightweight format. Use this endpoint when you only need top-of-book prices without the full order book depth.

```bash theme={null}
GET /v1/markets/{slug}/bbo
```

### Path Parameters

| Parameter | Type | Required | Description |
| - | - | - | - |
| `slug` | string | Yes | Market slug identifier |

### Response

```json theme={null}
{
  "marketData": {
    "marketSlug": "will-team-a-win",
    "currentPx": { "value": "0.55", "currency": "USD" },
    "lastTradePx": { "value": "0.55", "currency": "USD" },
    "bestBid": { "value": "0.54", "currency": "USD" },
    "bestAsk": { "value": "0.56", "currency": "USD" },
    "bidDepth": 5,
    "askDepth": 4,
    "sharesTraded": "50000",
    "openInterest": "125000",
    "settlementPx": { "value": "0.00", "currency": "USD" }
  }
}
```

### Market Data Lite Fields

| Field | Type | Description |
| - | - | - |
| `marketSlug` | string | Market identifier |
| `currentPx` | Amount | Current market price |
| `lastTradePx` | Amount | Price of the most recent trade |
| `bestBid` | Amount | Best (highest) bid price |
| `bestAsk` | Amount | Best (lowest) ask price |
| `bidDepth` | integer | Number of price levels on the bid side |
| `askDepth` | integer | Number of price levels on the ask side |
| `sharesTraded` | string | Total shares traded |
| `openInterest` | string | Current open interest |
| `settlementPx` | Amount | Settlement price (if resolved) |

### When to Use BBO vs Book

| Endpoint | Use Case |
| - | - |
| `/v1/markets/{slug}/bbo` | Display current prices, check spreads, lightweight polling |
| `/v1/markets/{slug}/book` | View full order book depth, analyze liquidity at each level |

> [!TIP]
>
> **Real-Time Updates**
>
> For continuous order book updates, use the [WebSocket Markets Stream](https://docs.polymarket.us/api-reference/websocket/markets.md) instead of polling these endpoints.

## Settlement

After a market resolves, query the settlement price:

```bash theme={null}
GET /v1/markets/will-x-happen/settlement
```

Response:

```json theme={null}
{
  "slug": "will-x-happen",
  "settlement": 1.00
}
```

Settlement values are typically `0.00` (No) or `1.00` (Yes).


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.