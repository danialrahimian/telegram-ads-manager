"""What each ad placement accepts. One place for the rule, used by bulk operations, replacements and
the new-ad form."""

from typing import Any

PHOTO_PLACEMENTS = {"channel_post", "channels"}


def supports_photo(ad: Any = None, *, placement: str = "", target_type: str = "") -> bool:
    """Only channel-post ads (targeting channels or users) carry a photo; bot banners and search results do not.

    Accepts an Ad (anything with `placement` and `target_type`) or the two values directly.
    """
    if ad is not None:
        placement = getattr(ad, "placement", "") or ""
        target_type = getattr(ad, "target_type", "") or ""
    if placement:
        return placement in PHOTO_PLACEMENTS
    return target_type in ("channels", "users")


def kind_supports_photo(kind: str) -> bool:
    """The new-ad form's choice: "channel" or "bot"."""
    return supports_photo(placement="channel_post" if kind == "channel" else "bot_banner")
