import os, re, sys, tarfile, zipfile
hashes = set(open(sys.argv[1]).read().split())
roots = sys.argv[2:]
hexre = re.compile(r'([0-9a-f]{64})')
found = {}; scanned = 0; archives = []; errors = []
for root in roots:
    if not os.path.exists(root):
        print("ROOT_ABSENT", root); continue
    for dp, dns, fns in os.walk(root, onerror=lambda e: errors.append(str(e))):
        for fn in fns:
            scanned += 1
            m = hexre.search(fn)
            if m and m.group(1) in hashes:
                found.setdefault(m.group(1), []).append(os.path.join(dp, fn))
            low = fn.lower()
            if low.endswith(('.tar', '.tar.gz', '.tgz', '.zip')):
                archives.append(os.path.join(dp, fn))
print("scanned_files", scanned, "matched_hashes", len(found), "archives", len(archives), "walk_errors", len(errors))
for a in archives:
    try:
        names = zipfile.ZipFile(a).namelist() if a.lower().endswith('.zip') else tarfile.open(a).getnames()
        hit = [n for n in names if (m := hexre.search(os.path.basename(n))) and m.group(1) in hashes]
        print("ARCHIVE", a, len(names), "hits", len(hit))
        for n in hit: found.setdefault(hexre.search(os.path.basename(n)).group(1), []).append(a + "::" + n)
    except Exception as e:
        print("ARCHIVE_ERR", a, type(e).__name__)
for h, locs in sorted(found.items()):
    for l in locs: print("FOUND", h, l)
for e in errors[:10]: print("ERR", e)
