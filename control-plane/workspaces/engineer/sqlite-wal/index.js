/**
 * SQLite WAL Mode Connection Wrapper
 * 
 * Implements WAL (Write-Ahead Logging) mode for SQLite connections in the control-plane.
 * 
 * Features:
 * - Automatic WAL mode enablement on connection open
 * - 5 second busy timeout for contention handling
 * - WAL checkpoint on graceful shutdown
 * - Backward compatible - existing databases auto-convert on first open
 * 
 * Task: tsk_7b78704fafc6
 * Thread: thr_7d61b6889ec3
 */

const { DatabaseSync } = require('node:sqlite');
const path = require('path');
const fs = require('fs');

/**
 * Default configuration for WAL mode connections
 */
const DEFAULT_CONFIG = {
  busyTimeoutMs: 5000,      // 5 second retry on SQLITE_BUSY
  synchronous: 'NORMAL',    // Safe with WAL mode, better performance than FULL
  cacheSize: -64000,        // 64MB cache (negative = KB)
  autoCheckpoint: 1000,     // Auto-checkpoint every 1000 pages (default)
};

/**
 * Creates a new SQLite database connection with WAL mode enabled.
 * 
 * @param {string} dbPath - Path to the database file
 * @param {Object} [options] - Configuration options
 * @param {number} [options.busyTimeoutMs=5000] - Busy timeout in milliseconds
 * @param {string} [options.synchronous='NORMAL'] - Synchronous pragma value
 * @param {number} [options.cacheSize=-64000] - Cache size (negative = KB)
 * @param {number} [options.autoCheckpoint=1000] - Auto-checkpoint threshold (pages)
 * @returns {DatabaseSync} Configured database connection
 */
function openDatabase(dbPath, options = {}) {
  const config = { ...DEFAULT_CONFIG, ...options };
  
  // Ensure directory exists
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  
  // Open the database
  const db = new DatabaseSync(dbPath);
  
  // Enable WAL mode - this persists in the database file
  // Existing databases will auto-convert on first open
  const journalModeResult = db.exec('PRAGMA journal_mode=WAL;');
  
  // Set busy timeout for contention handling (5 second default)
  db.exec(`PRAGMA busy_timeout=${config.busyTimeoutMs};`);
  
  // Set synchronous mode - NORMAL is safe with WAL and faster than FULL
  db.exec(`PRAGMA synchronous=${config.synchronous};`);
  
  // Set cache size for better performance
  db.exec(`PRAGMA cache_size=${config.cacheSize};`);
  
  // Set auto-checkpoint threshold
  db.exec(`PRAGMA wal_autocheckpoint=${config.autoCheckpoint};`);
  
  return db;
}

/**
 * Performs a WAL checkpoint with TRUNCATE mode.
 * Call this during graceful shutdown to ensure all WAL data is written to the main database
 * and the WAL file is truncated to zero bytes.
 * 
 * @param {DatabaseSync} db - The database connection
 * @returns {Object} Checkpoint result with busy, log, and checkpointed page counts
 */
function checkpoint(db) {
  // TRUNCATE mode: checkpoint and truncate WAL file to zero bytes
  // This ensures clean state on next open
  const result = db.exec('PRAGMA wal_checkpoint(TRUNCATE);');
  
  // Query checkpoint status for logging
  const status = db.prepare('PRAGMA wal_checkpoint;').get();
  return {
    busy: status?.busy ?? 0,
    log: status?.log ?? 0,
    checkpointed: status?.checkpointed ?? 0,
  };
}

/**
 * Gracefully closes a database connection with WAL checkpoint.
 * 
 * @param {DatabaseSync} db - The database connection
 * @param {Object} [options] - Options
 * @param {boolean} [options.checkpoint=true] - Whether to run checkpoint before close
 * @returns {Object|null} Checkpoint result if checkpoint was performed
 */
function closeDatabase(db, options = { checkpoint: true }) {
  let checkpointResult = null;
  
  if (options.checkpoint) {
    try {
      checkpointResult = checkpoint(db);
    } catch (err) {
      // Log but don't fail - checkpoint is best-effort during shutdown
      console.warn('WAL checkpoint warning:', err.message);
    }
  }
  
  db.close();
  return checkpointResult;
}

/**
 * Creates a managed database connection with automatic cleanup.
 * Use this for scripts that need reliable shutdown handling.
 * 
 * @param {string} dbPath - Path to the database file
 * @param {Object} [options] - Configuration options (see openDatabase)
 * @returns {Object} Database wrapper with connection and cleanup methods
 */
function createManagedConnection(dbPath, options = {}) {
  const db = openDatabase(dbPath, options);
  let closed = false;
  
  const wrapper = {
    db,
    
    /**
     * Execute a SQL statement
     */
    exec: (sql) => db.exec(sql),
    
    /**
     * Prepare a statement
     */
    prepare: (sql) => db.prepare(sql),
    
    /**
     * Run a checkpoint
     */
    checkpoint: () => checkpoint(db),
    
    /**
     * Close the connection gracefully
     */
    close: (options) => {
      if (!closed) {
        closed = true;
        return closeDatabase(db, options);
      }
      return null;
    },
    
    /**
     * Check if connection is closed
     */
    get isClosed() {
      return closed;
    },
  };
  
  // Register shutdown handlers for graceful cleanup
  const shutdownHandler = () => {
    if (!closed) {
      console.log('Performing graceful SQLite WAL checkpoint on shutdown...');
      wrapper.close();
    }
  };
  
  process.on('SIGINT', shutdownHandler);
  process.on('SIGTERM', shutdownHandler);
  process.on('beforeExit', shutdownHandler);
  
  return wrapper;
}

/**
 * Get WAL status information for a database
 * 
 * @param {DatabaseSync} db - The database connection
 * @returns {Object} WAL status information
 */
function getWalStatus(db) {
  const journalMode = db.prepare('PRAGMA journal_mode;').get();
  const walCheckpoint = db.prepare('PRAGMA wal_checkpoint;').get();
  const busyTimeout = db.prepare('PRAGMA busy_timeout;').get();
  const synchronous = db.prepare('PRAGMA synchronous;').get();
  
  return {
    journalMode: journalMode?.journal_mode ?? 'unknown',
    isWalMode: journalMode?.journal_mode === 'wal',
    checkpoint: {
      busy: walCheckpoint?.busy ?? 0,
      log: walCheckpoint?.log ?? 0,
      checkpointed: walCheckpoint?.checkpointed ?? 0,
    },
    // busy_timeout returns 'timeout' column, not 'busy_timeout'
    busyTimeoutMs: busyTimeout?.timeout ?? busyTimeout?.busy_timeout ?? 0,
    synchronous: synchronous?.synchronous ?? 'unknown',
  };
}

module.exports = {
  openDatabase,
  closeDatabase,
  checkpoint,
  createManagedConnection,
  getWalStatus,
  DEFAULT_CONFIG,
};
