from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from config import env_profile
from config.settings import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_environment_physical_isolation_invariants():
    if settings.IS_PRODUCTION:
        assert env_profile.ENVIRONMENT_ID == "production"
        assert env_profile.DEFAULT_PORT == 8003
        assert env_profile.FORCE_MAINTENANCE_ON_BOOT is True
        assert not (PROJECT_ROOT / "brokers" / "connectors" / "paper_b3.py").exists()
        assert not (PROJECT_ROOT / "backtest").exists()
        assert (PROJECT_ROOT / "brokers" / "connectors" / "clear_mt5.py").exists()
    else:
        assert env_profile.ENVIRONMENT_ID == "homologation"
        assert env_profile.DEFAULT_PORT == 9003
        assert env_profile.FORCE_MAINTENANCE_ON_BOOT is False
        assert (PROJECT_ROOT / "brokers" / "connectors" / "paper_b3.py").exists()
        assert (PROJECT_ROOT / "backtest" / "runner.py").exists()
        assert not (PROJECT_ROOT / "brokers" / "connectors" / "clear_mt5.py").exists()
        assert not (PROJECT_ROOT / "market" / "providers" / "mt5_prov.py").exists()
        assert not (PROJECT_ROOT / "infra" / "mt5_bridge").exists()


def test_promotion_script_guardrails():
    promo_script = PROJECT_ROOT / "scripts" / "promote_to_prod.py"
    if not promo_script.exists():
        return
    blocked = subprocess.run(
        [sys.executable, str(promo_script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert blocked.returncode != 0
