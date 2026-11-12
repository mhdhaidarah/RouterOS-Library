#!/usr/bin/env python3
"""Accuracy test of catalog.json — run it before every publish.

    python3 "DNS Generator/check.py"                 # test, exit 1 on a real collision
    python3 "DNS Generator/check.py" --write-groups  # also mark the apps that sit on public CDNs (group "cdn")
    python3 "DNS Generator/check.py" --here          # this probe's own view only (quick)

What it does: resolves every catalogue name the way a router in each of our customers' countries would (public
DNS asked with that country's client subnet — our own probe, no customer data), then looks at the ADDRESSES:

* apps of one company (`group` google / meta / microsoft / apple) share addresses by nature        -> reported
* an app that shares addresses with other apps or sites (public CDN edges, an ISP's cache) must say so:
  `group: "cdn"`. One that shares and is not marked                                                 -> FAIL
  (`--write-groups` marks them; read the diff - a NEW mark means that app's list now carries neighbours)
* a name that must never be touched (sign-in, banks, connectivity checks) found on an app's list    -> WARNING

One snapshot can miss a collision another moment shows; that is fine — the test catches what is wrong today,
and it runs again at every monthly update. Standard library only (DNS over HTTPS, dns.google).
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "catalog.json")
DOH = "https://dns.google/resolve"

# The views: one client subnet per country our customers are in (a big ISP of each).
VIEWS = {
    "here": None,
    "SY": "5.0.0.0/24", "IQ": "37.236.0.0/24", "LB": "77.42.128.0/24",
    "EG": "41.32.0.0/24", "SA": "188.48.0.0/24", "TR": "88.224.0.0/24",
}

# Names no rule on an app's list may ever catch: sign-in, connectivity checks, payments, push.
NEVER_TOUCH = [
    "accounts.google.com", "www.google.com", "oauth2.googleapis.com", "mtalk.google.com", "fcm.googleapis.com",
    "connectivitycheck.gstatic.com", "captive.apple.com", "appleid.apple.com", "gs.apple.com",
    "login.microsoftonline.com", "login.live.com", "www.msftconnecttest.com", "dns.msftncsi.com",
    "www.paypal.com", "www.visa.com", "www.mastercard.com", "www.westernunion.com", "js.stripe.com",
    "www.syriatel.sy", "www.mtn.com.sy", "www.touch.com.lb", "www.alfa.com.lb", "www.zain.com",
    "www.bankaudi.com.lb", "www.blom.com.lb", "www.wikipedia.org", "github.com", "www.who.int",
    "license-samm.securytik.com", "dl.securytik.com", "securytik.com",
]

# Networks whose addresses serve many unrelated customers.
CDN_AS = {
    "13335": "Cloudflare", "209242": "Cloudflare", "20940": "Akamai", "16625": "Akamai", "21342": "Akamai",
    "54113": "Fastly", "16509": "Amazon", "14618": "Amazon", "396982": "Google Cloud", "8075": "Microsoft",
    "8068": "Microsoft", "45102": "Alibaba", "37963": "Alibaba", "132203": "Tencent", "16276": "OVH",
    "24940": "Hetzner", "14061": "DigitalOcean", "63949": "Linode", "21859": "Zenlayer", "60068": "CDN77",
    "199524": "Gcore", "15133": "Edgecast", "22822": "Limelight", "19551": "Imperva",
}
COMPANY = {"google", "meta", "microsoft", "apple"}


def doh(name, rtype, subnet=None):
    q = {"name": name, "type": rtype}
    if subnet:
        q["edns_client_subnet"] = subnet
    req = urllib.request.Request(DOH + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "catalog-check/1"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r).get("Answer") or []
        except Exception:  # noqa: BLE001 - a lost lookup is retried, then counts as no answer
            continue
    return []


def addresses(job):
    name, subnet = job
    return job, sorted({a["data"] for a in doh(name, "A", subnet) if a.get("type") == 1})


def origin(ip):
    ans = doh(".".join(reversed(ip.split("."))) + ".origin.asn.cymru.com", "TXT")
    for a in ans:
        if a.get("type") == 16:
            return ip, a["data"].strip('"').split("|")[0].split()[0]
    return ip, "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-groups", action="store_true", help='mark CDN-hosted apps with group "cdn"')
    ap.add_argument("--here", action="store_true", help="this probe's view only")
    args = ap.parse_args()
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)
    entries = cat["apps"] + cat.get("games", [])
    views = {"here": None} if args.here else VIEWS

    owner = {}                                  # name -> slug ("!" = never touch)
    for a in entries:
        for d in a.get("domains") or []:
            owner[d] = owner["www." + d] = a["slug"]
    for n in NEVER_TOUCH:
        owner.setdefault(n, "!")
    jobs = [(n, s) for n in owner for s in views.values()]
    got = collections.defaultdict(set)          # name -> addresses over every view
    with cf.ThreadPoolExecutor(40) as ex:
        for (name, _s), ips in ex.map(addresses, jobs):
            got[name].update(ips)
    all_ips = sorted({ip for v in got.values() for ip in v})
    with cf.ThreadPoolExecutor(40) as ex:
        asn = dict(ex.map(origin, all_ips))

    by_ip = collections.defaultdict(set)        # address -> {slug or "!name"}
    for name, ips in got.items():
        who = owner[name]
        for ip in ips:
            by_ip[ip].add("!" + name if who == "!" else who)

    group = {a["slug"]: a.get("group") for a in entries}
    failed, rows, suggest, warned = [], [], {}, {}
    for a in entries:
        slug = a["slug"]
        names = [d for d in a.get("domains") or [] if got[d] or got["www." + d]]
        ips = {ip for d in a.get("domains") or [] for ip in got[d] | got["www." + d]}
        if not a.get("domains"):
            continue
        on_cdn = [d for d in names if any(asn.get(ip) in CDN_AS for ip in got[d] | got["www." + d])]
        share = len(on_cdn) / len(names) if names else 0.0
        others, never = set(), set()
        for ip in ips:
            for w in by_ip[ip]:
                if w.startswith("!"):
                    never.add(w[1:])
                elif w != slug and not (group[slug] in COMPANY and group.get(w) == group[slug]):
                    others.add(w)
        g = group[slug]
        verdict = "ok"
        if others or never:
            verdict = "shares"
            if g is None:
                verdict, suggest[slug] = "FAIL", "cdn"
                failed.append(slug)
        if never:
            warned[slug] = sorted(never)
        rows.append((slug, g or "-", len(names), len(a["domains"]), len(ips),
                     f"{share:.0%}", verdict, ", ".join(sorted(others))[:60], ", ".join(sorted(never))[:60]))

    print(f"{len(owner)} names x {len(views)} views, {len(all_ips)} addresses")
    print(f"{'app':22} {'group':9} {'names':>7} {'addr':>5} {'cdn':>4}  {'result':6}  shares with apps | never-touch names")
    for slug, g, n, tot, nip, share, verdict, others, never in rows:
        if verdict != "ok":
            print(f"{slug:22} {g:9} {n:>3}/{tot:<3} {nip:>5} {share:>4}  {verdict:6}  {others} | {never}")
    quiet = sum(1 for r in rows if r[6] == "ok")
    print(f"{quiet} apps share nothing; {len(rows) - quiet} share addresses (listed above)")
    no_answer = sorted(a["slug"] for a in entries if a.get("domains") and not any(
        got[d] or got["www." + d] for d in a["domains"]))
    if no_answer:
        print("no name of these apps answers (only hosts under them exist, or dead):", ", ".join(no_answer))

    if args.write_groups and suggest:
        for a in entries:
            if a["slug"] in suggest and a.get("group") is None:
                a["group"] = "cdn"
        with open(CATALOG, "w", encoding="utf-8") as f:
            json.dump(cat, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f'group "cdn" written for: {", ".join(sorted(suggest))}')
        failed = [s for s in failed if s not in suggest]
    for slug, names in sorted(warned.items()):
        print(f"WARNING {slug}: its list can hold the address of {', '.join(names)}")
    if failed:
        print("FAIL - these apps share addresses and are not marked (run --write-groups, then read the diff): "
              + ", ".join(sorted(failed)))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
