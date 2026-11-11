"""90-minute scores of sampled UEFA extra-time matches from Wikipedia {{Football box}} goal minutes.
Usage: python3 -I wiki_et.py <recheck_download_dir>. Reads saved wikitext JSON only (untrusted data, parsed as text)."""
import json, os, re, sys
D = sys.argv[1]
FILES = ['wp_2024-25_UCL_KO.json', 'wp_2024-25_UEL_KO.json', 'wp_2014-15_UCL_KO.json', 'wp_2005_UCL_final.json']
# (file, date, home substring, away substring, ESPN event id, ESPN 90-min from S1 count_output)
TARGETS = [
    ('wp_2024-25_UEL_KO.json', '2025|4|17', 'Manchester United', 'Lyon', '734499', '2-2'),
    ('wp_2024-25_UCL_KO.json', '2025|3|12', 'Atlético', 'Real Madrid', '733606', '1-0'),
    ('wp_2024-25_UCL_KO.json', '2025|3|11', 'Liverpool', 'Paris', '733605', '0-1'),
    ('wp_2024-25_UCL_KO.json', '2025|5|6', 'Inter', 'Barcelona', '733619', '3-3'),
    ('wp_2024-25_UEL_KO.json', '2025|4|17', 'Lazio', 'Bodø', '733642', '2-0'),
    ('wp_2014-15_UCL_KO.json', '2015|3|11', 'Chelsea', 'Paris', '410544', '1-1'),
    ('wp_2014-15_UCL_KO.json', '2015|3|17', 'Atlético', 'Leverkusen', '410540', '1-0'),
    ('wp_2005_UCL_final.json', '2005|5|25', 'Milan', 'Liverpool', '183634', '3-3'),
]
MIN = re.compile(r'^(\d+)(?:\+(\d+))?$')


def fields(box):
    out, key = {}, None
    for line in box.split('\n'):
        m = re.match(r'^\s*\|\s*([a-z0-9_]+)\s*=\s*(.*)$', line)
        if m:
            key = m.group(1); out[key] = m.group(2)
        elif key:
            out[key] += '\n' + line
    return out


def goal_minutes(txt):
    mins = []
    for g in re.findall(r'\{\{\s*goal\s*\|([^}]*)\}\}', txt, re.I):
        for a in g.split('|'):
            a = a.strip().replace("'", '')
            if MIN.match(a):
                mins.append(a)
    return mins


def ninety(mins):
    return sum(1 for a in mins if int(MIN.match(a).group(1)) <= 90)


for f, date, h, a, eid, espn90 in TARGETS:
    w = json.load(open(os.path.join(D, f)))['parse']['wikitext']
    boxes = [fields(m.group(1)) for m in re.finditer(r'\{\{\s*Football box[^\n]*\n(.*?)\n\}\}', w, re.S | re.I)]
    hit = [b for b in boxes if date in b.get('date', '') and h in b.get('team1', '') and a in b.get('team2', '')]
    if len(hit) != 1:
        print(eid, h, 'v', a, 'boxes matched', len(hit)); continue
    b = hit[0]
    m1, m2 = goal_minutes(b.get('goals1', '')), goal_minutes(b.get('goals2', ''))
    final = re.sub(r'\s+', ' ', b.get('score', ''))
    w90 = '%d-%d' % (ninety(m1), ninety(m2))
    print(eid, '|', h, 'v', a, '|', date.replace('|', '-'), '| wiki score', final, '| aet', b.get('aet', '').strip() or '-',
          '| pens', b.get('penaltyscore', '').strip() or '-', '| goals1', m1, '| goals2', m2,
          '| wiki goals total', '%d-%d' % (len(m1), len(m2)), '| wiki 90-min', w90, '| ESPN 90-min (S1)', espn90,
          '| AGREE' if w90 == espn90 else '| DIFFER')
