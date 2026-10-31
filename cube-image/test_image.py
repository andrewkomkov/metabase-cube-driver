#!/usr/bin/env python3
"""Tests of the decisions in image.py, with local git repositories and no network. Run: python3 cube-image/test_image.py"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import image


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(path, name, text, message):
    (Path(path) / name).parent.mkdir(parents=True, exist_ok=True)
    (Path(path) / name).write_text(text)
    git(path, "add", "-A")
    git(path, "commit", "-qm", message)
    return git(path, "rev-parse", "HEAD")


class Fixture(unittest.TestCase):
    """cube: master with a tag, a pull request ref, a merged pull request and a closed one. fork: the queue branch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cube = root / "cube"
        self.fork = root / "fork"
        for repo in (self.cube, self.fork):
            repo.mkdir()
        git(self.cube, "init", "-q", "-b", "master")
        git(self.cube, "config", "user.name", "t")
        git(self.cube, "config", "user.email", "t@t")
        git(self.cube, "config", "uploadpack.allowAnySHA1InWant", "true")
        git(self.cube, "config", "uploadpack.allowFilter", "true")
        base = commit(self.cube, "a.txt", "1\n2\n3\n", "base")
        commit(self.cube, "store/b.txt", "one\n", "second")
        git(self.cube, "tag", "v1.0.0")
        # A merged pull request that the tag v1.0.1 has.
        git(self.cube, "checkout", "-q", "-b", "merged", "v1.0.0")
        self.merged_head = commit(self.cube, "m.txt", "merged\n", "merged work")
        git(self.cube, "checkout", "-q", "master")
        self.merge_sha = commit(self.cube, "m.txt", "merged\n", "squash of merged work")
        git(self.cube, "tag", "v1.0.1")
        # An open pull request from a branch off the first tag.
        git(self.cube, "checkout", "-q", "-b", "pr", "v1.0.0")
        self.pr_head = commit(self.cube, "a.txt", "1\n2 changed\n3\n", "pr work")
        git(self.cube, "update-ref", "refs/pull/7/head", self.pr_head)
        git(self.cube, "update-ref", "refs/pull/8/head", self.merged_head)
        # A pull request that conflicts with the tag.
        git(self.cube, "checkout", "-q", "-b", "other", base)
        self.bad_head = commit(self.cube, "store/b.txt", "conflict\n", "other side")
        git(self.cube, "update-ref", "refs/pull/9/head", self.bad_head)
        git(self.cube, "checkout", "-q", "master")
        # The queue branch: one commit on the Cube store path.
        git(root, "clone", "-q", str(self.cube), str(self.fork / "w"))
        self.fork = self.fork / "w"
        git(self.fork, "config", "user.name", "t")
        git(self.fork, "config", "user.email", "t@t")
        git(self.fork, "checkout", "-q", "-b", "mcd/patch-queue", "origin/master")
        self.held = commit(self.fork, "store/c.txt", "held\n", "held work")
        self.source = image.Source(root / "src.git", cube_url=str(self.cube), fork_url=str(self.fork))
        self.source.init()

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, tag, patches, states=None):
        self.source.fetch_tag(tag)
        self.source.fetch_branch("mcd/patch-queue")
        states = states or {}

        def lookup(number):
            return states.get(number, {"state": "open", "merged": False})

        return image.apply_queue(self.source, tag, patches, Path(self.tmp.name) / "out", lookup)

    def test_applies_a_pull_request_and_a_held_commit(self):
        result = self.apply("v1.0.0", [
            {"id": "open", "pr": "https://github.com/x/y/pull/7"},
            {"id": "held", "pr": None, "branch": "b", "commit": self.held},
        ])
        self.assertEqual([p["id"] for p in result["applied"]], ["open", "held"])
        self.assertEqual(result["failures"], [])
        self.assertFalse(result["cubestore"], "the path is store/, not rust/cubestore/")
        diff = (Path(self.tmp.name) / "out" / "queue.diff").read_text()
        self.assertIn("2 changed", diff)
        self.assertIn("store/c.txt", diff)

    def test_skips_a_pull_request_that_the_tag_has(self):
        states = {8: {"state": "closed", "merged": True, "merge_commit_sha": self.merge_sha}}
        patches = [{"id": "m", "pr": "https://github.com/x/y/pull/8"}]
        self.assertEqual(self.apply("v1.0.1", patches, states)["released"], ["m"])

    def test_applies_a_merged_pull_request_that_the_tag_does_not_have(self):
        states = {8: {"state": "closed", "merged": True, "merge_commit_sha": self.merge_sha}}
        result = self.apply("v1.0.0", [{"id": "m", "pr": "https://github.com/x/y/pull/8"}], states)
        self.assertEqual(result["released"], [])
        self.assertEqual([p["id"] for p in result["applied"]], ["m"])

    def test_uses_the_fallback_when_a_pull_request_closes_with_no_merge(self):
        states = {7: {"state": "closed", "merged": False}}
        result = self.apply("v1.0.0", [{"id": "p", "pr": "https://github.com/x/y/pull/7", "branch": "b", "fallback_branch": "fb", "fallback_commit": self.held}], states)
        self.assertEqual(result["applied"][0]["source"], self.held)

    def test_reports_a_conflict_with_its_error(self):
        result = self.apply("v1.0.0", [
            {"id": "open", "pr": "https://github.com/x/y/pull/7"},
            {"id": "bad", "pr": None, "diff_file": "test-bad.patch"},
        ])
        self.assertEqual([f["cause"] for f in result["failures"]], ["patch-bad"])
        self.assertIn("package.json", result["failures"][0]["error"])
        self.assertEqual([p["id"] for p in result["applied"]], ["open"])

    def test_same_input_gives_the_same_tree(self):
        patches = [{"id": "open", "pr": "https://github.com/x/y/pull/7"}]
        self.assertEqual(self.apply("v1.0.0", patches)["tree"], self.apply("v1.0.0", patches)["tree"])

    def test_excluded_path_stays_out(self):
        result = self.apply("v1.0.0", [{"id": "open", "pr": "https://github.com/x/y/pull/7", "exclude": ["a.txt"]}])
        self.assertEqual(result["applied"], [])
        self.assertIn("no change", result["notes"][0])


class Decisions(unittest.TestCase):
    FP = "f" * 64

    def decide(self, **kw):
        values = {"applied": [{"id": "a"}], "failures": [], "queue_left": [{"id": "a"}], "labels_of_last": {}, "fingerprint_value": self.FP, "candidate_exists": False, "retry_blocked": False}
        values.update(kw)
        return image.decide(**values)

    def test_action(self):
        self.assertEqual(self.decide(), "build")
        self.assertEqual(self.decide(candidate_exists=True), "gate")
        self.assertEqual(self.decide(labels_of_last={"mcd.fingerprint": self.FP}), "skip")
        self.assertEqual(self.decide(failures=[{"cause": "x"}]), "fail")
        self.assertEqual(self.decide(queue_left=[], applied=[]), "retire")
        self.assertEqual(self.decide(retry_blocked=True), "blocked")

    def test_new_input_with_an_old_image_builds(self):
        self.assertEqual(self.decide(labels_of_last={"mcd.fingerprint": "0" * 64}), "build")

    def test_fingerprint_changes_with_each_input(self):
        one = image.fingerprint("t", ["amd64"], "r")
        self.assertEqual(one, image.fingerprint("t", ["amd64"], "r"))
        self.assertNotEqual(one, image.fingerprint("u", ["amd64"], "r"))
        self.assertNotEqual(one, image.fingerprint("t", ["amd64", "arm64"], "r"))
        self.assertNotEqual(one, image.fingerprint("t", ["amd64"], "s"))


class Tags(unittest.TestCase):
    def test_select(self):
        stable = ["v1.8.14", "v1.8.15", "v1.8.16", "v1.8.9"]
        self.assertEqual(image.select_tags(stable, set(), "v1.8.15"), ["v1.8.15", "v1.8.16"])
        self.assertEqual(image.select_tags(stable, {"v1.8.15"}, "v1.8.15"), ["v1.8.16"])
        self.assertEqual(image.select_tags(stable, {"v1.8.16"}, "v1.8.15"), ["v1.8.16"], "the newest tag stays, so a change of the queue reaches it")
        many = [f"v1.9.{n}" for n in range(9)]
        self.assertEqual(len(image.select_tags(many, set(), "v1.9.0")), image.MAX_NEW_TAGS)

    def test_published(self):
        tags = ["v1.8.15-mcd.2", "v1.8.15-mcd.10", "v1.8.15-mcd-candidate.abc", "v1.8.16-mcd.1", "buildcache-x"]
        self.assertEqual(image.published_tags(tags, "v1.8.15"), [(2, "v1.8.15-mcd.2"), (10, "v1.8.15-mcd.10")])
        self.assertEqual(image.built_cube_tags(tags), {"v1.8.15", "v1.8.16"})


class Gate(unittest.TestCase):
    def cell(self, passed, fails=()):
        features = [{"id": f"p{i}", "status": "pass"} for i in range(passed)] + [{"id": f, "status": "fail", "reason": "wrong"} for f in fails]
        return {"counts": {"pass": passed, "fail": len(fails), "total": len(features)}, "features": features, "coverage": "x"}

    def test_passes_with_no_fail_and_no_lower_count(self):
        self.assertEqual(image.judge(self.cell(10), self.cell(10)), [])
        self.assertEqual(image.judge(self.cell(11), self.cell(10)), [])

    def test_refuses(self):
        self.assertIn("f1", "\n".join(image.judge(self.cell(10, ["f1"]), self.cell(5))))
        self.assertIn("9 pass", image.judge(self.cell(9), self.cell(10))[0])
        self.assertTrue(image.judge(None, self.cell(10)))
        self.assertTrue(image.judge(self.cell(10), None))


class Queue(unittest.TestCase):
    def test_file_lists_the_required_patches(self):
        queue = image.load_queue()
        ids = [p["id"] for p in queue["patches"]]
        self.assertEqual(ids, ["B1", "datetime64", "interval", "M1-M2", "N1", "geo", "propagate", "gran", "m5", "N2", "big"])
        for patch in queue["patches"]:
            self.assertTrue(patch["branch"] and patch["finding"])
            self.assertIn("pr", patch)
            self.assertTrue(patch["pr"] or patch["commit"])
        for gone in ("extract", "m4", "B2"):
            self.assertNotIn(gone, ids)

    def test_a_save_keeps_the_content(self):
        queue = image.load_queue()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.json"
            image.save_queue(queue, path)
            self.assertEqual(json.loads(path.read_text()), queue)
            queue["patches"] = []
            image.save_queue(queue, path)
            self.assertEqual(json.loads(path.read_text())["patches"], [])


class Issues(unittest.TestCase):
    def test_marker(self):
        mark = image.read_marker(image.marker("gate", "v1.8.15", "abc.def") + "\ntext")
        self.assertEqual(mark, {"cause": "gate", "tag": "v1.8.15", "key": "abc.def"})
        self.assertIsNone(image.read_marker("no marker"))

    def test_error_text_comes_from_the_failed_step(self):
        lines = [f"line {n}" for n in range(200)] + ["error: real", "##[error]Process completed with exit code 1."]
        text = image.tail_error(lines)
        self.assertIn("error: real", text)
        self.assertNotIn("line 50\n", text)

    def test_compat_section_replaces_the_old_one(self):
        rows = [{"image": "i", "state": "candidate", "metabase": "m", "clickhouse": "c", "clickhouse_fixes": "on", "conformance": "success", "coverage": "x", "run_url": "u"}]
        once = image.merge_compat("# Compatibility\n", rows)
        self.assertEqual(once.count("## Cube image"), 1)
        self.assertEqual(image.merge_compat(once, rows).count("## Cube image"), 1)
        self.assertEqual(image.merge_compat(once, rows), once)


if __name__ == "__main__":
    unittest.main()
