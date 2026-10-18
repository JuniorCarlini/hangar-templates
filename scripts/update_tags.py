#!/usr/bin/env python3
"""Bump the image tags of the catalog's templates to the newest stable ones.

For every image in templates/<id>/compose.yaml, the script lists the tags of
its repository (Docker Registry HTTP API v2: Docker Hub or ghcr.io), keeps
the ones with the same shape as the current tag (same "v" prefix, same
number of version parts, same suffix: "4.3-management" only moves to
"N.N-management"), applies the template's rule from updates.toml and picks
the newest tag that is published for both linux/amd64 and linux/arm64.

When a template changes, its template.toml "version" goes up by one: the
panel merges the community catalog with the embedded one by id, and on a
tie the embedded template wins, so an unbumped template would never reach
anyone.

Rules (updates.toml):
  patch  same major.minor        (18.1 -> 18.3, never 18.4 -> 19.0)
  minor  same major (default)    (4.3-management -> 4.4-management)
  major  any newer version
  keep   never touch

Running it twice gives the same result; --dry-run only prints what would
change. Python 3.11+ standard library only.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RULES = ("patch", "minor", "major", "keep")
DEFAULT_RULE = "minor"
REQUIRED_PLATFORMS = (("linux", "amd64"), ("linux", "arm64"))
# How many candidate tags (newest first) are inspected per image before
# giving up; each one costs a manifest request.
MAX_CANDIDATES = 15
TIMEOUT = 30
USER_AGENT = "hangar-templates-updater/1"

DOCKER_HUB = "registry-1.docker.io"
INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
SINGLE_TYPES = (
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)

# ---------------------------------------------------------------- tag shape

TAG_RE = re.compile(r"^(?P<prefix>v?)(?P<nums>\d+(?:\.\d+)*)(?P<suffix>-[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*)?$")
# Suffixes that mark a pre-release or a moving channel: a tag shaped like
# that is never a target, even if the current tag has the same suffix.
UNSTABLE_RE = re.compile(r"(?i)(alpha|beta|rc|pre|preview|dev|nightly|snapshot|next|canary|edge|test)\d*")


@dataclasses.dataclass(frozen=True)
class Tag:
    raw: str
    prefix: str
    nums: tuple[int, ...]
    suffix: str

    @property
    def shape(self) -> tuple[str, int, str]:
        return (self.prefix, len(self.nums), self.suffix)


def parse_tag(raw: str) -> Tag | None:
    """Parse a version tag; None for anything else (latest, alpine, sha...)."""
    m = TAG_RE.match(raw)
    if not m:
        return None
    suffix = m.group("suffix") or ""
    if any(UNSTABLE_RE.fullmatch(part) for part in suffix.split("-")[1:]):
        return None
    nums = tuple(int(p) for p in m.group("nums").split("."))
    return Tag(raw, m.group("prefix"), nums, suffix)


def allowed(current: Tag, cand: Tag, rule: str) -> bool:
    """Whether cand is a newer tag of the same shape that rule permits."""
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}")
    if rule == "keep" or cand.shape != current.shape or cand.nums <= current.nums:
        return False
    if rule == "major":
        return True
    fixed = 1 if rule == "minor" else 2
    fixed = min(fixed, len(current.nums))
    return cand.nums[:fixed] == current.nums[:fixed]


def candidates(current: str, tags: list[str], rule: str) -> list[Tag]:
    """Tags rule allows from current, newest first."""
    cur = parse_tag(current)
    if cur is None:
        return []
    out = [t for t in (parse_tag(x) for x in tags) if t is not None and allowed(cur, t, rule)]
    out.sort(key=lambda t: t.nums, reverse=True)
    return out


# ------------------------------------------------------------- image lines

IMAGE_LINE_RE = re.compile(
    r"^(?P<lead>\s*image:\s*)(?P<q>[\"']?)(?P<ref>[^\s\"'@]+)(?P=q)(?P<tail>\s*(?:#.*)?)$"
)


@dataclasses.dataclass(frozen=True)
class ImageRef:
    registry: str  # registry host
    repo: str  # repository path on that registry
    name: str  # as written, without the tag
    tag: str


def parse_image(ref: str) -> ImageRef | None:
    """Split an image reference; None when it has no tag or uses a digest."""
    if "@" in ref:
        return None
    name, sep, tag = ref.rpartition(":")
    if not sep or "/" in tag:
        return None  # no tag (registry:port/image)
    first, slash, rest = name.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        registry, repo = first, rest
    else:
        registry, repo = DOCKER_HUB, name
    if registry in ("docker.io", "index.docker.io"):
        registry = DOCKER_HUB
    if registry == DOCKER_HUB and "/" not in repo:
        repo = "library/" + repo
    return ImageRef(registry, repo, name, tag)


def rewrite_tags(text: str, new_tags: dict[str, str]) -> str:
    """Replace "name:old" by "name:new" on image lines; new_tags maps the
    full old reference to the new tag. Everything else stays byte for byte."""
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        body = line.rstrip("\r\n")
        m = IMAGE_LINE_RE.match(body)
        if not m or m.group("ref") not in new_tags:
            continue
        img = parse_image(m.group("ref"))
        if img is None:
            continue
        ref = f"{img.name}:{new_tags[m.group('ref')]}"
        q = m.group("q")
        lines[i] = f"{m.group('lead')}{q}{ref}{q}{m.group('tail')}{line[len(body):]}"
    return "".join(lines)


def find_images(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = IMAGE_LINE_RE.match(line)
        if m and m.group("ref") not in out:
            out.append(m.group("ref"))
    return out


VERSION_RE = re.compile(r"(?m)^(version\s*=\s*)(\d+)")


def bump_version(toml_text: str) -> str:
    m = VERSION_RE.search(toml_text)
    if not m:
        raise ValueError("template.toml has no version")
    return toml_text[: m.start(2)] + str(int(m.group(2)) + 1) + toml_text[m.end(2) :]


# ------------------------------------------------------------------ policy


@dataclasses.dataclass
class Policy:
    default: str = DEFAULT_RULE
    templates: dict[str, dict] = dataclasses.field(default_factory=dict)

    def rule(self, template: str, image: ImageRef) -> str:
        t = self.templates.get(template, {})
        images = t.get("images", {})
        short = image.name.rsplit("/", 1)[-1]
        for key in (image.name, short):
            if key in images:
                return images[key]
        return t.get("rule", self.default)

    def channel(self, template: str) -> str | None:
        return self.templates.get(template, {}).get("stable_channel")


def load_policy(path: Path) -> Policy:
    if not path.exists():
        return Policy()
    data = tomllib.loads(path.read_text())
    p = Policy(default=data.get("default", DEFAULT_RULE), templates=data.get("templates", {}))
    for tid, t in [("default", {"rule": p.default})] + list(p.templates.items()):
        for rule in [t.get("rule", p.default), *t.get("images", {}).values()]:
            if rule not in RULES:
                raise ValueError(f"updates.toml: {tid}: unknown rule {rule!r} (one of {', '.join(RULES)})")
    return p


# ---------------------------------------------------------------- registry


class Registry:
    """Minimal anonymous client of the Docker Registry HTTP API v2."""

    def __init__(self) -> None:
        self.tokens: dict[tuple[str, str], str] = {}

    def _token(self, realm: str, service: str, scope: str) -> str:
        q = {"scope": scope}
        if service:
            q["service"] = service
        req = urllib.request.Request(realm + "?" + urllib.parse.urlencode(q), headers={"User-Agent": USER_AGENT})
        user, pw = os.environ.get("DOCKERHUB_USERNAME"), os.environ.get("DOCKERHUB_TOKEN")
        if user and pw and "docker.io" in realm:
            req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            body = json.load(res)
        return body.get("token") or body.get("access_token") or ""

    def request(self, registry: str, repo: str, path: str, accept: tuple[str, ...] = (), method: str = "GET"):
        url = path if path.startswith("https://") else f"https://{registry}{path}"
        for attempt in range(2):
            headers = {"User-Agent": USER_AGENT}
            if accept:
                headers["Accept"] = ", ".join(accept)
            token = self.tokens.get((registry, repo))
            if token:
                headers["Authorization"] = "Bearer " + token
            req = urllib.request.Request(url, headers=headers, method=method)
            try:
                res = urllib.request.urlopen(req, timeout=TIMEOUT)
                return res, res.read()
            except urllib.error.HTTPError as e:
                if e.code != 401 or attempt:
                    raise
                challenge = e.headers.get("WWW-Authenticate", "")
                params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
                if not challenge.lower().startswith("bearer") or "realm" not in params:
                    raise
                scope = params.get("scope", f"repository:{repo}:pull")
                self.tokens[(registry, repo)] = self._token(params["realm"], params.get("service", ""), scope)
        raise RuntimeError("unreachable")

    def tags(self, img: ImageRef) -> list[str]:
        out: list[str] = []
        path = f"/v2/{img.repo}/tags/list?n=1000"
        for _ in range(100):
            res, body = self.request(img.registry, img.repo, path)
            out += json.loads(body).get("tags") or []
            m = re.search(r"<([^>]+)>;\s*rel=\"?next\"?", res.headers.get("Link", ""))
            if not m:
                break
            nxt = m.group(1)
            path = nxt if nxt.startswith("https://") else urllib.parse.urljoin(f"https://{img.registry}", nxt)
        return out

    def platforms(self, img: ImageRef, tag: str) -> set[tuple[str, str]]:
        _, body = self.request(img.registry, img.repo, f"/v2/{img.repo}/manifests/{tag}", INDEX_TYPES + SINGLE_TYPES)
        return manifest_platforms(json.loads(body))

    def digest(self, img: ImageRef, tag: str) -> str:
        # HEAD: Docker Hub does not count it against the pull rate limit.
        res, _ = self.request(img.registry, img.repo, f"/v2/{img.repo}/manifests/{tag}", INDEX_TYPES + SINGLE_TYPES, method="HEAD")
        return res.headers.get("Docker-Content-Digest", "")


def manifest_platforms(manifest: dict) -> set[tuple[str, str]]:
    """Platforms of a manifest list / OCI index; a single-platform manifest
    gives an empty set (it cannot cover both architectures)."""
    out = set()
    for m in manifest.get("manifests") or []:
        p = m.get("platform") or {}
        if p.get("os") and p.get("architecture") and p.get("architecture") != "unknown":
            out.add((p["os"], p["architecture"]))
    return out


# -------------------------------------------------------------------- main


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: "
    print(prefix + msg, flush=True)


def channel_ceiling(reg: Registry, img: ImageRef, channel: str, current: Tag, tags: list[str]) -> tuple[int, ...] | None:
    """Version the moving tag channel (e.g. n8n's "latest") points to: the
    newest same-shape tag with the same digest. Newer tags are pre-releases."""
    want = reg.digest(img, channel)
    same = sorted((t for t in (parse_tag(x) for x in tags) if t and t.shape == current.shape), key=lambda t: t.nums, reverse=True)
    for t in same[: MAX_CANDIDATES * 2]:
        if reg.digest(img, t.raw) == want:
            return t.nums
    return None


def pick(reg: Registry, img: ImageRef, rule: str, channel: str | None) -> str | None:
    cur = parse_tag(img.tag)
    if cur is None:
        warn(f"{img.name}:{img.tag}: tag is not a version, skipped")
        return None
    tags = reg.tags(img)
    cands = candidates(img.tag, tags, rule)
    if channel and cands:
        ceiling = channel_ceiling(reg, img, channel, cur, tags)
        if ceiling is None:
            warn(f"{img.name}: could not tell which version {channel!r} is, skipped")
            return None
        cands = [c for c in cands if c.nums <= ceiling]
    for c in cands[:MAX_CANDIDATES]:
        plats = reg.platforms(img, c.raw)
        missing = [f"{o}/{a}" for o, a in REQUIRED_PLATFORMS if (o, a) not in plats]
        if not missing:
            return c.raw
        log(f"  {img.name}:{c.raw}: no {', '.join(missing)}, trying an older tag")
    return None


def run(root: Path, policy: Policy, dry_run: bool, reg: Registry) -> list[str]:
    changes: list[str] = []
    for compose in sorted((root / "templates").glob("*/compose.yaml")):
        tid = compose.parent.name
        text = compose.read_text()
        new_tags: dict[str, str] = {}
        for ref in find_images(text):
            img = parse_image(ref)
            if img is None:
                warn(f"{tid}: {ref}: no tag or pinned by digest, skipped")
                continue
            rule = policy.rule(tid, img)
            if rule == "keep":
                log(f"{tid}: {ref}: keep")
                continue
            try:
                newest = pick(reg, img, rule, policy.channel(tid))
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
                warn(f"{tid}: {ref}: {e}")
                continue
            if newest is None:
                log(f"{tid}: {ref}: up to date ({rule})")
                continue
            new_tags[ref] = newest
            changes.append(f"{tid}: {img.name} {img.tag} -> {newest}")
            log(f"{tid}: {ref}: {img.tag} -> {newest} ({rule})")
        if new_tags and not dry_run:
            compose.write_text(rewrite_tags(text, new_tags))
            meta = compose.parent / "template.toml"
            meta.write_text(bump_version(meta.read_text()))
    return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--dry-run", action="store_true", help="only print what would change")
    ap.add_argument("--summary", type=Path, help="write one line per change to this file")
    args = ap.parse_args(argv)
    policy = load_policy(args.root / "updates.toml")
    changes = run(args.root, policy, args.dry_run, Registry())
    if args.summary:
        args.summary.write_text("".join(c + "\n" for c in changes))
    verb = "would change" if args.dry_run else "changed"
    log(f"{len(changes)} image tag(s) {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
