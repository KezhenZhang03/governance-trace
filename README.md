# Governance Trace MVP (Durham AI Education System Upgrade)

## 模块说明
本仓库实现了一个 **Governance Trace MVP**，定位为“知识治理追踪层”（不是知识正文存储系统）。MVP 聚焦：
- Proposal 生命周期与状态机（proposed / approved / frontier / rejected，预留 under_review / superseded）。
- Decision / Trace 记录（reviewer、decision_reason、decided_at 必留痕）。
- Timeline（proposal 创建 + 后续治理事件有序展示）。
- Impact（下游资产、受影响知识单元、版本落点、canonical/frontier 边界）。

## 架构选择
> 说明：原计划使用 FastAPI + SQLAlchemy。由于当前执行环境无法联网安装依赖，改用 **Python 标准库 HTTP Server + sqlite3** 提供同等 API 与演示能力，确保 hackathon 可直接跑通。

- Backend: Python `http.server`
- Storage: SQLite (`sqlite3`)
- Validation: 轻量手写校验
- Demo UI: 后端直接服务静态 `HTML/CSS/JS`

## 目录结构
- `app/main.py`: API 路由、状态流转、seed、KB 联动规则
- `static/index.html`: Demo 页面（list + decision card + timeline + impact + summary）
- `tests/test_governance.py`: 核心 smoke tests（真实 HTTP 调用）
- `requirements.txt`: 仅测试依赖

## 运行方式
```bash
python app/main.py
```
启动后访问：
- Demo 页: `http://127.0.0.1:8000/`

## API 列表
### 1) GET `/governance/proposals`
- 可选筛选：`module`、`status`
- 返回 proposal summaries

### 2) GET `/governance/proposals/{id}`
- 返回完整 proposal 详情 + latest decision 摘要

### 3) POST `/governance/proposals`
- 创建 proposal
- 最小输入：`summary`, `source_of_proposal`, `target_knowledge_ids`, `evidence_refs`

### 4) POST `/governance/decisions/{proposal_id}`
- 创建治理决策
- 必填：`decision_status`, `reviewer`, `decision_reason`
- 决策状态支持：`approved | frontier | rejected`

### 5) GET `/governance/timeline/{proposal_id}`
- 返回按时间排序的治理事件

### 6) GET `/governance/impact/{proposal_id}`
- 返回下游影响与治理落点

### 7) GET `/governance/summary`
- 返回状态汇总与最近一条决策

## 示例请求
### 创建 Proposal
```bash
curl -X POST http://127.0.0.1:8000/governance/proposals \
  -H "Content-Type: application/json" \
  -d '{
    "summary": "Revise rubric language",
    "source_of_proposal": "manual",
    "target_knowledge_ids": ["kb_eval_rubric_01"],
    "evidence_refs": ["manual://teacher-feedback"]
  }'
```

### 提交 Decision
```bash
curl -X POST http://127.0.0.1:8000/governance/decisions/<proposal_id> \
  -H "Content-Type: application/json" \
  -d '{
    "decision_status": "frontier",
    "reviewer": "Prof. L. Singh",
    "decision_reason": "Need one more local validation cycle",
    "affected_assets": ["view:frontier-dashboard"]
  }'
```

## Seed 数据与演示场景
系统启动自动写入 4 条示例 Proposal：
1. **approved case**：有 evidence、review reason、KB 版本更新、impact 可见。
2. **frontier case**：有 evidence，被标记 frontier，未进入 canonical。
3. **rejected case**：有 reviewer/reason，被拒绝，无正式下游更新。
4. **live flow case**：仅 proposal（待决策），用于现场演示 signal -> decision 主链路。

## Demo Walkthrough（简短）
1. 打开 `/` 查看 Proposal list（状态颜色一致）。
2. 点击 approved 案例，展示 decision detail + timeline + impact（可见 resulting versions）。
3. 点击 frontier 案例，确认 impact 可见但 canonical outcome 为 frontier 保留。
4. 点击 rejected 案例，确认无 resulting knowledge versions。
5. 现场新建一条 proposal，再提交 decision，刷新列表展示闭环。

## 测试与验证
```bash
pytest -q
```
覆盖：
- proposal 创建
- proposal -> approved/frontier/rejected
- decision 必填字段约束
- timeline 完整链路
- approved 更新 KB 版本
- frontier/rejected 不更新 canonical 落点
- API 返回结构字段稳定

## Assumptions / Mock 说明
- 仅覆盖一个模块标签：`durham-ai-module`。
- 仅用 2–3 个知识单元做 KB mock，不保存正文。
- `resulting_knowledge_versions` 在 approved 时可自动生成；其他状态保持空。
- under_review/superseded 仅在状态枚举层预留。
- 时间统一使用 ISO 8601 字符串。
