"""
StreetDyno 2.0 - Fuel Economy & 2-Stroke Premix Manager
Lightweight, power-cut-safe (atomic JSON) manager for:
- European fuel stoichiometry tracking & chemical tank blending
- 2-stroke oil tracking (pre-portioned 110ml Motul 710 cans + balance account)
- Odometer & GPS consumption fusion (L/100km)
- Range estimation & live HUD telemetry
"""

from __future__ import annotations
import os
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

try:
    from config import (
        BASE_DIR,
        LOG_DIR,
        FUEL_STATE_FILE,
        USER_SETUP_FILE,
        TANK_CAPACITY_L,
        TANK_RESERVE_L,
        DEFAULT_OIL_DOSE_ML,
        TARGET_OIL_RATIO_PCT,
        MIN_SAFE_OIL_RATIO_PCT,
        MAX_SAFE_OIL_RATIO_PCT,
        EUROPEAN_FUEL_TYPES,
        FUEL_STOICHIOMETRY,
        load_carb_setup,
        save_carb_setup
    )
except ImportError:
    from src.config import (
        BASE_DIR,
        LOG_DIR,
        FUEL_STATE_FILE,
        USER_SETUP_FILE,
        TANK_CAPACITY_L,
        TANK_RESERVE_L,
        DEFAULT_OIL_DOSE_ML,
        TARGET_OIL_RATIO_PCT,
        MIN_SAFE_OIL_RATIO_PCT,
        MAX_SAFE_OIL_RATIO_PCT,
        EUROPEAN_FUEL_TYPES,
        FUEL_STOICHIOMETRY,
        load_carb_setup,
        save_carb_setup
    )


def _get_ratio_string(pct: float) -> str:
    """Converts a percentage (e.g. 2.15%) to standard ratio string (e.g. '1:47')."""
    if pct <= 0.001:
        return "Pur (0%)"
    ratio_denom = round(100.0 / pct)
    return f"1:{ratio_denom}"


def _get_oil_status(pct: float) -> Tuple[str, str, str]:
    """
    Returns (status_key, badge_css, label):
    - DANGER_LEAN: < 1.90% (> 1:53) -> danger red (Klemmergefahr!)
    - SLIGHTLY_LEAN: 1.90% - 2.05% (1:53 bis 1:49) -> warning orange
    - IDEAL: 2.05% - 2.35% (1:49 bis 1:43) -> green (optimale Schmierung)
    - SLIGHTLY_RICH: 2.35% - 2.60% (1:43 bis 1:38) -> blue (gut für Passfahrten)
    - TOO_RICH: > 2.60% (< 1:38) -> warning purple (Kerzenverrußung)
    """
    if pct < MIN_SAFE_OIL_RATIO_PCT:
        return "DANGER_LEAN", "danger", "🔴 ZU MAGER (Klemmergefahr!)"
    elif pct < 2.05:
        return "SLIGHTLY_LEAN", "warning", "🟡 Leicht mager (nachdosieren)"
    elif pct <= 2.35:
        return "IDEAL", "success", "🟢 IDEAL (Optimale Schmierung)"
    elif pct <= MAX_SAFE_OIL_RATIO_PCT:
        return "SLIGHTLY_RICH", "info", "🔵 Leicht fett (Sicher für Volllast)"
    else:
        return "TOO_RICH", "warning", "🟣 ZU FETT (Verrußungsgefahr)"


class FuelManager:
    """Manages persistent fuel tank state, oil blend balance, and refuel events."""

    def __init__(self, state_file: str = FUEL_STATE_FILE):
        self.state_file = state_file
        self.state: Dict[str, Any] = self.load_state()

    def _default_state(self) -> Dict[str, Any]:
        """Provides default initial state if no file exists yet."""
        target_oil_ml = TANK_CAPACITY_L * (TARGET_OIL_RATIO_PCT * 10.0)
        return {
            "tank": {
                "fuel_volume_l": TANK_CAPACITY_L,
                "oil_volume_ml": round(target_oil_ml, 1),
                "oil_ratio_pct": TARGET_OIL_RATIO_PCT,
                "oil_ratio_str": _get_ratio_string(TARGET_OIL_RATIO_PCT),
                "oil_balance_ml": 0.0,
                "fuel_type": "Super_E5",
                "stoich_afr": 14.30,
                "ethanol_pct": 5.0,
                "last_odometer_km": 0.0,
                "is_calibrated": False,
                "last_refuel_time": None
            },
            "stats": {
                "total_liters_fueled": 0.0,
                "total_km_tracked": 0.0,
                "avg_consumption_l_100km": 4.10,
                "last_trip_consumption_l_100km": 4.10
            },
            "history": []
        }

    def load_state(self) -> Dict[str, Any]:
        """Loads state from JSON. Resilient to corruption or missing file."""
        if not os.path.exists(self.state_file):
            state = self._default_state()
            self.save_state(state)
            return state
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "tank" not in data:
                state = self._default_state()
                self.save_state(state)
                return state
            return data
        except Exception:
            state = self._default_state()
            self.save_state(state)
            return state

    def save_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        """Atomic write to prevent file corruption upon power loss / ignition cut."""
        if state is not None:
            self.state = state
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
        tmp_file = f"{self.state_file}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_file, self.state_file)
        except Exception as e:
            if os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except Exception:
                    pass

    def get_refuel_recommendation(
        self,
        liters_to_add: float,
        is_full_tank: bool = True
    ) -> Dict[str, Any]:
        """
        Calculates recommended Motul 710 dosage (110ml cans) and projected
        mixture before the user actually commits the refuel.
        Correctly accounts for residual fuel/oil in tank.
        """
        liters_to_add = max(0.0, float(liters_to_add))
        if liters_to_add <= 0.0:
            return {
                "recommended_cans": 1,
                "recommended_ml": DEFAULT_OIL_DOSE_ML,
                "projected_ratio_str": self.state["tank"].get("oil_ratio_str", "1:47"),
                "projected_ratio_pct": self.state["tank"].get("oil_ratio_pct", TARGET_OIL_RATIO_PCT),
                "status": "IDEAL",
                "badge": "success",
                "status_label": "🟢 IDEAL",
                "advice": "Tanke mindestens 1 Liter, um eine Dosierempfehlung zu erhalten.",
                "projected_balance_ml": float(self.state["tank"].get("oil_balance_ml", 0.0))
            }

        tank = self.state["tank"]
        v_current = max(0.0, float(tank.get("fuel_volume_l", 0.0)))
        current_oil_ratio_pct = float(tank.get("oil_ratio_pct", TARGET_OIL_RATIO_PCT))
        balance_ml = float(tank.get("oil_balance_ml", 0.0))

        # 1. Residual fuel and oil in tank before refueling
        if is_full_tank:
            v_residual = max(0.0, TANK_CAPACITY_L - liters_to_add)
            o_residual = v_residual * (current_oil_ratio_pct * 10.0)
            v_projected = TANK_CAPACITY_L
        else:
            v_residual = v_current
            o_residual = float(tank.get("oil_volume_ml", 0.0))
            v_projected = min(TANK_CAPACITY_L, v_residual + liters_to_add)

        # 2. Target oil needed for the added fuel
        target_oil_for_added = liters_to_add * (TARGET_OIL_RATIO_PCT * 10.0)
        net_required_ml = target_oil_for_added - balance_ml

        # 3. Determine recommended cans based on target and safety
        # Candidate 0 cans:
        ratio_with_0 = (o_residual / (v_projected * 1000.0)) * 100.0 if v_projected > 0 else 0.0
        # Candidate 1 can:
        ratio_with_1 = ((o_residual + DEFAULT_OIL_DOSE_ML) / (v_projected * 1000.0)) * 100.0 if v_projected > 0 else 0.0

        if net_required_ml < 55.0 and ratio_with_0 >= MIN_SAFE_OIL_RATIO_PCT:
            recommended_cans = 0
        elif ratio_with_1 < MIN_SAFE_OIL_RATIO_PCT:
            # 1 can would leave the tank dangerously lean (< 1.90% / 1:53)
            recommended_cans = 2
        elif net_required_ml <= 165.0:
            recommended_cans = 1
        else:
            recommended_cans = 2

        recommended_ml = recommended_cans * DEFAULT_OIL_DOSE_ML
        o_projected = o_residual + recommended_ml
        ratio_pct = (o_projected / (v_projected * 1000.0)) * 100.0 if v_projected > 0 else TARGET_OIL_RATIO_PCT
        ratio_pct = max(0.5, min(5.0, ratio_pct))
        ratio_str = _get_ratio_string(ratio_pct)
        status_key, badge, label = _get_oil_status(ratio_pct)

        new_balance = balance_ml + (recommended_ml - target_oil_for_added)

        if recommended_cans == 1:
            advice = (
                f"💡 Empfehlung: 1 Dose Motul 710 ({DEFAULT_OIL_DOSE_ML:.0f} ml). "
                f"Ergibt im Tank {ratio_str} ({ratio_pct:.2f}%). Saldo: {new_balance:+.0f} ml."
            )
        elif recommended_cans == 0:
            advice = (
                f"💡 Empfehlung: Diesmal KEIN Öl zugeben (starker Öl-Überschuss im Tank). "
                f"Ergibt im Tank {ratio_str} ({ratio_pct:.2f}%). Saldo: {new_balance:+.0f} ml."
            )
        else:
            advice = (
                f"⚠️ Empfehlung: {recommended_cans} Dosen ({recommended_ml:.0f} ml) zugeben "
                f"(große Tankmenge). Ergibt {ratio_str} ({ratio_pct:.2f}%). Saldo: {new_balance:+.0f} ml."
            )

        # Projections for 1 and 2 cans for quick button feedback
        ratio_1_can = ((o_residual + DEFAULT_OIL_DOSE_ML) / (v_projected * 1000.0)) * 100.0 if v_projected > 0 else 0.0
        ratio_2_cans = ((o_residual + 2 * DEFAULT_OIL_DOSE_ML) / (v_projected * 1000.0)) * 100.0 if v_projected > 0 else 0.0

        return {
            "recommended_cans": recommended_cans,
            "recommended_ml": recommended_ml,
            "projected_ratio_str": ratio_str,
            "projected_ratio_pct": round(ratio_pct, 2),
            "status": status_key,
            "badge": badge,
            "status_label": label,
            "advice": advice,
            "projected_balance_ml": round(new_balance, 1),
            "options": {
                "cans_1_ratio": _get_ratio_string(ratio_1_can),
                "cans_1_pct": round(ratio_1_can, 2),
                "cans_2_ratio": _get_ratio_string(ratio_2_cans),
                "cans_2_pct": round(ratio_2_cans, 2),
            }
        }

    def record_refuel(
        self,
        odometer_km: float,
        liters_added: float,
        oil_cans: int = 1,
        manual_oil_ml: Optional[float] = None,
        fuel_type: str = "Super_E5",
        is_full_tank: bool = True
    ) -> Dict[str, Any]:
        """
        Records a completed refueling stop:
        - Calculates fuel consumption from odometer delta
        - Computes blended chemical stoichiometry in tank
        - Updates 2-stroke oil balance and tank ratio
        - Persists to state and synchronizes with user_setup.json
        """
        liters_added = max(0.1, float(liters_added))
        odometer_km = max(0.0, float(odometer_km))

        tank = self.state["tank"]
        v_current = max(0.0, float(tank.get("fuel_volume_l", 0.0)))
        current_oil_ratio_pct = float(tank.get("oil_ratio_pct", TARGET_OIL_RATIO_PCT))
        balance_ml = float(tank.get("oil_balance_ml", 0.0))
        prev_odo = float(tank.get("last_odometer_km", 0.0))
        prev_stoich = float(tank.get("stoich_afr", 14.30))

        # 1. Determine Oil Added
        if manual_oil_ml is not None and manual_oil_ml > 0:
            oil_added_ml = float(manual_oil_ml)
        else:
            oil_added_ml = float(oil_cans) * DEFAULT_OIL_DOSE_ML

        # 2. Distance and Fuel Consumption
        km_delta = (odometer_km - prev_odo) if (prev_odo > 0 and odometer_km > prev_odo) else 0.0
        if km_delta >= 5.0:
            consumption_l_100km = (liters_added / km_delta) * 100.0
            # Sanity clamp for display
            consumption_l_100km = max(1.5, min(12.0, consumption_l_100km))
        else:
            consumption_l_100km = float(self.state["stats"].get("avg_consumption_l_100km", 4.10))

        # 3. Residual Fuel and Oil Calculation
        if is_full_tank:
            v_new = TANK_CAPACITY_L
            v_residual = max(0.0, TANK_CAPACITY_L - liters_added)
            o_residual = v_residual * (current_oil_ratio_pct * 10.0)
        else:
            v_residual = v_current
            o_residual = float(tank.get("oil_volume_ml", 0.0))
            v_new = min(TANK_CAPACITY_L, v_residual + liters_added)

        fuel_meta = EUROPEAN_FUEL_TYPES.get(fuel_type, EUROPEAN_FUEL_TYPES["Super_E5"])
        new_stoich_target = float(fuel_meta["stoich_afr"])
        new_ethanol_target = float(fuel_meta["ethanol_pct"])

        if v_new > 0:
            blended_stoich = ((v_residual * prev_stoich) + (liters_added * new_stoich_target)) / v_new
            blended_ethanol = ((v_residual * float(tank.get("ethanol_pct", 5.0))) + (liters_added * new_ethanol_target)) / v_new
        else:
            blended_stoich = new_stoich_target
            blended_ethanol = new_ethanol_target

        # 4. Oil Ratio & Balance Calculation
        o_new = o_residual + oil_added_ml
        oil_ratio_pct = (o_new / (v_new * 1000.0)) * 100.0 if v_new > 0 else TARGET_OIL_RATIO_PCT
        oil_ratio_pct = max(0.5, min(5.0, oil_ratio_pct))
        oil_ratio_str = _get_ratio_string(oil_ratio_pct)

        target_oil_expected = liters_added * (TARGET_OIL_RATIO_PCT * 10.0)
        new_balance = balance_ml + (oil_added_ml - target_oil_expected)

        # 5. History Entry
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history_entry = {
            "date": now_iso,
            "odometer_km": round(odometer_km, 1),
            "liters_added": round(liters_added, 2),
            "oil_cans": oil_cans,
            "oil_ml": round(oil_added_ml, 1),
            "fuel_type": fuel_type,
            "fuel_label": fuel_meta["label"],
            "km_delta": round(km_delta, 1) if km_delta >= 5.0 else None,
            "consumption_l_100km": round(consumption_l_100km, 2) if km_delta >= 5.0 else None,
            "oil_ratio_after": oil_ratio_str,
            "oil_ratio_pct": round(oil_ratio_pct, 2)
        }

        # 6. Update State
        self.state["tank"] = {
            "fuel_volume_l": round(v_new, 2),
            "oil_volume_ml": round(o_new, 1),
            "oil_ratio_pct": round(oil_ratio_pct, 2),
            "oil_ratio_str": oil_ratio_str,
            "oil_balance_ml": round(new_balance, 1),
            "fuel_type": fuel_type,
            "stoich_afr": round(blended_stoich, 2),
            "ethanol_pct": round(blended_ethanol, 1),
            "last_odometer_km": round(odometer_km, 1),
            "is_calibrated": True,
            "last_refuel_time": now_iso
        }

        # Update lifetime stats
        stats = self.state.setdefault("stats", {})
        total_l = float(stats.get("total_liters_fueled", 0.0)) + liters_added
        total_km = float(stats.get("total_km_tracked", 0.0)) + km_delta
        stats["total_liters_fueled"] = round(total_l, 1)
        stats["total_km_tracked"] = round(total_km, 1)
        if km_delta >= 5.0:
            stats["last_trip_consumption_l_100km"] = round(consumption_l_100km, 2)
            # Weighted EMA of avg consumption
            old_avg = float(stats.get("avg_consumption_l_100km", 4.10))
            stats["avg_consumption_l_100km"] = round((old_avg * 0.7) + (consumption_l_100km * 0.3), 2)

        history = self.state.setdefault("history", [])
        history.insert(0, history_entry)
        self.state["history"] = history[:50]  # keep last 50 entries

        self.save_state()

        # 7. Synchronize fuel type into user_setup.json for jetting_advisor
        try:
            carb = load_carb_setup()
            carb["fuel_type"] = fuel_type
            carb["fuel_stoich_afr"] = round(blended_stoich, 2)
            save_carb_setup(carb)
        except Exception:
            pass

        return {
            "success": True,
            "tank": self.state["tank"],
            "entry": history_entry,
            "stats": self.state["stats"]
        }

    def update_burn(self, delta_km: float) -> None:
        """
        Called periodically or at trip close to deduct fuel & oil burned.
        Maintains proportional mixture in remaining fuel.
        """
        if delta_km <= 0.0:
            return
        tank = self.state["tank"]
        v_current = float(tank.get("fuel_volume_l", TANK_CAPACITY_L))
        avg_cons = float(self.state["stats"].get("avg_consumption_l_100km", 4.10))
        burned_l = (delta_km * avg_cons) / 100.0

        v_new = max(0.0, v_current - burned_l)
        o_current = float(tank.get("oil_volume_ml", 0.0))
        o_new = (o_current * (v_new / v_current)) if v_current > 0 else 0.0

        tank["fuel_volume_l"] = round(v_new, 2)
        tank["oil_volume_ml"] = round(o_new, 1)
        self.save_state()

    def get_hud_summary(self, cumulative_gps_km: float = 0.0) -> Dict[str, Any]:
        """Provides all metrics needed for the HUD header, card, and modal."""
        tank = self.state["tank"]
        stats = self.state["stats"]
        v_current = float(tank.get("fuel_volume_l", TANK_CAPACITY_L))
        avg_cons = float(stats.get("avg_consumption_l_100km", 4.10))

        # Range estimation
        est_range_km = (v_current / avg_cons * 100.0) if avg_cons > 0 else 0.0
        reserve_range_km = (max(0.0, v_current - TANK_RESERVE_L) / avg_cons * 100.0) if avg_cons > 0 else 0.0

        # Suggested odometer (pre-fill)
        last_odo = float(tank.get("last_odometer_km", 0.0))
        suggested_odo = round(last_odo + cumulative_gps_km, 1) if last_odo > 0 else 0.0

        ratio_pct = float(tank.get("oil_ratio_pct", TARGET_OIL_RATIO_PCT))
        status_key, badge, label = _get_oil_status(ratio_pct)

        fuel_type = tank.get("fuel_type", "Super_E5")
        fuel_meta = EUROPEAN_FUEL_TYPES.get(fuel_type, EUROPEAN_FUEL_TYPES["Super_E5"])

        return {
            "fuel_volume_l": round(v_current, 1),
            "fuel_pct": round((v_current / TANK_CAPACITY_L) * 100.0),
            "tank_capacity_l": TANK_CAPACITY_L,
            "est_range_km": round(est_range_km),
            "reserve_range_km": round(reserve_range_km),
            "is_on_reserve": v_current <= TANK_RESERVE_L,
            "oil_ratio_str": tank.get("oil_ratio_str", "1:47"),
            "oil_ratio_pct": ratio_pct,
            "oil_status": status_key,
            "oil_badge": badge,
            "oil_label": label,
            "oil_balance_ml": float(tank.get("oil_balance_ml", 0.0)),
            "fuel_type": fuel_type,
            "fuel_label": fuel_meta["label"],
            "stoich_afr": float(tank.get("stoich_afr", 14.30)),
            "avg_consumption_l_100km": avg_cons,
            "last_trip_consumption_l_100km": stats.get("last_trip_consumption_l_100km", avg_cons),
            "last_odometer_km": last_odo,
            "suggested_odometer_km": suggested_odo,
            "is_calibrated": tank.get("is_calibrated", False),
            "available_fuels": [
                {"key": k, "label": v["label"], "desc": v["description"], "stoich": v["stoich_afr"]}
                for k, v in EUROPEAN_FUEL_TYPES.items()
            ],
            "recent_refuels": self.state.get("history", [])[:5]
        }
