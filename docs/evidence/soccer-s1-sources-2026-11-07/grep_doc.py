# Usage: python3 -I grep_doc.py <file> <regex> [width]  -- strips HTML, prints matches with context
import sys, re, html
t = open(sys.argv[1], encoding='utf-8', errors='replace').read()
t = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', t)
t = html.unescape(re.sub(r'<[^>]+>', ' ', t)); t = re.sub(r'\s+', ' ', t)
w = int(sys.argv[3]) if len(sys.argv) > 3 else 250
seen = -10**9
for m in re.finditer(sys.argv[2], t, flags=re.I):
    if m.start() - seen < w: continue
    seen = m.start(); print('...', t[max(0, m.start()-w):m.start()+w], '...\n')
