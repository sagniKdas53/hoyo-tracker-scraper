# Hermes Cron Prompts

This file is the canonical home for the shared Hermes cron jobs that use the
HoYo and WuWa scraper repos together.

Before pasting any prompt into Hermes, replace these placeholders once:

- `<HOYO_REPO>`: path to the cloned `hoyo-tracker-scraper` repo
- `<WUWA_REPO>`: path to the cloned `wuwa-timeline-scraper` repo
- `<STATE_DIR>`: path to a persistent writable state directory shared by all jobs

Recommended layout inside Hermes:

- `<HOYO_REPO>` = `/workspace/hoyo-tracker-scraper`
- `<WUWA_REPO>` = `/workspace/wuwa-timeline-scraper`
- `<STATE_DIR>` = `/workspace/hermes-state`

Use the same Hermes thread for all jobs so the command processor can see replies.

## Recommended Setup

If Hermes runs in a Docker container and you do not want to keep expanding host
folder access, prefer this model:

1. Let Hermes clone both repos into its own internal workspace.
2. Give Hermes one persistent writable state directory.
3. Avoid mounting your general host project tree into the autonomous agent.

That keeps the trust boundary smaller. The only extra writable surface you need
for these jobs is the shared state directory.

## Job 1: HoYo Codes Watcher

- Name: `HoYo Codes Watcher`
- Deliver To: same local Hermes thread as the other jobs
- Schedule: `*/30 * * * *`

```text
Use these absolute paths:

- HoYo repo: <HOYO_REPO>
- Scraper: <HOYO_REPO>/scrape_hoyo_tracker.py
- Output file to read after running: <HOYO_REPO>/output/latest.json
- State dir: <STATE_DIR>
- State file: <STATE_DIR>/hoyo_codes_seen.json

Task:
1. Ensure the state directory exists.
2. Run the scraper for codes only:
   python3 <HOYO_REPO>/scrape_hoyo_tracker.py --include codes --timezone Asia/Kolkata
3. Read <HOYO_REPO>/output/latest.json from that scraper run.
4. From the JSON, collect all codes for genshin and starrail. Use the normalized `code` field as the key.
5. If the state file does not exist:
   - Create it with all current codes as already seen.
   - Store:
     - seen_codes
     - last_success_utc
     - weekly_runs
     - weekly_triggers
     - weekly_failures
     - current_week_key in YYYY-WW format
   - Post exactly one short setup message:
     "Seeded HoYo codes state with X total codes across Genshin and HSR. Future runs will only report newly added codes."
   - Do not dump the full code list.
6. If the state file exists:
   - Increment weekly_runs.
   - Compare current codes against seen_codes.
   - If there are no new codes:
     - Update last_success_utc.
     - Do not post any message.
   - If there are new codes:
     - Increment weekly_triggers.
     - Update seen_codes and last_success_utc.
     - Post a concise alert grouped by game.
     - For each new code include:
       - game
       - code
       - status
       - rewards if present
       - redemption_url if present
7. If the scraper fails or JSON cannot be read:
   - Increment weekly_failures if possible.
   - Do not overwrite prior seen_codes.
   - Post a short failure message with the error.

Rules:
- Never treat first run as a change alert.
- Never overwrite state on failure.
- Keep messages concise.
```

## Job 2: HoYo Event Reminder

- Name: `HoYo Event Reminder`
- Deliver To: same local Hermes thread as the other jobs
- Schedule: `0 9 * * *`

```text
Use these absolute paths:

- HoYo repo: <HOYO_REPO>
- Scraper: <HOYO_REPO>/scrape_hoyo_tracker.py
- Output file to read after running: <HOYO_REPO>/output/latest.json
- State dir: <STATE_DIR>
- Status file: <STATE_DIR>/hoyo_event_status.json

Task:
1. Ensure the state directory exists.
2. Run the scraper for active events only:
   python3 <HOYO_REPO>/scrape_hoyo_tracker.py --active-only --include events --timezone Asia/Kolkata
3. Read <HOYO_REPO>/output/latest.json.
4. For all genshin and starrail events:
   - Ignore expired events.
   - Identify events ending within the next 3 days from now, based on `end_at_utc`.
5. Build a stable key for each event:
   hoyo:{game}:{record_type}:{id}:{end_at_utc}
6. Maintain status per key in `<STATE_DIR>/hoyo_event_status.json` with:
   - status = active | done | ignored
   - last_reminded_date
   - name
   - game
   - end_at_utc
7. For each qualifying event:
   - If status is done or ignored, skip it.
   - If last_reminded_date is today, skip it.
   - Otherwise post a reminder and set last_reminded_date to today.
8. Reminder format:
   - Event name
   - Game
   - Ends at Asia/Kolkata time
   - Time remaining
   - Stable key
   - Final line:
     Reply with `Done "Exact Event Name"` to stop reminders after completion, or `Ignore "Exact Event Name"` to suppress reminders for this event.
9. If nothing qualifies today, post nothing.
10. If there is a failure, post one short error message and do not wipe status.

Rules:
- Do not remind more than once per event per day.
- Use exact event name matching when you mention the reply command.
- Preserve prior done and ignored state across runs.
```

## Job 3: Command Processor

- Name: `Game Reminder Command Processor`
- Deliver To: same local Hermes thread as the other jobs
- Schedule: `*/5 * * * *`

```text
This job handles user replies in this same Hermes thread.

Use these absolute paths:

- State dir: <STATE_DIR>
- HoYo status file: <STATE_DIR>/hoyo_event_status.json
- WuWa status file: <STATE_DIR>/wuwa_event_status.json
- Processor state file: <STATE_DIR>/command_processor_state.json

Task:
1. Ensure the state directory exists.
2. Read the latest user messages in this thread since the last processed checkpoint.
3. Look for exact commands in either format:
   - Done "Event Name"
   - Ignore "Event Name"
4. Match by exact event name against currently tracked active entries in both status files.
5. If exactly one matching event is found:
   - Set its status to done or ignored.
   - Record updated_at_utc.
   - Post a short confirmation:
     - "Marked done: <Event Name>"
     - or "Ignored: <Event Name>"
6. If multiple matches are found for the same name:
   - Post a short ambiguity message listing the stable keys.
   - Ask the user to reply with:
     Done key:<stable-key>
     or
     Ignore key:<stable-key>
7. Also support these exact commands:
   - Done key:<stable-key>
   - Ignore key:<stable-key>
8. Update the processor checkpoint so the same user command is not processed twice.
9. If there are no new commands, post nothing.
10. If thread history is unavailable in this Hermes cron environment, post one message saying:
    "Command processor could not read thread replies in this environment."
    and stop trying further in that run.

Rules:
- Only process new user messages after the last checkpoint.
- Never mark more than one event done or ignored from one name command unless the key is explicit.
- Keep confirmations short.
```

## Job 4: Weekly Heartbeat

- Name: `Game Tracker Weekly Heartbeat`
- Deliver To: same local Hermes thread as the other jobs
- Schedule: `0 18 * * 0`

```text
Use these absolute paths:

- State file: <STATE_DIR>/hoyo_codes_seen.json
- HoYo event file: <STATE_DIR>/hoyo_event_status.json
- WuWa event file: <STATE_DIR>/wuwa_event_status.json

Task:
1. Read the HoYo code watcher state.
2. Post a short weekly heartbeat summary including:
   - current week key
   - weekly_runs
   - weekly_triggers
   - weekly_failures
   - last_success_utc
   - total seen HoYo codes
   - count of HoYo events marked done
   - count of HoYo events ignored
   - count of WuWa events marked done
   - count of WuWa events ignored
3. After posting, reset only the weekly counters in `<STATE_DIR>/hoyo_codes_seen.json` for the new week:
   - weekly_runs = 0
   - weekly_triggers = 0
   - weekly_failures = 0
   - current_week_key = new YYYY-WW
4. Do not delete seen_codes or event status history.
5. If the state file does not exist yet, post:
   "Weekly heartbeat skipped because tracker state has not been initialized yet."

Rules:
- This is the silence detector. Always post a summary if state exists.
- Keep it concise and numeric.
```

## Notes

- Keep no-op runs silent except for the weekly heartbeat.
- Manually run the HoYo codes watcher once after creation so it seeds state without
  looking like a change alert.
- The WuWa-specific reminder job lives in the sibling repo:
  `../wuwa-timeline-scraper/HERMES_CRON.md`
