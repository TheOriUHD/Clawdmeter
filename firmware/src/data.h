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
    long clock_epoch;        // local wall-clock epoch (s) from daemon; 0 = not provided
    int  clock_fmt;          // 12 or 24 (hour format from daemon); defaults to 24
    bool ok;                 // data parse succeeded
    bool valid;              // false until first successful parse
};
