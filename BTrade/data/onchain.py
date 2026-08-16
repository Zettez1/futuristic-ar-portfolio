import requests

TIMEOUT = 10


def coingecko_market_data(symbols: list) -> dict:
    try:
        ids = {
            "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
            "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2",
            "LINK": "chainlink", "DOT": "polkadot", "MATIC": "matic-network", "LTC": "litecoin",
            "TON": "the-open-network", "SUI": "sui", "NEAR": "near",
        }
        wanted = [ids.get(s.split("/")[0], "") for s in symbols]
        wanted = [w for w in wanted if w]
        if not wanted:
            return {}
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": ",".join(wanted), "order": "market_cap_desc"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return {row["symbol"].upper(): row for row in r.json()}
    except Exception:
        return {}


def onchain_snapshot(symbols: list) -> dict:
    md = coingecko_market_data(symbols)
    out = {}
    for s in symbols:
        base = s.split("/")[0]
        row = md.get(base)
        if not row:
            continue
        out[base] = {
            "market_cap": row.get("market_cap"),
            "market_cap_rank": row.get("market_cap_rank"),
            "total_volume": row.get("total_volume"),
            "price_change_24h": row.get("price_change_percentage_24h"),
            "ath": row.get("ath"),
            "ath_change_24h": row.get("ath_change_percentage"),
            "circulating_supply": row.get("circulating_supply"),
        }
    return out
