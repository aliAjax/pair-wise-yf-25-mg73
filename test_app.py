import tempfile
import unittest
from pathlib import Path

from app import BusinessError, ReviewStore


class ReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReviewStore(Path(self.tmp.name) / "test.db")
        self.store.seed()

    def tearDown(self):
        self.tmp.cleanup()

    def _paper(self):
        return self.store.submit_paper("alice", "可靠分布式提交协议", "本文提出一种用于弱网环境的可靠提交协议，并通过模拟实验验证其安全性和性能。")["id"]

    def test_complete_flow_and_double_blind_view(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, 4, "方法严谨，缺少与最近工作的对比。")
        self.store.submit_review("r2", a2, 3, "实验充分，但部分结论需要进一步解释。")
        self.store.submit_rebuttal("alice", paper_id, "感谢意见，我们将补充对比并解释实验结论。")
        result = self.store.decide("chair", paper_id, "minor_revision", "补充实验后接收。")
        self.assertEqual(result["decision"], "minor_revision")
        self.assertIsNone(self.store.get_paper("r1", paper_id)["author_id"])
        self.assertIsNotNone(self.store.get_paper("chair", paper_id)["author_id"])
        history = self.store.history("chair", paper_id)
        self.assertEqual(history[-1]["action"], "decision.record")
        self.assertGreaterEqual(len(history), 8)

    def test_conflict_blocks_assignment_and_role_is_enforced(self):
        paper_id = self._paper()
        self.store.add_conflict("chair", paper_id, "r1", "同一导师团队成员")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", paper_id, "r1")
        self.assertEqual(ctx.exception.code, "conflict_of_interest")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("alice", paper_id, "r2")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.get_paper("r2", paper_id)
        self.assertEqual(ctx.exception.status, 403)

    def test_late_conflict_cancels_pending_invite_and_releases_load(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r3")["id"]
        paper2 = self.store.submit_paper(
            "bob", "另一个研究主题", "与第一篇无关的另一个完整摘要，用于验证负载释放。"
        )["id"]
        a2 = self.store.assign("chair", paper2, "r3")["id"]
        # r3 的上限是 2，已被两份待回应邀请占满。
        self.assertEqual(self.store.reviewer_loads("chair")[2]["active_load"], 2)
        paper3 = self.store.submit_paper(
            "bob", "第三个研究主题", "同样需要一份足够长的摘要来通过提交校验逻辑。"
        )["id"]
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", paper3, "r3")
        self.assertEqual(ctx.exception.code, "reviewer_at_capacity")
        # 事后才发现冲突：待回应邀请自动取消，负载随之释放。
        result = self.store.add_conflict("chair", paper_id, "r3", "邀请后发现合作关系")
        self.assertEqual(result["invalidated_assignments"][0]["status"], "cancelled")
        loads = {r["reviewer_id"]: r for r in self.store.reviewer_loads("chair")}
        self.assertEqual(loads["r3"]["active_load"], 1)
        self.assertEqual(loads["r3"]["remaining"], 1)
        # 释放后可以接受新邀请；被取消的邀请不能回应、不可恢复。
        self.store.assign("chair", paper3, "r3")
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_assignment("r3", a1, True)
        self.assertEqual(ctx.exception.code, "assignment_invalidated")
        # 另一篇论文的邀请不受影响。
        self.assertEqual(self.store.respond_assignment("r3", a2, True)["status"], "accepted")

    def test_late_conflict_invalidates_review_out_of_decision(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, 2, "存在严重缺陷，实验数据无法支撑结论。")
        self.store.submit_review("r2", a2, 4, "思路新颖，实验设计总体合理且可复现。")
        # r1 的完成意见因冲突失效：只剩一份有效完成意见，不能定论。
        result = self.store.add_conflict("chair", paper_id, "r1", "事后发现同机构利益冲突")
        self.assertEqual(result["invalidated_assignments"][0]["previous_status"], "completed")
        self.assertEqual(result["invalidated_assignments"][0]["status"], "invalid")
        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "accept")
        self.assertEqual(ctx.exception.code, "insufficient_reviews")
        # 失效终态不可恢复，原意见也不能再改。
        with self.assertRaises(BusinessError) as ctx:
            self.store.submit_review("r1", a1, 5, "冲突后试图改写原评审意见。")
        self.assertEqual(ctx.exception.code, "assignment_invalidated")
        # 原意见仍留在行里和审计历史中，但状态已是 invalid。
        overview = {p["paper_id"]: p for p in self.store.chair_overview("chair")}[paper_id]
        self.assertEqual(overview["valid_completed_reviews"], 1)
        r1_row = next(a for a in overview["assignments"] if a["reviewer_id"] == "r1")
        self.assertEqual(r1_row["status"], "invalid")
        self.assertEqual(r1_row["score"], 2)
        actions = [e["action"] for e in self.store.history("chair", paper_id)]
        self.assertIn("assignment.invalidate", actions)
        # 主席另找合适人选补位，凑齐两份有效完成意见后才能定论。
        a3 = self.store.assign("chair", paper_id, "r3")["id"]
        self.store.respond_assignment("r3", a3, True)
        self.store.submit_review("r3", a3, 4, "补位评审：整体质量良好，建议小修后录用。")
        decided = self.store.decide("chair", paper_id, "minor_revision", "依据两份有效评审。")
        self.assertEqual(decided["decision"], "minor_revision")

    def test_duplicate_conflict_returns_409_and_has_no_side_effects(self):
        paper_id = self._paper()
        self.store.add_conflict("chair", paper_id, "r1", "同一项目合作方")
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_conflict("chair", paper_id, "r1", "再次登记同一冲突")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "conflict_exists")
        history = self.store.history("chair", paper_id)
        self.assertEqual([e["action"] for e in history].count("conflict.add"), 1)


if __name__ == "__main__":
    unittest.main()
