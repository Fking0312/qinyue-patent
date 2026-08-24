# 琴岳知识产权 · 案件协同平台

专利代理所内部用的案件协同系统：客户 → 项目 → 案件 → 任务一条链。管理员派单、审核；撰写师办案、交稿；客户看自家案件和材料。

技术形态是 **Flask 单体 + SQLite**。管理端/员工端是同源页面内的 SPA 片段刷新（请求头 `X-Qy-Spa`），不是前后端分离。

---

## 接手前必看

1. **派单只给在职撰写师。** 流程人员、业务人员不进分配下拉，服务端也不得接受把案件指派给他们。空职能按撰写师。编制（正式/外包）和职能是两维，互不替代。规则写在 `.cursor/rules/staff-function-assignment.mdc`，改派单逻辑前先读。
2. **生产必须** `FLASK_ENV=production`，且 `SECRET_KEY` 至少 32 位随机串。漏设时 Gunicorn 等 WSGI 进程会拒绝启动，避免继续用开发密钥 `dev-secret-key`。
3. **不要在生产库跑** `scripts/seed_demo_data.py`。脚本会拒绝生产环境；**新建**演示账号口令是 `123456`，已存在账号不会被改密。
4. 协作时把 `migrations/` 一并提交。朋友克隆后先跑 `flask db upgrade`，不要只靠 SQLite 启动时的 `create_all`。
5. 本地比云服务器新。上线前看下面「云服务器尚未部署」；GitHub 有代码不等于已经部署到 `8.153.93.27`。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| Web | Flask 3、Jinja |
| 认证 | Flask-Login、Flask-WTF CSRF |
| 数据 | Flask-SQLAlchemy、Flask-Migrate / Alembic、默认 SQLite |
| 导出 | openpyxl |
| 测试 | pytest（**未写入** `requirements.txt`，需自行 `pip install pytest`） |

入口是 `app.py`：`app = create_app()`。没有单独的 `wsgi.py`，Gunicorn 用 `app:app`。

---

## 本地启动

```powershell
cd e:\test
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pytest
```

```powershell
$env:FLASK_APP = "app.py"
$env:FLASK_DEBUG = "1"
flask db upgrade
python app.py
```

打开 <http://127.0.0.1:5000>。未登录是落地页。

还没有账号时（仅开发）：

```powershell
$env:FLASK_DEBUG = "1"
flask init-demo-users
```

会创建 `admin` / `staff` / `client`，随机口令只打印一次；已存在用户不改密。需要固定口令时设 `QY_DEMO_FIXED_PASSWORD`。

更完整的演示数据（客户、项目、多状态案件、三个职能测试号）：

```powershell
python scripts/seed_demo_data.py
```

种子脚本里的职能测试账号（仅**新建**时密码 `123456`）：

| 用户名 | 职能 | 说明 |
| --- | --- | --- |
| `writer` | 撰写师 | 完整员工端：看板、案件、材料 |
| `process` | 流程人员 | 独立工作台，业务多为占位 |
| `business` | 业务人员 | 独立工作台（下单/收账占位） |
| `admin` | 管理员 | 管理端 |
| `staff` | 撰写师 | 演示员工 |

可用用户名或手机号登录（手机号仅员工、客户）。

补案件类型覆盖、月份统计、已完成案件：

```powershell
python scripts/seed_more_demo_cases.py
python scripts/seed_completed_demo_cases.py
```

清空本地测试业务数据（默认保留管理员）：

```powershell
python scripts/wipe_test_data.py --dry-run
python scripts/wipe_test_data.py --confirm DELETE-ALL-TEST-DATA
```

生产默认禁止清库；确需时还要 `QY_ALLOW_WIPE_TEST_DATA=1`。`--purge-test-admins` 会删用户名以下划线开头的测试管理员。

---

## 目录结构

```
app.py                      # python app.py / gunicorn app:app
config.py                   # 环境、密钥、Cookie、上传与登录锁定
app/
  __init__.py               # create_app：扩展、蓝图、CLI、SQLite 兜底
  extensions.py
  models.py                 # Customer / Project / Case / Task / User / 材料与审核留痕
  workflow.py               # 任务阶段、超期映射、员工可迁阶段
  spa_helpers.py            # 全页 / SPA 片段
  blueprints/               # admin / staff / client / auth
  templates/                # 全页 + snippets（SPA 内层）
  static/
scripts/                    # 演示数据与清库
tests/                      # 数据隔离在 instance/pytest-instance/
migrations/versions/        # Alembic，需提交进仓库
.cursor/rules/              # 派单规则，Cursor 会始终加载
```

案件看板、案件类型、材料上传、登录锁定、会话失效等按主题拆在 `app/` 根目录，不要把新逻辑继续堆进已经很长的 `blueprints/admin/routes.py`。

默认库：`instance/patent.db`。上传也在 instance 下。测试**不会**写这份库。

---

## 领域模型与角色

```
Customer 1──N Project 1──N Case 1──1 Task
User.role = admin | staff | client
```

- 案件序列号字段是 `application_no`：`YYMM` + 当月序号，全局唯一。
- 阶段定义在 `app/workflow.py` 的 `TaskPhase`。部分阶段超期会改写；**待审核不因超期改写**。员工不能把任务直接拨到「待递交」，须管理员审核通过。
- 库内时间多为 UTC，界面按北京时间显示。

**员工两维**

| 维 | 取值 |
| --- | --- |
| 编制 `staff_kind` | `formal` 正式 / `outsource` 外包 |
| 职能 `staff_function` | `writer` 撰写师 / `process` 流程 / `business` 业务 |

账号在「账号分发」创建、筛选；职能改派在「角色权限」`/admin/role-permissions`。离职/冻结是 `is_active=False`（软停用）。

登录后首页由 `User.home_endpoint` 决定。

---

## 功能进度

### 已能用

- 登录/登出；自助注册关闭（`/auth/register` 返回 404）
- 管理端：客户、立项、案件 CRUD、任务看板、派单/转派、审核、材料与下载留痕、在办/办结案件库、案件类型统计、账号分发、角色权限、批量删除说明
- 撰写师：工作台、任务看板、案件与材料、期限提醒、审核打回通知、个人月度统计
- 客户：工作台、自家案件、材料下载
- 员工离职：撰写中任务退回待分配；审核中/已完成保留承办留痕
- 安全：CSRF、密码散列、生产密钥校验、登录失败锁定、Cookie `HttpOnly` + `SameSite=Lax`（生产加 `Secure`）、离职/冻结后下一次请求清会话

### 占位（导航有入口，业务未接）

管理端：智能派单、官方期限、费用监控、官文分发、员工绩效、客户报表、利润核算、通知模板、历史归档。

员工端：进度更新、成果上传；流程「审核跟进」、业务「下单/收账」。

客户端：状态时间轴、在线留言。

改这些页时先确认是接真实数据还是继续占位，避免和现有审核流冲突。

---

## 云服务器尚未部署

对照线上 `qyapp@8.153.93.27`、`/opt/qy-patent`。**2026-08-20 11:57** 已确认当时那批已上线：重启不覆盖实际返稿时间、打开页面不偷偷改任务状态、办结库保存后保持滚动、自动补算不覆盖已有返稿时间、清库脚本生产保护。

下面是那之后本地已完成、**还没同步到云服务器**的更新。下次上线后把对应条目划掉或删掉，避免重复部署。

### 要跑数据库迁移

上线后在服务器执行 `flask db upgrade`（先备份 `instance/patent.db`）。本地 head 比线上多两级：

| 迁移 | 内容 |
| --- | --- |
| `p4e5f6a7b8c9` | 把现有空职能员工回填为撰写师 |
| `q5f6a7b8c9d0` | 新建 `login_throttles` 表（登录失败锁定） |

线上当时 `flask db current` 是 `o3d4e5f6a7b8`。若中间又升过级，以服务器上的 `flask db current` 为准。

### 业务与界面

| 更新 | 说明 |
| --- | --- |
| 员工职能分流 | 撰写师 / 流程人员 / 业务人员；登录进不同工作台；流程/业务为占位页 |
| 派单只给撰写师 | 分配池不含流程、业务人员；空职能按撰写师；服务端拦截 |
| 账号分发 | 职能标签、筛选；筛选栏对齐 |
| 角色权限 | 可改员工职能 |
| 公司首页 | 蓝色渐变落地页（不再用宣传大图） |

### 安全（上线时注意密钥）
| 更新 | 说明 |
| --- | --- |
| 生产密钥校验 | Gunicorn 等 WSGI 必须 `FLASK_ENV=production` 且 `SECRET_KEY` ≥32 位随机串，否则**拒绝启动** |
| 演示种子保护 | `scripts/seed_demo_data.py` 在生产直接退出；已有账号不改密 |
| 登录失败锁定 | 账号 15 分钟内 5 次失败锁 15 分钟；同一 IP 20 次；锁定返回 429 |
| 会话 Cookie | `HttpOnly` + `SameSite=Lax`；生产加 `Secure` |
| 离职/冻结踢会话 | 停用后下一次请求退出登录 |

生产 `.env` 里若还是弱密钥或未设 `SECRET_KEY`，先改密钥再重启，否则服务起不来。若已用 HTTPS 反代，Cookie `Secure` 才生效；纯 HTTP 访问时不要误开 `QY_SESSION_COOKIE_SECURE=1`。

占位页（智能派单、费用/官文、客户留言等）是产品未做完，不是「写好了没上线」，不要和上表混在一起。

---

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`tests/conftest.py` 会关闭 CSRF，并把实例目录指到 `instance/pytest-instance/`，避免污染 `instance/patent.db`。

---

## 环境变量

| 变量 | 作用 |
| --- | --- |
| `FLASK_ENV` | `development` / `production` |
| `SECRET_KEY` | 会话与 CSRF；生产 ≥32 位随机，例如 `openssl rand -hex 32` |
| `FLASK_DEBUG` | `1` 时启用 SQLite 列兜底 ALTER |
| `QY_DATABASE_URI` | 数据库 URI，默认本地 SQLite（`config.py` 读取此名） |
| `QY_INSTANCE_PATH` | Flask instance 目录（测试会覆盖） |
| `WTF_CSRF_ENABLED` | 默认开；测试关 |
| `QY_CASE_MATERIAL_MAX_MB` | 单材料大小，默认 500 |
| `MAX_CONTENT_LENGTH_MB` | 整次 POST 上限 |
| `QY_LOGIN_MAX_FAILURES` | 同一账号失败次数上限，默认 5 |
| `QY_LOGIN_FAILURE_WINDOW_MINUTES` | 失败计数窗口，默认 15 |
| `QY_LOGIN_LOCKOUT_MINUTES` | 锁定时长，默认 15 |
| `QY_LOGIN_IP_MAX_FAILURES` | 同一 IP 失败次数上限，默认 20 |
| `QY_SESSION_COOKIE_SECURE` | `1` 时开发环境 Cookie 也加 Secure |
| `QY_ALLOW_INIT_DEMO_USERS` | 非 debug 时允许 `flask init-demo-users` |
| `QY_DEMO_FIXED_PASSWORD` | 新建演示账号的固定口令 |
| `QY_ALLOW_WIPE_TEST_DATA` | 生产清库覆盖（仍要 `--confirm`） |
| `QY_ALLOW_STAFF_CLIENT_PASSWORD_RESET` | 生产允许把员工/客户密码批量打成 `123456` |

其它 CLI：

```powershell
flask refresh-task-overdue
flask backfill-actual-return-at --apply
flask backfill-project-codes
flask dedupe-download-logs
flask reset-staff-client-passwords
```

`reset-staff-client-passwords` 生产默认禁止。

生产示例：

```powershell
$env:FLASK_ENV = "production"
$env:SECRET_KEY = "<至少32位随机串>"
gunicorn app:app
```

对外走 HTTPS。生产 Cookie 带 `Secure`。

---

## 常见坑

- 改表：开发可以靠 `create_all` + SQLite ALTER；**生产只认** `flask db upgrade`。
- `app/blueprints/admin/routes.py` 很长，按文件头分区找客户/项目/案件/审核。
- 改页面要同时动全页模板和 `snippets/` 内层；前端靠 `data-spa-endpoint`。
- 静态资源用部署版本号破缓存。
- 不要给种子脚本或演示 CLI 加「生产强制覆盖」开关。
- `tests/conftest.py` 里若写了 `QY_DATABASE_URI`，必须和 `config.py` 读取的 `QY_DATABASE_URI` 一致，否则测试仍可能打到默认库。

---

## 建议的后续方向

1. 把流程/业务工作台从占位接到真实审核与商务数据，并继续守住「只派撰写师」。
2. 补管理端费用、官文、绩效，或先从导航拿掉未交付入口。
3. 把 pytest 写入依赖文件（或单独的 `requirements-dev.txt`）。
4. 生产若改 PostgreSQL：只改 `QY_DATABASE_URI` 并跑迁移，不要依赖 SQLite 兜底 ALTER。
