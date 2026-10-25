"""Retain only two completed caches in this worker's own namespace/ref."""
import argparse
import json
import os
import urllib.parse
import urllib.request


def stale_caches(caches, prefix, ref, keep=2):
    owned = [c for c in caches if c["key"].startswith(prefix) and c["ref"] == ref]
    return sorted(owned, key=lambda c: (c["created_at"], c["id"]), reverse=True)[keep:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", choices=("sec-read-v1-", "news-sec-v1-"))
    args = parser.parse_args()
    base = "https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"] + "/actions/caches"
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"],
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    ref = os.environ["GITHUB_REF"]
    caches = []
    for page in range(1, 101):
        query = urllib.parse.urlencode({"key": args.prefix, "ref": ref, "per_page": 100, "page": page})
        with urllib.request.urlopen(urllib.request.Request(base + "?" + query, headers=headers), timeout=30) as response:
            batch = json.load(response)["actions_caches"]
        caches.extend(batch)
        if len(batch) < 100:
            break
    for cache in stale_caches(caches, args.prefix, ref):
        request = urllib.request.Request(base + "/" + str(int(cache["id"])), headers=headers, method="DELETE")
        with urllib.request.urlopen(request, timeout=30):
            pass
    print("Retained at most two SEC read caches for this worker/ref.")


if __name__ == "__main__":
    main()
