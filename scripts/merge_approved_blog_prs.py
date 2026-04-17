"""Merged alle OPEN ZERODOX-PRs mit 'claude-approved' Label + MERGEABLE.

Sicherheit: Pfad-Check (nur content/blog/*), max 15 Dateien, Dry-Run default.
"""
import argparse
import json
import subprocess
import sys

REPO = "Commandershadow9/ZERODOX"
MAX_FILES = 15
ALLOWED_PATH_PREFIXES = (
    "content/blog/",
    "src/pages/blog/",
    "content/data/",
    "web/src/lib/blog-data.ts",
    "web/src/app/sitemap.ts",
    "web/src/app/blog/",
    "web/content/blog/",
)


def gh(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, check=True)
    return r.stdout


def main(apply: bool) -> int:
    prs = json.loads(
        gh([
            "pr", "list", "--repo", REPO, "--state", "open",
            "--label", "claude-approved", "--limit", "30",
            "--json", "number,mergeable,files,title",
        ])
    )
    to_merge, skipped = [], []
    for p in prs:
        if p.get("mergeable") != "MERGEABLE":
            skipped.append((p["number"], f"not mergeable ({p.get('mergeable')})"))
            continue
        files = [f["path"] for f in p.get("files") or []]
        if len(files) > MAX_FILES:
            skipped.append((p["number"], f"too many files ({len(files)})"))
            continue
        bad = [f for f in files if not f.startswith(ALLOWED_PATH_PREFIXES)]
        if bad:
            skipped.append((p["number"], f"path not whitelisted: {bad[:2]}"))
            continue
        to_merge.append(p)

    print(f"=== WILL MERGE ({len(to_merge)}) ===")
    for p in to_merge:
        print(f"  #{p['number']} {p['title'][:60]}")
    print(f"\n=== SKIP ({len(skipped)}) ===")
    for num, reason in skipped:
        print(f"  #{num}: {reason}")

    if not apply:
        print("\nDry-Run. Mit --apply scharf schalten.")
        return 0

    failures = 0
    for p in to_merge:
        try:
            gh(["pr", "merge", str(p["number"]), "--repo", REPO,
                "--squash", "--delete-branch"])
            print(f"  merged #{p['number']}")
        except subprocess.CalledProcessError as e:
            print(f"  FAIL #{p['number']}: {e.stderr[:200] if e.stderr else e}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    sys.exit(main(ap.parse_args().apply))
