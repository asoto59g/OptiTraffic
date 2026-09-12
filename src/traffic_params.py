"""Urban traffic physical parameters for OptiTraffic demand / SUMO."""

from __future__ import annotations

# Municipal urban limit (Liberia / city core MVP).
CITY_MAX_SPEED_KMH = 40.0
CITY_MAX_SPEED_MS = CITY_MAX_SPEED_KMH / 3.6  # ≈ 11.11 m/s

# Average passenger car footprint on street.
VEH_LENGTH_M = 5.0
MIN_GAP_M = 2.5
EFFECTIVE_VEH_M = VEH_LENGTH_M + MIN_GAP_M  # space per vehicle in queue

# Car-following / desire (SUMO Krauss-like defaults tuned for urban).
VEH_ACCEL = 2.0
VEH_DECEL = 4.5
VEH_SIGMA = 0.5
VEH_TAU = 1.2

# Congested if mean speed below half of city max (~20 km/h).
CONGESTION_SPEED_MS = CITY_MAX_SPEED_MS * 0.5


def jam_density_veh_per_km() -> float:
    """Maximum packing density (veh/km) with length + gap."""
    return 1000.0 / EFFECTIVE_VEH_M


def greenshields_capacity_vph(lanes: float = 1.0, vmax_ms: float = CITY_MAX_SPEED_MS) -> float:
    """
    Approximate lane capacity (veh/h) from Greenshields: q_max ≈ kj * vf / 4.
    At 40 km/h and 7.5 m effective length ≈ 370 veh/h/lane.
    """
    kj = jam_density_veh_per_km()
    vf_kmh = vmax_ms * 3.6
    return max(50.0, (kj * vf_kmh / 4.0) * max(1.0, lanes))


def spatial_flow_cap_vph(
    edge_length_m: float,
    lanes: float = 1.0,
    vmax_ms: float = CITY_MAX_SPEED_MS,
    traffic_level: float | None = None,
) -> float:
    """
    Cap demand by physical space + free-flow / peak speed.

    - Greenshields capacity scales with lanes and prevailing speed.
    - Short edges cannot sustain high continuous injection: soft factor by length.
    - TomTom relative speed (0–1) reduces effective vmax in peak hours.
    """
    level = 1.0 if traffic_level is None else max(0.15, min(1.0, float(traffic_level)))
    v_eff = vmax_ms * level
    q_max = greenshields_capacity_vph(lanes=lanes, vmax_ms=v_eff)
    # Short segment soft limit: need ~3 vehicle spacings to behave like a corridor
    storage = max(1.0, float(edge_length_m) / EFFECTIVE_VEH_M)
    length_factor = min(1.0, storage / 3.0)
    return max(30.0, q_max * length_factor)


def desired_speed_ms(traffic_level: float | None = None, vmax_ms: float = CITY_MAX_SPEED_MS) -> float:
    """Peak-hour desired speed from TomTom relative speed (floored at 15% of vmax)."""
    if traffic_level is None:
        return vmax_ms
    return vmax_ms * max(0.15, min(1.0, float(traffic_level)))
