# 琴岳知识产权 · 案件协同平台

专利代理所内部用的案件协同系统：客户 → 项目 → 案件 → 任务一条链。管理员派单、审核；撰写师办案、交稿；客户看自家案件和材料。

技术形态是 **Flask 单体 + SQLite**。管理端/员工端是同源页面内的 SPA 片段刷新（请求头 `X-Qy-Spa`），不是前后端分离。

---

## 接手前必看

1. **派单只给在职撰写师。** 流程人员、业务人员不进分配下拉，服务端也不得接受把案件指派给他们。空职能按撰写师。编制（正式/外包）和职能是两维，互不替代。规则写在 `.cursor/rules/staff-function-assignment.mdc`，改派单逻辑前先读。
2. **生产必须** `FLASK_ENV=production`，且 `SECRET_KEY` 至少 32 位随机串。漏设时 Gunicorn 等 WSGI 进程会拒绝启动，避免继续用开发密钥 `dev-secret-key`。
3. **不要在生产库跑** `scripts/seed_demo_data.py`。脚本会拒绝生产环境；**新建**演示账号口令是 `123456`，已存在账号不会被改密。
4. 协作时把 `migrations/` 一并提交。朋友克隆后先跑 `flask db upgrade`，不要只靠 SQLite 启动时的 `create_all`。
5. 本地 `main` 比云服务器新。线上代码看 `production` 分支；上线前看下面「云服务器尚未部署」。GitHub 有代码不等于已经部署。

---

## 仓库分支

| 分支 | 含义 |
| --- | --- |
| `main` | 正在开发：职能分流、staff 蓝图拆分、登录锁定等，**尚未全部上线** |
| `production` | **当前线上代码**。2026-09-08 已含「专利局退稿 → 内部案件」 |
| `feature/rejected-internal-cases` | 退稿功能的上线分支，已并入 `production` |

改线上先在 `production`（或从它拉出的功能分支）上改，再部署。不要把 `main` 整包拷到服务器。`git diff production main` 能看出两版差什么。

---

## 技术栈

| 层 | 选型 |
| --- | --- |
| Web | Flask 3、Jinja |
| 认证 | Flask-Login、Flask-WTF CSRF |
| 数据 | Flask-SQLAlchemy、Flask-Migrate / Alembic、默认 SQLite |
| 导出 | openpyxl |
| 测试 | pytest（**未写入** `requirements.txt`，需自行 `pip install pytest`） |

入口两个都在：本地开发用 `app.py`（`python app.py`），线上 systemd 跑 `wsgi:app`。**不要删 `wsgi.py`**，删了服务起不来。

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
    staff/                  # 单一 staff_bp，内部按职能分子模块
      guards.py             # ensure_staff / ensure_staff_function / ensure_writer
      utils.py              # 北京时区与时间文本
      common/               # 通知口径、侧栏未读数、职能占位工作台渲染
      writer/               # 撰写师：看板、案件、材料、期限、月度统计
      process/              # 流程人员：审核案件跟进（占位）
      business/             # 业务人员：下单与收账（占位）
  templates/                # 全页 + snippets（SPA 内层）
  static/
scripts/                    # 演示数据与清库
tests/                      # 数据隔离在 instance/pytest-instance/
migrations/versions/        # Alembic，需提交进仓库
.cursor/rules/              # 派单规则，Cursor 会始终加载
```

案件看板、案件类型、材料上传、登录锁定、会话失效等按主题拆在 `app/` 根目录，不要把新逻辑继续堆进已经很长的 `blueprints/admin/routes.py`。

`staff` 四个子模块共用同一个 `staff_bp`，endpoint 仍是 `staff.xxx`，模板和 `data-spa-endpoint` 不受影响。加员工页面时放进对应职能的 `routes.py`；三职能都要用的东西才进 `common/`。等某个职能真有业务逻辑了再给它加 `services.py`，别先建空文件。

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
- 管理端：客户、立项、案件 CRUD、任务看板、派单/转派、智能派单（撰写师忙闲排序 + 手动派单）、审核、材料与下载留痕、在办/办结案件库、案件类型统计、账号分发、角色权限、批量删除说明
- 撰写师：工作台、任务看板、案件与材料、期限提醒、审核打回通知、个人月度统计
- 客户：工作台、自家案件、材料下载
- 员工离职：撰写中任务退回待分配；审核中/已完成保留承办留痕
- 安全：CSRF、密码散列、生产密钥校验、登录失败锁定、Cookie `HttpOnly` + `SameSite=Lax`（生产加 `Secure`）、离职/冻结后下一次请求清会话

### 占位（导航有入口，业务未接）

管理端：官方期限、费用监控、官文分发、员工绩效、客户报表、利润核算、通知模板、历史归档。

员工端：进度更新、成果上传；流程「审核跟进」、业务「下单/收账」。

客户端：状态时间轴、在线留言。

改这些页时先确认是接真实数据还是继续占位，避免和现有审核流冲突。

---

## 云服务器尚未部署

### 线上现状（2026-09-08 实测）

| 项 | 值 |
| --- | --- |
| 主机 / 目录 | `qyapp@8.153.93.27`、`/opt/qy-patent` |
| 服务单元 | `/etc/systemd/system/qy-patent.service`，已 enabled；重启 `sudo systemctl restart qy-patent` |
| 启动命令 | `gunicorn -w 4 -b 127.0.0.1:8000 --timeout 300 wsgi:app`（前面是 Nginx 反代，对外 **http://8.153.93.27** ，不要再用 `:8000`） |
| 访问日志 | `/var/log/qy-patent/access.log` |
| 迁移版本 | `r6a7b8c9d0e1`（退稿/归属三列；此前是 `o3d4e5f6a7b8`） |
| 代码对应分支 | `production` |

`production` 先入库了 2026-08-20 那版服务器代码，2026-09-08 晚又并入退稿转内部案件。**改完线上先合进 `production` 再部署**，别再出现"服务器上的东西找不到对应提交"。`git diff production main` 随时能看出两版差什么。

服务器 `/opt/qy-patent` 下还堆着 `app-before-update/`（165 MB，含自带 venv）、`backups/`（80 MB，13 份库备份加 4 个上传件包）和 5 个 `app.bak.*` 目录。都是历史手工备份，不是运行代码，**没有入库**；要清理先确认磁盘占用再动手。

对照线上 `qyapp@8.153.93.27`、`/opt/qy-patent`。**2026-08-20 11:57** 已确认当时那批已上线：重启不覆盖实际返稿时间、打开页面不偷偷改任务状态、办结库保存后保持滚动、自动补算不覆盖已有返稿时间、清库脚本生产保护。

下面是那之后本地已完成、**还没同步到云服务器**的更新。下次上线后把对应条目划掉或删掉，避免重复部署。

### 要跑数据库迁移

上线后在服务器执行 `flask db upgrade`（先备份 `instance/patent.db`）。线上现已升到 `r6a7b8c9d0e1`。`main` 在此之上还有：

| 迁移 | 内容 |
| --- | --- |
| `p4e5f6a7b8c9` | 把现有空职能员工回填为撰写师 |
| `q5f6a7b8c9d0` | 新建 `login_throttles` 表（登录失败锁定；表已存在则跳过） |
| `s7b8c9d0e1f2` | 空合并：接住退稿线 `r6a7b8c9d0e1` 与职能线 `q5f6a7b8c9d0` 两个 head |
| `t8c9d0e1f2a3` | 案件留痕姓名快照：材料上传人、流转操作人、承办人；并回填存量 |

不要把 `r6a7b8c9d0e1` 改成接在 `q5f6a7b8c9d0` 后面：线上已经记录了 `r`，那样改会让 Alembic 以为 `p`、`q` 跑过了而静默跳过。

若中间又升过级，以服务器上的 `flask db current` 为准。

### 业务与界面

| 更新 | 说明 |
| --- | --- |
| 员工职能分流 | 撰写师 / 流程人员 / 业务人员；登录进不同工作台；流程/业务为占位页 |
| 派单只给撰写师 | 分配池不含流程、业务人员；空职能按撰写师；服务端拦截 |
| 账号分发 | 职能标签、筛选；筛选栏对齐 |
| 角色权限 | 可改员工职能 |
| 公司首页 | 蓝色渐变落地页（不再用宣传大图） |
| 智能派单落地 | 原占位页改为可用：左侧待分配案件、右侧撰写师建议顺序，仍是手动点「派给他」 |
| 退稿案件派回原撰写师 | 退稿时记下当时的承办人；派单页显示原撰写师并置顶，离职则提示重新分配 |
| 承办人已离职清单 | 捞出挂在离职者名下、离职退单漏掉的案件，可一键转入待分配 |
| 案件留痕姓名快照 | 上传、指派、提交审核、办结都把当时的人名钉在记录上；离职后材料与办结库仍显示名字 |

**智能派单的排序口径**（`app/assignment_advisor.py`）：

以选中案件的应返稿时间为窗口末端，只统计撰写师在办案件中**截止时间落在窗口内**的折算工作量，越少排越前。只数在办件数会失真——手上 5 件但都在三个月后交的人，比手上 2 件都在本周交的人更闲。

| 口径 | 取值 |
| --- | --- |
| 折算工作量 | 发明 3 / 实用新型 1 / 外观与商标 0.5 / 未知类型 1，改 `PRIMARY_WORKLOAD_WEIGHTS` 与 `LEAF_WORKLOAD_WEIGHTS` 即可调 |
| 有效截止时间 | 复用 `effective_task_due_at()`：任务 `due_at` → 案件应返稿时间 → 项目 `due_at` |
| 计入负载 | 已指派且未完成的任务；待分配与已完成都不算任何人的负载 |
| 窗口末端 | 至少取到「现在」。待派案件本身已超期时窗口会缩成过去的时间点，不兜住的话所有人都算成零负载，积压的超期案件反而不计入 |
| 平手顺序 | 已超期件数少 → 在办总工作量少 → 账号名 |
| 不设产能上限 | 只比相对忙闲，所以给的是建议顺序，不自动派单、不锁定 |

未填应返稿时间的案件不给推荐顺序，只提示去补时间（缺了窗口末端算不出忙闲）。分配池口径不变，仍只含在职撰写师。

**退稿案件的重新分配。** 退稿案件一般派回原来写的那个人，但那个人可能已经离职。原先 `mark_rejected` 写的 `office_reject` 日志 `recipient_id` 是空的，没记下当时是谁在写；全库 `assigned` 日志又极少，等于原撰写师查不到。现在：

| 环节 | 做法 |
| --- | --- |
| 记录 | 标记退稿时把当时的 `task.assignee_id` 写进 `office_reject` 日志的 `recipient_id`，这是最权威的一条 |
| 历史回落 | `office_reject` 日志 → 最后一条 `assigned` 日志 → `case.business_owner_id` → 撰写稿上传人 → 都没有就显示「查不到原撰写师」，不瞎猜 |
| 在职 | 置顶并标「原撰写师 · 建议派回」，按钮改为「派回原撰写师」；忙闲数字照常显示，负载达到最忙者八成时额外提示「他当前负载偏高」 |
| 离职/转职能 | 不置顶，提示「原撰写师已不在分配池，需要重新分配」，按忙闲排序推荐 |
| 「最闲」徽标 | 始终按负载判定，不跟着置顶跑——被顶到第一位的原撰写师不该冒充最闲 |

**离职退单漏掉退稿案件。** `_unassign_staff_workload` 只把 `in_progress` 的任务退回待分配，而退稿发生在提交之后（案件通常停在 `completed`），所以原撰写师离职时这类案件继续挂在他名下，既不在派单池里也没人跟，等于隐形。

这里**故意不自动退回**：退稿了不一定要自己重做，全自动退回会把一堆不打算重做的旧退稿案件塞进派单池。改为让它可见——派单页底部「承办人已离职」列出承办人已停用或已转非撰写职能、且案件未完成或已退稿的案件，逐件点「转入待分配」才进池子。已完成且未退稿的案件属于正常历史留痕，不列入。

**案件留痕不随人走。** 账号停用不会删 User 行，但材料列表原先靠 `uploaded_by.role` 实时 JOIN，账号对不上撰写材料就会从列表里消失；审核历史又只展示通过/打回，提交审核写了日志却看不见。现在：

| 环节 | 做法 |
| --- | --- |
| 姓名快照 | 材料 `uploaded_by_label`/`uploaded_by_role`，流转日志 `operator_label`/`recipient_label`，任务 `assignee_label`，案件 `business_owner_label`。页面优先显示还在的账号（含已离职徽标），账号行不在了就用快照 |
| 提交审核 | 撰写中 → 待审核时必须已有撰写文件；日志动作 `submit_for_review`，备注冻结当时的文件名。案件详情「案件留痕」能筛指派/提交/通过/打回 |
| 办结案件 | 离职退单不解除已完成案件的承办人；办结库用快照保底，硬删账号后名字仍在 |
| 材料不摘 | 分组不再要求上传人账号还在；员工上传的文件离职后仍在撰写材料列表 |

上线时：智能派单本身不需要迁移；**案件留痕需要 `flask db upgrade` 跑到 `t8c9d0e1f2a3`**。新增 `app/assignment_advisor.py`、`app/case_trace.py`、对应模板，`style.css` 末尾加了两条样式，**必须重启进程**刷新静态资源版本号。

### 重构（不改行为）

**staff 蓝图按职能分模块。** 原来 920 行的 `app/blueprints/staff/routes.py` 已删除，拆成：

| 新文件 | 内容 |
| --- | --- |
| `staff/__init__.py` | 创建 `staff_bp`，末尾导入四个子模块 |
| `staff/guards.py` | `ensure_staff` / `ensure_staff_function` / `ensure_writer` |
| `staff/utils.py` | 北京时区 `CN_TZ`、`beijing_datetime_text` |
| `staff/common/routes.py` | 通知查询口径、侧栏未读数注入、职能占位工作台渲染 |
| `staff/writer/routes.py` | 撰写师：看板、案件列表/详情、材料上传下载、期限提醒、月度统计、消息通知 |
| `staff/process/routes.py` | 流程人员：流程工作台、审核案件跟进（占位） |
| `staff/business/routes.py` | 业务人员：业务工作台、下单、收账（占位） |

四个子模块共用同一个 `staff_bp`，所以 **20 个 endpoint 和 URL 全部保持原样**（`staff.dashboard`、`staff.case_detail_by_id` 等），模板、`url_for`、`data-spa-endpoint`、`models.py` 的 `home_endpoint` 都没动。跨模块共享的三个权限闸去掉了下划线前缀（`_ensure_writer` → `ensure_writer`），仅撰写师内部使用的辅助函数仍留在 `writer/routes.py`。

上线时：**不需要跑迁移，不需要改模板或 Nginx**，`git pull` + 重启服务即可。若用复制而非 `git pull` 部署，确认服务器上的旧 `app/blueprints/staff/routes.py` 一并删掉；即使残留也不会被导入，但留着会误导人。重启后建议点一遍员工工作台、案件详情、消息通知，以及流程/业务账号的占位工作台。

### 缺陷修复

| 修复 | 说明 |
| --- | --- |
| 标签栏「首页」按职能取 | 原来写死 `/staff/dashboard`，流程/业务人员点固定的首页标签必然 403。改为读服务端注入的 `data-spa-home`（`url_for(current_user.home_endpoint)`） |
| 标签缓存按账号隔离 | `sessionStorage` 的标签 key 原来只按区域（`qySpaTabs:v1:/staff`），同一浏览器换账号登录会继承上一个人的标签，点开全是无权页面。key 现掺入账号与首页路径，改职能后旧标签也自动失效 |
| 员工端/客户端 Toast 失效 | `#qyToastContainer` 只写在 `admin/layout.html`，导致员工端与客户端所有 `redirect_with_qy_toast` 提示（上传成功、提交审核等）被静默丢弃。容器已移到 `base.html` 三端共用 |
| 无权访问不再整页跳错误页 | SPA 请求收到 403 原来 `location.assign` 跳到 403 页，标签栏一起丢失。改为留在当前页、弹权限提示并摘掉该标签 |

服务端跨职能 403 的口径未放宽，仍是硬边界；以上都是前端修复。上线只需 `git pull` + 重启，**注意静态资源版本号由 `create_app()` 计算，必须重启进程**，否则浏览器仍拿旧的 `main.js`。

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
