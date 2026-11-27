import type { AppConfig, ExpectedResult, Resolution, Verdict } from './types'

const PUNCT_RE = /[\s[\]【】()（）<>《》〈〉:：,，。.、/\\|_\-—－–~`'"“”‘’!！?？;；]+/g

export class Judge {
  private readonly contradictionAsFailure: boolean
  private readonly aliases = new Map<string, string>()

  constructor(config: AppConfig['judge']) {
    this.contradictionAsFailure = config.contradictionAsFailure
    for (const [source, target] of Object.entries(config.nodeAliases ?? {})) {
      this.aliases.set(Judge.normalizeToken(source), Judge.normalizeToken(target))
    }
  }

  static normalizeToken(text: string | null | undefined): string {
    if (!text) return ''
    return String(text).normalize('NFKC').trim().toLowerCase().replace(PUNCT_RE, '')
  }

  normalize(text: string | null | undefined): string {
    const token = Judge.normalizeToken(text)
    return this.aliases.get(token) ?? token
  }

  evaluate(expected: ExpectedResult, resolution: Resolution, timedOut = false): Verdict {
    const actualNodes = resolution.failedNodes.filter(Boolean)
    const verdict: Verdict = {
      status: 'FAIL',
      passed: false,
      reasons: [],
      expectedAllow: expected.allowPass,
      actualAllow: resolution.allowPass,
      expectedNode: expected.failedNode,
      actualNode: actualNodes.length > 0 ? actualNodes[actualNodes.length - 1] : null,
      actualNodes,
      resolutionNote: resolution.note,
      timedOut,
    }

    // 1) 未拿到任何有效日志结论
    if (!resolution.settled) {
      verdict.passed = false
      verdict.status = timedOut ? 'TIMEOUT' : 'FAIL'
      if (timedOut) {
        verdict.reasons.push(
          '超时仍未在该旅客的日志中找到「执行失败」或「离开通道」记录（未判定实际结果，按超时处理）'
        )
      } else {
        verdict.reasons.push('尚未从日志中判定出实际结果')
      }
      return verdict
    }

    // 2) 同一会话自相矛盾
    if (resolution.contradictory) {
      verdict.reasons.push('同一会话内既出现「节点执行失败」又出现「旅客离开通道」，需要人工复核')
      if (this.contradictionAsFailure) {
        verdict.passed = false
        verdict.status = 'FAIL'
        return verdict
      }
    }

    // 3) 是否允许通行
    const expectedText = expected.allowPass ? '允许通行' : '不允许通行'
    const actualText =
      resolution.allowPass === true ? '允许通行' : resolution.allowPass === false ? '不允许通行' : '未知'

    if (expected.allowPass !== resolution.allowPass) {
      verdict.reasons.push(`通行结论不一致：预期「${expectedText}」，实际「${actualText}」`)
      verdict.passed = false
      verdict.status = 'FAIL'
      return verdict
    }

    // 4) 不允许通行时，还要求失败节点一致
    if (!expected.allowPass) {
      if (!expected.failedNode) {
        verdict.reasons.push('预期未给出失败节点名称，跳过节点比对（仅比对了通行结论）')
        verdict.passed = true
        verdict.status = 'PASS'
        return verdict
      }
      const expectedToken = this.normalize(expected.failedNode)
      const actualTokens = new Set(verdict.actualNodes.map((n) => this.normalize(n)))
      if (actualTokens.has(expectedToken)) {
        verdict.passed = true
        verdict.status = 'PASS'
        verdict.reasons.push(`通行结论一致（不允许通行），失败节点一致：${expected.failedNode}`)
      } else {
        verdict.passed = false
        verdict.status = 'FAIL'
        const actualShow = verdict.actualNodes.join('、') || '无'
        verdict.reasons.push(`失败节点不一致：预期「${expected.failedNode}」，实际「${actualShow}」`)
      }
      return verdict
    }

    verdict.passed = true
    verdict.status = 'PASS'
    verdict.reasons.push('通行结论一致（允许通行）')
    return verdict
  }
}
