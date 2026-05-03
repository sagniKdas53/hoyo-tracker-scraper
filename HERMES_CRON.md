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

Use the same Hermes thread for all jobs so all reminders land in one place.

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
- Job status file: <STATE_DIR>/job_status.json
- Job name: HoYo Codes Watcher

Task:
1. Ensure the state directory exists.
2. Run the scraper for codes only:
   python3 <HOYO_REPO>/scrape_hoyo_tracker.py --include codes --timezone Asia/Kolkata
3. Read <HOYO_REPO>/output/latest.json from that scraper run.
4. From the JSON, collect all codes for genshin and starrail. Use the normalized `code` field as the key.
5. Load the job status file if it exists, otherwise start with an empty object.
6. If the state file does not exist:
   - Create it with all current codes as already seen.
   - Store:
     - seen_codes
     - last_success_utc
     - weekly_runs
     - weekly_triggers
     - weekly_failures
     - current_week_key in YYYY-WW format
   - Update the job status entry for `HoYo Codes Watcher` with at least:
     - runs_this_week
     - successes_this_week
     - failures_this_week
     - triggers_this_week
     - last_run_utc
     - last_success_utc
     - last_failure_utc
     - last_failure_reason
   - Post exactly one short setup message:
     "Seeded HoYo codes state with X total codes across Genshin and HSR. Future runs will only report newly added codes."
   - Do not dump the full code list.
7. If the state file exists:
   - Increment weekly_runs.
   - Compare current codes against seen_codes.
   - Update the job status entry with a successful run.
   - If there are no new codes:
     - Update last_success_utc.
     - Do not post any message.
   - If there are new codes:
     - Increment weekly_triggers.
     - Increment the job status trigger count.
     - Update seen_codes and last_success_utc.
     - Output only the alert grouped by game.
8. If the scraper fails or JSON cannot be read:
   - Increment weekly_failures if possible.
   - Update the job status entry with a failed run, last_failure_utc, and last_failure_reason.
   - Do not overwrite prior seen_codes.
   - Post a short failure message with the error.
9. Write the updated job status file back.

Rules:
- This is a one-shot cron run, not an ongoing project update.
- Never output task summaries, continuity notes, tool logs, plans, or management text.
- Never treat first run as a change alert.
- Never overwrite state on failure.
- If there are new codes, output only the alert itself.
- Format the alert like this:
  New Genshin Impact codes:
  - CODE: <URL>
  New Honkai: Star Rail codes:
  - CODE: <URL>
- Omit empty sections.
- Do not add any footer such as "To stop or manage this job".
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
- Job status file: <STATE_DIR>/job_status.json
- Job name: HoYo Event Reminder

Task:
1. Ensure the state directory exists.
2. Run the scraper for active events only:
   python3 <HOYO_REPO>/scrape_hoyo_tracker.py --active-only --include events --timezone Asia/Kolkata
3. Read <HOYO_REPO>/output/latest.json.
4. For all genshin and starrail events:
   - Ignore expired events.
   - Identify events ending within the next 3 days from now, based on `end_at_utc`.
5. Load the job status file if it exists, otherwise start with an empty object.
6. Also count all currently active HoYo events by game from this payload. Store at least:
   - active_genshin_events
   - active_starrail_events
   in the job status entry.
7. Build a stable key for each event:
   hoyo:{game}:{record_type}:{id}:{end_at_utc}
8. Maintain status per key in `<STATE_DIR>/hoyo_event_status.json` with:
   - status = active | done | ignored
   - last_reminded_date
   - name
   - game
   - end_at_utc
9. For each qualifying event:
   - If status is done or ignored, skip it.
   - If last_reminded_date is today, skip it.
   - Otherwise post a reminder and set last_reminded_date to today.
10. Update the job status entry for `HoYo Event Reminder` with at least:
   - runs_this_week
   - successes_this_week
   - failures_this_week
   - triggers_this_week
   - last_run_utc
   - last_success_utc
   - last_failure_utc
   - last_failure_reason
   - last_qualifying_count
   - active_genshin_events
   - active_starrail_events
11. Write the updated status and job status files back.
12. Reminder format:
   - Event name
   - Game
   - Ends at Asia/Kolkata time
   - Time remaining
   - Stable key
   - Final line:
     Use `/mark-event done "Exact Event Name"` to stop reminders after completion, or `/mark-event ignore "Exact Event Name"` to suppress reminders for this event.
13. If nothing qualifies today, post nothing.
14. If there is a failure:
   - Update the job status entry with a failed run.
   - Post one short error message and do not wipe status.

Rules:
- This is a one-shot cron run, not an ongoing project update.
- Never output task summaries, continuity notes, tool logs, plans, or management text.
- Do not remind more than once per event per day.
- Preserve prior done and ignored state across runs.
```

## Job 3: Weekly Heartbeat

- Name: `Game Tracker Weekly Heartbeat`
- Deliver To: same local Hermes thread as the other jobs
- Schedule: `0 18 * * 0`

```text
Use these absolute paths:

- State file: <STATE_DIR>/hoyo_codes_seen.json
- HoYo event file: <STATE_DIR>/hoyo_event_status.json
- WuWa event file: <STATE_DIR>/wuwa_event_status.json
- Job status file: <STATE_DIR>/job_status.json

Task:
1. Read the HoYo code watcher state.
2. Read the HoYo and WuWa event status files.
3. Read the job status file if it exists, otherwise treat it as empty.
4. Post a short weekly heartbeat summary including:
   - current week key
   - weekly_runs
   - weekly_triggers
   - weekly_failures
   - last_success_utc
   - total seen HoYo codes
   - active_genshin_events
   - active_starrail_events
   - active_wuwa_events
   - genshin_events_done
   - genshin_events_ignored
   - hsr_events_done
   - hsr_events_ignored
   - wuwa_events_done
   - wuwa_events_ignored
5. Split HoYo done and ignored counts by the `game` field in `hoyo_event_status.json` so Genshin and Honkai: Star Rail are listed separately.
6. Also include a per-job summary section using the job status file:
   - HoYo Codes Watcher: runs, successes, failures, triggers, last_success_utc, last_failure_reason if present
   - HoYo Event Reminder: runs, successes, failures, triggers, active_genshin_events, active_starrail_events, last_failure_reason if present
   - WuWa Event Reminder: runs, successes, failures, triggers, active_wuwa_events, last_failure_reason if present
7. Make the output directly answer:
   - Which jobs ran this week?
   - Which job failed this week?
8. After posting, reset only the weekly counters in `<STATE_DIR>/hoyo_codes_seen.json` for the new week:
   - weekly_runs = 0
   - weekly_triggers = 0
   - weekly_failures = 0
   - current_week_key = new YYYY-WW
9. Also reset the per-job weekly counters in `<STATE_DIR>/job_status.json` for the new week, but keep:
   - last_success_utc
   - last_failure_utc
   - last_failure_reason
   - latest active event counts
10. Do not delete seen_codes or event status history.
11. If the state file does not exist yet, post:
   "Weekly heartbeat skipped because tracker state has not been initialized yet."

Rules:
- This is a one-shot cron run, not an ongoing project update.
- Never output task summaries, continuity notes, tool logs, plans, or management text.
- This is the silence detector. Always post a summary if state exists.
- Keep it concise and numeric.
- Do not add any footer.
```

## Notes

- Keep no-op runs silent except for the weekly heartbeat.
- Manually run the HoYo codes watcher once after creation so it seeds state without
  looking like a change alert.
- Use a manual Hermes slash command such as `/mark-event` instead of a polling
  command-processor cron.
- The WuWa-specific reminder job lives in the sibling repo:
  `../wuwa-timeline-scraper/HERMES_CRON.md`
