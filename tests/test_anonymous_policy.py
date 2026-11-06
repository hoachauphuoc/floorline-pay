"""
Who may read the deployed service without signing in (review F00, 2026-11-05).

The public service answered an anonymous caller 200 on whoami, the payment ledger and a case, while the console asked everyone
to sign in and the README said an unauthenticated call gets 401. The code default is ANONYMOUS_ROLE=viewer, which makes reads
public, and render.yaml never set it. Pinned here: with ANONYMOUS_ROLE=none the API answers 401 to a caller with no
credentials on those reads and still answers /health; with the code default the same reads are public (which is why the
deployment has to say none); and the Render blueprint and the deployment notes set and describe ANONYMOUS_ROLE=none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READS = ["/api/v1/auth/whoami", "/api/v1/payments/ledger", "/api/v1/payments/cases/CASE-PROBE-1"]


@pytest.fixture
def api(monkeypatch):
    from vf_logistics import app as app_mod

    monkeypatch.setenv("STORE_BACKEND", "memory")
    monkeypatch.setattr(app_mod.limiter, "enabled", False)
    app_mod.app.config["TESTING"] = True

    def client(role):
        monkeypatch.setenv("ANONYMOUS_ROLE", role)
        return app_mod.app.test_client()

    return client


@pytest.mark.parametrize("path", READS)
def test_the_api_answers_401_to_a_caller_with_no_credentials_when_the_anonymous_role_is_none(api, path):
    response = api("none").get(path)
    assert response.status_code == 401


def test_the_health_check_still_answers_without_credentials_when_the_anonymous_role_is_none(api):
    assert api("none").get("/health").status_code == 200


def test_with_the_code_default_the_same_reads_are_public(api):
    assert api("viewer").get("/api/v1/payments/ledger").status_code == 200


def test_the_render_blueprint_turns_anonymous_reads_off():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert re.search(r"- key: ANONYMOUS_ROLE\s*\n\s*value: none\s*\n", text)
    assert re.search(r"- key: VF_PUBLIC_READS\s*\n\s*value: \"false\"\s*\n", text)


def test_the_google_notes_no_longer_tell_the_operator_to_leave_the_anonymous_role_unset():
    text = (ROOT / "docs" / "GCP_FOR_RENDER.md").read_text(encoding="utf-8")
    assert "Do not set `ANONYMOUS_ROLE`" not in text
    assert "`ANONYMOUS_ROLE` to `none`" in text


def test_the_render_notes_say_how_to_check_that_an_anonymous_read_is_refused():
    text = (ROOT / "docs" / "RENDER_DEPLOY.md").read_text(encoding="utf-8")
    assert "ANONYMOUS_ROLE=none" in text
    assert "answers 401" in text
