"""Kiểm thử listing-watcher bằng GitHub giả lập. Chạy: python -m unittest -v"""
import re
import unittest

import watcher

SAMPLE_DIFF = """\
diff --git a/modules/statics/src/base.ts b/modules/statics/src/base.ts
--- a/modules/statics/src/base.ts
+++ b/modules/statics/src/base.ts
@@ -2083,6 +2083,7 @@ export enum UnderlyingAsset {
   'hoodeth:skhy' = 'hoodeth:skhy',
+  'hoodeth:cashcat' = 'hoodeth:cashcat',
   'hemieth:hemi' = 'hemieth:hemi',
@@ -3108,6 +3109,7 @@ export enum UnderlyingAsset {
+  'polygon:infra' = 'polygon:infra',
diff --git a/modules/statics/src/coins/ofcHoodethTokens.ts b/modules/statics/src/coins/ofcHoodethTokens.ts
--- a/modules/statics/src/coins/ofcHoodethTokens.ts
+++ b/modules/statics/src/coins/ofcHoodethTokens.ts
@@ -10,6 +10,7 @@
+    'ofchoodeth:cashcat',
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
+Example: 'hoodeth:notatoken'
"""


def repo_cfg(**over):
    cfg = {
        "name": "BitGo/BitGoJS",
        "path_globs": ["modules/statics/src/*"],
        "identifier_regex": watcher.DEFAULT_IDENTIFIER_REGEX,
        "watch_chains": ["hoodeth"],
        "alert_other_chains": True,
    }
    cfg.update(over)
    cfg["_regex"] = re.compile(cfg["identifier_regex"])
    return cfg


class FakeGitHub:
    """Trả về dữ liệu dựng sẵn thay cho GitHub API."""

    def __init__(self):
        self.prs = []
        self.commits = []
        self.diffs = {}
        self.calls = []

    def get_json(self, path, use_etag=False):
        self.calls.append(path)
        if "/pulls?" in path:
            return self.prs
        if "/commits?" in path:
            return self.commits
        raise AssertionError(path)

    def get_diff(self, path):
        self.calls.append(path)
        return self.diffs[path]

    def add_pr(self, num, sha, diff, title="t"):
        self.prs = [p for p in self.prs if p["number"] != num]
        self.prs.append({"number": num, "head": {"sha": sha}, "title": title,
                         "html_url": f"https://github.com/BitGo/BitGoJS/pull/{num}"})
        self.diffs[f"/repos/BitGo/BitGoJS/pulls/{num}"] = diff

    def add_commit(self, sha, diff, message="m"):
        self.commits.insert(0, {"sha": sha, "commit": {"message": message},
                                "html_url": f"https://github.com/BitGo/BitGoJS/commit/{sha}"})
        self.diffs[f"/repos/BitGo/BitGoJS/commits/{sha}"] = diff


def one_line_diff(identifier, path="modules/statics/src/base.ts"):
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,1 +1,2 @@\n+  '{identifier}' = '{identifier}',\n")


class ExtractTests(unittest.TestCase):
    def test_extract_filters_paths_and_merges_ofc(self):
        hits = watcher.extract(SAMPLE_DIFF, repo_cfg())
        self.assertEqual(set(hits), {"hoodeth:cashcat", "polygon:infra"})
        self.assertEqual(len(hits["hoodeth:cashcat"]), 2)

    def test_removed_lines_and_urls_ignored(self):
        diff = ("diff --git a/modules/statics/src/a.ts b/modules/statics/src/a.ts\n"
                "--- a/modules/statics/src/a.ts\n+++ b/modules/statics/src/a.ts\n"
                "@@ -1,2 +1,2 @@\n-  'hoodeth:old' = 'hoodeth:old',\n"
                "+  url: 'https://example.com/x',\n")
        self.assertEqual(watcher.extract(diff, repo_cfg()), {})

    def test_alert_priority(self):
        hits = watcher.extract(SAMPLE_DIFF, repo_cfg())
        src = {"kind": "PR", "ref": "#1", "title": "x", "url": "u"}
        text = watcher.build_alert(repo_cfg(), hits, src)
        self.assertTrue(text.startswith("🔴 ƯU TIÊN CAO · hoodeth:cashcat"))
        self.assertIn("polygon:infra", text)
        only_other = {"polygon:infra": {"f"}}
        self.assertIsNone(watcher.build_alert(
            repo_cfg(alert_other_chains=False), only_other, src))
        self.assertTrue(watcher.build_alert(repo_cfg(), only_other, src).startswith("⚪"))


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.cfg = {"repos": [repo_cfg()]}
        self.state = {}
        self.notifier = watcher.Notifier(None, None)

    def cycle(self):
        return watcher.run_cycle(self.gh, self.cfg, self.state, self.notifier)

    def baseline(self):
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertTrue(self.notifier.sent[0].startswith("✅ Bắt đầu theo dõi"))
        self.notifier.sent.clear()

    def test_baseline_is_silent_then_new_pr_alerts_once(self):
        self.gh.add_pr(1, "aaa", one_line_diff("hoodeth:existing"))
        self.gh.add_commit("c0", one_line_diff("hoodeth:history"))
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 1)         # lần đầu: chỉ một tin tóm tắt
        self.assertIn("hoodeth:existing", self.notifier.sent[0])
        self.assertNotIn("ƯU TIÊN CAO", self.notifier.sent[0])
        self.notifier.sent.clear()
        self.assertNotIn("/repos/BitGo/BitGoJS/commits/c0", self.gh.calls)

        self.gh.add_pr(2, "bbb", SAMPLE_DIFF, title="onboard hoodeth:cashcat")
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("hoodeth:cashcat", self.notifier.sent[0])
        self.assertIn("pull/2", self.notifier.sent[0])

        self.cycle()                                         # không báo lặp
        self.assertEqual(len(self.notifier.sent), 1)

    def test_pr_update_alerts_only_new_identifiers(self):
        self.baseline()
        self.gh.add_pr(5, "s1", one_line_diff("hoodeth:one"))
        self.cycle()
        self.gh.add_pr(5, "s2", one_line_diff("hoodeth:one") + one_line_diff("hoodeth:two"))
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 2)
        self.assertIn("hoodeth:two", self.notifier.sent[1])
        self.assertNotIn("hoodeth:one", self.notifier.sent[1])

    def test_direct_commit_alerts_and_merge_of_seen_pr_does_not(self):
        self.baseline()
        self.gh.add_commit("c1", one_line_diff("hoodeth:direct"), "feat: direct push")
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("commit c1", self.notifier.sent[0])

        self.gh.add_pr(9, "p1", one_line_diff("hoodeth:viapr"))
        self.cycle()
        self.gh.add_commit("c2", one_line_diff("hoodeth:viapr"), "merge")
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 2)         # merge không báo lại

    def test_failed_delivery_is_retried_next_cycle(self):
        self.baseline()
        real_send, fail = self.notifier.send, [True]
        def flaky(text):
            real_send(text)
            return not fail[0]
        self.notifier.send = flaky
        self.gh.add_pr(3, "x1", one_line_diff("hoodeth:late"))
        self.gh.add_commit("c9", one_line_diff("hoodeth:latecommit"))
        self.cycle()
        rs = self.state["repos"]["BitGo/BitGoJS"]
        self.assertNotIn("hoodeth:late", rs["seen"])
        self.assertNotIn("3", rs["pr_heads"])
        self.assertNotIn("c9", rs["commits"])
        fail[0] = False
        self.notifier.sent.clear()
        self.cycle()
        self.assertEqual(len(self.notifier.sent), 2)
        self.assertIn("hoodeth:late", rs["seen"])
        self.assertIn("c9", rs["commits"])
        self.notifier.sent.clear()
        self.cycle()
        self.assertEqual(self.notifier.sent, [])

    def test_failed_baseline_message_repeats_baseline(self):
        self.notifier.send = lambda text: False
        self.cycle()
        self.assertFalse(self.state["repos"]["BitGo/BitGoJS"]["baselined"])

    def test_cycle_reports_errors(self):
        self.gh.get_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertEqual(self.cycle(), 1)


if __name__ == "__main__":
    unittest.main()
