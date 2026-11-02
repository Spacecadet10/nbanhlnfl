"""Render the sender's existing, validated message into a data card. No prediction or ranking logic lives here."""
import io
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

INK = '#1E1B4B'
MUTED = '#5D5A85'
PAPER = '#F8FAFC'
LINE = '#E4E2EE'
LIME = '#A3E635'


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _font(size, bold=False):
    paths = (['/System/Library/Fonts/Supplemental/Arial Bold.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'] if bold else
             ['/System/Library/Fonts/Supplemental/Arial.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    for path in paths:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


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
    image = Image.new('RGB', (1080, 7000), PAPER)
    draw = ImageDraw.Draw(image)

    def wrapped(text, x, y, width, *, size=30, bold=False, fill=INK):
        font = _font(size, bold)
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

    # Deliberately no logos, emojis or decorative iconography.
    draw.rounded_rectangle((32, 32, 1048, 244), radius=32, fill=INK)
    draw.text((70, 58), lines[brand], font=_font(30, True), fill='#D7D3ED')
    wrapped(lines[brand + 1], 70, 105, 930, size=58, bold=True, fill='white')
    draw.text((70, 190), lines[brand + 2], font=_font(28), fill='#D7D3ED')
    y = 266
    if preview:
        draw.rounded_rectangle((48, y, 1032, y + 54), radius=12, fill='#FEF3C7')
        draw.text((72, y + 12), 'TEST PREVIEW  /  PAST GAME — NOT A LIVE PICK', font=_font(24, True), fill='#854D0E')
        y += 76

    draw.rounded_rectangle((48, y, 1032, y + 238), radius=28, fill='#F2FBE0', outline='#D7EABB', width=2)
    draw.rounded_rectangle((48, y, 58, y + 238), radius=5, fill=LIME)
    draw.text((84, y + 26), 'RECOMMENDED PICK', font=_font(27, True), fill='#3F6212')
    end = wrapped(lines[rec + 1], 82, y + 69, 900, size=74 if len(lines[rec + 1]) < 22 else 52, bold=True)
    detail = next((line for line in lines[rec + 2:markets] if line and line != '━━━━━━━━━━━━━━'), '')
    wrapped(detail, 86, max(y + 164, end + 7), 900, size=28, fill=MUTED)
    y += 274
    draw.text((54, y), 'MARKET BREAKDOWN', font=_font(29, True), fill=INK)
    y += 56
    for group in groups:
        model = next(line for line in group if line.startswith('Model '))
        probability, record = model.split(' · ', 1)
        pick, _, provenance = group[1].partition(' · ')
        result = next((line.removeprefix('Result: ') for line in group if line.startswith('Result: ')), None)
        opposite = next((line for line in group if line.startswith(('Against model', 'Opposite scenario'))), None)
        note = next((line for line in group if line in ('Small sample.', 'No settled matching examples.')), '')
        # Measure the variable-height block, then draw it. All content comes from the same message as text delivery.
        label_lines = max(1, int(draw.textlength(pick, font=_font(40, True)) / 760) + 1)
        opposite_lines = (max(1, int(draw.textlength(opposite, font=_font(26, True)) / 880) + 1) if opposite else 0)
        height = (196 if provenance else 168) + (label_lines - 1) * 50 + (opposite_lines * 34 + 22 if opposite else 0)
        draw.rounded_rectangle((48, y, 1032, y + height), radius=24, fill='white', outline=LINE, width=2)
        draw.text((76, y + 21), group[0], font=_font(27, True), fill=MUTED)
        if result:
            color = '#3F6212' if result == 'Won' else '#BE123C' if result == 'Lost' else MUTED
            draw.text((964 - draw.textlength(result, font=_font(27, True)), y + 21), result, font=_font(27, True), fill=color)
        end = wrapped(pick, 76, y + 62, 745, size=40, bold=True)
        draw.text((982 - draw.textlength(probability.removeprefix('Model '), font=_font(38, True)), y + 62), probability.removeprefix('Model '), font=_font(38, True), fill=INK)
        draw.text((78, end + 5), record + ('  ·  ' + note if note else ''), font=_font(27), fill=MUTED)
        if provenance:
            draw.text((78, end + 42), provenance.capitalize(), font=_font(23), fill=MUTED)
        if opposite:
            wrapped(opposite, 78, y + height - opposite_lines * 34 - 14, 880, size=26, bold=True, fill='#854D0E')
        y += height + 18
    y += 9
    note = 'Same sport, market, direction, percentage and model.'
    if preview:
        note += ' Records include this game’s result.'
    y = wrapped(note, 58, y, 964, size=24, fill=MUTED)
    if any(line.startswith(('Against model', 'Opposite scenario')) for line in lines):
        y = wrapped('Experimental comparisons are unvalidated. Model lines and current opposite-side prices are not verified offers.', 58, y + 8, 964, size=24, fill=MUTED)
    y = wrapped(lines[-1], 58, y + 14, 964, size=24, fill=MUTED) + 36
    if y > 7000 or 1080 + y > 10000:
        raise ValueError('Telegram card is too tall to deliver safely.')
    output = io.BytesIO()
    image.crop((0, 0, 1080, y)).save(output, format='PNG', optimize=True)
    return output.getvalue()
