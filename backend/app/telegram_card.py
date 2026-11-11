"""Render the sender's existing, validated message into a data card. No prediction or ranking logic lives here."""
import io
import re
import json
from functools import lru_cache
import httpx
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageColor, ImageOps, ImageFilter

INK = '#152A33'
MUTED = '#61717B'
PAPER = '#FFFFFF'
LINE = '#E2E8EC'
LIME = '#14B8A6'


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _font(size, bold=False, display=False):
    fonts = Path(__file__).resolve().parents[2] / 'frontend/public/fonts'
    file = fonts / ('BarlowCondensed-Bold.ttf' if display else 'Inter.ttf')
    if file.is_file():
        font = ImageFont.truetype(str(file), size)
        if not display:
            font.set_variation_by_axes([14, 600 if bold else 400])
        return font
    avenir = Path('/System/Library/Fonts/Avenir Next.ttc')
    if avenir.is_file():
        return ImageFont.truetype(str(avenir), size, index=2 if bold else 7)
    paths = (['/System/Library/Fonts/Supplemental/Arial Bold.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'] if bold else
             ['/System/Library/Fonts/Supplemental/Arial.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    for path in paths:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _logo(league, abbr):
    """Public team art, cached locally; an unavailable logo leaves the real team abbreviation visible."""
    from . import config
    if league not in ('NBA', 'NHL', 'NFL') or not re.fullmatch(r'[A-Z]{2,5}', abbr):
        return None
    aliases = {'GSW':'GS','NYK':'NY','NOP':'NO','SAS':'SA','UTA':'UTAH','WAS':'WSH'} if league == 'NBA' else (
        {'NJD':'NJ','LAK':'LA','SJS':'SJ','TBL':'TB'} if league == 'NHL' else {})
    code = aliases.get(abbr, abbr).lower()
    path = config.data_dir() / 'assets/teamlogos' / league.lower() / (code + '.png')
    try:
        if path.is_file():
            body = path.read_bytes()
        else:
            response = httpx.get(f'https://a.espncdn.com/i/teamlogos/{league.lower()}/500/{code}.png', timeout=3)
            response.raise_for_status()
            body = response.content
            if len(body) > 2_000_000:
                return None
        logo = Image.open(io.BytesIO(body)).convert('RGBA')
        logo.thumbnail((154, 154))
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return logo
    except (httpx.HTTPError, OSError, ValueError):
        return None


def _mix(a, b, weight):
    a, b = ImageColor.getrgb(a), ImageColor.getrgb(b)
    return '#%02x%02x%02x' % tuple(round(x*(1-weight)+y*weight) for x,y in zip(a,b))


@lru_cache(maxsize=1)
def _team_branding():
    return json.loads(Path(__file__).with_name('team_colors.json').read_text())['teams']


def _team_palette(league, abbr):
    """Published team branding fields, never colors inferred from pixels. Unknown teams stay neutral."""
    aliases = {'GSW':'GS','NYK':'NY','NOP':'NO','SAS':'SA','UTA':'UTAH','WAS':'WSH','PHO':'PHX','BRK':'BKN'} if league == 'NBA' else ({'LAK':'LA','NJD':'NJ','SJS':'SJ','TBL':'TB','UTA':'UTAH'} if league == 'NHL' else {})
    team = _team_branding().get(league, {}).get(aliases.get(abbr, abbr))
    return (team['primary'],team['secondary']) if team else ('#09283F','#8495A2')


def fit_line(draw, text, font, width):
    """The text on one line of `width` px: whole items (split at ' · ' then ', ') are dropped from the end and replaced
    with '…' until it fits; a single item that is still too long is cut with '…'. Never wraps, never overflows."""
    if draw.textlength(text, font=font) <= width:
        return text
    words = text.split(' ')
    while len(words) > 1:
        words.pop()
        candidate = ' '.join(words).rstrip(',·: ') + ' …'
        if draw.textlength(candidate, font=font) <= width:
            return candidate
    cut = text
    while cut and draw.textlength(cut + '…', font=font) > width:
        cut = cut[:-1]
    return cut + '…'


def render_card(message):
    parser = Plain()
    parser.feed(message)
    lines = [line.strip() for line in ''.join(parser.parts).splitlines()]
    brand = next(i for i, line in enumerate(lines) if line.startswith('PROP51 PICKS'))
    rec = lines.index('Recommended pick')
    markets = lines.index('Market breakdown')
    footer = max(i for i, line in enumerate(lines) if line == '━━━━━━━━━━━━━━')
    groups, group = [], []
    for line in lines[markets + 1:footer] + ['']:
        if line:
            group.append(line)
        elif group:
            groups.append(group)
            group = []
    preview = any('TEST PREVIEW' in line for line in lines[:brand])
    league = lines[brand].rsplit(' · ', 1)[-1]
    if league not in ('NBA','NHL','NFL'):
        raise ValueError('Telegram card requires a recognized league label.')
    matchup = lines[brand + 1]
    teams = re.fullmatch(r'([A-Z]{2,5}) @ ([A-Z]{2,5})', matchup)
    logos = [_logo(league, abbr) for abbr in teams.groups()] if teams else [None,None]
    primary, accent = _team_palette(league, teams.group(1) if teams else '')
    home_primary, home_accent = _team_palette(league, teams.group(2) if teams else '')
    if max(ImageColor.getrgb(accent))-min(ImageColor.getrgb(accent)) < 30:
        accent = primary
    panel = _mix(primary, '#001424', .65)
    cream = _mix(accent, '#FFFFFF', .96)
    pale = _mix(accent, '#FFFFFF', .83)
    border = _mix(accent, '#C2BEB6', .75)
    image = Image.new('RGB', (1080, 7000), cream)
    draw = ImageDraw.Draw(image)

    def wrapped(text, x, y, width, *, size=30, bold=False, fill=INK, display=False):
        font = _font(size, bold, display)
        words, row = text.split(), ''
        rows = []
        for word in words:
            candidate = (row + ' ' + word).strip()
            if row and draw.textlength(candidate, font=font) > width:
                rows.append(row)
                row = word
            else:
                row = candidate
        if row:
            rows.append(row)
        for row in rows:
            draw.text((x, y), row, font=font, fill=fill, stroke_width=0)
            y += int(size * 1.28)
        return y

    draw.text((74, 38), 'PROP51', font=_font(50, True, True), fill=primary)
    draw.line((319,47,319,89), fill=accent, width=2)
    draw.text((345, 53), 'GAME CARD', font=_font(27, True), fill=INK)
    draw.rounded_rectangle((820, 41, 1005, 98), radius=27, fill=pale)
    draw.text((865, 48), league, font=_font(35, True), fill=primary)
    draw.line((73,113,1007,113), fill=accent, width=2)
    if teams:
        for abbr, logo, x, team_ink in zip(teams.groups(), logos, (260,820), (primary,home_primary)):
            if logo is not None:
                image.paste(logo,(x-logo.width//2,202-logo.height//2),logo)
            width = draw.textlength(abbr,font=_font(53,True,True))
            draw.text((x-width/2,267),abbr,font=_font(53,True,True),fill=team_ink)
        draw.text((519,190),'@',font=_font(45,True),fill=MUTED)
    else:
        wrapped(matchup,80,181,920,size=50,bold=True)
    width = draw.textlength(lines[brand+2],font=_font(27))
    draw.text(((1080-width)/2,340),lines[brand+2],font=_font(27),fill=MUTED)
    y=402
    if preview:
        draw.rounded_rectangle((58,y,1022,y+54),radius=18,fill=pale)
        draw.text((88,y+12),'TEST PREVIEW / DESIGN EXAMPLE — NOT A NEW LIVE PICK',font=_font(25,True),fill=primary)
        y+=74
    # A full outline replaces the asymmetric stripe from the reference.
    # Owner request Nov 11: one plain "who is playing" line under the pick, only when there is something to say.
    lineup=next((line.removeprefix('Lineup: ') for line in lines[rec+2:markets] if line.startswith('Lineup: ')),None)
    pick_size=72 if len(lines[rec+1])<22 else 48
    detail=next((line for line in lines[rec+2:markets] if line and line!='━━━━━━━━━━━━━━' and not line.startswith('Lineup: ')),'')
    def rows(text,width,size,bold=False):
        n,row=0,''
        for word in text.split():
            cand=(row+' '+word).strip()
            if row and draw.textlength(cand,font=_font(size,bold,bold))>width:
                n,row=n+1,word
            else:
                row=cand
        return n+(1 if row else 0)
    detail_top=max(155,59+rows(lines[rec+1],810,pick_size,True)*int(pick_size*1.28)+6)
    detail_end=detail_top+rows(detail,835,27)*int(27*1.28)
    lineup_top=max(168,detail_end+4)          # review LOW 10: never over a pick or detail that wraps
    panel_h=max(218,lineup_top+50) if lineup else 218
    gradient=ImageOps.colorize(Image.linear_gradient('L').rotate(90).resize((964,panel_h)),panel,_mix(home_primary,panel,.7))
    mask=Image.new('L',(964,panel_h),0);ImageDraw.Draw(mask).rounded_rectangle((0,0,963,panel_h-1),radius=27,fill=255)
    image.paste(gradient,(58,y),mask)
    draw.rounded_rectangle((58,y,1022,y+panel_h),radius=27,outline=accent,width=3)
    draw.text((92,y+18),'RECOMMENDED PICK',font=_font(26,True),fill=_mix(accent,'#FFFFFF',.65))
    end=wrapped(lines[rec+1],92,y+59,810,size=pick_size,bold=True,fill='white',display=True)
    wrapped(detail,95,max(y+155,end+6),835,size=27,fill='#E0E6EC')
    if lineup:
        draw.text((95,y+lineup_top),fit_line(draw,lineup,_font(26,True),860),font=_font(26,True),fill='#FFD27A')
    y+=panel_h+24
    draw.text((64,y),'MARKET BREAKDOWN',font=_font(31,True,True),fill=primary)
    y+=53
    for number, group in enumerate(groups, 1):
        model = next(line for line in group if line.startswith('Model '))
        probability, record = model.split(' · ', 1)
        pick, _, provenance = group[1].partition(' · ')
        result = next((line.removeprefix('Result: ') for line in group if line.startswith('Result: ')), None)
        opposite = next((line for line in group if line.startswith(('Against model', 'Opposite scenario'))), None)
        note = next((line for line in group if line in ('Small sample.', 'No settled matching examples.')), '')
        label_lines = max(1,int(draw.textlength(pick,font=_font(34,True))/470)+1)
        opposite_lines = max(1,int(draw.textlength(opposite,font=_font(24,True))/820)+1) if opposite else 0
        height=130+(label_lines-1)*44+(30 if provenance else 0)+(opposite_lines*32+12 if opposite else 0)
        draw.rounded_rectangle((58,y,1022,y+height),radius=25,fill=cream,outline=border,width=2)
        draw.rounded_rectangle((78,y+25,149,y+86),radius=17,fill=pale)
        draw.text((91,y+31),f'{number:02d}',font=_font(31,True,True),fill=primary)
        draw.line((174,y+21,174,y+height-21),fill=accent,width=2)
        draw.text((199,y+10),group[0].upper(),font=_font(21,True),fill=MUTED)
        team_ink = home_primary if teams and pick.startswith(teams.group(2)+' ') else primary
        end=wrapped(pick,199,y+38,470,size=38,bold=True,fill=team_ink,display=True)
        if result:
            bg,color=('#E6F4E4','#16832C') if result=='Won' else ('#FDE7E8','#C51D35') if result=='Lost' else ('#E9EDF0',MUTED)
            draw.rounded_rectangle((711,y+33,835,y+91),radius=16,fill=bg)
            w=draw.textlength(result,font=_font(27,True))
            draw.text((773-w/2,y+45),result,font=_font(27,True),fill=color)
        draw.rounded_rectangle((847,y+33,1004,y+91),radius=16,fill=_mix(primary,'#FFFFFF',.92))
        prob=probability.removeprefix('Model ')
        w=draw.textlength(prob,font=_font(34,True))
        draw.text((925-w/2,y+41),prob,font=_font(34,True),fill=primary)
        draw.text((200,end+4),record+(' · '+note if note else ''),font=_font(22),fill=INK)
        if provenance:
            draw.text((200,end+33),provenance.capitalize(),font=_font(20),fill=MUTED)
        if opposite:
            wrapped(opposite,200,y+height-opposite_lines*32-8,800,size=24,bold=True,fill=primary)
        y+=height+8
    y += 9
    note = 'Same sport, market, direction, percentage and model.'
    if preview and any(line in ('Result: Won','Result: Lost') for line in lines):
        note += ' Records include this game’s result.'
    y = wrapped(note, 58, y, 964, size=24, fill=MUTED)
    if any(line.startswith(('Against model', 'Opposite scenario')) for line in lines):
        y = wrapped('Experimental comparisons are unvalidated. Model lines and current opposite-side prices are not verified offers.', 58, y + 8, 964, size=24, fill=MUTED)
    y = wrapped(lines[-1], 58, y + 14, 964, size=24, fill=MUTED) + 36
    if y > 7000 or 1080 + y > 10000:
        raise ValueError('Telegram card is too tall to deliver safely.')
    output = io.BytesIO()
    content=image.crop((0,0,1080,y))
    outer=Image.new('RGB',(1080,y+40),_mix(accent,'#FFFFFF',.88))
    shadow=Image.new('RGBA',outer.size,(0,0,0,0))
    ImageDraw.Draw(shadow).rounded_rectangle((28,20,1052,y+14),radius=42,fill=(0,0,0,45))
    outer.paste(shadow.filter(ImageFilter.GaussianBlur(16)),(0,0),shadow.filter(ImageFilter.GaussianBlur(16)))
    frame=Image.new('L',(1080,y),0);ImageDraw.Draw(frame).rounded_rectangle((27,0,1053,y-1),radius=40,fill=255)
    outer.paste(content,(0,13),frame)
    outer.save(output, format='PNG', optimize=True)
    return output.getvalue()


# Soccer goal/corner/double-chance/draw-no-bet legs (review MEDIUM 2, Nov 9): picture-only delivery sends no text, so the
# card itself names each leg's line source and the limits that the text version states.
SOCCER_MARKET_NOTE = ('Soccer goal/corner/DC/DNB legs: 90 min + stoppage time; model line = no sportsbook line stored; '
                      'draw no bet and whole lines push (stake back). No proven skill against the bookmaker.')


def _soccer_market(leg):
    return leg.get('sport') == 'soccer' and leg.get('market', '1x2_regulation') != '1x2_regulation'


def _market_line_source(leg):
    """A short source for a soccer market leg, appended to its probability line: 'model line' (no sportsbook line stored),
    'DraftKings <price> at <time>' or 'no line' (double chance / draw no bet); None for any other leg. The footer
    (SOCCER_MARKET_NOTE) explains 'model line' and carries no-proven-skill and the push rule."""
    if not _soccer_market(leg):
        return None
    q = leg.get('quote_check') or {}
    if q.get('line_source') == 'bookmaker':
        from datetime import datetime
        from zoneinfo import ZoneInfo
        at = q.get('retrieved_at')
        when = (' at ' + datetime.fromisoformat(at.replace('Z', '+00:00')).astimezone(ZoneInfo('America/Toronto')).strftime('%I:%M %p')
                if at else '')
        price = f" {q['price_now']:+d}" if q.get('price_now') is not None else ''
        return f"{q.get('provider') or 'DraftKings'}{price}{when}"
    return 'no line' if leg.get('line') is None else 'model line'


def render_ticket_card(day, tickets, *, preview=False, number_from=1, total=None):
    """Ticket layout using the shared fonts, team logos and published palettes. A picture too tall with the per-leg
    sportsbook prices is drawn without them: they never make a ticket fail that would otherwise be drawn."""
    last = None
    for book_lines, shape_lines in ((True, True), (False, True), (False, False)):   # extras go before a ticket fails
        try:
            return _ticket_card(day, tickets, preview=preview, number_from=number_from, total=total,
                                book_lines=book_lines, shape_lines=shape_lines)
        except ValueError as exc:
            if 'layout bounds' not in str(exc):
                raise
            last = exc
    raise last


def _ticket_card(day, tickets, *, preview, number_from, total, book_lines, shape_lines=True):
    from .tickets import CARD_TICKETS, MAX_TICKETS, MIN_LEGS, MAX_LEGS, bet_type
    if not tickets or len(tickets) > MAX_TICKETS or any(not MIN_LEGS <= len(t['legs']) <= MAX_LEGS for t in tickets):
        raise ValueError('Ticket cards require one to six tickets of two to seven legs.')
    extra = len(tickets) - CARD_TICKETS
    tickets = tickets[:CARD_TICKETS]   # the readable text lists every option
    first = tickets[0]['legs'][0]
    abbr = first['matchup'].split(' @ ')[0]
    primary, accent = _team_palette(first['sport'].upper(), abbr)
    canvas = Image.new('RGB', (1080, 6000), _mix(accent, PAPER, .96))
    draw = ImageDraw.Draw(canvas)
    y = 48
    priced = False

    def line(text, *, size=29, bold=False, color=INK, x=72, width=936):
        nonlocal y
        font = _font(size, bold, display=bold)
        row = ''
        for word in str(text).split():
            candidate = (row + ' ' + word).strip()
            if row and draw.textlength(candidate, font=font) > width:
                draw.text((x,y),row,font=font,fill=color)
                y += int(size*1.3)
                row = word
            else:
                row = candidate
        draw.text((x,y),row,font=font,fill=color)
        y += int(size*1.3)

    line('PROP51 / COMBINED PARLAY TICKETS', size=49, bold=True, color=primary)
    line(day, size=28, color=MUTED)
    if total and total > len(tickets):   # one of several pictures: say which tickets this one holds
        line(f"Tickets {number_from}-{number_from + len(tickets) - 1} of {total}", size=28, bold=True, color=primary)
    if preview:
        line('TEST / PAST GAMES / RESULTS KNOWN', size=29, bold=True, color='#A83824')
    y += 20
    for ticket_number, ticket in enumerate(tickets, number_from):
        draw.rounded_rectangle((52,y,1028,y+78),radius=20,fill=primary)
        y += 11
        line(f"Ticket {ticket_number}: {ticket['label']}",size=32,bold=True,color=PAPER,x=76,width=928)
        y += 26
        line('Combine ALL listed legs in ONE parlay.',size=25,bold=True,color=primary)
        if ticket.get('ticket_id'):
            line(f"ID / {ticket['ticket_id']}",size=23,color=MUTED)
        if ticket.get('stake_usd') is not None:
            line(f"{'Example stake' if preview else 'Suggested stake'} / ${ticket['stake_usd']:.2f}",size=31,bold=True,color=primary)
            y += 16
        for index, leg in enumerate(ticket['legs'], 1):
            league = leg['sport'].upper()
            teams = leg['matchup'].split(' @ ')
            logos = [_logo(league, team) for team in teams]
            for logo, x in zip(logos,(78,150)):
                if logo is not None:
                    logo = logo.copy()
                    logo.thumbnail((62,62))
                    canvas.paste(logo,(x,y),logo)
            line(f"{league} / {leg['matchup']} / {bet_type(leg)}",size=27,bold=True,x=240,width=760)
            line(f"{index}. {leg['pick_label']}",size=38,bold=True,x=240,width=760)
            if leg.get('tipoff'):
                from datetime import datetime
                from zoneinfo import ZoneInfo
                start = datetime.fromisoformat(leg['tipoff'].replace('Z','+00:00')).astimezone(ZoneInfo('America/Toronto'))
                line(start.strftime('%b %d / %I:%M %p %Z'),size=23,color=MUTED,x=240,width=760)
            if preview:
                line(leg['result'],size=27,color='#11664D',x=240,width=760)
            else:
                source = _market_line_source(leg)      # same line: a separate one overflows 3 x 7 market legs
                line(f"Model probability {leg['probability']:.1%}" + (f" · {source}" if source else ''),size=27,color=MUTED,x=240,width=760)
                books = (leg.get('book_comparison') or {}).get('prices') or {}
                if books and book_lines:   # the same pick on the same line at each sportsbook
                    line(' · '.join(f"{book} {price:+d}" for book, price in sorted(books.items())),size=23,color=MUTED,x=240,width=760)
            y += 18
            draw.line((72,y,1008,y),fill=_mix(accent,PAPER,.65),width=2)
            y += 24
        probability = 1
        for leg in ticket['legs']:
            probability *= leg['probability']
        line(f"{'Pregame model estimate' if preview else 'Model estimate'} / {probability:.1%}",size=30,bold=True,color=primary)
        if preview:
            line(ticket['result'],size=34,bold=True,color='#11664D')
        else:
            from .tickets import best_book_line, ticket_assessment
            try:
                best = best_book_line(ticket.get('assessment') or ticket_assessment(ticket['legs']))
            except Exception:
                best = None
            if best and book_lines:
                line(best,size=30,bold=True,color=primary)
                priced = True
            if shape_lines:     # the ticket's shape in plain words: how often all legs land, and which leg is weakest
                from .tickets import ticket_shape
                try:
                    shape = ticket.get('shape') or ticket_shape(ticket['legs'])
                    line(shape['all_land_line'],size=27,color=INK)
                    line(shape['weakest_line'],size=27,color=INK)
                except Exception:
                    pass
        y += 36
    if extra > 0:
        line(f"+{extra} more option{'s' if extra > 1 else ''} in the message above.",size=27,bold=True,color=primary)
    identities = [(p['sport'], p.get('game_id', p['matchup'])) for t in tickets for p in t['legs']]
    shared = len(identities) != len(set(identities))
    line('App forecasts + AI selection. Distinct games within each ticket.',size=24,color=MUTED)
    if shared:
        line('Shared core picks: one loss can affect multiple tickets.',size=24,color=MUTED)
    line('Joint estimates assume independence. ' + ('Sportsbook prices are stored quotes: confirm before betting.' if priced
                                                   else 'Sportsbook prices unverified.'),size=24,color=MUTED)
    if any(_soccer_market(p) for t in tickets for p in t['legs']):
        line(SOCCER_MARKET_NOTE,size=24,color=MUTED)
    y += 35
    if y > 6000:
        raise ValueError('Ticket card exceeds layout bounds.')
    draw.rounded_rectangle((26,22,1054,y-5),radius=30,outline=_mix(accent,PAPER,.45),width=3)
    output = io.BytesIO()
    canvas.crop((0,0,1080,y+18)).save(output,format='PNG',optimize=True)
    return output.getvalue()
