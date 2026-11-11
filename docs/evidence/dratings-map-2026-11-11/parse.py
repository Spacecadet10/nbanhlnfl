import re,html,sys
def cells(tr): return [re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',c))).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
def tables(path):
    t=open(path).read(); out=[]
    for tb in re.findall(r'<table.*?</table>', t, re.S):
        head=re.findall(r'<thead.*?</thead>', tb, re.S)
        h=[re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',x))).strip() for x in re.findall(r'<th[^>]*>(.*?)</th>', head[0], re.S)] if head else []
        h=[re.split(r' (We use|Log loss|Using|Our)',x)[0] for x in h]
        rows=re.findall(r'<tr.*?</tr>', re.sub(r'<thead.*?</thead>','',tb,flags=re.S), re.S)
        out.append((h,[(cells(r), re.findall(r'datetime="([^"]+)"', r), re.findall(r'href="(/predictor/[^"]+/[0-9a-f-]{36})"', r)[:1]) for r in rows]))
    return t,out
if __name__=='__main__':
    for f in sys.argv[1:]:
        t,tb=tables(f)
        hs=[re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>','',m))).strip() for m in re.findall(r'<h[23][^>]*>(.*?)</h[23]>',t,re.S)][:6]
        print('=====',f, hs, 'pagination', sorted(set(re.findall(r'/(?:completed|upcoming)/\d+',t))))
        for h,rows in tb:
            print(' HEAD',h,'rows',len(rows)); 
            for r in rows[:1]: print('   ',r)
