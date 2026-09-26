import sqlite3
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

    def test_conflict_cancels_invitation_and_releases_load(self):
        papers = [self._paper() for _ in range(4)]
        a1 = self.store.assign("chair", papers[0], "r1")["id"]
        self.store.assign("chair", papers[1], "r1")
        self.store.assign("chair", papers[2], "r1")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", papers[3], "r1")
        self.assertEqual(ctx.exception.code, "reviewer_at_capacity")
        result = self.store.add_conflict("chair", papers[0], "r1", "与作者有项目合作")
        self.assertEqual(
            result["withdrawn_assignments"],
            [{"assignment_id": a1, "from_status": "invited", "to_status": "cancelled"}],
        )
        # 负载已释放，可邀请新论文；已取消的邀请不能回应，也不能重新邀请。
        self.store.assign("chair", papers[3], "r1")
        with self.assertRaises(BusinessError) as ctx:
            self.store.respond_assignment("r1", a1, True)
        self.assertEqual(ctx.exception.code, "assignment_invalid")
        with self.assertRaises(BusinessError) as ctx:
            self.store.assign("chair", papers[0], "r1")
        self.assertEqual(ctx.exception.code, "conflict_of_interest")
        loads = {row["reviewer_id"]: row for row in self.store.reviewer_loads("chair")}
        self.assertEqual(loads["r1"]["active_load"], 3)
        self.assertEqual(loads["r1"]["withdrawn"], 1)
        actions = [h["action"] for h in self.store.history("chair", papers[0])]
        self.assertIn("assignment.cancel", actions)

    def test_conflict_invalidates_completed_review_and_chair_backfills(self):
        paper_id = self._paper()
        a1 = self.store.assign("chair", paper_id, "r1")["id"]
        a2 = self.store.assign("chair", paper_id, "r2")["id"]
        self.store.respond_assignment("r1", a1, True)
        self.store.respond_assignment("r2", a2, True)
        self.store.submit_review("r1", a1, 5, "理论严谨，实验充分，可以直接接收。")
        result = self.store.add_conflict("chair", paper_id, "r1", "与作者同一实验室")
        self.assertEqual(result["withdrawn_assignments"][0]["to_status"], "invalid")
        overview = self.store.paper_assignments("chair", paper_id)
        self.assertEqual(overview["valid_completed"], 0)
        self.assertEqual(overview["conflicts"][0]["reviewer_id"], "r1")
        # 原意见留在历史，但不能进入决定；无效分配也不能再提交评审。
        with self.assertRaises(BusinessError) as ctx:
            self.store.decide("chair", paper_id, "accept")
        self.assertEqual(ctx.exception.code, "insufficient_reviews")
        with self.assertRaises(BusinessError) as ctx:
            self.store.submit_review("r1", a1, 1, "试图修改已失效的评审意见。")
        self.assertEqual(ctx.exception.code, "assignment_invalid")
        # 主席补位 r3，凑齐两份有效完成意见后定论。
        self.store.submit_review("r2", a2, 3, "实验充分，但部分结论需要进一步解释。")
        a3 = self.store.assign("chair", paper_id, "r3")["id"]
        self.store.respond_assignment("r3", a3, True)
        self.store.submit_review("r3", a3, 4, "补位评审：方法可靠，建议补充消融实验。")
        self.assertEqual(self.store.paper_assignments("chair", paper_id)["valid_completed"], 2)
        result = self.store.decide("chair", paper_id, "minor_revision", "补位后意见齐全。")
        self.assertEqual(result["decision"], "minor_revision")
        actions = [h["action"] for h in self.store.history("chair", paper_id)]
        self.assertIn("assignment.invalidate", actions)
        self.assertIn("conflict.add", actions)

    def test_duplicate_conflict_returns_409_and_chair_views_are_chair_only(self):
        paper_id = self._paper()
        self.store.add_conflict("chair", paper_id, "r1", "同一导师团队成员")
        with self.assertRaises(BusinessError) as ctx:
            self.store.add_conflict("chair", paper_id, "r1", "重复登记")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "conflict_exists")
        with self.assertRaises(BusinessError) as ctx:
            self.store.reviewer_loads("r1")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(BusinessError) as ctx:
            self.store.paper_assignments("r1", paper_id)
        self.assertEqual(ctx.exception.status, 403)

    def test_legacy_assignments_table_is_migrated(self):
        db = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, load_limit INTEGER NOT NULL DEFAULT 3);
            CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, author_id TEXT NOT NULL, title TEXT NOT NULL, abstract TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'submitted', created_at TEXT NOT NULL);
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER NOT NULL,
                reviewer_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'invited' CHECK (status IN ('invited','accepted','declined','completed')),
                score INTEGER, review_text TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE (paper_id, reviewer_id));
            INSERT INTO users VALUES ('r1','R1','reviewer',3),('chair','C','chair',0),('alice','A','author',0);
            INSERT INTO papers(id,author_id,title,abstract,created_at) VALUES (1,'alice','旧论文','旧论文摘要，长度足够通过校验。','2026-01-01T00:00:00+00:00');
            INSERT INTO assignments(id,paper_id,reviewer_id,status,created_at,updated_at) VALUES (1,1,'r1','accepted','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00');
            """
        )
        conn.close()
        store = ReviewStore(db)
        store.init_schema()  # 触发迁移，旧数据保留。
        store.seed()
        result = store.add_conflict("chair", 1, "r1", "迁移后登记冲突")
        self.assertEqual(
            result["withdrawn_assignments"],
            [{"assignment_id": 1, "from_status": "accepted", "to_status": "invalid"}],
        )


if __name__ == "__main__":
    unittest.main()
