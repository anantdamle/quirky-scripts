/**
 * CROSS-ACCOUNT CALENDAR SYNC (HUB MODEL - NATIVE RECURRENCE)
 * * Required Advanced Services: 
 * - Google Calendar API (v3) must be enabled in Services.
 */

const CONFIG = {
  // Replace with your actual @gmail.com address
  CALENDAR_A_ID: 'your.personal@gmail.com', 
  
  // 'primary' refers to the calendar of the account running this script (B or C)
  THIS_ACCOUNT_CALENDAR_ID: 'primary',      

  // Unique identifier for the account running this script (e.g., 'ORG_B' or 'ORG_C')
  // CRITICAL: Change this to a unique name (like 'ORG_C') when deploying in the second account.
  THIS_ACCOUNT_ORIGIN_NAME: 'ORG_B', 
  
  // Internal tags used to prevent infinite loops and map events
  ORIGINAL_CALENDAR_ID_KEY: 'originalCalendarId',
  ORIGINAL_EVENT_ID_KEY: 'originalId',
  COPIED_EVENT_KEY: 'IS_COPIED_EVENT'
};

/**
 * Main function triggered by Calendar event changes.
 * Runs manually (syncs both) or via trigger (syncs only the changed calendar).
 */
function onCalendarUpdate(e) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    Logger.log('Another sync in progress, skipping.');
    return;
  }

  try {
    // Look back 14 days to catch recent changes and exceptions (fallback if no syncToken)
    const timeMin = new Date();
    timeMin.setDate(timeMin.getDate() - 14); 
    
    // The event object 'e' will contain the ID of the calendar that triggered the update.
    const changedCalendarId = e ? e.calendarId : null;
    const myEmail = Session.getEffectiveUser().getEmail();
    
    // Determine if we should treat 'primary' as the trigger source
    const isSpokeChanged = !changedCalendarId || changedCalendarId === myEmail || changedCalendarId === CONFIG.THIS_ACCOUNT_CALENDAR_ID;
    const isHubChanged = !changedCalendarId || changedCalendarId === CONFIG.CALENDAR_A_ID;

    // 1. Sync THIS Account (B/C) to Personal (A) -> Keep Full Details
    if (isSpokeChanged) {
      SyncEngine.performSync({
        sourceId: CONFIG.THIS_ACCOUNT_CALENDAR_ID,
        targetId: CONFIG.CALENDAR_A_ID,
        timeMin: timeMin.toISOString(),
        stripDetails: false,
        directionSourceOrigin: CONFIG.THIS_ACCOUNT_ORIGIN_NAME
      });
    }

    // 2. Sync Personal (A) to THIS Account (B/C) -> Strip Details for Privacy
    if (isHubChanged) {
      SyncEngine.performSync({
        sourceId: CONFIG.CALENDAR_A_ID,
        targetId: CONFIG.THIS_ACCOUNT_CALENDAR_ID,
        timeMin: timeMin.toISOString(),
        stripDetails: true,
        directionSourceOrigin: 'PERSONAL_A'
      });
    }
  } finally {
    lock.releaseLock();
  }
}

/**
 * Setup UserCalendar Event Triggers instead of time-driven polling.
 * Run this ONCE manually to authorize and create the hooks.
 */
function registerCalendarTriggers() {
  // Deletes existing triggers to prevent duplicates
  ScriptApp.getProjectTriggers().forEach(trigger => ScriptApp.deleteTrigger(trigger));
  
  const myEmail = Session.getEffectiveUser().getEmail();
  
  // Creates a trigger for the Spoke (THIS_ACCOUNT) calendar
  ScriptApp.newTrigger('onCalendarUpdate')
    .forUserCalendar(myEmail)
    .onEventUpdated()
    .create();
    
  // Creates a trigger for the Hub (CALENDAR_A) calendar
  ScriptApp.newTrigger('onCalendarUpdate')
    .forUserCalendar(CONFIG.CALENDAR_A_ID)
    .onEventUpdated()
    .create();

  // Safety-net poll: onEventUpdated can occasionally miss or delay notifications
  ScriptApp.newTrigger('onCalendarUpdate')
    .timeBased()
    .everyHours(1)
    .create();
    
  Logger.log('Calendar event triggers + hourly safety poll created for ' + myEmail + ' and ' + CONFIG.CALENDAR_A_ID);
}