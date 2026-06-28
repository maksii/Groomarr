"""Tests for the web UI / management API (config, simulate, settings, status)
and the engine refactors that back them.

These mirror the mocked-qBittorrent style used in test_integration.py.
"""

import contextlib
import os
import sys
from importlib import reload
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Engine refactor tests (no app required)
# =============================================================================


class TestEngineRefactors:
    """apply_rename_rules_traced must match apply_rename_rules; filters robust."""

    def test_traced_matches_plain(self):
        from src.config import TrackerRules
        from src.rename import apply_rename_rules, apply_rename_rules_traced

        rules = TrackerRules.from_dict(
            {
                "remove_patterns": [r"\[.*?\]"],
                "replace_patterns": {r"\.": " "},
                "prefix": "[X] ",
                "suffix": " [Y]",
            }
        )
        for title in [
            "Some.Movie.2024.1080p.BluRay-GRP",
            "[grp] Anime - 01 [1080p].mkv",
            "Plain Title",
            "",
            "Movie:With/Invalid*Chars",
        ]:
            plain = apply_rename_rules(title, rules)
            traced, steps = apply_rename_rules_traced(title, rules)
            assert traced == plain, f"divergence for {title!r}"
            assert isinstance(steps, list)

    def test_traced_matches_plain_on_skip(self):
        from src.config import TrackerRules
        from src.rename import apply_rename_rules, apply_rename_rules_traced

        rules = TrackerRules.from_dict({"skip_title_patterns": ["PROPER"], "prefix": "X "})
        title = "Movie PROPER 1080p.mkv"
        assert apply_rename_rules_traced(title, rules)[0] == apply_rename_rules(title, rules)

    def test_invalid_patterns_do_not_crash(self):
        from src.config import TrackerRules
        from src.rename import (
            apply_rename_rules,
            apply_rename_rules_traced,
            evaluate_filters,
        )

        rules = TrackerRules.from_dict(
            {
                "remove_patterns": ["(unclosed"],
                "skip_title_patterns": ["(also bad"],
                "qualities_exclude": ["(bad"],
            }
        )
        # None of these should raise despite invalid regex
        apply_rename_rules("Movie 1080p", rules)
        _, steps = apply_rename_rules_traced("Movie 1080p", rules)
        assert any(s.get("error") for s in steps)
        # Invalid exclude pattern => treated as non-match => not excluded
        ok, _reason = evaluate_filters(rules, quality="CAM")
        assert ok is True

    def test_evaluate_filters_matches_should_process(self):
        """should_process must delegate to evaluate_filters with identical results."""
        from src.config import TrackerRules
        from src.models import RadarrRelease, RadarrWebhook
        from src.rename import evaluate_filters, should_process

        rules = TrackerRules.from_dict(
            {"qualities_exclude": ["CAM"], "indexers_exclude": ["Public.*"]}
        )
        payload = RadarrWebhook(
            eventType="Grab",
            movie={"id": 1, "title": "X"},
            release=RadarrRelease(releaseTitle="X CAM", quality="CAM", indexer="PublicHD"),
            downloadId="abc",
            downloadClient="qbit",
            downloadClientType="qBittorrent",
        )
        assert isinstance(payload.release, RadarrRelease)
        sp = should_process(payload, rules)
        ef = evaluate_filters(
            rules,
            indexer="PublicHD",
            quality="CAM",
            release_group="",
            custom_formats=[],
            custom_format_score=None,
            download_client="qbit",
        )
        assert sp == ef
        assert sp[0] is False


class TestSerialization:
    """RenameRules round-trips through to_dict/from_dict."""

    def test_roundtrip(self):
        from src.config import RenameRules

        data = {
            "global": {"qualities_exclude": ["CAM", "TS"], "prefix": "[G] "},
            "trackers": [
                {
                    "name": "anime",
                    "match": ["Nyaa*"],
                    "rules": {"suffix": " [Anime]", "remove_patterns": [r"\[.*?\]"]},
                }
            ],
        }
        rr = RenameRules.from_dict(data)
        out = rr.to_dict()
        assert out["global"]["qualities_exclude"] == ["CAM", "TS"]
        assert out["global"]["prefix"] == "[G] "
        assert out["trackers"][0]["name"] == "anime"
        assert out["trackers"][0]["rules"]["suffix"] == " [Anime]"
        # Re-loading the serialized form yields the same effective rules
        rr2 = RenameRules.from_dict(out)
        assert rr2.global_rules.qualities_exclude == ["CAM", "TS"]
        assert rr2.trackers[0].rules.suffix == " [Anime]"

    def test_to_dict_omits_defaults(self):
        from src.config import RenameRules

        rr = RenameRules.from_dict({"global": {}, "trackers": []})
        out = rr.to_dict()
        assert out == {"global": {}}  # no trackers key, no empty fields

    def test_meaningful_values_survive_roundtrip(self):
        """Values that look default-ish but are meaningful must be preserved."""
        from src.config import RenameRules

        data = {
            "global": {
                "min_customformat_score": 0,  # 0 is meaningful, not "unset"
                "score_validation_policy": "warn",
                "validate_custom_format_score": True,
            }
        }
        out = RenameRules.from_dict(data).to_dict()
        assert out["global"]["min_customformat_score"] == 0
        assert out["global"]["score_validation_policy"] == "warn"
        assert out["global"]["validate_custom_format_score"] is True
        rr = RenameRules.from_dict(out)
        assert rr.global_rules.min_customformat_score == 0
        assert rr.global_rules.score_validation_policy == "warn"

    def test_unicode_survives_save(self, tmp_path):
        from src.config import RenameRules, save_rules

        p = tmp_path / "rules.yaml"
        data = {
            "global": {},
            "trackers": [
                {"name": "自由农场", "match": ["Free Farm (自由农场)"], "rules": {"suffix": " ✓"}}
            ],
        }
        save_rules(RenameRules.from_dict(data).to_dict(), str(p))
        rr = RenameRules.from_yaml(str(p))
        assert rr.trackers[0].name == "自由农场"
        assert rr.trackers[0].rules.suffix == " ✓"

    def test_from_dict_coerces_malformed_types(self):
        """Wrong YAML types must not propagate to the engine (would crash GET)."""
        from src.config import RenameRules, TrackerRules

        tr = TrackerRules.from_dict(
            {
                "replace_patterns": ["foo", "bar"],  # should be a mapping
                "qualities_exclude": "CAM",  # should be a list
                "min_customformat_score": "500",  # should be an int
                "prefix": 123,  # should be a string
                "remove_patterns": [10, "x"],  # mixed types
            }
        )
        assert tr.replace_patterns == {}
        assert tr.qualities_exclude == []
        assert tr.min_customformat_score is None
        assert tr.prefix == ""
        assert tr.remove_patterns == ["10", "x"]  # coerced to strings
        # The whole pipeline (used by GET /api/config) must not raise
        RenameRules.from_dict({"global": {"replace_patterns": ["x"]}}).to_dict()


# =============================================================================
# Fixtures for the API app
# =============================================================================

HIERARCHICAL_YAML = (
    "global:\n"
    "  qualities_exclude:\n"
    "    - CAM\n"
    "trackers:\n"
    "  - name: anime\n"
    "    match:\n"
    "      - 'Nyaa*'\n"
    "    rules:\n"
    "      suffix: ' [Anime]'\n"
)


def _make_qbit_mock():
    m = MagicMock()
    m.check_connection = MagicMock(return_value=True)
    return m


def _env(rules_file: Path, static_dir: Path, **over) -> dict:
    env = {
        "QBITTORRENT_URL": "http://mock:8080",
        "QBITTORRENT_USERNAME": "test",
        "QBITTORRENT_PASSWORD": "supersecret",
        "SONARR_URL": "",
        "SONARR_API_KEY": "",
        "RADARR_URL": "",
        "RADARR_API_KEY": "",
        "RULES_FILE": str(rules_file),
        "STATIC_DIR": str(static_dir),
        "CONFIG_READONLY": "false",
    }
    env.update(over)
    return env


@pytest.fixture
def rules_file(tmp_path):
    p = tmp_path / "rename_rules.yaml"
    p.write_text(HIERARCHICAL_YAML, encoding="utf-8")
    return p


@pytest.fixture
def app_default(rules_file, tmp_path):
    """App with a writable rules file and a NON-existent static dir."""
    no_static = tmp_path / "no_static_here"
    with patch.dict(os.environ, _env(rules_file, no_static), clear=False):
        from src import config

        reload(config)
        from src import main

        reload(main)
        main.qbit_client = _make_qbit_mock()
        yield main.app


@pytest.fixture
async def client(app_default):
    transport = ASGITransport(app=app_default)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def app_readonly(rules_file, tmp_path):
    no_static = tmp_path / "no_static_here"
    with patch.dict(os.environ, _env(rules_file, no_static, CONFIG_READONLY="true"), clear=False):
        from src import config

        reload(config)
        from src import main

        reload(main)
        main.qbit_client = _make_qbit_mock()
        yield main.app


@pytest.fixture
async def readonly_client(app_readonly):
    transport = ASGITransport(app=app_readonly)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def app_with_static(rules_file, tmp_path):
    """App whose static dir contains a built index.html + assets/."""
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Groomarr</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    with patch.dict(os.environ, _env(rules_file, static), clear=False):
        from src import config

        reload(config)
        from src import main

        reload(main)
        main.qbit_client = _make_qbit_mock()
        yield main.app


@pytest.fixture
async def static_client(app_with_static):
    transport = ASGITransport(app=app_with_static)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# =============================================================================
# Config endpoint tests
# =============================================================================


class TestConfigEndpoints:
    @pytest.mark.asyncio
    async def test_get_config(self, client):
        r = await client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["config_found"] is True
        assert data["meta"]["config_format"] == "hierarchical"
        assert data["meta"]["readonly"] is False
        assert "CAM" in data["config"]["global"]["qualities_exclude"]
        assert data["config"]["trackers"][0]["name"] == "anime"
        assert data["config"]["trackers"][0]["rules"]["suffix"] == " [Anime]"

    @pytest.mark.asyncio
    async def test_put_config_roundtrip(self, client, rules_file):
        new = {
            "global": {"prefix": "[AUTO] ", "qualities_exclude": ["CAM", "TS"]},
            "trackers": [{"name": "pub", "match": ["*public*"], "rules": {"prefix": "[PUB] "}}],
        }
        r = await client.put("/api/config", json=new)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["config"]["global"]["prefix"] == "[AUTO] "
        # File written + backup created
        text = rules_file.read_text(encoding="utf-8")
        assert "[AUTO]" in text
        backup = rules_file.with_name(rules_file.name + ".bak")
        assert backup.exists()
        # GET reflects the new state (reloaded)
        r2 = await client.get("/api/config")
        assert r2.json()["config"]["global"]["prefix"] == "[AUTO] "
        assert r2.json()["config"]["trackers"][0]["name"] == "pub"

    @pytest.mark.asyncio
    async def test_put_config_invalid_regex_rejected(self, client):
        bad = {"global": {"qualities_exclude": ["(unclosed"]}, "trackers": []}
        r = await client.put("/api/config", json=bad)
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_put_config_invalid_match_regex_rejected(self, client):
        bad = {"global": {}, "trackers": [{"name": "x", "match": ["/(bad/"], "rules": {}}]}
        r = await client.put("/api/config", json=bad)
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_put_config_warns_on_empty_match(self, client):
        cfg = {"global": {}, "trackers": [{"name": "lonely", "match": [], "rules": {}}]}
        r = await client.put("/api/config", json=cfg)
        assert r.status_code == 200
        assert any("no match patterns" in w for w in r.json()["warnings"])

    @pytest.mark.asyncio
    async def test_put_config_readonly_forbidden(self, readonly_client):
        r = await readonly_client.put("/api/config", json={"global": {}, "trackers": []})
        assert r.status_code == 403


# =============================================================================
# Simulate endpoint tests
# =============================================================================


class TestSimulateEndpoint:
    @pytest.mark.asyncio
    async def test_global_skip(self, client):
        r = await client.post(
            "/api/rules/simulate",
            json={
                "release": {
                    "release_title": "Movie 2024 CAM",
                    "indexer": "SomeTracker",
                    "quality": "CAM",
                }
            },
        )
        assert r.status_code == 200
        d = r.json()
        assert d["would_process"] is False
        assert "exclude" in d["skip_reason"]
        assert d["used_global"] is True
        assert d["matched_tracker"] is None

    @pytest.mark.asyncio
    async def test_tracker_match_and_rename(self, client):
        r = await client.post(
            "/api/rules/simulate",
            json={
                "release": {
                    "release_title": "[SubsPlease] Show - 01 [1080p].mkv",
                    "indexer": "Nyaa",
                    "quality": "WEBDL-1080p",
                }
            },
        )
        d = r.json()
        assert d["matched_tracker"] == "anime"
        assert d["used_global"] is False
        assert d["would_process"] is True
        assert d["new_title"].endswith("[Anime]")
        assert d["changed"] is True
        assert len(d["steps"]) >= 1

    @pytest.mark.asyncio
    async def test_draft_config_overrides_saved(self, client):
        r = await client.post(
            "/api/rules/simulate",
            json={
                "release": {"release_title": "Movie CAM", "indexer": "X", "quality": "CAM"},
                "config": {"global": {"qualities_exclude": []}, "trackers": []},
            },
        )
        d = r.json()
        assert d["would_process"] is True  # draft removed the CAM exclusion

    @pytest.mark.asyncio
    async def test_too_many_trackers_rejected(self, client):
        trackers = [{"name": f"t{i}", "match": ["x"], "rules": {}} for i in range(201)]
        r = await client.post(
            "/api/rules/simulate",
            json={
                "release": {"release_title": "x"},
                "config": {"global": {}, "trackers": trackers},
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_redos_pattern_is_bounded(self, client):
        """A catastrophic-backtracking pattern must be killed, not hang the server."""
        import time

        cfg = {"global": {"remove_patterns": ["(a+)+$"]}, "trackers": []}
        release = {"release_title": "a" * 40 + "!"}
        t0 = time.monotonic()
        r = await client.post("/api/rules/simulate", json={"release": release, "config": cfg})
        elapsed = time.monotonic() - t0
        assert r.status_code == 200
        # SIMULATE_TIMEOUT_S is 2s; allow generous headroom for process spawn/kill.
        assert elapsed < 20, f"simulate hung for {elapsed:.1f}s"
        assert r.json()["status"] == "error"


# =============================================================================
# Validate-pattern / settings / status
# =============================================================================


class TestValidatePattern:
    @pytest.mark.asyncio
    async def test_valid_and_invalid_regex(self, client):
        ok = await client.post(
            "/api/rules/validate-pattern", json={"pattern": "Foo.*", "kind": "regex"}
        )
        assert ok.json()["valid"] is True
        bad = await client.post(
            "/api/rules/validate-pattern", json={"pattern": "(unclosed", "kind": "regex"}
        )
        bj = bad.json()
        assert bj["valid"] is False
        assert bj["error"]

    @pytest.mark.asyncio
    async def test_match_kinds(self, client):
        async def interp(p):
            r = await client.post(
                "/api/rules/validate-pattern", json={"pattern": p, "kind": "match"}
            )
            return r.json()

        assert (await interp("Tracker*"))["interpreted"] == "wildcard"
        assert (await interp("/Foo.*/"))["interpreted"] == "regex"
        assert (await interp("ExactName"))["interpreted"] == "exact"
        assert (await interp("/(bad/"))["valid"] is False


class TestSettingsAndStatus:
    @pytest.mark.asyncio
    async def test_settings_exposes_no_secrets(self, client):
        r = await client.get("/api/settings")
        assert r.status_code == 200
        d = r.json()
        assert d["rename_mode"]
        assert d["sonarr_configured"] is False
        assert d["radarr_configured"] is False
        # The configured password must never appear anywhere in the response
        assert "supersecret" not in r.text
        keys = {k.lower() for k in d}
        assert not any("password" in k or "api_key" in k or "apikey" in k for k in keys)

    @pytest.mark.asyncio
    async def test_status_connected(self, client):
        r = await client.get("/api/status")
        assert r.status_code == 200
        d = r.json()
        assert d["qbittorrent"] == "connected"
        assert d["status"] == "ok"
        assert d["version"]
        assert d["sonarr"] is None
        assert d["radarr"] is None


# =============================================================================
# Security / serving behavior
# =============================================================================


class TestSecurityAndServing:
    @pytest.mark.asyncio
    async def test_security_headers_present(self, client):
        r = await client.get("/api/status")
        csp = r.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"

    @pytest.mark.asyncio
    async def test_unknown_api_path_returns_json_404(self, client):
        r = await client.get("/api/does-not-exist")
        assert r.status_code == 404
        assert r.json()["status"] == "error"

    @pytest.mark.asyncio
    async def test_oversized_api_body_rejected(self, client):
        big = "x" * (2 * 1024 * 1024 + 64)
        r = await client.post("/api/rules/simulate", json={"release": {"release_title": big}})
        assert r.status_code == 413

    @pytest.mark.asyncio
    async def test_spa_not_built_returns_helpful_404(self, client):
        r = await client.get("/")
        assert r.status_code == 404
        assert "not built" in r.json()["reason"].lower()

    @pytest.mark.asyncio
    async def test_existing_health_endpoint_unaffected(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] in ("ok", "degraded")


class TestStaticServing:
    @pytest.mark.asyncio
    async def test_spa_root_served(self, static_client):
        r = await static_client.get("/")
        assert r.status_code == 200
        assert "Groomarr" in r.text
        assert r.headers.get("cache-control") == "no-cache"

    @pytest.mark.asyncio
    async def test_spa_client_route_served(self, static_client):
        # An unknown non-API path should serve the SPA shell (client-side routing)
        r = await static_client.get("/simulator")
        assert r.status_code == 200
        assert "Groomarr" in r.text

    @pytest.mark.asyncio
    async def test_asset_served(self, static_client):
        r = await static_client.get("/assets/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text


# =============================================================================
# Audit regression tests (broken / flat config files)
# =============================================================================

BROKEN_YAML = (
    "global:\n"
    "  replace_patterns:\n"  # wrong type: a list instead of a mapping
    "    - foo\n"
    "    - bar\n"
    "  qualities_exclude: CAM\n"  # wrong type: a scalar instead of a list
)

FLAT_YAML = "qualities_exclude:\n  - CAM\nprefix: '[X] '\n"


@contextlib.contextmanager
def _app_with_rules(yaml_text: str, tmp_path):
    rf = tmp_path / "rename_rules.yaml"
    rf.write_text(yaml_text, encoding="utf-8")
    no_static = tmp_path / "no_static"
    with patch.dict(os.environ, _env(rf, no_static), clear=False):
        from src import config

        reload(config)
        from src import main

        reload(main)
        main.qbit_client = _make_qbit_mock()
        yield main.app, rf


class TestAuditRegressions:
    @pytest.mark.asyncio
    async def test_get_config_survives_broken_file(self, tmp_path):
        with _app_with_rules(BROKEN_YAML, tmp_path) as (app, _rf):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.get("/api/config")
        assert r.status_code == 200, r.text
        data = r.json()
        # Malformed fields are coerced to safe empties so the UI can load and fix them
        assert data["config"]["global"]["replace_patterns"] == {}
        assert data["config"]["global"]["qualities_exclude"] == []

    @pytest.mark.asyncio
    async def test_put_flat_config_warns_about_format_migration(self, tmp_path):
        with _app_with_rules(FLAT_YAML, tmp_path) as (app, _rf):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.put("/api/config", json={"global": {"prefix": "[X] "}, "trackers": []})
        assert r.status_code == 200, r.text
        assert any("flat-format" in w for w in r.json()["warnings"])
