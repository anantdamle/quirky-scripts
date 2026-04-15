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
  ORIGINAL_CALENDAR_ID_KEY: 'ORIGINAL_CALENDAR_ID',
  ORIGINAL_EVENT_ID_KEY: 'ORIGINAL_EVENT_ID',
  COPIED_EVENT_KEY: 'IS_COPIED_EVENT'
};

/**
 * Main trigger function. 
 * Run this manually once, or set it up in Triggers to run Time-Driven (e.g. every 15 mins).
 */
function syncAllDirections() {
  // Look back 14 days to catch recent changes and exceptions
  const timeMin = new Date();
  timeMin.setDate(timeMin.getDate() - 14); 

  // 1. Sync THIS Account (B/C) to Personal (A) -> Keep Full Details
  SyncEngine.performSync({
    sourceId: CONFIG.THIS_ACCOUNT_CALENDAR_ID,
    targetId: CONFIG.CALENDAR_A_ID,
    timeMin: timeMin.toISOString(),
    stripDetails: false,
    directionSourceOrigin: CONFIG.THIS_ACCOUNT_ORIGIN_NAME
  });

  // 2. Sync Personal (A) to THIS Account (B/C) -> Strip Details for Privacy
  SyncEngine.performSync({
    sourceId: CONFIG.CALENDAR_A_ID,
    targetId: CONFIG.THIS_ACCOUNT_CALENDAR_ID,
    timeMin: timeMin.toISOString(),
    stripDetails: true,
    directionSourceOrigin: 'PERSONAL_A'
  });
}

function registerTimeDrivenTrigger() {
  // Deletes existing triggers to prevent duplicates
  ScriptApp.getProjectTriggers().forEach(trigger => ScriptApp.deleteTrigger(trigger));
  
  // Creates a trigger to run every 15 minutes
  ScriptApp.newTrigger('syncAllDirections')
    .timeBased()
    .everyMinutes(15)
    .create();
    
  Logger.log('15-minute trigger created successfully.');
}