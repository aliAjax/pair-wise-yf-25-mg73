# 学术会议同行评审系统

一个仅使用 Python 3.11+ 标准库的独立示例项目。SQLite 保存数据，`http.server` 提供 JSON API 和演示页面。

## 运行

```bash
python app.py --init --seed
python app.py
```

访问 <http://127.0.0.1:8101>。默认数据库为 `review.db`，端口为 `8101`。测试：

```bash
python -m unittest -v
```

## 角色和主要接口

演示用户：`alice`、`bob`（作者），`r1`、`r2`、`r3`（评审人），`chair`（主席）。所有 API 请求应带 `X-User-Id` 请求头。

- `POST /api/papers`：提交论文。
- `GET /api/papers` / `GET /api/papers/{id}`：按角色隔离查看；评审人看到双盲视图。
- `POST /api/papers/{id}/bids`：评审意向。
- `POST /api/papers/{id}/conflicts`：主席登记利益冲突，触发冲突撤评。
- `POST /api/papers/{id}/assignments`：主席邀请评审人，执行负载上限与冲突检查。
- `POST /api/assignments/{id}/respond`：接受或拒绝邀请。
- `POST /api/assignments/{id}/review`：提交 1-5 分评审。
- `POST /api/papers/{id}/rebuttal`：作者提交一次 Rebuttal。
- `POST /api/papers/{id}/decision`：收到至少两份**有效**评审后作决定。
- `GET /api/papers/{id}/history`：审计历史。
- `GET /api/chair/overview`：主席工作台汇总（论文、分配状态、冲突、有效完成意见数）。
- `GET /api/chair/reviewer-loads`：评审人负载统计（在途分配数/上限/剩余）。
- 主席页面：<http://127.0.0.1:8101/chair>（独立于作者/评审人页面维护）。

## 业务不变量

评审人不能查看未分配论文的作者身份；利益冲突禁止投标和分配；邀请和完成状态不能跳步；每位评审人的未完成分配（`invited`、`accepted`）受 `load_limit` 限制；每篇论文只能提交一次 Rebuttal；决定必须至少基于两份**有效**已完成评审。

### 冲突撤评（事后发现利益冲突）

邀请发出后才发现冲突时，主席仍可登记冲突，系统在同一事务内完成撤评：

- 待回应邀请（`invited`）自动转为 `cancelled`；已接受（`accepted`）或已完成（`completed`）的分配转为 `invalid`。
- 两类终态均**不可恢复**：不能再回应邀请、提交或修改评审；同一评审人也不能被重新分配给该论文。
- 撤评立即释放负载（终态不再计入 `load_limit`）。
- 原评分与意见保留在分配记录和审计历史中，但因状态不再是 `completed`，不能进入决定；有效完成意见不足两份时主席须另找合适人选补位。
- 重复登记同一（评审人, 论文）冲突返回 `409 conflict_exists`，且不产生任何副作用。
- 冲突处置（`add_conflict`/撤评）、负载统计（`reviewer_loads`）、主席页面（`/chair` 及其汇总接口）三部分分开维护。
