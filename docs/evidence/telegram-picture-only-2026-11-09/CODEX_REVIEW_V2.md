# Codex re-review and activation: Telegram picture delivery v2

Verdict: APPROVE scoped v2 patch. Activated guarded release commit `76d8ab42583887b1d37934c1e700648a34e4dd50` from clean `1121d608c7834e0893a704bcaac5e100b93acb48` with `git merge --ff-only`. Six author patch files were byte-identical to the tested export; commit also records review in HANDOFF.md. No unrelated main-checkout work staged.

## Review

- All accepted tickets are placed whole on pages of at most three tickets and nine legs. Caption fit checked before reservation; no truncation. Seven-leg preview visually inspected with ID/start and seventh leg visible. Existing full-text size guard still refuses large menus. Correction to first review: six seven-leg menus already failed that guard; the omission finding applied to smaller accepted menus.
- Archived image hash failure raises before sending. Renderer failure deliberately falls back to short caption text once, records failed image, returns partial_failed; this is the author/owner requirement to keep tickets arriving, not a hidden long-text fallback. Legacy text-sent batches retain original recovery path.
- Existing game-card keys preserved. New guard compares historical stored card identity, suppressing moved-line/reissued forecast repeats including uncertain/sending receipts. New side/start may send. Parser depends on existing card format; keep future formatting changes coordinated.
- Picture pages use independent receipts; uncertain pages are never blindly resent, earlier pages deduplicate. First batch/image receipts reference the same photo message. Existing receipt rows unchanged.

## Actual verification

- Isolated export `/tmp/telegram-v2-review.83GsFm`, release Python3.12.12. Five files: `tests/test_tickets.py tests/test_telegram.py tests/test_delivery_release.py tests/test_nfl_live.py tests/test_soccer_live.py`: **237 passed,1warning,20.75s**, mocked Telegram/private synthetic data. Log `/tmp/telegram-v2-review.83GsFm/tests.log`.
- `python3 scripts/check_project.py`: passed in isolated patched export. `git diff --check`: passed. Code/docs patch files matched tested export byte-for-byte before commit.
- Seven-leg layout rendered and inspected at `/tmp/telegram-v2-review.83GsFm/seven-leg-preview.png` (synthetic TEST ONLY, never sent).
- Post-activation pinned helper `status`: exit0, no release-guard refusal. Delivery checkout clean at76d8ab4. Existing receipt IDs305/306 and322/323 still sent.
- Installed review skill updated only for picture/page/receipt behavior, quiet health handling, seven-leg limit; previous skill archived in `skill-before-picture-delivery.md`. `quick_validate.py`: Skill is valid. New hash431a2de3d5fc4df32c08824bc6daad8451291d8a7f12a38b1182851768e2f85f. Scheduled automation updated via app tool, remains ACTIVE on same15-minute schedule; evidence-based selection preserved, no nightly forced long-shot quota.
- No real Telegram send, full backend suite, frontend/browser smoke or complete app readiness claimed. First genuinely justified new ticket will exercise production picture delivery; existing ticket receipts were not resent or rewritten.

## Remaining

GitHub token was invalid on previous check; no push claimed. Other active implementation branches must integrate this reviewed commit before replacing the release. This activation does not validate betting value, change forecasts or authorize tuning.
