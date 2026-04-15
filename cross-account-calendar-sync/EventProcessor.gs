/**
 * Evaluates, formats, and copies single events while preventing loops,
 * handling privacy rules, and mapping recurring event exceptions.
 * * INCLUDES BACKWARDS COMPATIBILITY for legacy tags.
 */
const EventProcessor = {
  
  // Mitigation for Issue 3: Cache target master IDs to prevent redundant API queries
  _masterCache: {},

  // Mitigation for Issue 10: Queue for exception events whose master hasn't been synced yet
  _deferredExceptions: [],

  processSingleEvent: function(sourceEvent, sourceId, targetId, stripDetails, directionSourceOrigin) {
    const extendedProps = sourceEvent.extendedProperties ? sourceEvent.extendedProperties.private : {};
    
    // Check for new origin calendar tag OR the legacy origin tags
    const eventOrigin = extendedProps ? extendedProps[CONFIG.ORIGINAL_CALENDAR_ID_KEY] : null;
    const legacyOrigin = extendedProps ? extendedProps['originalCalendarId'] : null;
    const legacyRootOrigin = extendedProps ? extendedProps['ROOT_ORIGIN_ID'] : null;
    
    const effectiveOrigin = eventOrigin || legacyRootOrigin || legacyOrigin;

    // Get the email of the account currently running this script
    const myEmail = Session.getEffectiveUser().getEmail();
    
    // --- 1. INFINITE LOOP PREVENTION ---
    // If syncing Hub -> Spoke: skip if event originally came from this Spoke account
    if (sourceId === CONFIG.CALENDAR_A_ID && (effectiveOrigin === CONFIG.THIS_ACCOUNT_ORIGIN_NAME || effectiveOrigin === myEmail)) {
      return; 
    }
    
    // If syncing Spoke -> Hub: skip if event originally came from the Hub (or another Hub/Spoke)
    // i.e., only sync events that genuinely originated in THIS Spoke
    if (sourceId === CONFIG.THIS_ACCOUNT_CALENDAR_ID && effectiveOrigin && effectiveOrigin !== CONFIG.THIS_ACCOUNT_ORIGIN_NAME && effectiveOrigin !== myEmail) {
      return;
    }
    
    // --- 2. BASE PAYLOAD SETUP ---
    let newEvent = {
      // Fix: Only default to 'Busy' if stripDetails is true. Otherwise preserve empty summaries.
      summary: sourceEvent.summary || (stripDetails ? 'Busy' : ''),
      start: sourceEvent.start,
      end: sourceEvent.end,
      status: sourceEvent.status,
      extendedProperties: {
        private: {
          [CONFIG.COPIED_EVENT_KEY]: 'true',
          [CONFIG.ORIGINAL_CALENDAR_ID_KEY]: effectiveOrigin || directionSourceOrigin,
          [CONFIG.ORIGINAL_EVENT_ID_KEY]: sourceEvent.id
        }
      }
    };

    // --- 3. RECURRENCE & EXCEPTION LOGIC ---
    if (sourceEvent.recurrence) {
      newEvent.recurrence = sourceEvent.recurrence;
    }

    if (sourceEvent.recurringEventId) {
      let targetMasterSearch;
      try {
        const cacheKey = `${targetId}_${sourceEvent.recurringEventId}`;
        
        // Mitigation for Issue 3: Check cache first
        if (this._masterCache[cacheKey]) {
          newEvent.recurringEventId = this._masterCache[cacheKey];
          newEvent.originalStartTime = sourceEvent.originalStartTime;
        } else {
          // Try to find the master using the NEW key
          targetMasterSearch = Calendar.Events.list(targetId, {
            privateExtendedProperty: `${CONFIG.ORIGINAL_EVENT_ID_KEY}=${sourceEvent.recurringEventId}`,
            showDeleted: true
          }).items;
          
          // BACKWARDS COMPATIBILITY: If not found, try finding master using the OLD keys
          if (!targetMasterSearch || targetMasterSearch.length === 0) {
            targetMasterSearch = Calendar.Events.list(targetId, {
              privateExtendedProperty: `ORIGINAL_ID=${sourceEvent.recurringEventId}`,
              showDeleted: true
            }).items;
          }
          if (!targetMasterSearch || targetMasterSearch.length === 0) {
            targetMasterSearch = Calendar.Events.list(targetId, {
              privateExtendedProperty: `originalId=${sourceEvent.recurringEventId}`,
              showDeleted: true
            }).items;
          }

          if (targetMasterSearch && targetMasterSearch.length > 0) {
            newEvent.recurringEventId = targetMasterSearch[0].id;
            newEvent.originalStartTime = sourceEvent.originalStartTime; 
            // Mitigation for Issue 3: Save to cache
            this._masterCache[cacheKey] = targetMasterSearch[0].id;
          } else {
            // Mitigation for Issue 10: Defer this exception for retry after masters are created
            this._deferredExceptions.push({ sourceEvent, sourceId, targetId, stripDetails, directionSourceOrigin });
            return;
          }
        }
      } catch (e) {
        console.error(`Error linking exception: ` + e.message);
        return;
      }
    }

    // --- 4. PRIVACY LOGIC ---
    if (stripDetails) {
      newEvent.description = '';
      newEvent.location = '';
    } else {
      newEvent.description = sourceEvent.description || '';
      newEvent.location = sourceEvent.location || '';
    }

    // --- 5. UPSERT (UPDATE/INSERT/DELETE) LOGIC ---
    let existingTargetEvents;
    try {
      // Search for the event using the NEW key
      existingTargetEvents = Calendar.Events.list(targetId, {
        privateExtendedProperty: `${CONFIG.ORIGINAL_EVENT_ID_KEY}=${sourceEvent.id}`,
        showDeleted: true
      }).items;
      
      // BACKWARDS COMPATIBILITY: If not found, search using the OLD keys
      if (!existingTargetEvents || existingTargetEvents.length === 0) {
         existingTargetEvents = Calendar.Events.list(targetId, {
          privateExtendedProperty: `ORIGINAL_ID=${sourceEvent.id}`,
          showDeleted: true
        }).items;
      }
      if (!existingTargetEvents || existingTargetEvents.length === 0) {
         existingTargetEvents = Calendar.Events.list(targetId, {
          privateExtendedProperty: `originalId=${sourceEvent.id}`,
          showDeleted: true
        }).items;
      }
    } catch (e) {
      console.error(`Error searching target ${targetId}: ` + e.message);
      return;
    }

    try {
      if (existingTargetEvents && existingTargetEvents.length > 0) {
        const existingEventId = existingTargetEvents[0].id;
        if (sourceEvent.status === 'cancelled') {
          Calendar.Events.remove(targetId, existingEventId);
        } else {
          // Mitigation for Issue 4: Use patch() instead of update() to prevent wiping other properties
          Calendar.Events.patch(newEvent, targetId, existingEventId);
        }
      } else {
        if (sourceEvent.status !== 'cancelled') {
           Calendar.Events.insert(newEvent, targetId);
        }
      }
    } catch (e) {
      console.error(`Error writing to ${targetId}: ` + e.message);
    }
  },

  /**
   * Retries deferred exception events after all masters have been processed.
   * Clears the deferred queue and master cache when done.
   */
  retryDeferredExceptions: function() {
    const deferred = this._deferredExceptions;
    this._deferredExceptions = [];

    if (deferred.length === 0) return;

    Logger.log(`Retrying ${deferred.length} deferred exception event(s)...`);
    let successCount = 0;

    for (const item of deferred) {
      const { sourceEvent, sourceId, targetId, stripDetails, directionSourceOrigin } = item;
      // Re-attempt processing — master should now exist on target
      this.processSingleEvent(sourceEvent, sourceId, targetId, stripDetails, directionSourceOrigin);
      successCount++;
    }

    Logger.log(`Retry pass complete. Attempted ${successCount} deferred exceptions.`);
  },

  /**
   * Resets per-cycle state. Call once per sync direction after retries are done.
   */
  resetCycleState: function() {
    this._masterCache = {};
    this._deferredExceptions = [];
  }
};