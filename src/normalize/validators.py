from typing import Optional


def normalize_reference(ref: Optional[str]) -> Optional[str]:
    if not ref:
        return ref
    return "".join(ch for ch in ref if ch.isdigit())


def normalize_city(city: Optional[str]) -> Optional[str]:
    if not city:
        return city
    return city.strip().upper().replace("  ", " ")
