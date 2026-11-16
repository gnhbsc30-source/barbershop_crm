"""Local server-side mapping between public slugs and active businesses."""

import json
import os
import re
import unicodedata
from types import MappingProxyType


SLUG = re.compile(r"(?=.{1,63}$)[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)


def _unique_mapping(pairs):
    mapping = {}
    for slug, business_id in pairs:
        if slug in mapping:
            raise ValueError("Duplicate slug")
        mapping[slug] = business_id
    return mapping


def local_slug_mapping(connection):
    try:
        mapping = json.loads(os.environ.get("BARBERSHOP_DEV_SLUGS", "{}"), object_pairs_hook=_unique_mapping)
        if not isinstance(mapping, dict):
            raise ValueError("Expected mapping")
        used = set()
        for slug, business_id in mapping.items():
            if (not SLUG.fullmatch(slug) or type(business_id) is not int
                    or not 0 < business_id <= 2**63 - 1 or business_id in used):
                raise ValueError("Invalid mapping")
            if not connection.execute("SELECT 1 FROM businesses WHERE id = ?", (business_id,)).fetchone():
                raise ValueError("Unknown business")
            used.add(business_id)
    except (ValueError, RecursionError):
        raise ValueError("INVALID_DEVELOPMENT_SLUG_CONFIGURATION") from None
    return MappingProxyType(mapping)


def public_business(connection, mapping, slug):
    if not SLUG.fullmatch(slug):
        return None
    business_id = mapping.get(slug)
    if business_id is None:
        return None
    return connection.execute(
        "SELECT id, display_name, business_name FROM businesses WHERE id = ? AND is_active = 1",
        (business_id,),
    ).fetchone()


def public_display_name(row):
    for candidate in (row["display_name"], row["business_name"]):
        if isinstance(candidate, str):
            clean = "".join(char for char in candidate if unicodedata.category(char) not in {"Cc", "Cf"})
            if name := clean.strip()[:120].strip():
                return name
    return "מספרה"
