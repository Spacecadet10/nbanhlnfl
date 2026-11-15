// The one parser for /games/<id> links, shared by the layout and every league's screens (pure, so Node tests it
// directly: src/components/gameRoute.test.mjs). decodeURIComponent throws on a malformed percent-encoding such as
// /games/%E0%A4%A; called during render, that blanked the whole app. Here it becomes a state the page can show.

export type GameRoute = { kind: 'none' } | { kind: 'game'; id: string } | { kind: 'malformed' };

export function gameRoute(pathname: string): GameRoute {
  if (!pathname.startsWith('/games/')) return { kind: 'none' };
  const raw = pathname.slice('/games/'.length);
  if (!raw || raw.includes('/')) return { kind: 'malformed' };
  let id: string;
  try { id = decodeURIComponent(raw); } catch { return { kind: 'malformed' }; }
  return id.trim() ? { kind: 'game', id } : { kind: 'malformed' };
}
