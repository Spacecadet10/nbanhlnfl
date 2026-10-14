import gzip,json,re,sys,collections,html as H
def load(p):
    b=open(p,'rb').read()
    if b[:2]==b'\x1f\x8b': b=gzip.decompress(b)
    return b.decode('utf-8','ignore')
def parse(h):
    m=re.search(r"window\['__espnfitt__'\]\s*=\s*(\{.*?\});</script>",h,re.S)
    rows=[]
    if m:
        inj=json.loads(m.group(1))['page']['content']['injuries']
        for t in inj:
            for it in t['items']:
                desc=it.get('description','')
                rd=re.match(r'([A-Z][a-z]{2} \d{1,2}):',desc)
                rows.append(dict(team=t['displayName'],player=it['athlete']['name'],status=it.get('statusDesc'),
                    date_field=it.get('date'),comment_date=rd.group(1) if rd else None,comment=desc))
        return 'json',rows
    team=None; last=None
    for tr in re.findall(r'<tr class="([^"]*)"[^>]*>(.*?)</tr>',h,re.S):
        cls,body=tr; cells=[H.unescape(re.sub('<[^>]+>','',c)).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>',body,re.S)]
        if cls=='stathead': team=cells[0]
        elif ('oddrow' in cls or 'evenrow' in cls):
            if len(cells)==3: last=dict(team=team,player=cells[0],status=cells[1],date_field=cells[2],comment_date=cells[2],comment=''); rows.append(last)
            elif len(cells)==1 and last: last['comment']=cells[0].replace('Comment:','').strip()
    return 'table',rows
if __name__=='__main__':
    for p in sys.argv[1:]:
        kind,rows=parse(load(p))
        uniq={(r['team'],r['player']) for r in rows}
        st=collections.Counter(r['status'] for r in rows)
        teams=len({r['team'] for r in rows})
        nodate=sum(1 for r in rows if not r['comment_date'])
        print(p.split('/')[-1],kind,'rows',len(rows),'unique players',len(uniq),'teams',teams,'statuses',dict(st),'rows w/o report date',nodate)
        for r in rows[:2]: print('   ',r)
