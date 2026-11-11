import sys, hashlib, json
from app import tickets as t
LONG = ['Wolverhampton Wanderers', 'Brighton and Hove Albion', 'Borussia Monchengladbach', 'Paris Saint-Germain',
        'Nottingham Forest', 'Atletico Madrid', 'Bayer Leverkusen', 'Manchester United', 'Real Sociedad', 'Newcastle United']
legs = [dict(sport=s, forecast_id=i, game_id=f'g{i}', market='winner', probability=.6, tipoff='2026-11-08T18:00:00Z',
             matchup=f'{LONG[(i+3)%10]} @ {LONG[i%10]}', pick_label=f'{LONG[i%10]} win') for i in range(1, 6) for s in ('nba', 'nhl', 'nfl')]
out = []
for count, size in ((6, 2), (5, 3), (4, 4)):
    tickets = [dict(ticket_id=f'2026-11-08-{n:012d}', label=f'Option {n}', legs=legs[n*size:(n+1)*size]) for n in range(count)]
    try:
        out.append(f'{count}x{size}: {len(t.ticket_text("2026-11-08", tickets))}')
    except ValueError as e:
        out.append(f'{count}x{size}: refused ({e})')
print(sys.argv[1], '; '.join(out))
