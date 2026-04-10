"""Apuja profiilipohjaisten laitteiden käsittelyyn."""
from __future__ import annotations

from typing import Any

from .const import CONF_PROFILE_TYPE, PROFILE_KEY_NAME, PROFILE_KEY_TYPE


def normalize_profiles(raw_profiles: Any) -> list[dict[str, Any]]:
    """Palauta profiilit listana, suodattaen virheelliset."""
    if not isinstance(raw_profiles, list):
        return []
    return [profile for profile in raw_profiles if isinstance(profile, dict)]


def get_profiles_by_type(
    profiles: list[dict[str, Any]],
    profile_type: str,
) -> list[dict[str, Any]]:
    """Suodata profiilit tyypin perusteella."""
    return [
        profile
        for profile in profiles
        if str(profile.get(CONF_PROFILE_TYPE, "")).lower() == profile_type
    ]


def collect_profile_points(profiles: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Kerää profiileissa viitatut ADS-pointit muotoon [{name, type}, ...]."""
    points: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for profile in profiles:
        for section in profile.values():
            if not isinstance(section, dict):
                continue
            point_name = section.get(PROFILE_KEY_NAME)
            point_type = section.get(PROFILE_KEY_TYPE)
            if not point_name or not point_type:
                continue
            key = (str(point_name), str(point_type).upper())
            if key in seen:
                continue
            seen.add(key)
            points.append(
                {
                    PROFILE_KEY_NAME: key[0],
                    PROFILE_KEY_TYPE: key[1],
                }
            )
    return points
