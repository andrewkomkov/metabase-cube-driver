#!/usr/bin/env python3
"""Keeps the Cube image with our fixes current. The workflows .github/workflows/cube-image*.yml call this file.

See docs/cube-image.md. Run with --help for the subcommands.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUEUE = HERE / "queue.json"
RECIPE = ["image.tail.Dockerfile", "build-native.sh"]
CUBE_REPO = "cube-js/cube"
FORK_REPO = "andrewkomkov/cube"
IMAGE_REPO = "andrewkomkov/cube"
IMAGE = f"ghcr.io/{IMAGE_REPO}"
STABLE = re.compile(r"^v\d+\.\d+\.\d+$")
LABEL = "cube-image"
MAX_NEW_TAGS = 3
MAX_ERROR = 40000
RETIRE_TEXT = (
    "The patch queue of the Cube image is empty: cube-js/cube has released each fix.\n\n"
    "The image `ghcr.io/andrewkomkov/cube` can retire, and users can go back to stock `cubejs/cube`. "
    "See [docs/cube-image.md](https://github.com/andrewkomkov/metabase-cube-driver/blob/main/docs/cube-image.md)."
)


def version_key(tag):
    return tuple(int(part) for part in re.findall(r"\d+", tag))


def run(*args, cwd=None, check=True, env=None):
    return subprocess.run(args, cwd=cwd, check=check, env=env, capture_output=True, text=True).stdout.strip()


def set_outputs(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    for key, value in values.items():
        print(f"{key}={value}")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for key, value in values.items():
                f.write(f"{key}={value}\n")


def summary(text):
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def load_queue(path=QUEUE):
    return json.loads(Path(path).read_text())


def save_queue(queue, path=QUEUE):
    # One patch for each line, so that a removal is one line in the diff.
    patches = ",\n".join("    " + json.dumps(patch) for patch in queue["patches"])
    head = {key: value for key, value in queue.items() if key != "patches"}
    lines = [f"  {json.dumps(key)}: {json.dumps(value)}," for key, value in head.items()]
    body = "\n".join(lines) + '\n  "patches": [' + (f"\n{patches}\n  " if patches else "") + "]"
    Path(path).write_text("{\n" + body + "\n}\n")


def pr_number(patch):
    url = patch.get("pr")
    return int(url.rstrip("/").rsplit("/", 1)[1]) if url else None


class Registry:
    """Reads ghcr.io with the token of the run. A package that does not exist reads as no tags."""

    def __init__(self, repo=IMAGE_REPO, token=None):
        self.repo = repo
        self.token = token if token is not None else os.environ.get("GH_TOKEN", "")
        self._bearer = None

    def _get(self, url, accept=None):
        headers = {"Authorization": f"Bearer {self._bearer}"} if self._bearer else {}
        if accept:
            headers["Accept"] = accept
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
            return json.load(response)

    def _login(self):
        request = urllib.request.Request(f"https://ghcr.io/token?service=ghcr.io&scope=repository:{self.repo}:pull")
        if self.token:
            basic = base64.b64encode(f"x:{self.token}".encode()).decode()
            request.add_header("Authorization", f"Basic {basic}")
        with urllib.request.urlopen(request) as response:
            self._bearer = json.load(response)["token"]

    def tags(self):
        try:
            self._login()
            return self._get(f"https://ghcr.io/v2/{self.repo}/tags/list?n=1000").get("tags") or []
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                return []
            raise

    def labels(self, tag):
        if not self._bearer:
            self._login()
        accept = ",".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ])
        manifest = self._get(f"https://ghcr.io/v2/{self.repo}/manifests/{tag}", accept)
        if "manifests" in manifest:
            first = next(m for m in manifest["manifests"] if m.get("platform", {}).get("os") == "linux")
            manifest = self._get(f"https://ghcr.io/v2/{self.repo}/manifests/{first['digest']}", accept)
        config = self._get(f"https://ghcr.io/v2/{self.repo}/blobs/{manifest['config']['digest']}")
        return config.get("config", {}).get("Labels") or {}


def published_tags(tags, cube_tag):
    """Returns (n, tag) of the published images of this Cube tag, the newest last."""
    pattern = re.compile(re.escape(cube_tag) + r"-mcd\.(\d+)$")
    return sorted((int(m.group(1)), t) for t in tags if (m := pattern.match(t)))


def candidate_tag(cube_tag, fingerprint_value):
    return f"{cube_tag}-mcd-candidate.{fingerprint_value[:12]}"


def built_cube_tags(tags):
    return {m.group(1) for t in tags if (m := re.match(r"^(v\d+\.\d+\.\d+)-mcd[.-]", t))}


def stock_image_exists(tag):
    url = f"https://hub.docker.com/v2/repositories/cubejs/cube/tags/{tag}"
    try:
        with urllib.request.urlopen(url):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def select_tags(stable, built, first_tag):
    """The tags after the newest tag with an image, or else the newest tag, so that a change of the queue reaches it."""
    candidates = sorted((t for t in stable if version_key(t) >= version_key(first_tag)), key=version_key)
    if not candidates:
        return []
    last_built = max(built, key=version_key) if built else None
    new = [t for t in candidates if last_built is None or version_key(t) > version_key(last_built)]
    return (new or candidates[-1:])[-MAX_NEW_TAGS:]


def plan(args):
    queue = load_queue()
    platforms = args.platforms or ",".join(queue["platforms"])
    if args.tag:
        tags = [args.tag]
    else:
        lines = run("git", "ls-remote", "--tags", "--refs", f"https://github.com/{CUBE_REPO}.git").splitlines()
        stable = [line.split("refs/tags/")[1] for line in lines if STABLE.match(line.split("refs/tags/")[1])]
        # The stock image comes some time after the tag, and the gate needs that image.
        tags = [t for t in select_tags(stable, built_cube_tags(Registry().tags()), queue["first_tag"]) if stock_image_exists(t)]
    summary(f"Tags to process: {', '.join(tags) or 'none'}. Platforms: {platforms}.")
    set_outputs(tags=json.dumps(tags), platforms=json.dumps(platforms.split(",")))


class Source:
    """A partial git clone with the tag, master, the pull requests and the queue branch. It reads a blob only when needed."""

    def __init__(self, path, cube_url=None, fork_url=None):
        self.path = str(path)
        self.cube_url = cube_url or f"https://github.com/{CUBE_REPO}.git"
        self.fork_url = fork_url or f"https://github.com/{FORK_REPO}.git"

    def git(self, *args, check=True, env=None):
        return run("git", "-C", self.path, *args, check=check, env=env)

    def init(self):
        Path(self.path).mkdir(parents=True, exist_ok=True)
        self.git("init", "-q")
        self.git("remote", "add", "origin", self.cube_url)
        self.git("remote", "add", "fork", self.fork_url)
        for key, value in (
            ("remote.origin.promisor", "true"),
            ("remote.origin.partialclonefilter", "blob:none"),
            ("remote.fork.promisor", "true"),
            ("remote.fork.partialclonefilter", "blob:none"),
            ("extensions.partialClone", "origin"),
            ("user.name", "cube-image"),
            ("user.email", "cube-image@localhost"),
        ):
            self.git("config", key, value)

    def fetch_tag(self, tag):
        self.git("fetch", "-q", "origin", "+refs/heads/master:refs/remotes/origin/master", f"+refs/tags/{tag}:refs/tags/{tag}")

    def fetch_pr(self, number):
        self.git("fetch", "-q", "origin", f"+refs/pull/{number}/head:refs/mcd/pr/{number}")
        return self.git("rev-parse", f"refs/mcd/pr/{number}")

    def fetch_branch(self, branch):
        self.git("fetch", "-q", "fork", f"+refs/heads/{branch}:refs/mcd/branch/{branch}")

    def contains(self, commit, tag):
        return subprocess.run(["git", "-C", self.path, "merge-base", "--is-ancestor", commit, f"refs/tags/{tag}"], capture_output=True).returncode == 0

    def has(self, commit):
        return subprocess.run(["git", "-C", self.path, "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True).returncode == 0

    def fetch_commit(self, commit):
        if not self.has(commit):
            self.git("fetch", "-q", "origin", commit)

    def diff(self, base, head, exclude=()):
        paths = [".", *[f":(exclude){p}" for p in exclude]]
        return subprocess.run(["git", "-C", self.path, "diff", "--binary", "--full-index", base, head, "--", *paths], check=True, capture_output=True).stdout

    def apply(self, patch, index_file):
        """Applies a patch to the index only. Returns the error text, or None."""
        env = {**os.environ, "GIT_INDEX_FILE": index_file}
        done = subprocess.run(["git", "-C", self.path, "apply", "--cached", "--3way", "--whitespace=nowarn", "-"], input=patch, env=env, capture_output=True)
        if done.returncode == 0:
            return None
        return (done.stderr + done.stdout).decode(errors="replace").strip() or f"git apply exited with {done.returncode}"


def github_pr(number):
    return json.loads(run("gh", "api", f"repos/{CUBE_REPO}/pulls/{number}"))


def resolve_patch(source, patch, tag, pr_lookup=github_pr):
    """Finds the diff of one patch. Returns a dict with status released or diff, and a note for the summary."""
    number = pr_number(patch)
    state = pr_lookup(number) if number else None
    if state and state.get("merged"):
        merge = state["merge_commit_sha"]
        source.fetch_commit(merge)
        if source.contains(merge, tag):
            return {"status": "released", "note": f"#{number} is merged and in {tag}"}
    if patch.get("diff_file"):
        return {"status": "diff", "diff": (HERE / patch["diff_file"]).read_bytes(), "source": patch["diff_file"], "note": "test patch"}
    exclude = patch.get("exclude", ())
    closed = state and state.get("state") == "closed" and not state.get("merged")
    if number and closed and patch.get("fallback_commit"):
        commit = patch["fallback_commit"]
        return {"status": "diff", "diff": source.diff(f"{commit}^", commit, exclude), "source": commit, "note": f"#{number} closed with no merge, so this is {patch['fallback_branch']}"}
    if number:
        head = source.fetch_pr(number)
        base = source.git("merge-base", head, "refs/remotes/origin/master")
        note = f"#{number} at {head[:9]}" + (" (closed with no merge)" if closed else "")
        return {"status": "diff", "diff": source.diff(base, head, exclude), "source": head, "note": note}
    commit = patch["commit"]
    return {"status": "diff", "diff": source.diff(f"{commit}^", commit, exclude), "source": commit, "note": f"{patch['branch']} at {commit[:9]}"}


def touches_cubestore(diff):
    return re.search(rb"^diff --git a/rust/cubestore/", diff, re.MULTILINE) is not None


def recipe_hash():
    digest = hashlib.sha256()
    for name in RECIPE:
        digest.update((HERE / name).read_bytes())
    return digest.hexdigest()


def fingerprint(tree, platforms, recipe):
    return hashlib.sha256(f"{tree}\n{','.join(sorted(platforms))}\n{recipe}\n".encode()).hexdigest()


def apply_queue(source, tag, patches, out, pr_lookup=github_pr):
    """Applies the patches in order and writes queue.diff. Returns the result as data."""
    out = Path(out)
    with tempfile.TemporaryDirectory() as tmp:
        index = str(Path(tmp) / "index")
        env = {**os.environ, "GIT_INDEX_FILE": index}
        source.git("read-tree", f"refs/tags/{tag}", env=env)
        applied, released, failures, notes = [], [], [], []
        cubestore = False
        for patch in patches:
            try:
                found = resolve_patch(source, patch, tag, pr_lookup)
            except subprocess.CalledProcessError as e:
                failures.append({"cause": f"patch-{patch['id']}", "title": f"patch {patch['id']} cannot be read", "error": f"{' '.join(e.cmd)}\n{e.stderr}"})
                continue
            if found["status"] == "released":
                released.append(patch["id"])
                notes.append(f"| {patch['id']} | released | {found['note']} |")
                continue
            if not found["diff"].strip():
                notes.append(f"| {patch['id']} | no change | {found['note']} |")
                continue
            error = source.apply(found["diff"], index)
            if error:
                failures.append({"cause": f"patch-{patch['id']}", "title": f"patch {patch['id']} does not apply on {tag}", "error": error})
                notes.append(f"| {patch['id']} | **does not apply** | {found['note']} |")
                continue
            applied.append({"id": patch["id"], "finding": patch.get("finding"), "pr": patch.get("pr"), "source": found["source"]})
            notes.append(f"| {patch['id']} | applied | {found['note']} |")
            cubestore = cubestore or touches_cubestore(found["diff"])
        tree = source.git("write-tree", env=env)
    tag_tree = source.git("rev-parse", f"refs/tags/{tag}^{{tree}}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "queue.diff").write_bytes(source.diff(tag_tree, tree))
    return {"tree": tree, "applied": applied, "released": released, "failures": failures, "notes": notes, "cubestore": cubestore}


def open_issues():
    text = run("gh", "issue", "list", "--label", LABEL, "--state", "open", "--limit", "200", "--json", "number,title,body", "--repo", os.environ["GITHUB_REPOSITORY"])
    return json.loads(text)


def marker(cause, tag, key):
    return f"<!-- cube-image cause={cause} tag={tag} key={key} -->"


def read_marker(body):
    m = re.search(r"<!-- cube-image cause=(\S+) tag=(\S*) key=(\S*) -->", body or "")
    return {"cause": m.group(1), "tag": m.group(2), "key": m.group(3)} if m else None


def run_key(fingerprint_value, driver_sha):
    return f"{fingerprint_value[:12]}.{driver_sha[:12]}"


def decide(applied, failures, queue_left, labels_of_last, fingerprint_value, candidate_exists, retry_blocked):
    if not queue_left:
        return "retire"
    if failures:
        return "fail"
    if not applied:
        return "no-patches"
    if labels_of_last.get("mcd.fingerprint") == fingerprint_value:
        return "skip"
    if retry_blocked:
        return "blocked"
    return "gate" if candidate_exists else "build"


def prepare(args):
    queue = load_queue()
    patches = list(queue["patches"])
    platforms = json.loads(args.platforms)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.test_mode == "empty-queue":
        patches = []
    if args.test_mode == "bad-patch":
        patches.append({"id": "test-bad-patch", "finding": "test", "branch": "none", "pr": None, "diff_file": "test-bad.patch"})

    result = {"tree": "", "applied": [], "released": [], "failures": [], "notes": [], "cubestore": False}
    if patches:
        source = Source(Path(args.work) / "cube.git")
        source.init()
        source.fetch_tag(args.tag)
        source.fetch_branch(queue["patch_branch"])
        result = apply_queue(source, args.tag, patches, out)

    left = [p for p in patches if p["id"] not in result["released"]]
    registry = Registry()
    tags = registry.tags()
    value = fingerprint(result["tree"], platforms, recipe_hash())
    published = published_tags(tags, args.tag)
    labels = registry.labels(published[-1][1]) if published else {}
    candidate = candidate_tag(args.tag, value)
    # A scheduled run does not repeat a build or a gate that failed with the same input. A dispatch always tries.
    key = run_key(value, args.driver_sha)
    blocked = args.event == "schedule" and any((read_marker(i["body"]) or {}).get("key") == key for i in open_issues())
    action = decide(result["applied"], result["failures"], left, labels, value, candidate in tags, blocked)
    if args.test_mode != "none" and action in ("build", "gate"):
        action = "test-end"

    (out / "applied.json").write_text(json.dumps(result["applied"], indent=1) + "\n")
    (out / "failures.json").write_text(json.dumps(result["failures"], indent=1) + "\n")
    summary("\n".join([
        f"## Cube image: {args.tag}",
        "",
        f"Action: **{action}**. Fingerprint `{value[:12]}`. Cube Store from source: {str(result['cubestore']).lower()}.",
        f"Last published image: `{published[-1][1] if published else 'none'}`. Candidate: `{candidate}`.",
        "",
        "| Patch | Result | Source |",
        "| --- | --- | --- |",
        *result["notes"],
    ]))
    set_outputs(
        action=action,
        fingerprint=value,
        candidate=f"{IMAGE}:{candidate}",
        candidate_tag=candidate,
        cubestore=str(result["cubestore"]).lower(),
        patches=",".join(p["id"] for p in result["applied"]),
        released=json.dumps(result["released"]),
        key=key,
    )


def remove(args):
    ids = set(json.loads(args.ids))
    queue = load_queue()
    before = len(queue["patches"])
    queue["patches"] = [p for p in queue["patches"] if p["id"] not in ids]
    save_queue(queue)
    print(f"removed {before - len(queue['patches'])} patches, {len(queue['patches'])} left")


def read_cell(directory):
    try:
        return json.loads((Path(directory) / "result.json").read_text())
    except (OSError, ValueError):
        return None


def judge(candidate, baseline):
    """Publish only with 0 fail, and a pass count that is not lower than on stock Cube with the dialect. Returns the errors."""
    errors = []
    if candidate is None:
        errors.append("The candidate has no conformance result.")
    if baseline is None:
        errors.append("Stock Cube with the dialect has no conformance result, so the pass counts cannot be compared.")
    if errors:
        return errors
    fails = [f for f in candidate["features"] if f.get("status") == "fail"]
    if candidate["counts"].get("total", 0) == 0:
        errors.append("The candidate result has no features.")
    if fails:
        errors.append(f"The candidate has {len(fails)} failed features:")
        errors += [f"  {f.get('id')}: {f.get('reason')}" for f in fails]
    if candidate["counts"]["pass"] < baseline["counts"]["pass"]:
        before = {f.get("id"): f.get("status") for f in baseline["features"]}
        lower = [f.get("id") for f in candidate["features"] if before.get(f.get("id")) == "pass" and f.get("status") != "pass"]
        errors.append(f"The candidate has {candidate['counts']['pass']} pass, and stock Cube with the dialect has {baseline['counts']['pass']}. Lower: {', '.join(map(str, lower))}")
    return errors


def gate_row(name, cell):
    if not cell:
        return f"| {name} | no data | | | |"
    counts = cell["counts"]
    return f"| {name} | {counts.get('pass')} | {counts.get('fail')} | {counts.get('total')} | {cell.get('coverage')} |"


def gate(args):
    candidate, baseline, alone = read_cell(args.candidate), read_cell(args.baseline), read_cell(args.alone)
    errors = judge(candidate, baseline)
    summary("\n".join([
        "## Gate",
        "",
        "| Run | Pass | Fail | Total | Coverage line |",
        "| --- | --- | --- | --- | --- |",
        gate_row("Candidate with the dialect (gate)", candidate),
        gate_row("Stock Cube with the dialect (baseline)", baseline),
        gate_row("Candidate with no dialect (report only)", alone),
        "",
        "Verdict: **" + ("fail" if errors else "pass") + "**",
        "",
        *errors,
    ]))
    Path(args.out).write_text("\n".join(errors) + ("\n" if errors else ""))
    set_outputs(verdict="fail" if errors else "pass")


def next_tag(args):
    published = published_tags(Registry().tags(), args.tag)
    n = published[-1][0] + 1 if published else 1
    set_outputs(tag=f"{args.tag}-mcd.{n}", image=f"{IMAGE}:{args.tag}-mcd.{n}")


def failed_job_errors(run_id, tag, names):
    """The end of the log of each failed job of this tag. A job log is available before the run ends."""
    repo = os.environ["GITHUB_REPOSITORY"]
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"))
    errors = {}
    for page in pages:
        for job in page["jobs"]:
            cause = next((n for n in names if f"{tag} {n}" in job["name"]), None)
            if cause is None or job["conclusion"] not in ("failure", "timed_out"):
                continue
            log = subprocess.run(["gh", "api", f"repos/{repo}/actions/jobs/{job['id']}/logs"], capture_output=True, text=True).stdout
            lines = [re.sub(r"^\S+Z ", "", item) for item in log.splitlines()]
            errors[cause] = job["name"] + "\n" + tail_error(lines)
    return errors


def tail_error(lines):
    marks = [i for i, item in enumerate(lines) if "##[error]" in item]
    if not marks:
        return "\n".join(lines[-80:])
    return "\n".join(lines[max(0, marks[0] - 80):marks[-1] + 1])


def strip_generated(body):
    """The body with no marker and no link to a run, to compare the text of an error."""
    return re.sub(r"Last run: \S+\s*$", "", re.sub(r"<!-- cube-image [^>]*-->", "", body or "")).strip()


def upsert_issue(existing, cause, tag, key, title, text, run_url):
    """One open issue for each cause. The same error only refreshes the marker. Another error also adds a comment."""
    repo = os.environ["GITHUB_REPOSITORY"]
    body = f"{marker(cause, tag, key)}\n\n{text[:MAX_ERROR]}\n\nLast run: {run_url}\n"
    found = next((i for i in existing if (read_marker(i["body"]) or {}).get("cause") == cause), None)
    if not found:
        url = run("gh", "issue", "create", "--repo", repo, "--title", title, "--label", LABEL, "--body", body)
        print(f"issue opened: {url}")
        return int(url.rsplit("/", 1)[1])
    same = strip_generated(found["body"]) == strip_generated(body)
    run("gh", "issue", "edit", str(found["number"]), "--repo", repo, "--title", title, "--body", body)
    if not same:
        run("gh", "issue", "comment", str(found["number"]), "--repo", repo, "--body", f"The error changed. See the description. Run: {run_url}")
    print(f"issue #{found['number']} {'has the same error' if same else 'updated'}")
    return found["number"]


def report(args):
    repo = os.environ["GITHUB_REPOSITORY"]
    run_url = f"https://github.com/{repo}/actions/runs/{args.run_id}"
    run("gh", "label", "create", LABEL, "--repo", repo, "--force", "--color", "5319e7", "--description", "Failure or notice of the Cube image workflow")
    existing = open_issues()
    tag = args.tag
    stays = "The last good image stays. No image was published by this run."

    if args.action == "retire":
        upsert_issue(existing, "queue-empty", "", "", "cube image: the queue is empty, the image can retire", RETIRE_TEXT, run_url)
        return

    if args.action == "fail":
        for failure in json.loads(Path(args.failures).read_text()):
            text = f"{failure['title']}.\n\n{stays}\n\n```text\n{failure['error']}\n```"
            upsert_issue(existing, failure["cause"], tag, args.key, f"cube image: {failure['title']}", text, run_url)
        return

    errors = failed_job_errors(args.run_id, tag, ["native", "cubestored", "image", "manifest", "publish"]) if args.action in ("build", "gate") else {}
    for cause, error in errors.items():
        text = f"The step `{cause}` failed for Cube `{tag}`.\n\n{stays}\n\n```text\n{error}\n```"
        upsert_issue(existing, f"build-{cause}", tag, args.key, f"cube image: {cause} failed", text, run_url)

    gate_errors = Path(args.gate_file).read_text().strip() if args.gate_file and Path(args.gate_file).exists() else ""
    if gate_errors:
        text = f"The gate refused the candidate `{args.candidate}` for Cube `{tag}`.\n\n{stays}\n\n```text\n{gate_errors}\n```"
        upsert_issue(existing, "gate", tag, args.key, "cube image: the gate refused the candidate", text, run_url)

    if args.published:
        for issue in existing:
            mark = read_marker(issue["body"])
            if mark and mark["cause"] != "queue-empty" and mark["tag"] and version_key(mark["tag"]) <= version_key(tag):
                run("gh", "issue", "close", str(issue["number"]), "--repo", repo, "--comment", f"Image `{args.published}` passed the gate. Run: {run_url}")


def compat_row(args, name, directory):
    result = read_cell(directory)
    try:
        cell = json.loads((Path(directory) / "cell.json").read_text())
    except (OSError, ValueError):
        cell = {}
    return {
        "image": name,
        "metabase": cell.get("metabase"),
        "cube": args.tag,
        "clickhouse": cell.get("clickhouse"),
        "clickhouse_fixes": cell.get("clickhouse_fixes"),
        "conformance": cell.get("conformance"),
        "coverage": result["coverage"] if result else None,
        "counts": result["counts"] if result else None,
        "state": "published" if args.published else "candidate",
        "patches": args.patches,
        "run_url": cell.get("run_url"),
        "date": cell.get("date"),
    }


def compat_section(rows):
    lines = [
        "",
        "## Cube image with our fixes",
        "",
        "The image `ghcr.io/andrewkomkov/cube` is temporary. See [the page of the image](cube-image.md). A candidate is not for use.",
        "",
        "| Image | State | Metabase | ClickHouse | cube-clickhouse | Conformance | Coverage | Run |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| `{r['image']}` | {r['state']} | `{r['metabase']}` | `{r['clickhouse']}` | {r['clickhouse_fixes']} "
            f"| {r['conformance']} | {r['coverage'] or 'no data'} | [run]({r['run_url']}) |"
        )
    return "\n".join(lines) + "\n"


MARK = "<!-- cube-image rows -->"


def merge_compat(page, rows):
    head = page.split(MARK)[0].rstrip()
    return f"{head}\n\n{MARK}\n{compat_section(rows)}" if rows else head + "\n"


def compat(args):
    """Writes cube-image.json and the section of compatibility.md. Nightly rewrites that page, so cube-image-compat.yml adds the section again."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/status"

    def fetch(name, default):
        try:
            with urllib.request.urlopen(f"{base}/{name}") as response:
                return response.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return default
            raise

    rows = json.loads(fetch("cube-image.json", "[]"))
    if args.candidate:
        name = args.published or args.candidate.rsplit(":", 1)[1]
        new = [compat_row(args, name, args.with_dialect), compat_row(args, name, args.alone)]
        keep = [r for r in rows if r["cube"] != args.tag or r["state"] == "published" and not args.published]
        rows = sorted(keep + new, key=lambda r: (version_key(r["cube"]), r["image"], str(r["clickhouse_fixes"])))
        (out / "cube-image.json").write_text(json.dumps(rows, indent=1) + "\n")
    page = fetch("compatibility.md", "# Compatibility\n\nCI did not publish the version matrix yet.\n")
    (out / "compatibility.md").write_text(merge_compat(page, rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="find the Cube tags to process")
    p.add_argument("--tag", default="")
    p.add_argument("--platforms", default="")
    p.set_defaults(func=plan)

    p = sub.add_parser("prepare", help="apply the queue on a tag and decide what the run does")
    p.add_argument("--tag", required=True)
    p.add_argument("--platforms", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--driver-sha", required=True)
    p.add_argument("--test-mode", default="none", choices=["none", "bad-patch", "empty-queue"])
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=prepare)

    p = sub.add_parser("remove", help="remove patches from queue.json")
    p.add_argument("--ids", required=True)
    p.set_defaults(func=remove)

    p = sub.add_parser("gate", help="compare the conformance results and give the verdict")
    p.add_argument("--candidate", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--alone", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=gate)

    p = sub.add_parser("next-tag", help="print the tag of the next published image")
    p.add_argument("--tag", required=True)
    p.set_defaults(func=next_tag)

    p = sub.add_parser("report", help="open, update or close the issues of this workflow")
    p.add_argument("--tag", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--key", default="")
    p.add_argument("--run-id", required=True)
    p.add_argument("--failures", default="")
    p.add_argument("--gate-file", default="")
    p.add_argument("--candidate", default="")
    p.add_argument("--published", default="")
    p.set_defaults(func=report)

    p = sub.add_parser("compat", help="write the rows of the image for the compatibility table")
    p.add_argument("--tag", default="")
    p.add_argument("--candidate", default="")
    p.add_argument("--published", default="")
    p.add_argument("--patches", default="")
    p.add_argument("--with-dialect", default="")
    p.add_argument("--alone", default="")
    p.add_argument("--out", required=True)
    p.set_defaults(func=compat)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
