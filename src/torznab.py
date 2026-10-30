"""Per-file names from ukr-torznab's parser (``/toloka/info``, ``/utp/info``)."""

import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Details link recorded with the grab -> ukr-torznab info route.
_INFO_ROUTES = (
    (re.compile(r"toloka\.to/t(\d+)"), "toloka"),
    (re.compile(r"utp\.to/torrents/(\d+)"), "utp"),
)


async def fetch_file_names(base_url: str, info_url: str | None) -> dict[str, str] | None:
    """Map lower-cased torrent file paths to the names ukr-torznab gives them.

    ``info_url`` is the grab's details link (``toloka.to/t123``,
    ``utp.to/torrents/123``). Returns ``None`` when the grab is not a
    ukr-torznab release, the topic is not parsed (yet), or the service is
    unreachable: the caller then falls back to its filename heuristics.
    """
    route = next(
        ((t, m.group(1)) for p, t in _INFO_ROUTES if (m := p.search(info_url or ""))), None
    )
    if route is None:
        return None
    url = f"{base_url.rstrip('/')}/{route[0]}/info/{route[1]}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"ukr-torznab lookup failed for {url}: {e}")
        return None
    release = data.get("release", data)  # /toloka/info nests it, /utp/info does not
    names = {
        e["file"].lower(): e["renamed"]
        for e in release.get("episodes") or []
        if e.get("file") and e.get("renamed")
    }
    return names or None
