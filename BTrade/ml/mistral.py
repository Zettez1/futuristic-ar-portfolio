import json
import re

import requests

from core.logger import get_logger


log = get_logger("ai")

SYSTEM_PROMPT = (
    "You are a strict trading decision engine. You may use ONLY the supplied "
    "TradingView Supply/Demand confluence data and Indicator 1 VAP/DOM data. "
    "Do not calculate or mention RSI, MACD, trend, news, funding, sentiment, "
    "or any other indicator. Return ONLY valid JSON."
)

ENTRY_PROMPT = (
    SYSTEM_PROMPT + " Decide whether the single candidate is tradable now. "
    "First build SHORT LOGICAL CHAIN over the DOM/VAP state exactly like this: "
    "assess which walls are largest and on which side, what delta/CVD/pressure "
    "imply about who is absorbing, then state how price most likely moves next. "
    "Return TIGHT JSON ONLY: {\"chain\":[...5-8 strings...],\"scenario\":\"break_out\"|\"break_down\"|"
    "\"reject\"|\"squeeze\"|\"range\",\"prob_entry_side_up\":0.0,"
    "\"action\":\"trade\"|\"hold\",\"symbol\":\"...\",\"side\":\"long\"|\"short\","
    "\"confidence\":0.0,\"reason\":\"short reason\"}. "
    "Trade only if price is near a zone supported by at least two timeframes and "
    "the VAP/DOM does not contradict it. Use the smc_path data as the route: if a "
    "trajectory exists and the candidate side matches its direction, treat the "
    "path legs (retracement -> reaction -> target) as the expected route; if the "
    "candidate side contradicts the smc_path trend, prefer hold. "
    "A candidate marked mirrored reuses the "
    "same side whose primary was rejected: strong one-sided VAP/DOM pressure "
    "confirms that wall is being broken, so pressure must agree with the "
    "candidate side (bearish pressure -> short, bullish pressure -> long) to trade it."
)

EXIT_PROMPT = (
    SYSTEM_PROMPT + " Decide whether an open position should be closed early. "
    "First build a SHORT LOGICAL CHAIN: assess walls/pressure/delta from the "
    "DOM/VAP and conclude whether the position's direction is still supported or "
    "a MATERIAL reversal is forming. "
    "Return TIGHT JSON ONLY: {\"chain\":[3-5 strings],\"action\":\"exit\"|\"hold\","
    "\"confidence\":0.0,\"reason\":\"short reason\"}. "
    "Hard stop-loss is handled outside you; there is no fixed take-profit. "
    "Use only Indicator 1 DOM/VAP. "
    "Be PATIENT with a position that is in profit: small pullbacks against the "
    "position are NORMAL. "
    "HOLD rules symmetric for both sides: for a LONG, hold while pressure/delta/"
    "walls are neutral-to-bullish OR price is still inside the entry demand zone "
    "(being below POC/VAL near the entry zone is NORMAL). "
    "For a SHORT, hold while pressure/delta/walls are neutral-to-bearish OR "
    "price is still inside the entry supply zone (being above POC/VAH near the "
    "entry zone is NORMAL). "
    "Use the smc_path route as guidance: while price is moving along the "
    "predicted trajectory legs toward the target, HOLD — the move is on plan "
    "even if the retracement leg briefly touches the demand/supply level. "
    "EXIT only on a MATERIAL reversal, confirmed by MULTIPLE signals together: "
    "pressure AND delta both flipping against the position side, walls forming "
    "against it, price breaking out of the entry zone in the wrong direction "
    "with momentum, and value-area behaviour rejecting the move. "
    "A single pullback or one slightly counter-directional bar is NOT enough. "
    "Do not exit just because price is below/above the POC or value area."
)


def _content_text(data: dict) -> str:
    choices = data.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content", "")
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content or "")
    return ""


def _parse_json(content: str, default: dict) -> dict:
    if not content:
        return dict(default)
    cleaned = content.strip().replace("```json", "").replace("```", "").strip()
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else dict(default)
    except (TypeError, ValueError):
        pass
    candidates = [m.group(0) for m in re.finditer(r"\{.*?\}", cleaned, re.DOTALL)]
    if "{" in cleaned and "}" in cleaned:
        candidates.append(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and any(key in value for key in ("action", "confidence", "chain", "scenario")):
            return value
    return dict(default)


def _normal_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _indicator_context(bundle) -> dict:
    raw = (bundle.raw if bundle else {}) or {}
    zones = raw.get("zones") or {}
    compact_zones = []
    for zone in (zones.get("confluences") or []):
        compact_zones.append({
            "type": zone.get("type"),
            "bottom": zone.get("bottom"),
            "top": zone.get("top"),
            "distance_atr": zone.get("distance_atr"),
            "near": zone.get("near"),
            "timeframes": zone.get("timeframes"),
            "timeframe_count": zone.get("timeframe_count"),
        })
    dom = raw.get("order_book") or raw.get("indicator1") or {}
    compact_dom = {
        key: dom.get(key)
        for key in (
            "source", "source_timeframe", "poc", "vah", "val", "buy_volume", "sell_volume", "buy_pct",
            "delta", "delta_ratio", "cvd", "dom_pressure", "volume_pressure",
            "close_pressure", "body_pressure", "value_area_position", "wall_bid_count",
            "wall_ask_count", "wall_bid_size", "wall_ask_size", "wall_direction",
            "best_ask", "best_bid", "mid_spread",
        )
    }
    compact_dom["asks"] = [
        {key: row.get(key) for key in ("price", "size", "wall", "active")}
        for row in (dom.get("asks") or [])
    ]
    compact_dom["bids"] = [
        {key: row.get(key) for key in ("price", "size", "wall", "active")}
        for row in (dom.get("bids") or [])
    ]
    smc = raw.get("smc") or {}
    compact_smc = {
        "trend": smc.get("trend"),
        "last_event": smc.get("last_event"),
        "bos_count": smc.get("bos_count"),
        "choch_count": smc.get("choch_count"),
        "last_hi": smc.get("last_hi"),
        "last_lo": smc.get("last_lo"),
        "equilibrium": smc.get("equilibrium"),
        "price_vs_eq": smc.get("price_vs_eq"),
        "fvg_bull": smc.get("fvg_bull"),
        "fvg_bear": smc.get("fvg_bear"),
        "bull_ob": smc.get("bull_ob"),
        "bear_ob": smc.get("bear_ob"),
        "targets": smc.get("targets") or {},
        "paths": smc.get("paths") or {},
    }
    return {
        "symbol": raw.get("symbol") or getattr(bundle, "symbol", ""),
        "price": raw.get("price"),
        "zones": compact_zones,
        "near_demand": zones.get("near_demand"),
        "near_supply": zones.get("near_supply"),
        "indicator1_dom": compact_dom,
        "smc_path": compact_smc,
        "indicator1_probability": raw.get("indicator1_probability") or {},
    }


class MistralAgent:
    """Compatibility wrapper for a single OpenAI-compatible Mistral endpoint."""

    def __init__(self, api_key: str, model: str = "mistral-large-latest", base: str = "https://api.mistral.ai/v1"):
        self.api_key = api_key
        self.model = model
        self.base = base

    def chat(self, user: str, system: str = SYSTEM_PROMPT, max_tokens: int = 300) -> str:
        if not self.api_key:
            return ""
        try:
            response = requests.post(
                f"{self.base}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                },
                timeout=30,
            )
            response.raise_for_status()
            return _content_text(response.json())
        except Exception:
            return ""


class LLMAgent:
    """Provider failover with a one-time health probe and strict decisions."""

    def __init__(self, providers: list):
        self.providers = [dict(provider) for provider in (providers or []) if provider.get("api_key")]
        self.active_provider = None
        self.status = {}
        self.last_error = ""

    @property
    def ready(self) -> bool:
        return self.active_provider is not None

    @property
    def active_name(self) -> str:
        return str((self.active_provider or {}).get("name") or "")

    def probe(self) -> dict:
        """Test every configured provider without ever logging its secret."""
        self.status = {}
        self.active_provider = None
        for provider in self.providers:
            name = provider.get("name", provider.get("type", "provider"))
            try:
                result = self._call_provider(
                    provider,
                    "Reply with the word READY.",
                    "Return a short health-check response.",
                    max_tokens=64,
                )
                if result:
                    self.status[name] = "ok"
                    if self.active_provider is None:
                        self.active_provider = provider
                else:
                    self.status[name] = "empty response"
            except Exception as exc:
                self.status[name] = type(exc).__name__
        return dict(self.status)

    def chat(self, user: str, system: str = SYSTEM_PROMPT, max_tokens: int = 1200) -> str:
        ordered = []
        if self.active_provider:
            ordered.append(self.active_provider)
        ordered.extend(provider for provider in self.providers if provider not in ordered)
        for provider in ordered:
            name = provider.get("name", provider.get("type", "provider"))
            try:
                result = self._call_provider(provider, user, system, max_tokens)
                if result:
                    self.active_provider = provider
                    self.status[name] = "ok"
                    return result
                self.status[name] = "empty response"
                log.error("AI provider %s returned empty response", name)
            except Exception as exc:
                self.status[name] = type(exc).__name__
                self.last_error = f"{name}: {type(exc).__name__}"
                detail = str(getattr(exc, "response", exc))
                log.error("AI provider %s failed: %s: %s", name, type(exc).__name__, detail[:400])
        self.active_provider = None
        return ""

    @staticmethod
    def _call_provider(provider: dict, user: str, system: str, max_tokens: int) -> str:
        provider_type = provider.get("type", "openai")
        if provider_type == "cohere":
            return LLMAgent._call_cohere(provider, user, system, max_tokens)
        return LLMAgent._call_openai_compat(provider, user, system, max_tokens)

    @staticmethod
    def _call_openai_compat(provider: dict, user: str, system: str, max_tokens: int) -> str:
        payload = {
            "model": provider.get("model", ""),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        json_mode = bool(provider.get("json_mode", True))
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = f"{provider['base'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider['api_key']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "Supply Demand DOM bot",
        }
        response = requests.post(url, headers=headers, json=payload,
                                 timeout=float(provider.get("timeout", 12.0)))
        if json_mode and response.status_code == 400 and "response_format" in payload:
            # провайдер не поддерживает JSON-режим — повторяем без него
            payload.pop("response_format")
            response = requests.post(url, headers=headers, json=payload,
                                     timeout=float(provider.get("timeout", 12.0)))
        if response.status_code in (401, 403, 404, 429, 500, 502, 503):
            response.raise_for_status()
        response.raise_for_status()
        return _content_text(response.json())

    @staticmethod
    def _call_cohere(provider: dict, user: str, system: str, max_tokens: int) -> str:
        payload = {
            "model": provider.get("model", "command-a-plus-05-2026"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max(4096, int(max_tokens)),
            "temperature": 0.1,
        }
        json_mode = bool(provider.get("json_mode", True))
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {provider['api_key']}", "Content-Type": "application/json"}
        response = requests.post("https://api.cohere.com/v2/chat", headers=headers, json=payload,
                                 timeout=float(provider.get("timeout", 12.0)))
        if json_mode and response.status_code == 400 and "response_format" in payload:
            payload.pop("response_format")
            response = requests.post("https://api.cohere.com/v2/chat", headers=headers, json=payload,
                                     timeout=float(provider.get("timeout", 12.0)))
        response.raise_for_status()
        content = response.json().get("message", {}).get("content") or []
        if isinstance(content, str):
            return content
        parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("text")]
        if not parts:
            parts = [part.get("thinking", "") for part in content if isinstance(part, dict) and part.get("thinking")]
        return "".join(parts)

    def choose_best(self, market_summary: str, candidates: list) -> dict:
        """Legacy-compatible chooser, now constrained to the two indicators."""
        user = f"MARKET DATA:\n{market_summary}\n\nCANDIDATE:\n{json.dumps(candidates, ensure_ascii=False)[:6000]}"
        content = self.chat(user, ENTRY_PROMPT)
        return _parse_json(content, {"action": "hold", "confidence": 0.0, "reason": "no AI decision"})

    def decide_entry(self, signal, bundle, neural_probability: float = 0.5) -> dict:
        context = _indicator_context(bundle)
        context["candidate"] = {
            "symbol": signal.symbol,
            "side": signal.side,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "zone_timeframes": signal.features.get("zone_timeframes", ""),
            "zone_timeframe_count": signal.features.get("zone_timeframe_count", 0),
            "neural_probability": neural_probability,
            "mirrored": bool(signal.features.get("mirrored", False)),
        }
        content = self.chat(json.dumps(context, ensure_ascii=False), ENTRY_PROMPT)
        result = _parse_json(content, {"action": "hold", "confidence": 0.0, "reason": "AI unavailable"})
        result["confidence"] = _normal_confidence(result.get("confidence"))
        result["action"] = "trade" if str(result.get("action", "")).lower() == "trade" else "hold"
        result.setdefault("symbol", signal.symbol)
        result["side"] = str(result.get("side") or signal.side).lower()
        chain = result.get("chain") or []
        if not isinstance(chain, list):
            chain = [str(chain)]
        result["chain"] = [str(item) for item in chain if str(item).strip()][:8]
        if result["chain"]:
            log.info(f"AI {signal.symbol} {signal.side} chain: "
                     f"{' -> '.join(result['chain'])} | scenario={result.get('scenario', '?')}")
        return result

    def decide_exit(self, position, bundle, r_multiple: float = 0.0) -> dict:
        full_context = _indicator_context(bundle)
        raw = bundle.raw or {}
        dom = raw.get("order_book") or {}
        entry_zone = None
        if position.side == "long":
            for zone in (raw.get("zones") or {}).get("confluences") or ():
                if zone.get("type") == "demand" and float(zone.get("distance_atr") or 0.0) <= 1.0:
                    entry_zone = {
                        "type": "demand", "bottom": zone.get("bottom"), "top": zone.get("top"),
                        "timeframes": zone.get("timeframes"), "timeframe_count": zone.get("timeframe_count"),
                        "distance_atr": zone.get("distance_atr"),
                    }
                    break
        else:
            for zone in (raw.get("zones") or {}).get("confluences") or ():
                if zone.get("type") == "supply" and float(zone.get("distance_atr") or 0.0) <= 1.0:
                    entry_zone = {
                        "type": "supply", "bottom": zone.get("bottom"), "top": zone.get("top"),
                        "timeframes": zone.get("timeframes"), "timeframe_count": zone.get("timeframe_count"),
                        "distance_atr": zone.get("distance_atr"),
                    }
                    break
        context = {
            "symbol": full_context.get("symbol"),
            "price": full_context.get("price"),
            "indicator1_dom": full_context.get("indicator1_dom"),
            "position_zone": entry_zone,
        }
        context["position"] = {
            "symbol": position.symbol,
            "side": position.side,
            "entry": position.entry,
            "stop_loss": position.stop_loss,
            "r_multiple": r_multiple,
            "zone_of_entry": entry_zone,
        }
        content = self.chat(json.dumps(context, ensure_ascii=False), EXIT_PROMPT)
        result = _parse_json(content, {"action": "hold", "confidence": 0.0, "reason": "AI unavailable"})
        result["confidence"] = _normal_confidence(result.get("confidence"))
        result["action"] = "exit" if str(result.get("action", "")).lower() == "exit" else "hold"
        chain = result.get("chain") or []
        if not isinstance(chain, list):
            chain = [str(chain)]
        result["chain"] = [str(item) for item in chain if str(item).strip()][:8]
        return result


def build_summary(symbols_state: dict) -> str:
    lines = []
    for symbol, state in symbols_state.items():
        bundle = state.get("bundle")
        if not bundle:
            continue
        raw = bundle.raw or {}
        dom = raw.get("order_book") or {}
        demand = raw.get("near_demand") or {}
        supply = raw.get("near_supply") or {}
        pressure = float(dom.get("dom_pressure") or 0.0)
        buy_pct = float(dom.get("buy_pct") or 50.0)
        lines.append(
            f"{symbol}: price={raw.get('price')} "
            f"demand={demand.get('timeframes', '-')}/{demand.get('distance_atr', '-') } "
            f"supply={supply.get('timeframes', '-')}/{supply.get('distance_atr', '-') } "
            f"dom_pressure={pressure:+.3f} "
            f"buy_pct={buy_pct:.1f} walls={dom.get('wall_bid_count', 0)}/{dom.get('wall_ask_count', 0)}"
        )
    return "\n".join(lines) if lines else "no data"
