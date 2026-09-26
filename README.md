# 学术会议同行评审系统

一个仅使用 Python 3.11+ 标准库的独立示例项目。SQLite 保存数据，`http.server` 提供 JSON API 和演示页面。

## 运行

```bash
python app.py --init --seed
python app.py
```

访问 <http://127.0.0.1:8101>。主席工作台在 <http://127.0.0.1:8101/chair>（冲突处置、负载统计、补位与决定）。默认数据库为 `review.db`，端口为 `8101`。测试：

```bash
python -m unittest -v
```

## 角色和主要接口

演示用户：`alice`、`bob`（作者），`r1`、`r2`、`r3`（评审人），`chair`（主席）。所有 API 请求应带 `X-User-Id` 请求头。

- `POST /api/papers`：提交论文。
- `GET /api/papers` / `GET /api/papers/{id}`：按角色隔离查看；评审人看到双盲视图。
- `POST /api/papers/{id}/bids`：评审意向。
- `POST /api/papers/{id}/conflicts`：主席登记利益冲突；登记后该评审人在本论文的待回应邀请自动取消、已接受或完成的分配转为无效（撤评），负载随之释放，重复登记返回 409。
- `POST /api/papers/{id}/assignments`：主席邀请评审人，执行负载上限与冲突检查。
- `GET /api/papers/{id}/assignments`：主席查看分配清单、有效完成数与已登记冲突。
- `GET /api/reviewers/load`：主席查看评审人负载统计。
- `POST /api/assignments/{id}/respond`：接受或拒绝邀请。
- `POST /api/assignments/{id}/review`：提交 1-5 分评审。
- `POST /api/papers/{id}/rebuttal`：作者提交一次 Rebuttal。
- `POST /api/papers/{id}/decision`：凑齐至少两份有效完成评审后作决定。
- `GET /api/papers/{id}/history`：审计历史。

## 业务不变量

评审人不能查看未分配论文的作者身份；利益冲突禁止投标和分配；登记冲突即撤评——该评审人的待回应邀请自动取消、已接受或完成的分配转为无效且不可恢复，评分留在审计历史但不计入决定；负载只统计待回应与已接受的分配，撤评后自动释放；邀请和完成状态不能跳步；每位评审人的进行中分配受 `load_limit` 限制；每篇论文只能提交一次 Rebuttal；决定必须基于至少两份有效完成评审。
