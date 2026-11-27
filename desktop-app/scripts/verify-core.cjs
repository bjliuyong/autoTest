const assert = require('assert')
const fs = require('fs')
const path = require('path')
const { createJiti } = require('jiti')
const initSqlJs = require('sql.js')

const jiti = createJiti(__filename)

async function main() {
  const { parseGateMessage, getPassengerCandidates } = await jiti.import('../lib/autotest/protocol.ts')
  const { RoundTracker } = await jiti.import('../lib/autotest/rounds.ts')
  const { LogStore, linesToText } = await jiti.import('../lib/autotest/logstore.ts')
  const { Judge } = await jiti.import('../lib/autotest/judge.ts')
  const { DEFAULT_APP_CONFIG } = await jiti.import('../lib/autotest/types.ts')

  const logStore = new LogStore(DEFAULT_APP_CONFIG.patterns, DEFAULT_APP_CONFIG.run)
  const judge = new Judge({ ...DEFAULT_APP_CONFIG.judge, nodeAliases: { 证照核验: '证件核验' } })
  const rounds = new RoundTracker()

  // 1) PASS (allow_pass = true)
  const raw1 = {
    type: 'gate.capture',
    case_id: 'C-PASS-1',
    serial: 'S1_a',
    expected: { allow_pass: true },
    capture: { passenger_name: '张三', cert_no: '110101' },
  }
  rounds.observe(raw1, logStore.nextIndex)
  const cap1 = parseGateMessage(raw1)
  logStore.appendMessage(
    '[流程引擎]创建新会话S0001\n[流程引擎]旅客[张三]证件读取成功\n[通行控制]旅客[张三]离开通道'
  )
  const res1 = logStore.resolve(
    getPassengerCandidates(cap1, DEFAULT_APP_CONFIG.judge.passengerKeys),
    cap1.logMarker
  )
  const v1 = judge.evaluate(cap1.expected, res1, false)
  assert.strictEqual(v1.status, 'PASS', 'Case 1 should PASS')
  assert.ok(linesToText(logStore.sliceSession(res1.sid)).includes('创建新会话S0001'))

  // 2) PASS via node alias + lenient Chinese boolean
  const raw2 = {
    case_id: 'C-ALIAS-2',
    allow_pass: '不允许',
    failed_node: '证件核验',
    passenger_name: '李四',
    cert_no: '310101',
  }
  const cap2 = parseGateMessage(raw2)
  cap2.logMarker = logStore.nextIndex
  logStore.appendMessage('[流程引擎]创建新会话S0002\n[证件]会话ID[S0002]-节点证照核验执行失败 原因=过期')
  const res2 = logStore.resolve(
    getPassengerCandidates(cap2, DEFAULT_APP_CONFIG.judge.passengerKeys),
    cap2.logMarker
  )
  const v2 = judge.evaluate(cap2.expected, res2, false)
  assert.strictEqual(v2.status, 'PASS', 'Case 2 should PASS via node alias')

  // 3) FAIL on node mismatch
  const raw3 = {
    case_id: 'C-FAIL-3',
    expected: { allow_pass: false, failed_node: '人脸比对' },
    capture: { passenger_name: '王五', cert_no: '440301' },
  }
  const cap3 = parseGateMessage(raw3)
  cap3.logMarker = logStore.nextIndex
  logStore.appendMessage('[流程引擎]创建新会话S0003\n[证件]会话ID[S0003]-节点证件核验执行失败')
  const res3 = logStore.resolve(
    getPassengerCandidates(cap3, DEFAULT_APP_CONFIG.judge.passengerKeys),
    cap3.logMarker
  )
  const v3 = judge.evaluate(cap3.expected, res3, false)
  assert.strictEqual(v3.status, 'FAIL', 'Case 3 should FAIL on node mismatch')

  // 4) TIMEOUT
  const raw4 = {
    case_id: 'C-TO-4',
    expected: { allow_pass: true },
    capture: { passenger_name: '赵六', cert_no: '500101' },
  }
  const cap4 = parseGateMessage(raw4)
  cap4.logMarker = logStore.nextIndex
  logStore.appendMessage('[流程引擎]创建新会话S0004\n[流程引擎]旅客[赵六]等待摄像头')
  const res4 = logStore.resolve(
    getPassengerCandidates(cap4, DEFAULT_APP_CONFIG.judge.passengerKeys),
    cap4.logMarker
  )
  const v4 = judge.evaluate(cap4.expected, res4, true)
  assert.strictEqual(v4.status, 'TIMEOUT', 'Case 4 should TIMEOUT')

  // 5) SQLite WASM persistence roundtrip
  const SQL = await initSqlJs({ locateFile: () => require.resolve('sql.js/dist/sql-wasm.wasm') })
  const sqlDb = new SQL.Database()
  sqlDb.run("CREATE TABLE test_batches (id INTEGER PRIMARY KEY, run_code TEXT); INSERT INTO test_batches VALUES (1, 'SELF-TEST');")
  const tmpFile = path.join(__dirname, '.verify-tmp.sqlite')
  fs.writeFileSync(tmpFile, Buffer.from(sqlDb.export()))
  const reloaded = new SQL.Database(fs.readFileSync(tmpFile))
  const stmt = reloaded.prepare('SELECT run_code FROM test_batches WHERE id = 1')
  assert.ok(stmt.step())
  assert.strictEqual(stmt.getAsObject().run_code, 'SELF-TEST')
  stmt.free()
  sqlDb.close()
  reloaded.close()
  fs.unlinkSync(tmpFile)

  console.log('ALL_CORE_AND_SQLITE_TESTS_PASSED (5/5)')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
