# DNS Generator

Single source of truth for the **IP Collector DNS** monitoring script.

Instead of hand-editing the 350-line RouterOS file, all apps/sites live in
**`catalog.json`**. A small generator turns that into the `.rsc` file at the
repo root, so:

- **Manual MikroTik users** keep downloading `../IP Collector DNS` exactly as before.
- **SAMM** keeps fetching that same root file via *DNS → Websites & App Filter → Update from repo*.
- Adding an app = one entry in `catalog.json` (no copy-paste, no drift).

## Usage

```bash
python3 "DNS Generator/generate.py"
```

This regenerates `../IP Collector DNS` from `catalog.json`, then the router
auto-updater files in `../ipdns-updater/` (`build_updater.py`). Commit all of them.

## Catalog format

```json
{
  "apps": [
    {
      "slug": "youtube",            // SAMM slug (lowercase, dashes)
      "label": "Youtube",           // address-list = "<label> IPs"
      "section": "YOUTUBE / GOOGLE VIDEO",
      "category": "streaming",      // SAMM QoS category
      "priority": 6,                // SAMM QoS default slot 1-8
      "domains": ["youtube.com", "googlevideo.com"],
      "cidrs": []                   // optional published IP ranges
    }
  ],
  "games": [
    { "slug": "pubg-mobile", "label": "PUBG Mobile", "category": "gaming",
      "priority": 4, "counter": true,
      "domains": ["pubgmobile.com", "igamecj.com"],   // optional, same collectors as apps
      "asns": [58521],                                // optional, see "Networks" below
      "tcp": "10012,17500", "udp": "10010,10013" }    // optional port collectors
  ]
}
```

A game is found by **name first, network second, port last**. Names and
networks are exact; a port range also catches every other app that happens to
use the same port. The older single `"protocol"` + `"ports"` pair still works.
Port collectors carry `connection-state=new`, so they run once per connection
instead of on every packet of every game session.

**Port collectors are TCP, the original sets.** Read on customer routers (2026-11-12): COD Mobile's TCP ports
saw 55 new connections in 4.5 days, the UDP range 7500-8000 added in October saw 8.7 million — strangers, all of
them put in "COD Mobile IPs". The UDP sets are gone; the generator refuses a UDP collector on a game that has a
name, a network or a TCP port. Clash of Clans and Clash Royale share TCP 9330-9340, as they always did.

### Networks (`asns` → `cidrs`)

For a service whose traffic comes from **its own network** — Netflix Open
Connect, Telegram, Roblox, Riot, Valve, Blizzard, Garena — the catalog lists the
ASNs, and

```bash
python3 "DNS Generator/generate.py" --refresh-cidrs
```

rewrites that entry's `cidrs` from the prefixes those ASNs announce today
(RIPEstat, IPv4, aggregated), then generates. Those ranges go into a static
`/ip firewall address-list`, so game servers reached by IP (no DNS lookup at
all) are still counted. Never give an ASN of a cloud or CDN (Google, Amazon,
Akamai, Cloudflare, Tencent Cloud, Meta): it would sweep in thousands of other
sites. Plain generation never touches the network.

**Vendor-published ranges** go straight into `cidrs` (an entry without `asns`
is never touched by `--refresh-cidrs`), or into `cidrs_extra` for an entry that
also has `asns`, so a refresh keeps them. Calls (Zoom, Teams, Meet, WhatsApp)
and VPN tunnels go to raw addresses no DNS lookup ever names, so without these
the call bytes are not counted at all. Sources (2026-11-01, IPv4 only — the
`cidrs` land in `/ip firewall address-list`, which holds no IPv6):

| Entry | Source |
|---|---|
| Zoom | `https://assets.zoom.us/docs/ipranges/Zoom.txt` |
| Microsoft Teams | `https://endpoints.office.com/endpoints/worldwide` id 11 (Teams media, Optimize) |
| Microsoft 365 | same API, id 1 (Exchange Online, Optimize) + id 31 (SharePoint/OneDrive for Business, Optimize) |
| Telegram | `https://core.telegram.org/resources/cidr.txt` (`cidrs_extra`, plus its ASNs) |
| Cloudflare WARP | Cloudflare One client firewall page: 162.159.192.0/24 (consumer WARP), 162.159.193.0/24 (WireGuard), 162.159.197.0/24 (MASQUE), the two API addresses |
| Meta | AS32934 — a separate umbrella entry ("Meta IPs"), never folded into Facebook/Instagram/WhatsApp: every Meta app shares that network, so it is right for "block/free everything Meta" and wrong for any one app. Its bytes are ALSO counted under the app whose DNS list holds the same address. |

### One domain, one app

RouterOS takes the **first** matching `/ip dns static` entry (lab-proven on
7.24.4): with `apple.com` above `tv.apple.com`, Apple TV lookups landed in the
App Store list. The generator therefore refuses a domain listed under two apps,
or a domain sitting under another app's domain. Keep app domains specific — a
parent domain like `apple.com` or `activision.com` swallows every sibling
service.

### Shared CDN hosts

A hostname that is a CNAME to a shared CDN edge (Akamai `*.akamai.net` /
`edgesuite.net`, Fastly, CloudFront, Cloudflare) collects an address that also
serves unrelated sites, and every byte to that address is then counted for
this app. Only list such a host when it carries the app's real payload (video,
downloads); static web assets are not worth it. Measured example:
`ttwstatic.com` (TikTok web scripts) shares `a1013.dsct.akamai.net` with Roblox's
`rbxcdn.com` and pulled 96% of a Roblox session into TikTok — so it is not listed.
For the same reason a game's marketing site (pubgmobile.com, ff.garena.com,
mobilelegends.com...) is never listed: only its backends, own network and
asset hosts.

### One app, one list

Every app has **one** address list, `<Label> IPs`, and it is the same list in SAMM, SOVA, SICO and on a router
that pasted the file or runs the updater. Operators write their own rules on these lists (send an app through a
line or a VPN, keep one direct), so a list is never renamed, split or emptied: the old "dedicated / shared"
split (`domain_roles`, November 2026) moved names like `youtube.com` out of the list their rules read, and is
gone. The generator refuses the key.

| Job | Use |
|---|---|
| Send an app through a line or a VPN, keep it direct | `<Label> IPs` in your own mangle rule; the first rule that matches an address wins |
| Count, shape (QoS) | `<Label> IPs` |
| Block an app | by **name**, not by address: `/ip dns static add name=tiktok.com match-subdomain=yes type=NXDOMAIN` for the whole network; per user or per site it is SAMM's parental control and SOVA's app policies. An address rule also blocks every other app on the same addresses |

### Apps that share addresses (`group`)

Some addresses belong to more than one app, and no list can change that:

```json
{ "slug": "youtube", "label": "Youtube", "group": "google", "domains": ["youtube.com", "googlevideo.com"] }
```

| `group` | Meaning |
|---|---|
| `google`, `meta`, `microsoft`, `apple` | apps of one company: one front-end pool (and, for Google, one set of caches: YouTube video and Play Store downloads come from the same address). They travel together — a rule for one also carries the other's traffic on the shared addresses, and your rule order decides. "Play Store through a VPN, YouTube direct" cannot be exact on any router |
| `cdn` | the app sits on a public CDN (Akamai, CloudFront, Fastly, Cloudflare): its list holds edges that also serve unrelated sites. Written by `check.py --write-groups`, not by hand |

`group` is information only: it never changes what the router gets. Products show it as a hint.

### The accuracy test (`check.py`)

```bash
python3 "DNS Generator/check.py"
```

resolves every name as a router in each customer country would (Syria, Iraq, Lebanon, Egypt, Saudi Arabia,
Turkey, plus this machine) and fails when an app that claims its own addresses shares one with another app or
with a name that must never be touched (sign-in, banks, connectivity checks). Run it before every publish.

### How long a collected address stays (lab-measured, RouterOS 7.24.4)

- An address lives for the DNS record's TTL plus `address-list-extra-time`.
  SAMM leaves that at the RouterOS default `0s`; the full file and the updater set `1h` (`1d` until November
  2026: on a busy router a CDN-hosted app's list then held three to four times more neighbours).
  5-minute YouTube and Twitch sessions were counted the same at `0s`, `1h` and
  `1d` (92–99%), while the longer times only kept shared CDN addresses around
  longer (more cross-app counting). Clients look names up again often enough.
- RouterOS remembers what it learnt: an entry removed by hand is **not**
  re-learnt — not after a cache flush, not after toggling the static — until
  its original lifetime has passed. Never "clean" the `<App> IPs` lists to
  test something; reboot the router or wait.

### Categories

`category` is **not free-form** — SAMM groups the App Filter and QoS screens by
it, and each one needs a display order, a translated heading and a chip colour
on the SAMM side. A category SAMM has never seen renders as an untinted chip at
the bottom of the list under a Title-Cased fallback heading, which looks like a
rendering bug rather than a missing entry.

    messaging   finance   conferencing   ai       social    streaming
    gaming      store     productivity   download vpn       speedtest   other

`priority` is the default SAMM QoS slot, **1–8**, 1 being the most protected.

The generator validates both and refuses to write on a typo, a duplicate slug,
an app with no domains, or a category it does not recognise — so a mistake
fails at generate time instead of shipping to every router.

**Adding a NEW category is a two-repo change.** Add it to `CATEGORIES` in
`generate.py` *and* to SAMM's `_APP_CAT_ORDER`, `_app_cat_label` and QoS chip
colours (`app/admin/routes.py`, `app/admin/templates/_mikrotik_qos.html`).

### Changing the catalogue (by hand, once a month)

Nothing is added automatically. A name enters only when routers really see it for that app (the learnt entry
reads "created for <name>", or the router's DNS cache shows the alias behind it) or the vendor documents it —
a public list of "domains of app X" is not evidence.

1. Edit `catalog.json` (copy an entry; `label`, `domains`, `category`, `priority`).
2. `python3 "DNS Generator/generate.py"` — validates, writes `IP Collector DNS` and `ipdns-updater/`.
3. `python3 "DNS Generator/check.py"` — the accuracy test.
4. Keep the change small: an installed SAMM or SOVA refuses a list more than 20% smaller than the one it uses.
5. Commit and push, then publish the mirror so GitHub-blocked installs get it too. SAMM reads GitHub raw
   **first** and falls back to `dl.securytik.com`, so an unpushed commit means "Update from repo" keeps
   returning the old catalogue even though the mirror is current.

The IPs themselves are **not** stored here — they're collected live on each
router from real DNS lookups. The catalog only defines *which* apps to watch.

## `settings` block (advanced)

```json
"settings": {
  "ipv6_counters": true,          // emit /ipv6 firewall mangle counters too
  "download_packet_marks": true,  // emit disabled mark-packet rules (default on)
  "anti_bypass": {
    "force_dns_tcp": true,        // also redirect TCP/53 (not just UDP)
    "block_dot": true,            // drop DNS-over-TLS (port 853)
    "block_doh": true,            // collect DoH endpoints + drop 443 to them
    "fasttrack_note": true,       // emit the fasttrack-accuracy guidance comment
    "doh_servers": ["dns.google", "cloudflare-dns.com", ...]
  }
}
```

All anti-bypass output lives **inside the `# SAMM skipped` block**, so SAMM's
own push is unaffected — it only applies when a manual user pastes the whole
file. The IPv6 counters and DoH lines are also ignored by SAMM's parser today
(SAMM reads only `/ip ...`), so they're additive and safe.

### Download packet marks

With `download_packet_marks` on (the default), the generator emits one
`action=mark-packet` rule per app/website (plus measured games) on the
**download** direction (`chain=prerouting`, `src-address-list="<label> IPs"`,
`new-packet-mark="<label> download"`), each created **`disabled=yes`**. These
are a ready-made seed for a manual QoS queue tree: the operator enables the
rules they want and points a queue at the matching packet mark.

The whole block is wrapped in `# SAMM skipped … end`, so SAMM's parser drops it
entirely — SAMM never pushes, wipes, or counts these rules. They exist purely
for manual full-file users. Set `"download_packet_marks": false` to omit them.

**Caveats for manual users:** the DoT/DoH drop rules append to the `forward`
chain — make sure they sit above any blanket accept. IPv6 counters need the
IPv6 package enabled. The fasttrack fix is order-sensitive and ships as a
commented note, not an auto-injected rule.
