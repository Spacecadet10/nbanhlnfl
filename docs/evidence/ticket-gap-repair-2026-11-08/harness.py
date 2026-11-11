"""E2E harness: run app.tickets.main() against isolated store copies with a MOCKED Telegram transport.

Every Telegram request is answered locally by httpx.MockTransport and logged to $MOCK_LOG; nothing leaves the machine.
The token is a fixed fake value, so even an unpatched call could not reach the paired chat.
MOCK_MODE: ok | stop (getUpdates returns /stop from the paired chat) | uncertain (sendMessage/sendPhoto -> HTTP 500)
"""
import json
import os
import sys

import httpx

os.environ['TELEGRAM_BOT_TOKEN'] = '000000:FAKE-TOKEN-FOR-ISOLATED-TEST'
os.environ['TELEGRAM_CHAT_ID'] = os.environ.get('E2E_CHAT', '924039348')
MODE = os.environ.get('MOCK_MODE', 'ok')
LOG = os.environ['MOCK_LOG']

from app import telegram as tg, tickets  # noqa: E402


def handler(request):
    method = request.url.path.rsplit('/', 1)[-1]
    entry = {'method': method}
    if method in ('sendMessage',):
        entry['text'] = json.loads(request.content)['text']
    if method == 'sendPhoto':
        body = request.content
        entry['photo_bytes'] = len(body)
        entry['caption'] = body.split(b'name="caption"\r\n\r\n', 1)[1].split(b'\r\n--', 1)[0].decode()
    with open(LOG, 'a') as handle:
        handle.write(json.dumps(entry) + '\n')
    if method == 'getMe':
        return httpx.Response(200, json={'ok': True, 'result': {'is_bot': True, 'username': 'Prop51picksbot'}})
    if method == 'getWebhookInfo':
        return httpx.Response(200, json={'ok': True, 'result': {'url': ''}})
    if method == 'getUpdates':
        updates = []
        if MODE == 'stop':
            updates = [{'update_id': 999999999990, 'message': {'chat': {'id': int(os.environ['TELEGRAM_CHAT_ID']), 'type': 'private'},
                                                         'text': '/stop'}}]
        return httpx.Response(200, json={'ok': True, 'result': updates})
    if MODE == 'uncertain':
        return httpx.Response(500, json={'ok': False})
    return httpx.Response(200, json={'ok': True, 'result': {'message_id': 900000 + sum(1 for _ in open(LOG))}})


if os.environ.get('E2E_FORCE_FRESH') == '1':
    # Labelled harness override for delivery-path tests only: the static copies cannot stay inside the 7-minute window.
    from app import freshness
    real_summary = freshness.summary

    def forced(conn, sport, now):
        return dict(real_summary(conn, sport, now), source_state='fresh', failing=False, e2e_forced_fresh=True)
    freshness.summary = forced

original = tg.Telegram.__init__


def mocked(self, token, *, transport=None):
    original(self, token, transport=httpx.MockTransport(handler))


tg.Telegram.__init__ = mocked
sys.argv = ['tickets', *sys.argv[1:]]
raise SystemExit(tickets.main())
