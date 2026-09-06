#pragma once
#include <Arduino.h>

// Weekly scoped-model limits — the "ws" payload key. Some plans meter one
// model separately inside the weekly window (today: Fable on Max plans; the
// API reports it as limits[] kind "weekly_scoped" with a model scope). The
// label is the API's own display name, so a future scoped model needs no
// firmware change. All weekly limits share the weekly reset instant.
#define MAX_SCOPED_WEEKLY 4
struct ScopedWeekly {
    char  name[16];          // model label from the daemon, e.g. "Fable"
    float pct;               // utilization 0-100 (0% is a real reading)
};

struct UsageData {
    float session_pct;       // utilization 0-100 (5h window Pro/Max; spending % Enterprise)
    int session_reset_mins;  // minutes until reset
    float weekly_pct;        // 7-day utilization (Pro/Max only; 0 for Enterprise)
    int weekly_reset_mins;   // minutes until weekly reset (Pro/Max only)
    int scoped_weekly_count; // 0 = no scoped weekly limits ("ws" absent or empty)
    ScopedWeekly scoped_weekly[MAX_SCOPED_WEEKLY];
    char status[16];         // "allowed", "limited", etc.
    bool chime;              // play the session-reset chime; false unless daemon opts in
    bool enterprise;         // true = Enterprise spending-limit account
    int time_pct;            // 0-100: fraction of billing period elapsed (Enterprise)
    int period_days;         // total billing period length in days (Enterprise)
    char reset_date[12];     // formatted reset date e.g. "Jul 1" (Enterprise)
    int  host_batt_pct;      // the host machine's battery 0-100 ("hb"); -1 = not provided
    bool host_batt_charging; // host battery charging ("hc")
    long clock_epoch;        // local wall-clock epoch (s) from daemon; 0 = not provided
    int  clock_fmt;          // 12 or 24 (hour format from daemon); defaults to 24
    bool has_usage;          // payload carried the usage keys (a companion-only beat does not)
    bool ok;                 // data parse succeeded
    bool valid;              // false until first successful parse
};

// ---- Companion: live Claude Code session state — the "cc" payload key ----
// Claude Code hooks post every event to the daemon (daemon/companion.py), which
// boils them down to the headline session's state. Codes mirror the daemon's.
enum CompanionState : uint8_t {
    CC_NONE = 0,      // no live session (or the companion is not installed)
    CC_IDLE,          // a session is open, waiting for a prompt
    CC_THINKING,      // Claude is composing / reasoning
    CC_TOOL,          // Claude is running a tool (label says which)
    CC_DONE,          // a short exchange ended — your turn, quietly
    CC_ATTENTION,     // Claude needs you NOW: permission, a question, a plan to approve
    CC_ERROR,         // the turn ended in an API error
    CC_COMPACTING,    // context compaction in progress
    CC_TURN_DONE,     // a long turn finished — your turn, worth a nudge
    CC_STATE_COUNT,
};
#define CC_LABEL_MAX   24
#define CC_PROJECT_MAX 16
#define CC_MODEL_MAX   12
struct CompanionData {
    bool    present;                 // the payload carried "cc" (companion installed)
    uint8_t state;                   // CompanionState of the headline session
    uint8_t sessions;                // live sessions ("n")
    uint8_t attention;               // sessions needing you ("a")
    uint8_t agents;                  // subagents of the headline session ("g")
    int     elapsed_s;               // seconds in `state` when the payload left the daemon ("e")
    char    label[CC_LABEL_MAX + 1]; // "Editing ui.cpp", "Permission: Bash", …
    char    project[CC_PROJECT_MAX + 1];
    char    model[CC_MODEL_MAX + 1];
    char    host[13];                // remote host name, empty when local
};

// ---- Stats: lifetime Claude Code numbers — the "st" payload key ----
// Computed by the daemon from the local transcripts (daemon/stats.py): the
// eight figures of the Claude app's stats card plus an activity heatmap, one
// char per day ('0'..'4' intensity, 'x' = still in the future), oldest first,
// in whole Sunday→Saturday weeks ending with the current one.
#define ST_MAX_DAYS (7 * 30)
struct StatsData {
    bool     present;
    uint32_t sessions;
    uint32_t messages;
    uint64_t tokens;
    uint16_t active_days;
    uint16_t streak;          // current streak, days
    uint16_t best_streak;
    int8_t   peak_hour;       // 0-23 local, -1 unknown
    char     model[13];       // favourite model, e.g. "Fable 5"
    uint16_t days;            // chars in heat[]
    char     heat[ST_MAX_DAYS + 1];
};

