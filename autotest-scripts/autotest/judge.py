"""判定：把"预期结果"和"日志推断的实际结果"做比对。

只有 **是否允许通行** 与 **失败节点名称** 都一致才算用例通过。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .config import JudgeConfig
from .logstore import Resolution
from .protocol import Expected


@dataclass(slots=True)
class Verdict:
    """用例判定结果。"""

    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    expected_allow: bool | None = None
    actual_allow: bool | None = None
    expected_node: str | None = None
    actual_node: str | None = None
    actual_nodes: list[str] = field(default_factory=list)
    resolution_note: str = ""
    timed_out: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "expected_allow_pass": self.expected_allow,
            "actual_allow_pass": self.actual_allow,
            "expected_failed_node": self.expected_node,
            "actual_failed_node": self.actual_node,
            "actual_failed_nodes": list(self.actual_nodes),
            "resolution": self.resolution_note,
            "timed_out": self.timed_out,
        }


_PUNCT_RE = re.compile(r"[\s\[\]【】()（）<>《》〈〉:：,，。.、/\\|_\-—－–~`'\"“”‘’!！?？;；]+")


class Judge:
    """预期与实际比对器。"""

    def __init__(self, config: JudgeConfig) -> None:
        self._config = config
        self._aliases: dict[str, str] = {}
        for source, target in config.node_aliases.items():
            self._aliases[self._normalize_token(source)] = self._normalize_token(target)

    @staticmethod
    def _normalize_token(text: str | None) -> str:
        if not text:
            return ""
        value = unicodedata.normalize("NFKC", str(text)).strip().casefold()
        return _PUNCT_RE.sub("", value)

    def normalize(self, text: str | None) -> str:
        """节点名归一化：全角转半角、去空白与标点、小写，再套别名表。"""
        token = self._normalize_token(text)
        return self._aliases.get(token, token)

    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        expected: Expected,
        resolution: Resolution,
        *,
        timed_out: bool = False,
    ) -> Verdict:
        verdict = Verdict(
            expected_allow=expected.allow_pass,
            expected_node=expected.failed_node,
            actual_allow=resolution.allow_pass,
            actual_nodes=[node for node in resolution.failed_nodes if node],
            resolution_note=resolution.note,
            timed_out=timed_out,
        )
        if verdict.actual_nodes:
            verdict.actual_node = verdict.actual_nodes[-1]

        # 1) 没拿到任何有效日志
        if not resolution.settled:
            verdict.passed = False
            if timed_out:
                verdict.reasons.append(
                    f"超时仍未在该旅客的日志中找到「执行失败」或「离开通道」记录"
                    f"（未判定实际结果，按不通过处理）"
                )
                verdict.reasons.append(
                    "如果日志里确实有结论行，说明措辞与 patterns 不符："
                    "跑 python tools\\check_channels.py --only logs 看真实行样，"
                    "把措辞加进 config.yaml 的 patterns.passenger_exit"
                )
            else:
                verdict.reasons.append("尚未从日志中判定出实际结果")
            return verdict

        # 2) 同一会话自相矛盾
        if resolution.contradictory:
            verdict.reasons.append(
                "同一会话内既出现「节点执行失败」又出现「旅客离开通道」，需要人工复核"
            )
            if self._config.contradiction_as_failure:
                verdict.passed = False
                return verdict

        # 3) 是否允许通行
        expected_text = "允许通行" if expected.allow_pass else "不允许通行"
        actual_text = (
            "允许通行" if resolution.allow_pass else "不允许通行" if resolution.allow_pass is not None else "未知"
        )
        if expected.allow_pass != resolution.allow_pass:
            verdict.reasons.append(f"通行结论不一致：预期「{expected_text}」，实际「{actual_text}」")
            verdict.passed = False
            return verdict

        # 4) 不允许通行时，还要求失败节点一致
        if not expected.allow_pass:
            if not expected.failed_node:
                verdict.reasons.append("预期未给出失败节点名称，跳过节点比对（仅比对了通行结论）")
                verdict.passed = True
                return verdict
            expected_token = self.normalize(expected.failed_node)
            actual_tokens = {self.normalize(node) for node in verdict.actual_nodes}
            if expected_token in actual_tokens:
                verdict.passed = True
                verdict.reasons.append(
                    f"通行结论一致（不允许通行），失败节点一致：{expected.failed_node}"
                )
            else:
                verdict.passed = False
                actual_show = "、".join(verdict.actual_nodes) or "无"
                verdict.reasons.append(
                    f"失败节点不一致：预期「{expected.failed_node}」，实际「{actual_show}」"
                )
            return verdict

        verdict.passed = True
        verdict.reasons.append("通行结论一致（允许通行）")
        return verdict
