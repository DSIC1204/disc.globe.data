#!/usr/bin/env python3
"""
DSIC Global Operations Picture - public data mirror.

Run by GitHub Actions every 5 minutes. Fetches the public sources a browser cannot read directly
(State Dept, NHC, FBI Most Wanted, AP headlines via Google News) and, once every 24 hours, the SAC/ASAC
listings on each fbi.gov field office "About" page. Results are written to the output folder, which
the workflow publishes to the "data" branch. The globe reads them from raw.githubusercontent.com.

Standard library only.   Usage:  python3 dsic_update.py <output-folder>
"""
import datetime as dt, gzip, html, json, os, re, sys, time, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else 'out'
os.makedirs(OUT, exist_ok=True)

SOURCES = {
    # file name               upstream URL
    'advisories.xml':      'https://travel.state.gov/_res/rss/TAsTWs.xml',
    'advisories_alt.json': 'https://cadataapi.state.gov/api/TravelAdvisories',
    'storms.json':         'https://www.nhc.noaa.gov/CurrentStorms.json',
    'wanted.json':         'https://api.fbi.gov/wanted/v1/list?poster_classification=ten&pageSize=50',
    'news.xml':            'https://news.google.com/rss/search?q=when:12h+site:apnews.com&hl=en-US&gl=US&ceid=US:en',
}
LEAD_URL = 'https://www.fbi.gov/contact-us/field-offices/{}/about'
LEAD_EVERY_H = 24
OFFICES = ('albany albuquerque anchorage atlanta baltimore billings birmingham boston buffalo charlotte chicago cincinnati '
           'cleveland columbia dallas denver detroit elpaso honolulu houston indianapolis jackson jacksonville kansascity '
           'lasvegas littlerock losangeles louisville miami milwaukee minneapolis mobile nashville newhaven neworleans newyork '
           'newark norfolk oklahomacity omaha philadelphia phoenix pittsburgh portland richmond sacramento saltlakecity '
           'sanantonio sandiego sanfrancisco sanjuan seattle springfield stlouis tampa washingtondc').split()

# test hook: {"news.xml": "http://127.0.0.1:9001/news", "lead": "http://127.0.0.1:9002/{}"}
_T = json.loads(os.environ.get('DSIC_TEST_MAP') or '{}')
SOURCES.update({k: v for k, v in _T.items() if k in SOURCES})
LEAD_URL = _T.get('lead', LEAD_URL)
LEAD_DELAY = float(os.environ.get('DSIC_LEAD_DELAY', '1.5'))

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip',
}
now = lambda: dt.datetime.now(dt.timezone.utc)
iso = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
    return body


def load_json(name, default):
    try:
        with open(os.path.join(OUT, name), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save(name, data):
    tmp = os.path.join(OUT, name + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(data if isinstance(data, bytes) else data.encode('utf-8'))
    os.replace(tmp, os.path.join(OUT, name))


def valid(name, body):
    """Reject error pages and empty payloads so a bad fetch never replaces good data."""
    if len(body) < 200:
        return False
    head = body[:400].lstrip().lower()
    if name.endswith('.json'):
        try:
            j = json.loads(body)
        except Exception:
            return False
        if name == 'storms.json':
            return isinstance(j, dict) and 'activeStorms' in j
        if name == 'wanted.json':
            return isinstance(j, dict) and isinstance(j.get('items'), list) and len(j['items']) > 0
        return bool(j)
    return head.startswith(b'<?xml') or head.startswith(b'<rss') or b'<rss' in head or b'<feed' in head


# ------------------------------------------------------------------ leadership parser
STOP = set('officer officers administrative counsel chief section division director special agent agents assistant charge '
           'contact office offices field fbi about history leadership news press more read learn view the of and in for our '
           'with public affairs unit squad program resident agency agencies federal bureau investigation washington email '
           'phone tips submit'.split())
NAME_RX = re.compile(r"^[\"“]?[^\W\d_][\w.'’“”\"-]*(,?\s+[\"“]?[^\W\d_][\w.'’“”\"-]*){1,5}$", re.U)
TITLE_RX = re.compile(r'^(?P<pre>Acting\s+)?(?P<t>Assistant\s+Director\s+in\s+Charge|Assistant\s+Special\s+Agents?\s+in\s+Charge|'
                      r'Special\s+Agents?\s+in\s+Charge)\s*(?P<act>\(\s*acting\s*\))?\s*[:\-–]?\s*(?P<rest>.*)$', re.I)
ACT_RX = re.compile(r'\(\s*acting\s*\)|^acting\s+|,\s*acting$', re.I)


def is_name(n):
    if not 4 <= len(n) <= 48 or re.search(r'\d', n) or not NAME_RX.match(n):
        return False
    return not any(w.strip('."') in STOP for w in re.split(r'[\s,]+', n.lower()))


def parse_leadership(page):
    t = re.sub(r'(?is)<(script|style|noscript|svg|nav|footer)\b.*?</\1\s*>', ' ', page)
    t = re.sub(r'(?i)<br\s*/?>', '\n', t)
    t = re.sub(r'(?i)</?(p|div|h[1-6]|li|ul|ol|tr|td|th|section|article|header|dt|dd|dl|table|tbody)\b[^>]*>', '\n', t)
    t = html.unescape(re.sub(r'<[^>]+>', '', t))
    t = re.sub(r'[﻿​‌‍⁠]', '', t)
    t = re.sub(r'[ \t ]+', ' ', t)
    lines = [l.strip() for l in t.splitlines() if l.strip()]
    out, seen, i = [], set(), 0
    while i < len(lines):
        m = TITLE_RX.match(lines[i])
        if not m:
            i += 1
            continue
        tt = m.group('t').lower()
        code = 'ADIC' if tt.startswith('assistant director') else 'ASAC' if tt.startswith('assistant special') else 'SAC'
        title_acting = bool(m.group('pre') or m.group('act'))
        cands = [m.group('rest').strip()] if m.group('rest').strip() else []
        j = i + 1
        while j < len(lines) and len(cands) < 14:
            if TITLE_RX.match(lines[j]) or not is_name(ACT_RX.sub('', lines[j]).strip()):
                break
            cands.append(lines[j])
            j += 1
        for c in cands:
            n = ACT_RX.sub('', c).strip().rstrip(',')
            if not is_name(n) or (code, n.lower()) in seen:
                continue
            seen.add((code, n.lower()))
            out.append({'t': code, 'n': n, 'acting': title_acting or bool(ACT_RX.search(c))})
        i = max(j, i + 1)
    rank = {'ADIC': 0, 'SAC': 1, 'ASAC': 2}
    return sorted(out, key=lambda p: rank[p['t']])   # stable: keeps page order within a title


def refresh_leadership(status):
    lead = load_json('leadership.json', {'offices': {}})
    last = lead.get('lastRun') or ''
    attempt = lead.get('lastAttempt') or ''
    t = now()
    age_h = (t - dt.datetime.fromisoformat(last.replace('Z', '+00:00'))).total_seconds() / 3600 if last else 1e9
    att_h = (t - dt.datetime.fromisoformat(attempt.replace('Z', '+00:00'))).total_seconds() / 3600 if attempt else 1e9
    if os.environ.get('DSIC_FORCE_LEAD') != '1' and (age_h < LEAD_EVERY_H or att_h < 1):
        status['leadership.json'] = status.get('leadership.json') or {'at': last, 'ok': bool(last)}
        return
    got, failed = {}, []
    for oid in OFFICES:
        try:
            people = parse_leadership(fetch(LEAD_URL.format(oid)).decode('utf-8', 'replace'))
            if any(p['t'] in ('SAC', 'ADIC') for p in people):
                got[oid] = people
            else:
                failed.append(oid)
        except Exception:
            failed.append(oid)
        time.sleep(LEAD_DELAY)
    offices = dict(lead.get('offices') or {})
    offices.update(got)
    stamp = iso(now())
    lead.update({'source': 'fbi.gov field office About pages', 'lastAttempt': stamp, 'failed': failed, 'offices': offices})
    ok = len(got) >= len(OFFICES) // 2
    if ok:
        lead['lastRun'] = stamp
        lead['retrieved'] = now().strftime('%d %b %Y').upper()
    save('leadership.json', json.dumps(lead, ensure_ascii=False, separators=(',', ':')))
    status['leadership.json'] = {'at': lead.get('lastRun', ''), 'ok': ok, 'read': len(got), 'failed': failed,
                                 'err': '' if ok else f'only {len(got)} of {len(OFFICES)} pages read'}
    print(f'leadership: {len(got)}/{len(OFFICES)} read' + (f'; kept previous for {", ".join(failed)}' if failed else ''))


def main():
    status = load_json('status.json', {}).get('files', {})
    for name, url in SOURCES.items():
        prev = status.get(name, {})
        try:
            body = fetch(url)
            if not valid(name, body):
                raise ValueError(f'unexpected response ({len(body)} bytes)')
            save(name, body)
            status[name] = {'at': iso(now()), 'ok': True, 'bytes': len(body)}
            print(f'ok    {name:22s} {len(body):>9,d} bytes')
        except Exception as e:
            status[name] = {'at': prev.get('at', ''), 'ok': False, 'err': str(e)[:200], 'lastTry': iso(now())}
            print(f'FAIL  {name:22s} {e}')
    try:
        refresh_leadership(status)
    except Exception as e:
        print('leadership error:', e)
    save('status.json', json.dumps({'generated': iso(now()), 'files': status}, indent=1))
    save('README.md', '# DSIC data branch\n\nWritten automatically by the "Update DSIC feeds" workflow. Do not edit.\n')


if __name__ == '__main__':
    main()
