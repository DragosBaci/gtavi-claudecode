---
name: campaign-submit
description: Submit posted clip links to the paid clipping campaigns they qualify for (Content Rewards / Whop, Clip) through Claude in Chrome, signed in as the human. Use after clips are posted, when the Earn page's "Submit with Claude" button runs, or when the user says "submit my clips", "submit to the campaign", "send the links to Kai's campaign".
---

# Campaign submit

When a clip goes live on a platform, `uploader.py mark` checks it against the campaigns the human **joined** (`campaigns.json`) and adds a `to_submit` entry per campaign and platform it qualifies for (`queue/submissions.json`). This skill sends those links to the campaigns' dashboards in Chrome and records the result. You submit; you never join, sign up, accept terms or handle money.

`UPL` = `python "${CLAUDE_PLUGIN_ROOT}/scripts/uploader.py"` (run from the clip desk folder).

## 1. What to submit

`UPL submissions` prints every `to_submit` entry: `clip`, `platform`, `url` (the live post), `campaign`, `campaign_name`, `submit_url` (the campaign's page). Nothing listed: say so and stop.

## 2. Hard stops (record and move on, never work around them)

- The dashboard isn't signed in, or asks for a password, a code, a CAPTCHA or identity/KYC: stop for that campaign. `UPL submitted <clip> <platform> <campaign> --status to_submit --note "needs you: <what>"` is not needed (it stays to_submit); tell the human in the final reply.
- The campaign page says you haven't joined, or asks to join / accept terms / connect an account: **don't**. Tell the human: "Join <campaign> on its page first".
- Payment, payout or tax settings, wallets, bank details: never open or change them.
- The campaign is ended, paused or out of budget: `UPL submitted <clip> <platform> <campaign> --status skipped --note "campaign ended"`.
- The form wants something you don't have (a different platform's link, a file, an answer you'd have to make up): skip it with `--status skipped --note "<what it wanted>"`.

## 3. Submit, one entry at a time

1. Open `submit_url` in a new tab (Content Rewards: `contentrewards.com/c/campaigns/<id>`; Whop: the campaign's page; Clip: their submission page). Check the signed-in account looks like the human's (avatar or name), not someone else's.
2. Find the submit control ("Submit", "Submit content", "Add submission", "Submit a clip"). Pick the platform the entry is for if asked.
3. Paste exactly the entry's `url`. Fill only fields the form requires and that you know from the entry (platform, the link). Leave optional fields empty.
4. Screenshot the filled form, then press the final submit button. Wait for the confirmation (the submission listed as pending/under review, or a success message).
5. Record it: `UPL submitted <clip> <platform> <campaign>` (status `submitted`). If the page already shows this link as submitted, record `submitted` with `--note "already submitted"`.
6. Next entry. Reuse the tab for entries of the same campaign.

The page shows a submission as approved, rejected or paid later: when you see that while there, record it with `--status approved|rejected|paid` and the reason in `--note`.

## 4. Finish

Close the tabs you opened. Reply with one line per entry: campaign, platform, submitted / skipped / needs you + why.

Never invent views, earnings or links. Never submit a link that isn't the entry's own post.
