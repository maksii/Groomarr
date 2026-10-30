"""Regressions found by auditing production renames and ukr-torznab's release catalogue.

Each case is a real title/file shape that was renamed wrongly (or not at all).
"""

import httpx
import pytest

from src import torznab
from src.config import TrackerRules
from src.rename import (
    evaluate_filters,
    insert_episode_into_name,
    sanitize_filename,
    validate_rename_plan,
)

TORZNAB_TITLE = (
    "The Exiled Heavy Knight Knows How to Game the System S01E01-17 1080p WEBRip H.265 "
    "MVO Japanese.Ukrainian subUKR RobotaGolosom-deeman"
)


def _bases(plan):
    return {old.rsplit("/", 1)[-1]: new.rsplit("/", 1)[-1] for old, new in plan}


class TestTitleEnvelopes:
    def test_torznab_range_without_second_e_collapses(self):
        # Live bug: every file became "S01E05-17", which Sonarr read as E05..E17.
        assert insert_episode_into_name(TORZNAB_TITLE, ("01", "E", "05")) == TORZNAB_TITLE.replace(
            "S01E01-17", "S01E05"
        )

    @pytest.mark.parametrize("tail", ["1080p WEB", "720p", "10bit x265"])
    def test_dash_glued_resolution_is_not_a_range(self, tail):
        title = f"Show S01E05-{tail}"
        assert insert_episode_into_name(title, ("01", "E", "05")) == title

    def test_season_followed_by_e_word(self):
        # "S01 extended" matched as "S0" -> "S0E041 extended".
        title = "Wildest Islands of Indonesia S01 extended cut 1080p WEB-DL"
        assert insert_episode_into_name(title, ("01", "E", "04")) == (
            "Wildest Islands of Indonesia S01E04 extended cut 1080p WEB-DL"
        )
        assert insert_episode_into_name("Show S02 English 1080p", ("01", "E", "03")) == (
            "Show S02E03 English 1080p"
        )

    def test_season_range_title_leaves_seasonless_files(self):
        # Was "Hell Mode S01E01-S02 ..." for every file of a 22-episode two-season pack.
        title = "Hell Mode S01-S02 (2026) WEBRip 1080p x265 Ukrainian-Deeman"
        files = [{"name": f"Pack/[RG] Hell Mode {i:02d}.mkv"} for i in range(1, 23)]
        plan, _ = validate_rename_plan(files, title, "Pack")
        assert all(old == new for old, new in plan)
        # Files that name their own season are still placed correctly.
        files = [{"name": "Pack/Show S01E03.mkv"}, {"name": "Pack/Show S02E04.mkv"}]
        names = _bases(validate_rename_plan(files, title, "Pack", preserve_folder=True)[0])
        assert names["Show S02E04.mkv"].startswith("Hell Mode S02E04 ")


class TestLength:
    def test_cap_keeps_release_group(self):
        # Live bug: a 250-char cap cut "-SubsPlease" to "-SubsPleas" (lost CF score).
        title = (
            "A "
            + "Very Long Title " * 15
            + "S01E11 JAPANESE 1080p CR WEB-DL AAC 2.0 H.264-SubsPlease"
        )
        name = sanitize_filename(title)
        assert len(name.encode()) <= 250
        assert name.endswith("S01E11 JAPANESE 1080p CR WEB-DL AAC 2.0 H.264-SubsPlease")

    def test_cap_counts_bytes(self):
        # Cyrillic is 2 bytes per char: 200 chars used to pass a 250-char cap.
        name = sanitize_filename("Серіал " * 30 + "S01 1080p WEB-DL-Group")
        assert len(name.encode()) <= 250
        assert name.endswith("S01 1080p WEB-DL-Group")

    def test_episode_insert_stays_within_cap(self):
        files = [{"name": f"F/x S01E0{i}.mkv"} for i in (1, 2)]
        plan, _ = validate_rename_plan(files, "T" * 240 + " S01 1080p", "F")
        assert all(len(new.rsplit("/", 1)[-1].encode()) <= 255 for _old, new in plan)


class TestTriggerFilters:
    def test_unknown_group_is_not_in_include_list(self):
        # Goki/TAoE releases with no parsed group were renamed by a SubsPlease-only rule.
        rules = TrackerRules()
        rules.release_groups_include = ["SubsPlease"]
        ok, reason = evaluate_filters(rules, release_group="")
        assert not ok and "not in include list" in reason
        assert evaluate_filters(rules, release_group="SubsPlease")[0]


class TestPlanner:
    def test_radarr_movie_gets_no_episode(self):
        files = [{"name": "doctor who s11e00 twice upon a time.mkv"}]
        plan, _ = validate_rename_plan(
            files, "Doctor Who Twice Upon a Time 2017 720p", None, movie=True
        )
        assert plan[0][1] == "Doctor Who Twice Upon a Time 2017 720p.mkv"

    def test_batch_sequence_beats_stray_number_in_episode_title(self):
        files = [
            {"name": "N/naruto 024 - kakashi.avi"},
            {"name": "N/naruto 025 - the last stand, question #10.avi"},
            {"name": "N/naruto 026 - special report.avi"},
        ]
        names = _bases(validate_rename_plan(files, "Naruto S01 480p DVD", "N")[0])
        assert "S01E025" in names["naruto 025 - the last stand, question #10.avi"]

    def test_outlier_does_not_sink_batch(self):
        # A promo "_2" next to episode "_002" made every file collapse (whole pack aborted).
        files = [{"name": f"C/el_clon_{i:03d}[x].avi"} for i in range(1, 11)]
        files.append({"name": "C/el_clon_promo_2[x].avi"})
        names = _bases(validate_rename_plan(files, "The Clone S01E01-59 480p", "C")[0])
        assert names["el_clon_005[x].avi"] == "The Clone S01E005 480p.avi"
        assert names["el_clon_promo_2[x].avi"] == "el_clon_promo_2[x].avi"

    def test_guess_outside_grabbed_episodes_is_not_applied(self):
        # "naruto 036" in an "S01" pack is absolute (TVDB S02E01); Sonarr grabbed S01E01-35.
        files = [{"name": f"N/naruto {i:03d} - x.avi"} for i in range(30, 40)]
        allowed = {(1, e) for e in range(1, 36)}
        names = _bases(
            validate_rename_plan(files, "Naruto S01 480p", "N", allowed_episodes=allowed)[0]
        )
        assert names["naruto 035 - x.avi"] == "Naruto S01E035 480p.avi"
        assert names["naruto 036 - x.avi"] == "naruto 036 - x.avi"

    def test_indexer_file_map_wins(self):
        files = [
            {"name": "Pack/ep13 ova.mkv"},
            {"name": "Pack/ep01.mkv"},
            {"name": "Pack/v1 ep02.mkv"},
            {"name": "Pack/v2 ep02.mkv"},
            {"name": "Pack/extra.mkv"},
        ]
        names_map = {
            "pack/ep13 ova.mkv": "Show S00E01 720p-grp.mkv",
            "ep01.mkv": "Show S01E01 720p-grp.mkv",  # basename key also accepted
            "pack/v1 ep02.mkv": "Show S01E02 720p-grp.mkv",
            "pack/v2 ep02.mkv": "Show S01E02 720p-grp.mkv",  # two cuts of one episode
        }
        plan, warnings = validate_rename_plan(
            files, "Show S01 720p-grp", "Pack", preserve_folder=True, file_names=names_map
        )
        names = _bases(plan)
        assert names["ep13 ova.mkv"] == "Show S00E01 720p-grp.mkv"
        assert names["ep01.mkv"] == "Show S01E01 720p-grp.mkv"
        assert "v1 ep02.mkv" not in names and "extra.mkv" not in names  # left unchanged
        assert any("v1 ep02.mkv" in w for w in warnings)


class TestTorznabLookup:
    @pytest.fixture
    def serve(self, monkeypatch):
        seen = []
        real = httpx.AsyncClient

        def install(payload, status=200):
            def handler(request):
                seen.append(str(request.url))
                return httpx.Response(status, json=payload)

            monkeypatch.setattr(
                torznab.httpx,
                "AsyncClient",
                lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
            )
            return seen

        return install

    @pytest.mark.asyncio
    async def test_toloka_topic(self, serve):
        seen = serve(
            {"state": "ok", "release": {"episodes": [{"file": "A/B.mkv", "renamed": "X.mkv"}]}}
        )
        names = await torznab.fetch_file_names("http://tz:8000/", "https://toloka.to/t698730")
        assert names == {"a/b.mkv": "X.mkv"}
        assert seen == ["http://tz:8000/toloka/info/698730"]

    @pytest.mark.asyncio
    async def test_utp_and_unparsed(self, serve):
        seen = serve({"episodes": [{"file": "b.mkv", "renamed": "Y.mkv"}]})
        assert await torznab.fetch_file_names("http://tz", "https://utp.to/torrents/31641") == {
            "b.mkv": "Y.mkv"
        }
        assert seen == ["http://tz/utp/info/31641"]
        serve({"state": "pending"})
        assert await torznab.fetch_file_names("http://tz", "https://toloka.to/t1") is None

    @pytest.mark.asyncio
    async def test_other_indexer_or_error(self, serve):
        serve({}, status=500)
        assert await torznab.fetch_file_names("http://tz", "https://aither.cc/torrents/1") is None
        assert await torznab.fetch_file_names("http://tz", "https://toloka.to/t1") is None


class TestKeepOriginalInfo:
    """Indexer titles drop what the original names carried (retags, AMZN, audio, subs)."""

    def test_carry_tokens_only_missing_categories(self):
        from src.rename import carry_tokens

        title = "Backrooms (2026) WEB-DL 1080p Ukrainian-Kolodii_Treileriv"
        original = "Backrooms.2026.1080p.AMZN.WEB-DL.DD+5.1.H.264-playWEB"
        assert carry_tokens(title, original) == (
            "Backrooms (2026) WEB-DL 1080p Ukrainian AMZN DD+5.1 H.264-Kolodii_Treileriv"
        )
        # The title already names a service and codec: nothing of those categories is added.
        assert carry_tokens("Show S01E01 NF WEB-DL x265-G", "show.s01e01.amzn.h264") == (
            "Show S01E01 NF WEB-DL x265-G"
        )

    def test_subtitle_phrase_carried_whole(self):
        from src.rename import carry_tokens

        out = carry_tokens(
            "Yani Neko S01E01 (2026) WEB-DL 1080p x265 Ukrainian-GlassMoon",
            "[GM] Yani Neko - s01e01 (WEBDL 1080p HEVC AAC) Ukr DVO SUB",
        )
        assert (
            out == "Yani Neko S01E01 (2026) WEB-DL 1080p x265 Ukrainian AAC Ukr DVO SUB-GlassMoon"
        )

    def test_with_group(self):
        from src.rename import with_group

        assert with_group("Crime 101 (2026) Remux 1080p-Neo_Men", "Hurtom") == (
            "Crime 101 (2026) Remux 1080p-Hurtom"
        )
        assert with_group("No Group 1080p", "X") == "No Group 1080p-X"

    @pytest.mark.asyncio
    async def test_score_decides_original_group(self, monkeypatch):
        from src import main
        from src.arrapi import ParseResult

        scores = {"Hurtom": 8800, "Neo_Men": 8000}

        class Arr:
            async def parse_title(self, name):
                group = name.rsplit("-", 1)[-1].rsplit(".", 1)[0]
                if "[Hurtom]" in name:
                    group = "Hurtom"
                return ParseResult(
                    title=name,
                    custom_format_score=scores.get(group, 0),
                    custom_formats=[group],
                    parsed_title=None,
                    release_group=group,
                    quality="Remux-1080p",
                    matched=True,
                )

        monkeypatch.setattr(main, "radarr_client", Arr())
        files = [{"name": "Crime 101 (2026) BDRemux 1080p Ukr Eng [Hurtom].mkv"}]
        names = await main._enrich_file_names(
            "radarr",
            files,
            "Crime 101 (2026) BluRay Remux 1080p Ukrainian-Neo_Men",
            True,
            None,
            None,
        )
        assert names == {
            "crime 101 (2026) bdremux 1080p ukr eng [hurtom].mkv": (
                "Crime 101 (2026) BluRay Remux 1080p Ukrainian Eng-Hurtom.mkv"
            )
        }
        # A lower-scoring original group is not swapped in.
        scores["Hurtom"] = 0
        names = await main._enrich_file_names(
            "radarr",
            files,
            "Crime 101 (2026) BluRay Remux 1080p Ukrainian-Neo_Men",
            True,
            None,
            None,
        )
        assert names[next(iter(names))].endswith("-Neo_Men.mkv")
