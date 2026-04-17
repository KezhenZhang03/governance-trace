# Governance Trace MVP (Durham AI Education System Upgrade)

## 模块说明
这是一个基于 **Python 标准库 `http.server` + `sqlite3`** 的 Governance Trace MVP，负责治理追踪而不是知识正文存储。

新增能力：**AI proposal screening**
- proposal 可自动触发 AI 学术评审
- AI 输出结构化评审（分项评分、优缺点、建议、verdict）
- 后端根据规则映射到 `approved/frontier/rejected`
- 自动生成专业 `decision_reason` 并持久化到 `ai_reviews`

## 架构选择
- Backend: Python `http.server`
- Storage: SQLite (`sqlite3`)
- AI service layer: `app/ai_review.py`（只在后端调用，不暴露 key）
- Demo UI: `static/index.html`

## 安全与环境变量
> **不要把 API key 写进代码/前端/README 示例值。**

必读变量：
- `OPENAI_API_KEY`（真实调用时必需）
- `OPENAI_REVIEW_MODEL`（可选，默认 `gpt-4.1-mini`）
- `AI_REVIEW_ENABLED`（可选，默认 `true`）
- `AI_REVIEW_MOCK`（可选，`1` 时走离线 mock，不访问外网）

### 本地运行（真实 AI）
```bash
export OPENAI_API_KEY="<your_key>"
export OPENAI_REVIEW_MODEL="gpt-4.1-mini"
export AI_REVIEW_ENABLED=1
export AI_REVIEW_MOCK=0
python app/main.py
```

### 本地运行（离线 mock，推荐开发/测试）
```bash
export AI_REVIEW_ENABLED=1
export AI_REVIEW_MOCK=1
python app/main.py
```

## 快捷命令
```bash
make run   # 启动服务
make test  # 跑测试
make demo  # 自动化演示并输出 JSON
```

## 主要 API
- `GET /governance/proposals`
- `GET /governance/proposals/{id}`（新增 `proposal_text` + `latest_ai_review`）
- `POST /governance/proposals`（新增 `proposal_text`, `auto_screen`）
- `POST /governance/decisions/{proposal_id}`（保留人工决策）
- `POST /governance/ai-screen/{proposal_id}`（新增：手动触发 AI 审查）
- `GET /governance/timeline/{proposal_id}`
- `GET /governance/impact/{proposal_id}`
- `GET /governance/summary`

## curl 示例
### 1) 创建 proposal 并自动 AI 审查
```bash
curl -X POST http://127.0.0.1:8000/governance/proposals \
  -H "Content-Type: application/json" \
  -d '{
    "summary": "AI screening live proposal",
    "source_of_proposal": "manual",
    "target_knowledge_ids": ["kb_eval_rubric_01"],
    "evidence_refs": ["manual://live-demo"],
    "rationale": "Demonstrate AI governance screening",
    "proposal_text": "This proposal introduces a clear method and evaluation plan",
    "auto_screen": true
  }'
```

### 2) 对已有 proposal 手动触发 AI review
```bash
curl -X POST http://127.0.0.1:8000/governance/ai-screen/<proposal_id>
```

## 页面演示步骤
1. 打开 `http://127.0.0.1:8000/`
2. 在 Proposal List 中选择 `proposed` 项目并点击 **Run AI Review**
3. 观察 Decision Detail：Reviewer / Final Verdict / Decision Reason / Average Score / Review Model
4. 查看 AI Review Details：Summary、Score Table、Strengths、Weaknesses、Suggestions
5. 查看 Timeline 与 Impact，确认状态与下游落点变化

## 测试
```bash
pytest -q
```
测试覆盖：
- 人工 decision 相关回归
- 创建 proposal 时 `auto_screen=true` 的 reject/frontier/approve 路径
- `/governance/ai-screen/{id}` 对已有 proposal 的触发
- 缺少 `OPENAI_API_KEY` 时返回可读错误信息
- 离线无外网可跑（mock 路径）

## 目录结构
- `app/main.py`：路由与 orchestration、DB 迁移、AI screening 接入
- `app/ai_review.py`：prompt 构建、OpenAI 调用、解析、分类、reason 生成
- `static/index.html`：demo UI（包含 Run AI Review 按钮和 AI 评审详情展示）
- `tests/test_governance.py`：smoke + AI screening tests
- `scripts/auto_demo_flow.py`：自动演示脚本

## Assumptions / Mock
- 仅覆盖一个模块：`durham-ai-module`
- KB 仅 mock 元信息（version/canonical），不存正文
- `AI_REVIEW_MOCK=1` 时返回 deterministic mock review
