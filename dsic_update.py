#!/usr/bin/env python3
"""
DSIC Global Operations Picture - public data mirror.

Run by GitHub Actions every 5 minutes. Fetches the public sources a browser cannot read directly
(State Dept, NHC, FBI Most Wanted, AP headlines via Google News) and, once every 24 hours, the SAC/ASAC
listings on each fbi.gov field office "About" page. Results are written to the output folder, which
the workflow publishes to the "data" branch. The globe reads them from raw.githubusercontent.com.

Standard library only.
    python3 dsic_update.py --ci        GitHub Actions: load data branch, fetch, publish, keep schedule alive
    python3 dsic_update.py <folder>    fetch into a local folder only
"""
import datetime as dt, gzip, html, io, json, os, re, subprocess, sys, tarfile, time, urllib.error, urllib.parse, urllib.request

CI = '--ci' in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith('--')]
OUT = _args[0] if _args else 'out'
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
LEAD_BUDGET = float(os.environ.get('DSIC_LEAD_BUDGET', '360'))   # seconds; keeps the job well inside its 15-minute limit

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip',
}
now = lambda: dt.datetime.now(dt.timezone.utc)
iso = lambda t: t.strftime('%Y-%m-%dT%H:%M:%SZ')


# fbi.gov sits behind a bot filter that rejects ordinary scripts; for it, use a client that presents as Chrome.
CHROME_HOSTS = ('fbi.gov',)
_cr = None


def chrome_client():
    global _cr
    if _cr is None:
        _cr = False
        try:
            from curl_cffi import requests as cr
            _cr = cr
        except ImportError:
            if CI:
                subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--user', '--break-system-packages', 'curl_cffi'], check=False)
                import importlib, site
                sys.path.append(site.getusersitepackages()); importlib.invalidate_caches()
                try:
                    from curl_cffi import requests as cr
                    _cr = cr
                except ImportError:
                    print('curl_cffi unavailable; using standard client')
    return _cr


def fetch(url, timeout=30, meta=None):
    host = urllib.parse.urlparse(url).hostname or ''
    cr = chrome_client() if any(host == h or host.endswith('.' + h) for h in CHROME_HOSTS) else None
    if cr:
        r = cr.get(url, impersonate='chrome', timeout=timeout)
        if r.status_code != 200:
            raise urllib.error.HTTPError(url, r.status_code, f'HTTP Error {r.status_code}', None, None)
        if meta is not None:
            meta.update({k.lower(): v for k, v in r.headers.items()})
        return r.content
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
        if meta is not None:
            meta.update({k.lower(): v for k, v in r.headers.items()})
    return body


def fetch_archived(url):
    """Latest Internet Archive capture of a page; returns (body, capture date)."""
    meta = {}
    body = fetch('https://web.archive.org/web/20991231000000id_/' + url, timeout=25, meta=meta)
    when = meta.get('memento-datetime', '')
    try:
        when = dt.datetime.strptime(when, '%a, %d %b %Y %H:%M:%S GMT').strftime('%Y-%m-%d')
    except Exception:
        pass
    return body, when


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
            j = json.loads(body.decode('utf-8-sig'))
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
           'phone tips submit senior supervisory intelligence analyst analysts manager specialist coordinator advisor legal executive '
           'operations support management'.split())
NAME_RX = re.compile(r"^[\"“]?[^\W\d_][\w.'’“”\"-]*(,?\s+[\"“]?[^\W\d_][\w.'’“”\"-]*){1,5}$", re.U)
TITLE_RX = re.compile(r'^(?P<pre>Acting\s+)?(?P<t>Assistant\s+Director\s+in\s+Charge|Assistant\s+Special\s+Agents?\s+in\s+Charge|'
                      r'Special\s+Agents?\s+in\s+Charge)\s*(?P<act>\(\s*acting\s*\))?\s*[:\-–]?\s*(?P<rest>.*)$', re.I)
ACT_RX = re.compile(r'\(\s*acting\s*\)|^acting\s+|,\s*acting$', re.I)
NOTE_RX = re.compile(r'\s*\((?!\s*acting\s*\))[^)]*\)\s*$', re.I)   # trailing portfolio, e.g. "(Criminal)", "(Austin)"
clean = lambda c: NOTE_RX.sub('', ACT_RX.sub('', NOTE_RX.sub('', c))).strip().rstrip(',')


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
            if TITLE_RX.match(lines[j]) or not is_name(clean(lines[j])):
                break
            cands.append(lines[j])
            j += 1
        for c in cands:
            n = clean(c)
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
    forced = os.environ.get('DSIC_FORCE_LEAD') == '1' or os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    if not forced and (age_h < LEAD_EVERY_H or att_h < 1):
        status['leadership.json'] = status.get('leadership.json') or {'at': last, 'ok': bool(last)}
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
    has_top = lambda p: bool(p) and any(x['t'] in ('SAC', 'ADIC') for x in p)
    got, archived, errs, direct_ok = {}, {}, set(), 0
    deadline = time.time() + LEAD_BUDGET
    # 1) fbi.gov directly; one probe first so a blocked site costs seconds, not minutes
    for oid in OFFICES:
        if time.time() > deadline:
            break
        try:
            p = parse_leadership(fetch(LEAD_URL.format(oid), timeout=15).decode('utf-8', 'replace'))
            direct_ok += 1
            if has_top(p):
                got[oid] = p
        except Exception as e:
            errs.add('fbi.gov: ' + str(e)[:80])
            if direct_ok == 0:
                break                           # blocked: go straight to the archive
        time.sleep(LEAD_DELAY)
    # 2) Internet Archive's latest capture for anything still missing, a few at a time
    todo = [o for o in OFFICES if o not in got]

    def from_archive(oid):
        body, when = fetch_archived(LEAD_URL.format(oid))
        return oid, parse_leadership(body.decode('utf-8', 'replace')), when

    if todo and time.time() < deadline:
        ex = ThreadPoolExecutor(4)
        futs = [ex.submit(from_archive, o) for o in todo]
        try:
            for f in as_completed(futs, timeout=max(5, deadline - time.time())):
                try:
                    oid, p, when = f.result()
                    if has_top(p):
                        got[oid], archived[oid] = p, when
                except Exception as e:
                    errs.add('archive: ' + str(e)[:60])
        except FutTimeout:
            errs.add('time budget reached')
        ex.shutdown(wait=False, cancel_futures=True)
    failed = [o for o in OFFICES if o not in got]
    offices = dict(lead.get('offices') or {})
    offices.update(got)
    stamp = iso(now())
    lead.update({'source': 'fbi.gov field office About pages', 'lastAttempt': stamp, 'failed': failed, 'offices': offices,
                 'archived': archived})   # offices read from the Internet Archive's latest capture, with capture date
    ok = len(got) >= len(OFFICES) // 2
    if ok:
        lead['lastRun'] = stamp
        lead['retrieved'] = now().strftime('%d %b %Y').upper()
    save('leadership.json', json.dumps(lead, ensure_ascii=False, separators=(',', ':')))
    note = f'{len(archived)} from Internet Archive captures' if archived else ''
    status['leadership.json'] = {'at': lead.get('lastRun', ''), 'ok': ok, 'read': len(got), 'failed': failed, 'archived': len(archived),
                                 'err': '' if ok else f'only {len(got)} of {len(OFFICES)} pages read ({"; ".join(sorted(errs))[:200]})', 'note': note}
    print(f'leadership: {len(got)}/{len(OFFICES)} read (fbi.gov {len(got) - len(archived)}, archive {len(archived)})' + (f'; kept previous for {", ".join(failed)}' if failed else ''))
    if errs:
        print('  errors:', '; '.join(sorted(errs))[:400])


# ------------------------------------------------------------------ global surface wind (NOAA GFS, 10 m) for the wind-flow layer
WIND_URL = ('https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_1p00.pl?dir=%2Fgfs.{d}%2F{c:02d}%2Fatmos&file=gfs.t{c:02d}z.pgrb2.1p00.f{f:03d}'
            '&var_UGRD=on&var_VGRD=on&lev_10_m_above_ground=on')
WIND_URL = _T.get('wind', WIND_URL)
WIND_EVERY_H = 3


def eccodes_mod():
    try:
        import eccodes
        return eccodes
    except ImportError:
        if not CI:
            raise
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--user', '--break-system-packages', 'eccodes', 'eccodeslib'], check=False)
        import importlib, site
        sys.path.append(site.getusersitepackages()); importlib.invalidate_caches()
        import eccodes
        return eccodes


def decode_wind(grib):
    ec = eccodes_mod()
    import tempfile
    fields = {}
    with tempfile.NamedTemporaryFile(suffix='.grib2') as tf:
        tf.write(grib); tf.flush()
        with open(tf.name, 'rb') as f:
            while True:
                gid = ec.codes_grib_new_from_file(f)
                if gid is None:
                    break
                try:
                    sn = ec.codes_get(gid, 'shortName')
                    ni, nj = ec.codes_get(gid, 'Ni'), ec.codes_get(gid, 'Nj')
                    la1, lo1 = ec.codes_get(gid, 'latitudeOfFirstGridPointInDegrees'), ec.codes_get(gid, 'longitudeOfFirstGridPointInDegrees')
                    dla = ec.codes_get(gid, 'jDirectionIncrementInDegrees') * (1 if ec.codes_get(gid, 'jScansPositively') else -1)
                    dlo = ec.codes_get(gid, 'iDirectionIncrementInDegrees')
                    vals = ec.codes_get_values(gid)
                    fields[sn] = dict(ni=ni, nj=nj, la1=la1, lo1=lo1, dla=dla, dlo=dlo, v=vals)
                finally:
                    ec.codes_release(gid)
    u, v = fields.get('10u') or fields.get('u'), fields.get('10v') or fields.get('v')
    if not u or not v:
        raise ValueError('wind components missing: ' + ', '.join(fields))
    step = max(1, round(2 / u['dlo']))      # thin to ~2 degrees
    ni, nj = u['ni'], u['nj']
    rows = range(0, nj, step)
    cols = range(0, ni, step)
    q = lambda a: [int(round(a[j * ni + i] * 10)) for j in rows for i in cols]
    return {'lo0': u['lo1'], 'la0': u['la1'], 'dlo': u['dlo'] * step, 'dla': u['dla'] * step, 'nx': len(cols), 'ny': len(rows), 'scale': 0.1, 'u': q(u['v']), 'v': q(v['v'])}


def refresh_wind(status):
    prev = load_json('wind.json', {})
    gen = prev.get('generated', '')
    if gen and (now() - dt.datetime.fromisoformat(gen.replace('Z', '+00:00'))).total_seconds() < WIND_EVERY_H * 3600 and os.environ.get('DSIC_FORCE_WIND') != '1':
        status['wind.json'] = status.get('wind.json') or {'at': gen, 'ok': True}
        return
    t = now()
    base = t - dt.timedelta(hours=4)                              # GFS cycles post roughly 4 hours after their nominal time
    cyc0 = base.replace(hour=base.hour - base.hour % 6, minute=0, second=0, microsecond=0)
    err = ''
    for k in range(3):
        cyc = cyc0 - dt.timedelta(hours=6 * k)
        fh = int(round((t - cyc).total_seconds() / 3600 / 3)) * 3
        fh = max(0, min(fh, 24))
        url = WIND_URL.format(d=cyc.strftime('%Y%m%d'), c=cyc.hour, f=fh)
        try:
            w = decode_wind(fetch(url, timeout=60))
            w.update({'generated': iso(now()), 'valid': iso(cyc + dt.timedelta(hours=fh)), 'cycle': cyc.strftime('%Y-%m-%d %HZ'), 'source': 'NOAA GFS 1.0 deg, 10 m wind'})
            save('wind.json', json.dumps(w, separators=(',', ':')))
            status['wind.json'] = {'at': w['generated'], 'ok': True, 'valid': w['valid']}
            print(f'ok    wind.json              GFS {w["cycle"]} +{fh}h, {w["nx"]}x{w["ny"]}')
            return
        except Exception as e:
            err = f'{cyc:%d %HZ} f{fh:03d}: {e}'[:200]
    status['wind.json'] = {'at': gen, 'ok': False, 'err': err, 'lastTry': iso(now())}
    print('FAIL  wind.json             ', err)


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
    try:
        refresh_wind(status)
    except Exception as e:
        print('wind error:', e)
    save('status.json', json.dumps({'generated': iso(now()), 'files': status}, indent=1))
    save('README.md', '# DSIC data branch\n\nWritten automatically by the "Update DSIC feeds" workflow. Do not edit.\n')


# ------------------------------------------------------------------ GitHub Actions plumbing
BOT = ['-c', 'user.name=dsic-feed-bot', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com']


def git(*args, cwd=None, check=True, capture=False):
    r = subprocess.run(['git', *args], cwd=cwd, check=False, capture_output=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a for a in args if 'x-access-token' not in a)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def ci_load():
    """Bring back the previous run's files (leadership, status) from the data branch."""
    if git('fetch', '--depth=1', 'origin', 'data', check=False).returncode:
        print('no data branch yet (first run)')
        return
    tar = git('archive', 'FETCH_HEAD', capture=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(OUT, filter='data') if hasattr(tarfile, 'data_filter') else t.extractall(OUT)
    print('loaded previous data:', ', '.join(sorted(os.listdir(OUT))))


def ci_publish():
    token, repo = os.environ.get('GH_TOKEN'), os.environ.get('GITHUB_REPOSITORY')
    if not token or not repo:
        raise RuntimeError('GH_TOKEN / GITHUB_REPOSITORY not set')
    if os.path.isdir(os.path.join(OUT, '.git')):
        subprocess.run(['rm', '-rf', os.path.join(OUT, '.git')], check=True)   # fresh single-commit branch every publish
    git('init', '-q', '-b', 'data', cwd=OUT)
    git('add', '-A', cwd=OUT)
    git(*BOT, 'commit', '-qm', 'feeds ' + now().strftime('%Y-%m-%dT%H:%MZ'), cwd=OUT)
    url = os.environ.get('DSIC_PUSH_URL') or f'https://x-access-token:{token}@github.com/{repo}.git'
    git('push', '-qf', url, 'data', cwd=OUT)
    print('published data branch')


def ci_keepalive():
    """GitHub pauses scheduled workflows after 60 days without repository activity."""
    last = int(git('log', '-1', '--format=%ct', capture=True).stdout.strip() or 0)
    if time.time() - last > 45 * 86400:
        git(*BOT, 'commit', '--allow-empty', '-qm', 'keepalive')
        git('push', '-q')
        print('keepalive commit pushed')


if __name__ == '__main__':
    # --loop MINUTES: keep refreshing every 5 minutes inside one job. GitHub's free scheduler often fires only once an hour
    # or less, so each run stays alive and publishes on its own 5-minute clock until the next run takes over.
    loop_min = 0
    if '--loop' in sys.argv:
        try:
            loop_min = float(sys.argv[sys.argv.index('--loop') + 1])
        except (IndexError, ValueError):
            loop_min = 0
    every = float(os.environ.get('DSIC_EVERY_SEC', '300'))
    t_end = time.time() + loop_min * 60
    if CI:
        ci_load()
    while True:
        t0 = time.time()
        main()
        if CI:
            try:
                ci_publish()
            except Exception as e:
                print('publish error:', e)
        if time.time() + every > t_end:
            break
        time.sleep(max(5, every - (time.time() - t0)))
    if CI:
        ci_keepalive()
