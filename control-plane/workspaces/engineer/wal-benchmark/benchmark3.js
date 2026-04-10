#!/usr/bin/env node
/**
 * WAL Exponential Backoff Benchmark v3
 * 
 * Hybrid approach: moderate busy_timeout + exponential backoff
 * - busy_timeout=1000ms gives SQLite some internal retry
 * - App-level exponential backoff for additional resilience
 * - Target: P99 < 1000ms with 100 concurrent writers
 */

const { DatabaseSync } = require('node:sqlite');
const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');
const path = require('path');
const fs = require('fs');

const CONFIG = {
  DB_PATH: path.join(__dirname, 'benchmark3.db'),
  NUM_WORKERS: 100,
  WRITES_PER_WORKER: 50,
  BASE_DELAY_MS: 10,
  MAX_RETRIES: 5,
  JITTER_FACTOR: 0.5,
  // Hybrid: moderate busy_timeout + app-level backoff
  BUSY_TIMEOUT_MS: 500,
};

// Worker code
if (!isMainThread) {
  const { workerId, dbPath, writesPerWorker, busyTimeout, baseDelay, maxRetries, jitterFactor } = workerData;
  
  const results = [];
  const db = new DatabaseSync(dbPath);
  db.exec(`PRAGMA busy_timeout = ${busyTimeout};`);
  
  const insert = db.prepare('INSERT INTO benchmark (worker_id, sequence, data, timestamp) VALUES (?, ?, ?, ?)');
  
  for (let i = 0; i < writesPerWorker; i++) {
    const startTime = performance.now();
    let success = false;
    let attempts = 0;
    let lastError = null;
    
    while (!success && attempts <= maxRetries) {
      try {
        if (attempts > 0) {
          // Exponential backoff: 10ms, 20ms, 40ms, 80ms, 160ms with jitter
          const delay = Math.max(1, Math.floor(
            baseDelay * Math.pow(2, attempts - 1) * (1 + jitterFactor * (Math.random() * 2 - 1))
          ));
          const end = Date.now() + delay;
          while (Date.now() < end) {}
        }
        
        db.exec('BEGIN IMMEDIATE');
        try {
          insert.run(workerId, i, `data-${workerId}-${i}-${Date.now()}`, Date.now());
          db.exec('COMMIT');
          success = true;
        } catch (e) {
          try { db.exec('ROLLBACK'); } catch {}
          throw e;
        }
      } catch (err) {
        lastError = err;
        attempts++;
        if (err.message && (err.message.includes('database is locked') || err.message.includes('SQLITE_BUSY'))) {
          continue;
        }
        break;
      }
    }
    
    const endTime = performance.now();
    results.push({
      workerId,
      sequence: i,
      success,
      attempts,
      latencyMs: endTime - startTime,
      error: success ? null : (lastError ? lastError.message : 'unknown')
    });
  }
  
  db.close();
  parentPort.postMessage(results);
  process.exit(0);
}

async function runBenchmark() {
  console.log('=== WAL Exponential Backoff Benchmark v3 (Hybrid) ===\n');
  console.log('Configuration:');
  console.log(`  Workers: ${CONFIG.NUM_WORKERS}`);
  console.log(`  Writes per worker: ${CONFIG.WRITES_PER_WORKER}`);
  console.log(`  Total writes: ${CONFIG.NUM_WORKERS * CONFIG.WRITES_PER_WORKER}`);
  console.log(`  Base delay: ${CONFIG.BASE_DELAY_MS}ms`);
  console.log(`  Max retries: ${CONFIG.MAX_RETRIES}`);
  console.log(`  Jitter factor: ${CONFIG.JITTER_FACTOR}`);
  console.log(`  Busy timeout: ${CONFIG.BUSY_TIMEOUT_MS}ms (hybrid)`);
  console.log('');
  
  for (const suffix of ['', '-wal', '-shm']) {
    const p = CONFIG.DB_PATH + suffix;
    if (fs.existsSync(p)) fs.unlinkSync(p);
  }
  
  const db = new DatabaseSync(CONFIG.DB_PATH);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA synchronous = NORMAL;');
  db.exec(`PRAGMA busy_timeout = ${CONFIG.BUSY_TIMEOUT_MS};`);
  db.exec(`
    CREATE TABLE IF NOT EXISTS benchmark (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      worker_id INTEGER NOT NULL,
      sequence INTEGER NOT NULL,
      data TEXT,
      timestamp INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_worker ON benchmark(worker_id);
  `);
  db.close();
  
  console.log('Starting benchmark...\n');
  const startTime = performance.now();
  
  const workers = [];
  const allResults = [];
  
  for (let i = 0; i < CONFIG.NUM_WORKERS; i++) {
    const worker = new Worker(__filename, {
      workerData: {
        workerId: i,
        dbPath: CONFIG.DB_PATH,
        writesPerWorker: CONFIG.WRITES_PER_WORKER,
        busyTimeout: CONFIG.BUSY_TIMEOUT_MS,
        baseDelay: CONFIG.BASE_DELAY_MS,
        maxRetries: CONFIG.MAX_RETRIES,
        jitterFactor: CONFIG.JITTER_FACTOR,
      }
    });
    
    workers.push(new Promise((resolve, reject) => {
      worker.on('message', (results) => {
        allResults.push(...results);
        resolve();
      });
      worker.on('error', reject);
      worker.on('exit', (code) => {
        if (code !== 0) reject(new Error(`Worker exited with code ${code}`));
      });
    }));
  }
  
  await Promise.all(workers);
  const totalTime = performance.now() - startTime;
  
  const successResults = allResults.filter(r => r.success);
  const failedResults = allResults.filter(r => !r.success);
  const latencies = successResults.map(r => r.latencyMs).sort((a, b) => a - b);
  
  const successRate = (successResults.length / allResults.length) * 100;
  const throughput = successResults.length / (totalTime / 1000);
  
  const p50 = latencies[Math.floor(latencies.length * 0.50)] || 0;
  const p90 = latencies[Math.floor(latencies.length * 0.90)] || 0;
  const p95 = latencies[Math.floor(latencies.length * 0.95)] || 0;
  const p99 = latencies[Math.floor(latencies.length * 0.99)] || 0;
  const max = latencies[latencies.length - 1] || 0;
  const min = latencies[0] || 0;
  const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length || 0;
  
  const retryDist = {};
  for (const r of allResults) {
    retryDist[r.attempts] = (retryDist[r.attempts] || 0) + 1;
  }
  
  console.log('=== Results ===\n');
  console.log('Overall:');
  console.log(`  Total time: ${totalTime.toFixed(2)}ms`);
  console.log(`  Total writes attempted: ${allResults.length}`);
  console.log(`  Successful writes: ${successResults.length}`);
  console.log(`  Failed writes: ${failedResults.length}`);
  console.log(`  Success rate: ${successRate.toFixed(2)}%`);
  console.log(`  Throughput: ${throughput.toFixed(2)} writes/sec`);
  console.log('');
  console.log('Latency (successful writes):');
  console.log(`  Min: ${min.toFixed(2)}ms`);
  console.log(`  Avg: ${avg.toFixed(2)}ms`);
  console.log(`  P50: ${p50.toFixed(2)}ms`);
  console.log(`  P90: ${p90.toFixed(2)}ms`);
  console.log(`  P95: ${p95.toFixed(2)}ms`);
  console.log(`  P99: ${p99.toFixed(2)}ms  ${p99 < 1000 ? '✓ PASS' : '✗ FAIL'} (target: <1000ms)`);
  console.log(`  Max: ${max.toFixed(2)}ms`);
  console.log('');
  console.log('Retry distribution:');
  for (const [attempts, count] of Object.entries(retryDist).sort((a,b) => a[0] - b[0])) {
    console.log(`  ${attempts} attempt(s): ${count} (${(count/allResults.length*100).toFixed(1)}%)`);
  }
  
  if (failedResults.length > 0) {
    console.log('\nFailed write errors:');
    const errorCounts = {};
    for (const r of failedResults) {
      const err = r.error || 'unknown';
      errorCounts[err] = (errorCounts[err] || 0) + 1;
    }
    for (const [err, count] of Object.entries(errorCounts)) {
      console.log(`  ${err}: ${count}`);
    }
  }
  
  console.log('\n=== Summary ===');
  const passed = p99 < 1000 && successRate >= 99;
  if (passed) {
    console.log('✓ BENCHMARK PASSED');
    console.log(`  P99 ${p99.toFixed(2)}ms < 1000ms target`);
    console.log(`  Success rate ${successRate.toFixed(2)}% >= 99%`);
  } else {
    console.log('✗ BENCHMARK NEEDS TUNING');
    if (p99 >= 1000) console.log(`  P99 ${p99.toFixed(2)}ms >= 1000ms target`);
    if (successRate < 99) console.log(`  Success rate ${successRate.toFixed(2)}% < 99%`);
  }
  
  return {
    config: CONFIG,
    totalTimeMs: totalTime,
    totalWrites: allResults.length,
    successfulWrites: successResults.length,
    failedWrites: failedResults.length,
    successRate,
    throughput,
    latency: { min, avg, p50, p90, p95, p99, max },
    retryDistribution: retryDist,
    passed,
  };
}

runBenchmark().then(results => {
  fs.writeFileSync(
    path.join(__dirname, 'results3.json'),
    JSON.stringify(results, null, 2)
  );
  process.exit(results.passed ? 0 : 1);
}).catch(err => {
  console.error('Benchmark failed:', err);
  process.exit(1);
});
