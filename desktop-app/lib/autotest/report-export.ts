import type { TestBatch, TestCaseRecord } from './types'

function esc(s: unknown): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function buildBatchHtmlReport(batch: TestBatch, cases: TestCaseRecord[]): string {
  const rows = cases
    .map((c) => {
      const badgeColor =
        c.verdictStatus === 'PASS' ? '#16a34a' : c.verdictStatus === 'TIMEOUT' ? '#d97706' : '#dc2626'
      return `<tr>
        <td><code>${esc(c.caseId)}</code></td>
        <td><span style="background:${badgeColor};color:#fff;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;">${esc(c.verdictStatus)}</span></td>
        <td>${esc(c.passengerName || '-')} / ${esc(c.certNo || '-')}</td>
        <td>${c.expectedAllow ? '允许通行' : `拒绝 (${esc(c.expectedNode || '未指定')})`}</td>
        <td>${c.actualAllow === null ? '超时未判定' : c.actualAllow ? '允许通行' : `拒绝 (${esc(c.actualNode || '未知')})`}</td>
        <td>${esc(c.reasons.join('；'))}</td>
        <td>${esc(c.judgedAt)}</td>
      </tr>`
    })
    .join('\n')

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>测试批次报告 - ${esc(batch.runCode)}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #0f172a; background: #f8fafc; }
  .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 20px 24px; margin-bottom: 20px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-top: 12px; }
  .kpi { background: #f1f5f9; padding: 14px; border-radius: 8px; }
  .kpi .val { font-size: 24px; font-weight: 700; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }
  th { background: #f8fafc; font-weight: 600; color: #475569; }
</style>
</head>
<body>
  <div class="card">
    <h1 style="margin:0 0 6px 0;">闸机自动化测试批次报告 · ${esc(batch.name)}</h1>
    <div style="color:#64748b;font-size:13px;">批次号：${esc(batch.runCode)} ｜ 开始时间：${esc(batch.startedAt)} ｜ 状态：${esc(batch.status)}</div>
    <div class="grid">
      <div class="kpi"><div style="color:#64748b;font-size:12px;">总用例数</div><div class="val">${batch.totalCases}</div></div>
      <div class="kpi"><div style="color:#16a34a;font-size:12px;">通过数</div><div class="val" style="color:#16a34a;">${batch.passedCases}</div></div>
      <div class="kpi"><div style="color:#dc2626;font-size:12px;">失败 / 超时</div><div class="val" style="color:#dc2626;">${batch.failedCases} / ${batch.timeoutCases}</div></div>
      <div class="kpi"><div style="color:#2563eb;font-size:12px;">通过率</div><div class="val" style="color:#2563eb;">${batch.passRate}%</div></div>
    </div>
  </div>
  <div class="card">
    <h2 style="margin:0 0 12px 0;font-size:16px;">用例判定明细（${cases.length} 条）</h2>
    <table>
      <thead>
        <tr><th>用例编号</th><th>结果</th><th>旅客 / 证件号</th><th>预期结论</th><th>实际结论</th><th>判定说明</th><th>判定时间</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
</body>
</html>`
}
