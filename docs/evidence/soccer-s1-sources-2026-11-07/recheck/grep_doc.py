"""Usage: python3 -I grep_doc.py <file> <regex> [context_chars]. HTML->text, case-insensitive grep, prints context."""
import html, re, sys
t = open(sys.argv[1], encoding='utf-8', errors='replace').read()
t = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', t)
t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
n = int(sys.argv[3]) if len(sys.argv) > 3 else 250
hits = [m.start() for m in re.finditer(sys.argv[2], t, re.I)]
print('hits', len(hits), 'text_chars', len(t))
for s in hits[:8]:
    print('...', t[max(0, s - n):s + n], '...\n')
