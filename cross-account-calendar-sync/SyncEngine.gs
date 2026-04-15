/**
 * Handles the pagination and Calendar API list requests
 */
const SyncEngine = {
  
  performSync: function(params) {
    const { sourceId, targetId, timeMin, stripDetails, directionSourceOrigin } = params;
    let pageToken = null;
    let processedCount = 0;
    
    // Mitigations for Issues 1, 2, & 5: Setup Sync Tokens
    const props = PropertiesService.getUserProperties();
    const syncTokenKey = `syncToken_${sourceId}_to_${targetId}`;
    const syncToken = props.getProperty(syncTokenKey);
    let nextSyncToken = null;
    
    do {
      const options = {
        // FALSE: Returns the native Master recurring event and Exception events
        singleEvents: false, 
        maxResults: 250,
        pageToken: pageToken,
        showDeleted: true 
      };

      // Use syncToken if available. Otherwise use updatedMin to safely grab modified masters.
      if (syncToken) {
        options.syncToken = syncToken;
      } else {
        options.updatedMin = timeMin;
      }
      
      let response;
      try {
        response = Calendar.Events.list(sourceId, options);
      } catch (e) {
        // Handle expired/invalid sync tokens gracefully (HTTP 410 Gone)
        if (e.message.includes('Sync token') || e.message.includes('410') || e.message.includes('full sync')) {
          props.deleteProperty(syncTokenKey);
          console.warn(`Sync token expired for ${sourceId}. Will do full re-sync on next run.`);
          return;
        }
        console.error(`Error reading from ${sourceId}: ` + e.message);
        return;
      }
      
      const events = response.items;
      if (events && events.length > 0) {
        for (let i = 0; i < events.length; i++) {
          EventProcessor.processSingleEvent(events[i], sourceId, targetId, stripDetails, directionSourceOrigin);
          processedCount++;
        }
      }
      pageToken = response.nextPageToken;
      // Capture the next sync token provided by the API
      if (response.nextSyncToken) {
        nextSyncToken = response.nextSyncToken;
      }
    } while (pageToken);
    
    // Save the sync token for the next execution
    if (nextSyncToken) {
      props.setProperty(syncTokenKey, nextSyncToken);
    }

    // Mitigation for Issue 10: Retry exceptions whose masters were just created this cycle
    EventProcessor.retryDeferredExceptions();
    EventProcessor.resetCycleState();
    
    Logger.log(`Finished processing ${processedCount} events from ${sourceId} to ${targetId}`);
  }
};