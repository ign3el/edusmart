"""Read-only view of the RunPod image-generation spend counter.

The counter itself is written by the reserve-before-spend guard in
services/story_service.py (search for "Simple spend guard") - this module
does not write to db_data/runpod_usage.json, only reads it, for the admin
cost dashboard. Mirrors services/vision_budget.py's snapshot() shape: a
best-effort read, never raises, never touches the write path's locking.
"""
import json
import os
import time

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_USAGE_FILE = os.path.join(_APP_ROOT, "db_data", "runpod_usage.json")


def snapshot() -> dict:
    """Current month's RunPod image spend, for the admin system dashboard."""
    month_key = time.strftime("%Y-%m")
    cap_aed = float(os.getenv("RUNPOD_MONTHLY_CAP_AED", "25"))
    cost_per_image = float(os.getenv("RUNPOD_COST_AED_PER_IMAGE", "0.02"))

    images = 0
    month = month_key
    try:
        with open(_USAGE_FILE, "r") as f:
            data = json.load(f)
        if data.get("month") == month_key:
            images = data.get("images", 0)
        else:
            # Stale counter from a prior month - report zero for the current
            # month rather than a number that will reset on the next image
            # generation anyway (see story_service.py's own reset check).
            images = 0
        month = month_key
    except FileNotFoundError:
        pass
    except Exception:
        # A dashboard read must never 500 the admin panel over a corrupt
        # or mid-write counter file - report what we can.
        pass

    spent_aed = round(images * cost_per_image, 3)
    return {
        "month": month,
        "images": images,
        "spent_aed": spent_aed,
        "cap_aed": cap_aed,
        "remaining_aed": round(max(0.0, cap_aed - spent_aed), 3),
    }
