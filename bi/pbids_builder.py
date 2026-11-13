from __future__ import annotations

from typing import Any


def build_pbids_dict(base_url: str, environment_id: str) -> dict[str, Any]:
    clean_base = (base_url or "http://localhost:8003").strip().rstrip("/")
    _ = environment_id
    return {
        "version": "0.1",
        "connections": [
            {
                "details": {
                    "protocol": "http",
                    "address": {
                        "url": f"{clean_base}/api/bi/v1/metadata",
                    },
                },
                "options": {},
                "mode": "Import",
            }
        ],
    }
