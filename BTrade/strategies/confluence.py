"""Candidate signals for the two-indicator trading model."""

from core.models import Signal
from strategies.base import Strategy


class SupplyDemandConfluenceStrategy(Strategy):
    name = "supply_demand_confluence"
    horizon = "1m-1h"

    def __init__(self, mirror_enabled: bool = True, mirror_dom_threshold: float = 0.5,
                 mirror_near_atr: float = 1.0, probability_threshold: float = 55.0):
        self.mirror_enabled = bool(mirror_enabled)
        self.mirror_dom_threshold = float(mirror_dom_threshold)
        self.mirror_near_atr = float(mirror_near_atr)
        self.probability_threshold = float(probability_threshold)

    def _candidate(self, snap, bundle, zone, side, mirrored: bool = False):
        price = float(snap.last_price or bundle.raw.get("price") or 0.0)
        if price <= 0 or not zone or zone.get("timeframe_count", 0) < 2:
            return None
        prob = bundle.raw.get("indicator1_probability") or {}
        if not prob.get("ready"):
            return None
        bull_prob = int(prob.get("bull_prob") or 50)
        bear_prob = int(prob.get("bear_prob") or 50)
        thr = self.probability_threshold
        if side == "long" and bull_prob < thr:
            return None
        if side == "short" and bear_prob < thr:
            return None
        smc = bundle.raw.get("smc") or {}
        if smc.get("ready"):
            ev_side = (smc.get("last_event") or {}).get("side")
            eq = smc.get("equilibrium")
            if ev_side is not None or eq is not None:
                if side == "long":
                    with_smc = bool(ev_side == "bull") or (eq is not None and price > float(eq))
                else:
                    with_smc = bool(ev_side == "bear") or (eq is not None and price < float(eq))
                if not with_smc:
                    return None
        atr = float(zone.get("atr") or 0.0)
        width = max(float(zone.get("top", 0.0)) - float(zone.get("bottom", 0.0)), 0.0)
        buffer = max(atr * 0.15, width * 0.10, price * 1e-5)
        if side == "long":
            stop_loss = float(zone["bottom"]) - buffer
            risk = price - stop_loss
        else:
            stop_loss = float(zone["top"]) + buffer
            risk = stop_loss - price
        if risk <= 0:
            return None
        count = int(zone.get("timeframe_count") or 0)
        confidence = min(0.95, 0.60 + max(0, count - 2) * 0.08)
        timeframes = ",".join(zone.get("timeframes") or ())
        reason = (
            f"{side} у {zone.get('type')} confluence: {timeframes} "
            f"({count} TF), distance {float(zone.get('distance_atr') or 0.0):.2f} ATR; "
            "AI receives Indicator 1 VAP/DOM state"
        )
        if mirrored:
            reason = (
                f"{side} у {zone.get('type')} - стена, давление DOM ломает её "
                f"({timeframes}, {count} TF, distance {float(zone.get('distance_atr') or 0.0):.2f} ATR); "
                "AI получает Indicator 1 VAP/DOM state"
            )
        extra = {
            "zone_type": zone.get("type", ""),
            "zone_timeframes": timeframes,
            "zone_timeframe_count": count,
            "zone_bottom": float(zone["bottom"]),
            "zone_top": float(zone["top"]),
            "zone_distance_atr": float(zone.get("distance_atr") or 0.0),
            "mirrored": mirrored,
            "probability_bull": bull_prob,
            "probability_bear": bear_prob,
            "probability_reasons": prob.get("reasons") or [],
        }
        return self._sig(snap, bundle, side, price, stop_loss, None, confidence,
                         self.horizon, reason, extra)

    def signals(self, snap, bundle, engine):
        if not bundle or not bundle.raw:
            return []
        dom = bundle.raw.get("order_book") or {}
        if not dom.get("ready"):
            return []
        candidates = []
        demand = bundle.raw.get("near_demand")
        supply = bundle.raw.get("near_supply")
        long_signal = self._candidate(snap, bundle, demand, "long")
        short_signal = self._candidate(snap, bundle, supply, "short")
        if long_signal:
            candidates.append(long_signal)
        if short_signal:
            candidates.append(short_signal)
        if self.mirror_enabled:
            mirror = self._mirror_candidate(snap, bundle, dom, long_signal, short_signal)
            if mirror:
                candidates.append(mirror)
        return candidates

    def _mirror_candidate(self, snap, bundle, dom, long_signal, short_signal):
        """Зеркальный вход: давление DOM подтверждает пробой стены.

        - У demand-зоны кандидат LONG, а Indicator 1 показывает сильное медвежье
          давление (dom_pressure <= -порог) — медведи продавливают demand вниз
          -> пробуем SHORT (пробой вниз) от той же самой стены.
        - Симметрично: кандидат SHORT у supply-зоны, а давление бычье (>= порог)
          -> быки пробивают supply наверх -> пробуем LONG от той же стены.
        """
        pressure = float(dom.get("dom_pressure") or 0.0)
        zones = bundle.raw.get("zones") or {}
        zone_ref = None
        if short_signal and pressure >= self.mirror_dom_threshold:
            zone_ref = bundle.raw.get("near_supply") or self._nearby_supply(zones, self.mirror_near_atr)
            if not zone_ref:
                return None
            mirror = self._candidate(snap, bundle, zone_ref, "long", mirrored=True)
        elif long_signal and pressure <= -self.mirror_dom_threshold:
            zone_ref = bundle.raw.get("near_demand") or self._nearby_demand(zones, self.mirror_near_atr)
            if not zone_ref:
                return None
            mirror = self._candidate(snap, bundle, zone_ref, "short", mirrored=True)
        else:
            return None
        if mirror is None:
            return None
        if mirror.side == "long" and long_signal:
            return None
        if mirror.side == "short" and short_signal:
            return None
        return mirror

    @staticmethod
    def _nearby_supply(zones: dict, near_atr: float):
        candidate = None
        for zone in zones.get("confluences") or ():
            if zone.get("type") != "supply":
                continue
            if float(zone.get("distance_atr") or 0.0) <= float(near_atr):
                if candidate is None or float(zone.get("distance_atr") or 0.0) < float(
                        candidate.get("distance_atr") or 0.0):
                    candidate = zone
        return candidate

    @staticmethod
    def _nearby_demand(zones: dict, near_atr: float):
        candidate = None
        for zone in zones.get("confluences") or ():
            if zone.get("type") != "demand":
                continue
            if float(zone.get("distance_atr") or 0.0) <= float(near_atr):
                if candidate is None or float(zone.get("distance_atr") or 0.0) < float(
                        candidate.get("distance_atr") or 0.0):
                    candidate = zone
        return candidate