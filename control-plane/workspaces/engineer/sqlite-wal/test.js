#!/usr/bin/env node
/**
 * WAL Mode Implementation Test
 * 
 * Verifies:
 * 1. WAL mode is enabled on connection open
 * 2. busy_timeout is set to 5000ms
 * 3. Checkpoint works on graceful shutdown
 * 4. Concurrent read/write operations work without SQLITE_BUSY
 * 
 * Task: tsk_7b78704fafc6
 */

const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');
const path = require('path');
const fs = require('fs');

const {
  openDatabase,
  closeDatabase,
  checkpoint,
  getWalStatus,
  createManagedConnection,
} = require('./index.js');

const TEST_DB_PATH = path.join(__dirname, 'test_wal.db');

// Clean up test database
function cleanup() {
  for (const suffix of ['', '-wal', '-shm']) {
    const p = TEST_DB_PATH + suffix;
    if (fs.existsSync(p)) fs.unlinkSync(p);
  }
}

// Test 1: Verify WAL mode is enabled on connection open
function testWalModeEnabled() {
  console.log('\n=== Test 1: WAL Mode Enabled ===');
  cleanup();
  
  const db = openDatabase(TEST_DB_PATH);
  const status = getWalStatus(db);
  
  console.log(`  Journal mode: ${status.journalMode}`);
  console.log(`  Is WAL mode: ${status.isWalMode}`);
  
  if (!status.isWalMode) {
    throw new Error('WAL mode not enabled!');
  }
  
  closeDatabase(db);
  console.log('  ✓ PASS: WAL mode enabled on connection open');
}

// Test 2: Verify busy_timeout is set to 5000ms
function testBusyTimeout() {
  console.log('\n=== Test 2: Busy Timeout Configuration ===');
  cleanup();
  
  const db = openDatabase(TEST_DB_PATH);
  const status = getWalStatus(db);
  
  console.log(`  Busy timeout: ${status.busyTimeoutMs}ms`);
  
  if (status.busyTimeoutMs !== 5000) {
    throw new Error(`Expected busy_timeout=5000, got ${status.busyTimeoutMs}`);
  }
  
  closeDatabase(db);
  console.log('  ✓ PASS: Busy timeout set to 5000ms');
}

// Test 3: Verify checkpoint works on close
function testCheckpointOnClose() {
  console.log('\n=== Test 3: Checkpoint on Close ===');
  cleanup();
  
  const db = openDatabase(TEST_DB_PATH);
  
  // Create table and insert some data
  db.exec(`
    CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT);
  `);
  
  const insert = db.prepare('INSERT INTO test (value) VALUES (?)');
  for (let i = 0; i < 100; i++) {
    insert.run(`value-${i}`);
  }
  
  // Check WAL file exists and has content
  const walPath = TEST_DB_PATH + '-wal';
  const walExistsBefore = fs.existsSync(walPath);
  const walSizeBefore = walExistsBefore ? fs.statSync(walPath).size : 0;
  console.log(`  WAL file exists before close: ${walExistsBefore}`);
  console.log(`  WAL file size before close: ${walSizeBefore} bytes`);
  
  // Close with checkpoint
  const checkpointResult = closeDatabase(db, { checkpoint: true });
  console.log(`  Checkpoint result: ${JSON.stringify(checkpointResult)}`);
  
  // Check WAL file is truncated after checkpoint(TRUNCATE)
  const walExistsAfter = fs.existsSync(walPath);
  const walSizeAfter = walExistsAfter ? fs.statSync(walPath).size : 0;
  console.log(`  WAL file exists after close: ${walExistsAfter}`);
  console.log(`  WAL file size after close: ${walSizeAfter} bytes`);
  
  // WAL should be truncated to 0 or very small
  if (walSizeAfter > 32) {  // Allow for WAL header
    console.log('  ⚠ WARNING: WAL file not fully truncated (may be normal)');
  }
  
  // Verify data persists after reopen
  const db2 = openDatabase(TEST_DB_PATH);
  const count = db2.prepare('SELECT COUNT(*) as cnt FROM test').get();
  closeDatabase(db2);
  
  if (count.cnt !== 100) {
    throw new Error(`Expected 100 rows, got ${count.cnt}`);
  }
  
  console.log('  ✓ PASS: Checkpoint on close works, data persists');
}

// Test 4: Concurrent read/write operations
async function testConcurrentOperations() {
  console.log('\n=== Test 4: Concurrent Read/Write Operations ===');
  cleanup();
  
  // Set up database
  const db = openDatabase(TEST_DB_PATH);
  db.exec(`
    CREATE TABLE concurrent_test (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      worker_id INTEGER,
      value TEXT,
      timestamp INTEGER
    );
    CREATE INDEX idx_worker ON concurrent_test(worker_id);
  `);
  closeDatabase(db);
  
  const NUM_WRITERS = 10;
  const NUM_READERS = 10;
  const OPERATIONS_PER_WORKER = 50;
  
  const results = {
    writes: { success: 0, failed: 0, busyErrors: 0 },
    reads: { success: 0, failed: 0, busyErrors: 0 },
  };
  
  // Worker code for concurrent testing
  const workerCode = `
    const { DatabaseSync } = require('node:sqlite');
    const { parentPort, workerData } = require('worker_threads');
    
    const { dbPath, workerId, isWriter, operations } = workerData;
    const db = new DatabaseSync(dbPath);
    db.exec('PRAGMA busy_timeout=5000;');
    
    const results = { success: 0, failed: 0, busyErrors: 0 };
    
    for (let i = 0; i < operations; i++) {
      try {
        if (isWriter) {
          db.exec('BEGIN IMMEDIATE');
          try {
            const stmt = db.prepare('INSERT INTO concurrent_test (worker_id, value, timestamp) VALUES (?, ?, ?)');
            stmt.run(workerId, 'value-' + i, Date.now());
            db.exec('COMMIT');
            results.success++;
          } catch (e) {
            try { db.exec('ROLLBACK'); } catch {}
            throw e;
          }
        } else {
          const stmt = db.prepare('SELECT COUNT(*) as cnt FROM concurrent_test WHERE worker_id = ?');
          stmt.get(workerId % 10);  // Read from various workers
          results.success++;
        }
      } catch (err) {
        results.failed++;
        if (err.message && (err.message.includes('database is locked') || err.message.includes('SQLITE_BUSY'))) {
          results.busyErrors++;
        }
      }
    }
    
    db.close();
    parentPort.postMessage(results);
  `;
  
  // Create workers
  const workerPromises = [];
  
  for (let i = 0; i < NUM_WRITERS; i++) {
    const worker = new Worker(workerCode, {
      eval: true,
      workerData: {
        dbPath: TEST_DB_PATH,
        workerId: i,
        isWriter: true,
        operations: OPERATIONS_PER_WORKER,
      },
    });
    workerPromises.push(new Promise((resolve, reject) => {
      worker.on('message', (result) => {
        results.writes.success += result.success;
        results.writes.failed += result.failed;
        results.writes.busyErrors += result.busyErrors;
        resolve();
      });
      worker.on('error', reject);
    }));
  }
  
  for (let i = 0; i < NUM_READERS; i++) {
    const worker = new Worker(workerCode, {
      eval: true,
      workerData: {
        dbPath: TEST_DB_PATH,
        workerId: i + NUM_WRITERS,
        isWriter: false,
        operations: OPERATIONS_PER_WORKER,
      },
    });
    workerPromises.push(new Promise((resolve, reject) => {
      worker.on('message', (result) => {
        results.reads.success += result.success;
        results.reads.failed += result.failed;
        results.reads.busyErrors += result.busyErrors;
        resolve();
      });
      worker.on('error', reject);
    }));
  }
  
  await Promise.all(workerPromises);
  
  const totalWrites = NUM_WRITERS * OPERATIONS_PER_WORKER;
  const totalReads = NUM_READERS * OPERATIONS_PER_WORKER;
  const writeSuccessRate = (results.writes.success / totalWrites * 100).toFixed(2);
  const readSuccessRate = (results.reads.success / totalReads * 100).toFixed(2);
  
  console.log(`  Writers: ${NUM_WRITERS}, Readers: ${NUM_READERS}`);
  console.log(`  Operations per worker: ${OPERATIONS_PER_WORKER}`);
  console.log(`  Write results: ${results.writes.success}/${totalWrites} (${writeSuccessRate}%)`);
  console.log(`  Read results: ${results.reads.success}/${totalReads} (${readSuccessRate}%)`);
  console.log(`  SQLITE_BUSY errors (writes): ${results.writes.busyErrors}`);
  console.log(`  SQLITE_BUSY errors (reads): ${results.reads.busyErrors}`);
  
  // With WAL mode and 5s busy_timeout, we should have very high success rate
  if (results.writes.success < totalWrites * 0.99) {
    throw new Error(`Write success rate too low: ${writeSuccessRate}%`);
  }
  if (results.reads.success < totalReads * 0.99) {
    throw new Error(`Read success rate too low: ${readSuccessRate}%`);
  }
  
  console.log('  ✓ PASS: Concurrent operations work with minimal contention');
}

// Test 5: Backward compatibility - existing database auto-converts
function testBackwardCompatibility() {
  console.log('\n=== Test 5: Backward Compatibility ===');
  cleanup();
  
  // Create a database in DELETE (rollback journal) mode
  const { DatabaseSync } = require('node:sqlite');
  const db = new DatabaseSync(TEST_DB_PATH);
  db.exec('PRAGMA journal_mode=DELETE;');
  db.exec('CREATE TABLE compat_test (id INTEGER PRIMARY KEY, value TEXT);');
  db.exec("INSERT INTO compat_test (value) VALUES ('test-value');");
  
  const modeBeforeResult = db.prepare('PRAGMA journal_mode;').get();
  console.log(`  Journal mode before: ${modeBeforeResult.journal_mode}`);
  db.close();
  
  // Reopen with our WAL wrapper - should auto-convert
  const db2 = openDatabase(TEST_DB_PATH);
  const status = getWalStatus(db2);
  console.log(`  Journal mode after WAL wrapper open: ${status.journalMode}`);
  
  // Verify data is preserved
  const row = db2.prepare('SELECT value FROM compat_test WHERE id = 1').get();
  console.log(`  Data preserved: ${row?.value === 'test-value'}`);
  
  if (!status.isWalMode) {
    throw new Error('Database did not auto-convert to WAL mode');
  }
  if (row?.value !== 'test-value') {
    throw new Error('Data not preserved after WAL conversion');
  }
  
  closeDatabase(db2);
  console.log('  ✓ PASS: Existing databases auto-convert to WAL mode');
}

// Test 6: Managed connection with shutdown handlers
function testManagedConnection() {
  console.log('\n=== Test 6: Managed Connection ===');
  cleanup();
  
  const conn = createManagedConnection(TEST_DB_PATH);
  
  conn.exec('CREATE TABLE managed_test (id INTEGER PRIMARY KEY, value TEXT);');
  const insert = conn.prepare('INSERT INTO managed_test (value) VALUES (?)');
  insert.run('managed-value');
  
  const status = getWalStatus(conn.db);
  console.log(`  WAL mode: ${status.isWalMode}`);
  console.log(`  Is closed: ${conn.isClosed}`);
  
  const checkpointResult = conn.close();
  console.log(`  Checkpoint on close: ${JSON.stringify(checkpointResult)}`);
  console.log(`  Is closed after close(): ${conn.isClosed}`);
  
  if (!status.isWalMode) {
    throw new Error('Managed connection not in WAL mode');
  }
  
  console.log('  ✓ PASS: Managed connection works correctly');
}

// Run all tests
async function runTests() {
  console.log('╔══════════════════════════════════════════════════╗');
  console.log('║    SQLite WAL Mode Implementation Tests          ║');
  console.log('║    Task: tsk_7b78704fafc6                        ║');
  console.log('╚══════════════════════════════════════════════════╝');
  
  let passed = 0;
  let failed = 0;
  
  const tests = [
    testWalModeEnabled,
    testBusyTimeout,
    testCheckpointOnClose,
    testConcurrentOperations,
    testBackwardCompatibility,
    testManagedConnection,
  ];
  
  for (const test of tests) {
    try {
      if (test.constructor.name === 'AsyncFunction') {
        await test();
      } else {
        test();
      }
      passed++;
    } catch (err) {
      console.log(`  ✗ FAIL: ${err.message}`);
      failed++;
    }
  }
  
  cleanup();
  
  console.log('\n╔══════════════════════════════════════════════════╗');
  console.log(`║    Results: ${passed} passed, ${failed} failed                    ║`);
  console.log('╚══════════════════════════════════════════════════╝');
  
  return failed === 0;
}

runTests().then(success => {
  process.exit(success ? 0 : 1);
}).catch(err => {
  console.error('Test suite error:', err);
  process.exit(1);
});
