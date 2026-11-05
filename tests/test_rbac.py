"""
Role-based access, end to end: who gets which role, and whether the routes honour it.

WHY THIS FILE EXISTS

Until this change the role hierarchy was decorative. Every request through the
console carried the API key, the key granted GOVERNANCE_ADMIN, and a console
session changed only the name on the audit record -- so a signed-in person of any
job, and an anonymous visitor reading through the console, held every role. The
Access Control screen drew a hierarchy nobody was subject to.

Roles now come from ADMIN_EMAILS / OPERATOR_EMAILS / REVIEWER_EMAILS on this
service, looked up by the email a verified console session (or IAP) names, and
capped at what the key grants. These tests pin that, at the level of real routes
where it matters, and walk the route table so the policy the Access Control
screen reports cannot drift from the decorators that enforce it.

Class names use the `Test*` prefix: see test_console_session.py for why that is
load-bearing in this suite.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flask import Flask, jsonify  # noqa: E402

from vf_logistics import auth  # noqa: E402
from test_console_session import TEST_KEY, TEST_SECRET, mint  # noqa: E402

ADMIN = "admin@forwarder.example"
OPERATOR = "ops@forwarder.example"
REVIEWER = "reviewer@forwarder.example"
STRANGER = "somebody@forwarder.example"

ROLE_ENV = {
    "VF_API_KEY": TEST_KEY,
    "VF_SESSION_SECRET": TEST_SECRET,
    "IAP_ENABLED": "false",
    "MULTI_TENANT": "false",
    "ANONYMOUS_ROLE": "viewer",
    "ADMIN_EMAILS": ADMIN,
    "OPERATOR_EMAILS": OPERATOR,
    "REVIEWER_EMAILS": REVIEWER,
    # The one-click tests below mint a guest session for ADMIN; a guest token is
    # honoured only while the guest door is open for that account (finding S01).
    "VF_ONE_CLICK_JUDGE": "true",
    "VF_ONE_CLICK_EMAIL": ADMIN,
}


def _matrix_app() -> Flask:
    """One route per role, so the matrix tests the decorators and nothing else."""
    app = Flask("rbac-matrix")

    @app.route("/viewer")
    @auth.require_viewer
    def viewer_route():
        return jsonify(ok=True)

    @app.route("/reviewer", methods=["POST"])
    @auth.require_reviewer
    def reviewer_route():
        return jsonify(ok=True)

    @app.route("/operator", methods=["POST"])
    @auth.require_operator
    def operator_route():
        return jsonify(ok=True)

    @app.route("/admin", methods=["POST"])
    @auth.require_governance_admin
    def admin_route():
        return jsonify(ok=True)

    @app.route("/clear", methods=["POST"])
    @auth.require_operator
    @auth.require_password_session
    def clear_route():
        return jsonify(ok=True)

    return app


def _session(email: str, amr: str | None = "password") -> dict[str, str]:
    return {"X-VF-API-Key": TEST_KEY, "X-VF-Session": mint(email, amr=amr)}


class TestRoleMatrix(unittest.TestCase):
    """Every identity against every role, through the real decorators."""

    ROUTES = (("GET", "/viewer"), ("POST", "/reviewer"), ("POST", "/operator"), ("POST", "/admin"))

    # Expected status per route, in ROUTES order.
    EXPECTED = {
        "anonymous": (200, 403, 403, 403),
        "key only": (200, 200, 200, 200),
        "listed admin": (200, 200, 200, 200),
        "listed operator": (200, 200, 200, 403),
        "listed reviewer": (200, 200, 403, 403),
        "unlisted person": (200, 403, 403, 403),
        "forged session": (401, 401, 401, 401),
    }

    HEADERS = {
        "anonymous": {},
        "key only": {"X-VF-API-Key": TEST_KEY},
        "listed admin": _session(ADMIN),
        "listed operator": _session(OPERATOR),
        "listed reviewer": _session(REVIEWER),
        "unlisted person": _session(STRANGER),
        "forged session": {
            "X-VF-API-Key": TEST_KEY,
            "X-VF-Session": mint(ADMIN, secret="not-the-real-secret-0123456789abcdef"),
        },
    }

    def test_matrix(self):
        client = _matrix_app().test_client()
        with patch.dict(os.environ, ROLE_ENV):
            for identity, expected in self.EXPECTED.items():
                for (method, path), want in zip(self.ROUTES, expected):
                    with self.subTest(identity=identity, route=path):
                        response = client.open(path, method=method, headers=self.HEADERS[identity])
                        self.assertEqual(response.status_code, want)

    def test_a_refusal_names_the_role_it_wanted(self):
        """The console turns this into 'requires the operator role', so it must say which."""
        client = _matrix_app().test_client()
        with patch.dict(os.environ, ROLE_ENV):
            body = client.post("/operator", headers=_session(REVIEWER)).get_json()
        self.assertEqual(body["required_role"], "operator")
        self.assertEqual(body["user_roles"], ["reviewer", "viewer"])


class TestPasswordOnlyRoute(unittest.TestCase):
    """
    A route that needs a password sign-in on top of its role.

    The one-click judge button hands anybody an admin session, so the role alone
    cannot keep the board from being cleared. These pin who still gets through.
    """

    CASES = {
        "anonymous": ({}, 403),
        # The seeding tools and the network-defence tests clear the board this way.
        "key only": ({"X-VF-API-Key": TEST_KEY}, 200),
        "admin, password": (_session(ADMIN), 200),
        "operator, password": (_session(OPERATOR), 200),
        "admin, one-click": (_session(ADMIN, amr="one_click"), 403),
        # A token from before the claim existed, or one with a value nobody mints.
        "admin, no claim": (_session(ADMIN, amr=None), 403),
        "admin, unknown claim": (_session(ADMIN, amr="magic"), 403),
        # The role check runs first, so a reviewer is refused for the role.
        "reviewer, password": (_session(REVIEWER), 403),
    }

    def test_who_may_use_it(self):
        client = _matrix_app().test_client()
        with patch.dict(os.environ, ROLE_ENV):
            for identity, (headers, want) in self.CASES.items():
                with self.subTest(identity=identity):
                    self.assertEqual(client.post("/clear", headers=headers).status_code, want)

    def test_the_refusal_says_a_password_is_what_is_missing(self):
        """The console turns this into 'sign in with your password', so it must say so."""
        client = _matrix_app().test_client()
        with patch.dict(os.environ, ROLE_ENV):
            body = client.post("/clear", headers=_session(ADMIN, amr="one_click")).get_json()
        self.assertEqual(body["required_auth"], "password")
        self.assertEqual(body["session_method"], "one_click")
        self.assertNotIn("required_role", body)

    def test_the_same_session_keeps_its_other_rights(self):
        """One-click is still the account's role everywhere else -- that is the point."""
        client = _matrix_app().test_client()
        with patch.dict(os.environ, ROLE_ENV):
            self.assertEqual(
                client.post("/admin", headers=_session(ADMIN, amr="one_click")).status_code, 200
            )


class TestBoardReset(unittest.TestCase):
    """The real reset route: password-only, and it leaves a record of itself."""

    def setUp(self):
        from vf_logistics import app as app_mod

        self.env = patch.dict(os.environ, dict(ROLE_ENV, STORE_BACKEND="memory"))
        self.env.start()
        self.addCleanup(self.env.stop)
        limiter_was = app_mod.limiter.enabled
        app_mod.limiter.enabled = False
        self.addCleanup(setattr, app_mod.limiter, "enabled", limiter_was)
        self.client = app_mod.app.test_client()

    def _latest_reset(self) -> dict | None:
        import asyncio

        from vf_logistics.store import get_store

        rows = asyncio.run(get_store().list_audit(limit=20))
        return next((r for r in rows if r.get("action") == "board_reset"), None)

    def test_a_one_click_admin_cannot_clear_the_board(self):
        response = self.client.post(
            "/api/v1/orchestrator/reset", headers=_session(ADMIN, amr="one_click")
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["required_auth"], "password")

    def test_a_password_admin_can_and_the_trail_says_who(self):
        response = self.client.post("/api/v1/orchestrator/reset", headers=_session(ADMIN))
        self.assertEqual(response.status_code, 200)
        row = self._latest_reset()
        self.assertIsNotNone(row, "a reset must write its own audit row")
        self.assertEqual(row["actor"], ADMIN)
        self.assertEqual(row["actor_auth"], "password")
        self.assertEqual(row["detail"]["cleared"], response.get_json()["cleared"])


class TestRoleLists(unittest.TestCase):
    """How the three lists are read."""

    def test_entries_are_trimmed_and_case_folded(self):
        """
        A list typed as "Admin@X.com, other@y.com" used to match neither the
        lowercased session email nor anything after the first comma's space.
        """
        env = dict(ROLE_ENV, ADMIN_EMAILS=" Admin@Forwarder.Example , other@y.example ")
        with patch.dict(os.environ, env):
            self.assertIn(auth.Role.GOVERNANCE_ADMIN, auth._get_user_roles(ADMIN))
            self.assertIn(auth.Role.GOVERNANCE_ADMIN, auth._get_user_roles("OTHER@y.example"))

    def test_empty_entries_grant_nothing(self):
        """A trailing comma must not make the empty string a listed person."""
        env = dict(ROLE_ENV, ADMIN_EMAILS=f"{ADMIN},,")
        with patch.dict(os.environ, env):
            self.assertEqual(auth._get_user_roles(""), {auth.Role.VIEWER})

    def test_iap_identities_use_the_same_lists(self):
        """
        IAP was the only path that read these lists, and no test ran it end to end.
        The console session path now reads them too; both must agree.
        """
        from vf_logistics.app import app

        env = dict(ROLE_ENV, IAP_ENABLED="true")
        claims = {"email": "Reviewer@Forwarder.Example", "sub": "iap-subject-1"}
        with patch.dict(os.environ, env), patch.object(auth, "_verify_iap_jwt", return_value=claims):
            with app.test_request_context("/", headers={"X-Goog-IAP-JWT-Assertion": "token"}):
                self.assertIsNone(auth.authenticate_request())
                ctx = auth.get_auth_context()
        self.assertTrue(ctx.has_role(auth.Role.REVIEWER))
        self.assertFalse(ctx.has_role(auth.Role.OPERATOR))


class TestPolicyKillSwitchesNeedAPassword(unittest.TestCase):
    """
    The real revoke and prefilter-rules routes, against a one-click admin.

    The judge button is public and its account is an admin. Either route, pressed
    once by a stranger, changes the demo for every judge after them, so both now
    ask for the password sign-in the board reset already asked for.
    """

    def setUp(self):
        from vf_logistics import app as app_mod

        self.env = patch.dict(os.environ, dict(ROLE_ENV, STORE_BACKEND="memory"))
        self.env.start()
        self.addCleanup(self.env.stop)
        limiter_was = app_mod.limiter.enabled
        app_mod.limiter.enabled = False
        self.addCleanup(setattr, app_mod.limiter, "enabled", limiter_was)
        self.client = app_mod.app.test_client()

    def test_a_one_click_admin_cannot_revoke_the_boundary(self):
        response = self.client.post(
            "/api/v1/governance/revoke", json={"note": "trying it"},
            headers=_session(ADMIN, amr="one_click"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["required_auth"], "password")

    def test_a_one_click_admin_cannot_rewrite_the_prefilter_rules(self):
        response = self.client.put(
            "/api/v1/governance/prefilter-rules", json={"blacklist_companies": []},
            headers=_session(ADMIN, amr="one_click"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["required_auth"], "password")

    def test_a_password_admin_still_passes_the_gate(self):
        """Not refused for the sign-in method: whatever comes back is the route's own answer."""
        for method, path, body in (
            ("post", "/api/v1/governance/revoke", {"note": "drill"}),
            ("put", "/api/v1/governance/prefilter-rules", {"low_value_threshold_usd": 100}),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json=body, headers=_session(ADMIN))
                self.assertNotEqual(
                    (response.get_json() or {}).get("required_auth"), "password",
                    f"{path} refused a password sign-in",
                )

    def test_a_one_click_admin_can_still_read_and_simulate(self):
        """The judge keeps everything that writes nothing: reading the rules, and simulate."""
        headers = _session(ADMIN, amr="one_click")
        self.assertEqual(
            self.client.get("/api/v1/governance/prefilter-rules", headers=headers).status_code, 200
        )
        response = self.client.post(
            "/api/v1/governance/simulate",
            json={"permissions": {"auto_release": {"permitted": False}}},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))


class TestPublishNeedsAPassword(unittest.TestCase):
    """
    Finding A01 of the authorization review of bd6a210.

    Revoke was password-only and publish was not, so the one-click guest admin
    could publish {"allowed_actions": []} -- a boundary that reads ACTIVE and
    denies every protected action, which suspends the agent as surely as a revoke.
    Gating the kill switch while leaving the switch that replaces the whole policy
    open protected nothing.
    """

    DENY_ALL = {"permissions": {"allowed_actions": []}, "note": "A01 reproduction"}

    def setUp(self):
        import asyncio

        from vf_logistics import app as app_mod
        from vf_logistics import store as store_mod

        self.env = patch.dict(os.environ, dict(ROLE_ENV, STORE_BACKEND="memory"))
        self.env.start()
        self.addCleanup(self.env.stop)
        # A fresh store per test: the history assertions below compare before and
        # after, and a boundary left by another test would make them vacuous.
        # Reset the module singleton the way the rest of the suite does, because
        # governance.py binds get_store at import and a patch would miss it.
        store_mod._store = None
        self.addCleanup(setattr, store_mod, "_store", None)
        self.store = store_mod.get_store()
        self._run = asyncio.run
        limiter_was = app_mod.limiter.enabled
        app_mod.limiter.enabled = False
        self.addCleanup(setattr, app_mod.limiter, "enabled", limiter_was)
        self.client = app_mod.app.test_client()

    def _history(self) -> list[dict]:
        return self._run(self.store.list_boundaries(50))

    def _audit_actions(self) -> list[str]:
        return [r.get("action") for r in self._run(self.store.list_audit(limit=50))]

    def test_refused_sessions_change_neither_the_policy_nor_the_trail(self):
        for label, amr in (("one-click", "one_click"), ("no claim", None), ("unknown claim", "magic")):
            with self.subTest(session=label):
                before_history, before_audit = self._history(), self._audit_actions()
                response = self.client.post(
                    "/api/v1/governance/publish", json=self.DENY_ALL,
                    headers=_session(ADMIN, amr=amr),
                )
                self.assertEqual(response.status_code, 403, response.get_data(as_text=True))
                self.assertEqual(response.get_json()["required_auth"], "password")
                self.assertEqual(self._history(), before_history)
                self.assertEqual(self._audit_actions(), before_audit)

    def test_a_password_admin_publishes(self):
        response = self.client.post(
            "/api/v1/governance/publish", json={"note": "template"}, headers=_session(ADMIN),
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["boundary"]["published_by"], ADMIN)

    def test_the_key_alone_still_publishes(self):
        """seed_full_board.py publishes this way; it has no session to ask about."""
        response = self.client.post(
            "/api/v1/governance/publish", json={"author": "seed-script"},
            headers={"X-VF-API-Key": TEST_KEY},
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))


class TestWhoami(unittest.TestCase):
    """GET /api/v1/auth/whoami reports what the decorators will enforce."""

    def _whoami(self, headers: dict[str, str]) -> dict:
        from vf_logistics.app import app

        with patch.dict(os.environ, ROLE_ENV):
            response = app.test_client().get("/api/v1/auth/whoami", headers=headers)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_anonymous(self):
        me = self._whoami({})
        self.assertEqual(me["authenticated_by"], "anonymous")
        self.assertEqual(me["role"], "viewer")
        self.assertEqual(me["role_source"], "ANONYMOUS_ROLE")
        self.assertFalse(me["acts_for_a_person"])

    def test_listed_reviewer(self):
        me = self._whoami(_session(REVIEWER))
        self.assertEqual(me["authenticated_by"], "console_session")
        self.assertEqual(me["email"], REVIEWER)
        self.assertEqual(me["role"], "reviewer")
        self.assertEqual(me["grants"], ["viewer", "reviewer"])
        self.assertEqual(me["role_source"], "REVIEWER_EMAILS")

    def test_unlisted_person_is_told_why(self):
        me = self._whoami(_session(STRANGER))
        self.assertEqual(me["role"], "viewer")
        self.assertIn("not listed", me["role_source"])

    def test_key_only(self):
        me = self._whoami({"X-VF-API-Key": TEST_KEY})
        self.assertEqual(me["authenticated_by"], "api_key")
        self.assertEqual(me["role"], "governance_admin")
        self.assertIsNone(me["session_method"])

    def test_the_sign_in_method_is_reported_beside_the_role(self):
        """The console locks Clear board from this, so it must match the decorator."""
        one_click = self._whoami(_session(ADMIN, amr="one_click"))
        password = self._whoami(_session(ADMIN))
        self.assertEqual(one_click["authenticated_by"], "console_session")
        self.assertEqual(one_click["role"], "governance_admin")
        self.assertEqual(one_click["session_method"], "one_click")
        self.assertEqual(password["session_method"], "password")


class TestRoutePolicy(unittest.TestCase):
    """The policy the Access Control screen shows is the one the routes enforce."""

    # Public on purpose. Each is either liveness, a schema, a static list of
    # capabilities, or a page for a standalone backend.
    PUBLIC = {
        "/", "/legacy", "/health", "/agents",
        "/api/v1/agents", "/api/v1/openapi.json",
    }

    # A POST that only reads: it replays history against a proposed boundary and
    # changes nothing, which is why a viewer may run it.
    READ_ONLY_POSTS = {"/api/v1/governance/simulate"}

    def setUp(self):
        from vf_logistics.app import app

        self.rows = auth.route_policy(app)

    def test_every_route_declares_a_role_or_is_knowingly_public(self):
        """
        If a decorator above require_* stopped using functools.wraps, the role would
        still be enforced but vanish from the policy -- and the screen would call an
        enforced route public. This catches that as well as a missing decorator.
        """
        for row in self.rows:
            with self.subTest(path=row["path"]):
                if row["path"] in self.PUBLIC:
                    self.assertIsNone(row["required_role"])
                else:
                    self.assertIn(row["required_role"], [r.value for r in auth.Role])

    def test_every_write_needs_more_than_viewer(self):
        for row in self.rows:
            writes = set(row["methods"]) & {"POST", "PUT", "PATCH", "DELETE"}
            if not writes or row["path"] in self.READ_ONLY_POSTS:
                continue
            with self.subTest(path=row["path"]):
                self.assertNotIn(row["required_role"], (None, "viewer"))

    def test_policy_reflects_the_decorators(self):
        by_path = {(r["path"], tuple(r["methods"])): r["required_role"] for r in self.rows}
        self.assertEqual(by_path[("/api/v1/review/queue", ("GET",))], "viewer")
        self.assertEqual(by_path[("/api/v1/review/<case_id>/decide", ("POST",))], "reviewer")
        self.assertEqual(by_path[("/api/v1/review/<case_id>/document", ("GET",))], "reviewer")
        self.assertEqual(by_path[("/api/v1/billing/usage", ("GET",))], "operator")
        self.assertEqual(by_path[("/api/v1/governance/publish", ("POST",))], "governance_admin")

    def test_only_the_board_reset_and_the_policy_writes_ask_for_a_password(self):
        """
        The flag survives the rate limiter and the role decorator above it, and it
        is on exactly the routes it was put on. Widening it would quietly take a
        right away from the one-click judge session.

        Revoke and the prefilter PUT joined the reset because each one changes the
        demo for every visitor after the person who pressed it: a revoked boundary
        suspends the agent, an emptied blacklist clears what it should hold.
        Publish joined them after review finding A01: replacing the shared boundary
        with one that permits nothing suspends the agent too.
        """
        flagged = {r["path"] for r in self.rows if r["requires_password_session"]}
        self.assertEqual(flagged, {
            "/api/v1/orchestrator/reset",
            "/api/v1/governance/publish",
            "/api/v1/governance/revoke",
            "/api/v1/governance/prefilter-rules",
            "/api/v1/review/<case_id>/approve",
            "/api/v1/review/<case_id>/execute",
            "/api/v1/review/<case_id>/approve-exception",
            "/api/v1/review/<case_id>/second-approve",
            "/api/v1/carriers/payee",
            "/api/v1/carriers/<carrier_key>/payee/<version>/verify",
            "/api/v1/payments/pause",
            "/api/v1/payments/resume",
            "/api/v1/payments/limits",
            "/api/v1/payments/freezes/lift",
            "/api/v1/payments/cases/<case_id>/decision/redeliver",
        })
        put = next(
            r for r in self.rows
            if r["path"] == "/api/v1/governance/prefilter-rules" and "PUT" in r["methods"]
        )
        self.assertTrue(put["requires_password_session"])
        get = next(
            r for r in self.rows
            if r["path"] == "/api/v1/governance/prefilter-rules" and "GET" in r["methods"]
        )
        self.assertFalse(get["requires_password_session"], "reading the rules stays open")

    def test_policy_endpoint_reports_list_sizes_not_members(self):
        from vf_logistics.app import app

        with patch.dict(os.environ, ROLE_ENV):
            body = app.test_client().get("/api/v1/auth/policy").get_json()
        self.assertEqual(body["roles"], ["viewer", "reviewer", "operator", "governance_admin"])
        self.assertEqual(body["anonymous_role"], "viewer")
        assignment = {a["variable"]: a for a in body["role_assignment"]}
        self.assertEqual(assignment["ADMIN_EMAILS"]["accounts"], 1)
        self.assertNotIn(ADMIN, str(body), "the policy must not publish who holds a role")


class TestReviewQueueIsReadable(unittest.TestCase):
    """The queue is viewer, the original document and the decision are not."""

    def test_anonymous_can_read_the_queue_but_not_the_document(self):
        from vf_logistics.app import app

        client = app.test_client()
        with patch.dict(os.environ, ROLE_ENV):
            self.assertEqual(client.get("/api/v1/review/queue").status_code, 200)
            self.assertEqual(client.get("/api/v1/review/CASE-1/document").status_code, 403)
            self.assertEqual(client.get("/api/v1/billing/usage").status_code, 403)


if __name__ == "__main__":
    unittest.main()
