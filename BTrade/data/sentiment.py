import requests

TIMEOUT = 10


def fear_greed_index() -> float:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()["data"][0]
        return float(data["value"])
    except Exception:
        return 50.0


def funding_summary(client, symbols: list) -> dict:
    out = {}
    for s in symbols:
        fr = client.fetch_funding_rate(s)
        if fr is not None:
            out[s] = float(fr)
    return out


def long_short_ratio(client, symbol: str):
    try:
        r = requests.get(
            f"https://contract.mexc.com/api/v1/contract/global/{symbol.replace('/', '_')}",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def sentiment_snapshot(client, symbols: list) -> dict:
    fg = fear_greed_index()
    funding = funding_summary(client, symbols)
    avg_funding = sum(funding.values()) / len(funding) if funding else 0.0
    return {
        "fear_greed": fg,
        "fear_greed_label": "fear" if fg < 45 else ("greed" if fg > 55 else "neutral"),
        "funding": funding,
        "avg_funding": avg_funding,
    }
