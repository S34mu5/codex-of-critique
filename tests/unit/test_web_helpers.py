from datetime import datetime, timedelta, timezone
import unittest

from app import web


class TestParseRepoIdentifiers(unittest.TestCase):
    def test_dedupes_and_trims(self):
        result = web._parse_repo_identifiers(
            " Frakt24/frakt24 , Frakt24/fleetx-mobile-app , Frakt24/frakt24 "
        )

        self.assertEqual(result, [
            "Frakt24/frakt24",
            "Frakt24/fleetx-mobile-app",
        ])

    def test_rejects_invalid_format(self):
        with self.assertRaisesRegex(ValueError, "Invalid repository format"):
            web._parse_repo_identifiers("Frakt24")


class TestResolveSelectedRepositories(unittest.TestCase):
    def test_rejects_unknown_repositories(self):
        original = web._configured_repositories
        web._configured_repositories = lambda: [
            {"owner": "Frakt24", "repo": "frakt24", "id": "Frakt24/frakt24"}
        ]
        try:
            with self.assertRaisesRegex(ValueError, "Unknown repositories requested"):
                web._resolve_selected_repositories("Frakt24/missing")
        finally:
            web._configured_repositories = original

    def test_returns_configured_repositories_only(self):
        original = web._configured_repositories
        web._configured_repositories = lambda: [
                {"owner": "Frakt24", "repo": "frakt24", "id": "Frakt24/frakt24"},
                {
                    "owner": "Frakt24",
                    "repo": "fleetx-mobile-app",
                    "id": "Frakt24/fleetx-mobile-app",
                },
            ]
        try:
            result = web._resolve_selected_repositories(
                "Frakt24/fleetx-mobile-app,Frakt24/frakt24"
            )
        finally:
            web._configured_repositories = original

        self.assertEqual(result, [
            {"owner": "Frakt24", "repo": "fleetx-mobile-app"},
            {"owner": "Frakt24", "repo": "frakt24"},
        ])


class TestAddRepoFilterConditions(unittest.TestCase):
    def test_adds_include_and_exclude_clauses(self):
        conditions = []
        params = {}

        web._add_repo_filter_conditions(
            conditions,
            params,
            alias="rp",
            include_repositories="Frakt24/frakt24",
            exclude_repositories="Frakt24/fleetx-mobile-app",
            prefix="search_repo",
        )

        self.assertEqual(conditions, [
            "((rp.owner = :search_repo_include_owner_0 AND rp.name = :search_repo_include_name_0))",
            "NOT ((rp.owner = :search_repo_exclude_owner_0 AND rp.name = :search_repo_exclude_name_0))",
        ])
        self.assertEqual(params, {
            "search_repo_include_owner_0": "Frakt24",
            "search_repo_include_name_0": "frakt24",
            "search_repo_exclude_owner_0": "Frakt24",
            "search_repo_exclude_name_0": "fleetx-mobile-app",
        })

    def test_supports_legacy_repo_name(self):
        conditions = []
        params = {}

        web._add_repo_filter_conditions(
            conditions,
            params,
            alias="rp",
            legacy_repo="frakt24",
            prefix="legacy_repo",
        )

        self.assertEqual(conditions, ["rp.name = :legacy_repo_legacy_name"])
        self.assertEqual(params, {"legacy_repo_legacy_name": "frakt24"})


class TestParseFilterValues(unittest.TestCase):
    def test_dedupes_and_trims(self):
        result = web._parse_filter_values(
            " fredrikborgstein , greptile-apps, fredrikborgstein "
        )

        self.assertEqual(result, [
            "fredrikborgstein",
            "greptile-apps",
        ])


class TestAddValueFilterConditions(unittest.TestCase):
    def test_adds_include_and_exclude_clauses(self):
        conditions = []
        params = {}

        web._add_value_filter_conditions(
            conditions,
            params,
            column="pr.author_login",
            include_values="fredrikborgstein,FranBas6",
            exclude_values="greptile-apps",
            prefix="search_pr_author",
        )

        self.assertEqual(conditions, [
            "(pr.author_login = :search_pr_author_include_0 OR pr.author_login = :search_pr_author_include_1)",
            "NOT (pr.author_login = :search_pr_author_exclude_0)",
        ])
        self.assertEqual(params, {
            "search_pr_author_include_0": "fredrikborgstein",
            "search_pr_author_include_1": "FranBas6",
            "search_pr_author_exclude_0": "greptile-apps",
        })

    def test_supports_legacy_value(self):
        conditions = []
        params = {}

        web._add_value_filter_conditions(
            conditions,
            params,
            column="rc.comment_author_login",
            legacy_value="greptile-apps",
            prefix="search_reviewer",
        )

        self.assertEqual(
            conditions,
            ["(rc.comment_author_login = :search_reviewer_include_0)"],
        )
        self.assertEqual(
            params,
            {"search_reviewer_include_0": "greptile-apps"},
        )


class TestIsoUtc(unittest.TestCase):
    def test_assumes_naive_datetimes_are_utc(self):
        result = web._iso_utc(datetime(2026, 3, 22, 19, 15, 0))

        self.assertEqual(result, "2026-03-22T19:15:00Z")

    def test_normalizes_aware_datetimes_to_utc(self):
        local_time = datetime(
            2026,
            3,
            22,
            21,
            15,
            0,
            tzinfo=timezone(timedelta(hours=2)),
        )

        result = web._iso_utc(local_time)

        self.assertEqual(result, "2026-03-22T19:15:00Z")


class TestNextSyncAt(unittest.TestCase):
    def test_returns_next_future_cron_tick_from_now(self):
        original_cron = web.settings.sync_cron
        web.settings.sync_cron = "*/15 * * * *"
        try:
            result = web._next_sync_at(datetime(2026, 3, 22, 19, 46, 41))
        finally:
            web.settings.sync_cron = original_cron

        self.assertEqual(result, "2026-03-22T20:00:00Z")


if __name__ == "__main__":
    unittest.main()
