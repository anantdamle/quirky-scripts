/**
 * Evaluates, formats, and copies single events while preventing loops,
 * handling privacy rules, and mapping recurring event exceptions.
 */
const EventProcessor = {
  
  // Cache target master IDs to prevent redundant API queries
  _masterCache: {},

  // Queue for exception events whose master hasn't been synced yet
  _deferredExceptions: [],

  processSingleEvent: function(sourceEvent, sourceId, targetId, stripDetails, directionSourceOrigin) {
    const extendedProps = sourceEvent.extendedProperties ? sourceEvent.extendedProperties.private : {};
    const effectiveOrigin = extendedProps ? extendedProps[CONFIG.ORIGINAL_CALENDAR_ID_KEY] : null;
    const myEmail = Session.getEffectiveUser().getEmail();
    
    // --- 1. INFINITE LOOP PREVENTION ---
    if (sourceId === CONFIG.CALENDAR_A_ID && (effectiveOrigin === CONFIG.THIS_ACCOUNT_ORIGIN_NAME || effectiveOrigin === myEmail)) {
      return; 
    }
    if (sourceId === CONFIG.THIS_ACCOUNT_CALENDAR_ID && effectiveOrigin && effectiveOrigin !== CONFIG.THIS_ACCOUNT_ORIGIN_NAME && effectiveOrigin !== myEmail) {
      return;
    }
    
    // --- 2. BASE PAYLOAD SETUP ---
    let newEvent = {
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
      try {
        const cacheKey = targetId + '_' + sourceEvent.recurringEventId;
        
        if (this._masterCache[cacheKey]) {
          newEvent.recurringEventId = this._masterCache[cacheKey];
          newEvent.originalStartTime = sourceEvent.originalStartTime;
        } else {
          // 1 read: find the master on the target
          var targetMasterSearch = SyncEngine.callWithBackoff(function() {
            return Calendar.Events.list(targetId, {
              privateExtendedProperty: CONFIG.ORIGINAL_EVENT_ID_KEY + '=' + sourceEvent.recurringEventId,
              showDeleted: true
            });
          }).items;

          if (targetMasterSearch && targetMasterSearch.length > 0) {
            newEvent.recurringEventId = targetMasterSearch[0].id;
            newEvent.originalStartTime = sourceEvent.originalStartTime; 
            this._masterCache[cacheKey] = targetMasterSearch[0].id;
          } else {
            // Defer: master hasn't been synced yet this cycle
            this._deferredExceptions.push({ sourceEvent, sourceId, targetId, stripDetails, directionSourceOrigin });
            return;
          }
        }
      } catch (e) {
        console.error('Error linking exception: ' + e.message);
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

    // --- 4b. COLOR ---
    if (directionSourceOrigin === CONFIG.THIS_ACCOUNT_ORIGIN_NAME && CONFIG.EXPORT_COLOR_ID) {
      // Exporting spoke→hub: stamp this spoke's color
      newEvent.colorId = CONFIG.EXPORT_COLOR_ID;
    } else if (directionSourceOrigin === 'PERSONAL_A' && !sourceEvent.colorId && CONFIG.HUB_IMPORT_COLOR_ID) {
      // Importing a hub-native event (no color from another spoke): use hub import color
      newEvent.colorId = CONFIG.HUB_IMPORT_COLOR_ID;
    } else if (sourceEvent.colorId) {
      // Importing hub→spoke: preserve the color set by the exporting spoke
      newEvent.colorId = sourceEvent.colorId;
    }

    // --- 5. UPSERT (1 read + 1 write) ---
    let existingTargetEvents;
    try {
      existingTargetEvents = SyncEngine.callWithBackoff(function() {
        return Calendar.Events.list(targetId, {
          privateExtendedProperty: CONFIG.ORIGINAL_EVENT_ID_KEY + '=' + sourceEvent.id,
          showDeleted: true
        });
      }).items;
    } catch (e) {
      console.error('Error searching target ' + targetId + ': ' + e.message);
      return;
    }

    try {
      if (existingTargetEvents && existingTargetEvents.length > 0) {
        const existingEventId = existingTargetEvents[0].id;
        if (sourceEvent.status === 'cancelled') {
          Calendar.Events.remove(targetId, existingEventId);
        } else {
          Calendar.Events.patch(newEvent, targetId, existingEventId);
        }
      } else {
        if (sourceEvent.status !== 'cancelled') {
          Calendar.Events.insert(newEvent, targetId);
        }
      }
    } catch (e) {
      console.error('Error writing to ' + targetId + ': ' + e.message);
    }
  },

  retryDeferredExceptions: function() {
    const deferred = this._deferredExceptions;
    this._deferredExceptions = [];
    if (deferred.length === 0) return;

    Logger.log('Retrying ' + deferred.length + ' deferred exception event(s)...');
    for (const item of deferred) {
      this.processSingleEvent(item.sourceEvent, item.sourceId, item.targetId, item.stripDetails, item.directionSourceOrigin);
    }
  },

  resetCycleState: function() {
    this._masterCache = {};
    this._deferredExceptions = [];
  }
};
