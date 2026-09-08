"""SQLAlchemy 数据模型：客户、项目、案件、任务及其留痕表。

阶段 A 的领域基线：
- 客户 1—N 项目；项目 1—N 案件；案件 1—1 任务（通过 `phase_status` 表达流程）。
- 超期由后台维护（见 `app.workflow`），表层不区分独立 overdue 表。
- `User.role` 枚举：admin / staff / client；客户端账号归属 `customer_id`。
"""

import re
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager


class CustomerKind:
    """客户类型枚举：用字符串字面量直接落库，便于 SQL 查询/导出。"""

    COMPANY = "company"
    INDIVIDUAL = "individual"


class Customer(db.Model):
    """客户：以公司为主，也支持个人；其下可挂多个联系人账号（User.role=client）。"""

    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(20), nullable=False, default=CustomerKind.COMPANY)
    name = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(120), nullable=True)
    contact_phone = db.Column(db.String(40), nullable=True)
    note = db.Column(db.Text, nullable=True)
    fee_standard = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    projects = db.relationship("Project", back_populates="customer", lazy="dynamic")
    users = db.relationship(
        "User",
        back_populates="customer",
        lazy="dynamic",
        foreign_keys="User.customer_id",
    )
    created_by = db.relationship("User", back_populates="created_customers", foreign_keys=[created_by_id])


class Project(db.Model):
    """项目：隶属于客户；案件挂在项目下。项目级 due_at 为系统判定超期的默认依据。"""

    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(64), nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)
    due_at = db.Column(db.DateTime, nullable=True)
    initiated_at = db.Column(db.DateTime, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    customer = db.relationship("Customer", back_populates="projects")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    cases = db.relationship("Case", back_populates="project", lazy="dynamic")


class Case(db.Model):
    """
    案件（专利案）：一件专利在业务上只属于一个项目（防重复申请）。
    application_no 兼容旧字段名，业务上作为案件序列号：6 位自动编号（YYMM+当月序号），全局唯一。
    """

    __tablename__ = "cases"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    application_no = db.Column(db.String(100), unique=True, nullable=False, index=True)
    patent_application_no = db.Column(db.String(100), nullable=True, index=True)
    formal_status = db.Column(db.String(80), nullable=True)
    project_type = db.Column(db.String(80), nullable=True)
    case_type_code = db.Column(db.String(80), nullable=True, index=True)
    business_owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    # 指派时冻结的负责人姓名；账号停用或将来删行后办结案件仍能显示是谁做的。
    business_owner_label = db.Column(db.String(120), nullable=True)
    order_at = db.Column(db.DateTime, nullable=True)
    expected_return_at = db.Column(db.DateTime, nullable=True)
    actual_return_at = db.Column(db.DateTime, nullable=True)
    case_note = db.Column(db.Text, nullable=True)
    material_upload_port = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # 归属：专利局退回的案件转为内部案件，客户端不再可见，也不计入客户案件统计。
    # 退稿（rejected_at）是结果，归属（attribution）是口径，分开存以便将来出现
    # 「本所自行申请」这类非退稿的内部案件。
    ATTRIBUTION_CUSTOMER = "customer"
    ATTRIBUTION_INTERNAL = "internal"

    rejected_at = db.Column(db.DateTime, nullable=True, index=True)
    reject_note = db.Column(db.Text, nullable=True)
    attribution = db.Column(
        db.String(20),
        nullable=False,
        default=ATTRIBUTION_CUSTOMER,
        server_default=ATTRIBUTION_CUSTOMER,
        index=True,
    )

    project = db.relationship("Project", back_populates="cases")
    business_owner_user = db.relationship("User", foreign_keys=[business_owner_id])

    @property
    def owner_display(self) -> str:
        """业务负责人展示名：账号还在用实时姓名，否则用指派时的快照。"""
        from app.case_trace import display_trace

        return display_trace(self.business_owner_user, self.business_owner_label)
    task = db.relationship(
        "Task",
        back_populates="case",
        uselist=False,
        cascade="all, delete-orphan",
    )
    review_logs = db.relationship("CaseReviewLog", back_populates="case", lazy="dynamic", cascade="all, delete-orphan")
    materials = db.relationship("CaseMaterial", back_populates="case", lazy="dynamic", cascade="all, delete-orphan")
    material_download_logs = db.relationship(
        "CaseMaterialDownloadLog",
        back_populates="case",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    @property
    def is_internal(self) -> bool:
        """是否为内部案件；历史数据归属为空时按客户案件处理。"""
        return (self.attribution or self.ATTRIBUTION_CUSTOMER) == self.ATTRIBUTION_INTERNAL

    @property
    def is_rejected(self) -> bool:
        """是否已标记专利局退稿。"""
        return self.rejected_at is not None

    @property
    def attribution_label(self) -> str:
        return "内部案件" if self.is_internal else "客户案件"

    @staticmethod
    def attribution_filter(attribution: str):
        """归属筛选条件；历史行的 attribution 可能为空，一律按客户案件处理。"""
        if attribution == Case.ATTRIBUTION_INTERNAL:
            return Case.attribution == Case.ATTRIBUTION_INTERNAL
        return db.or_(
            Case.attribution.is_(None),
            Case.attribution == Case.ATTRIBUTION_CUSTOMER,
        )

    @property
    def case_type_labels(self) -> tuple[str, ...]:
        from app.case_types import case_type_labels

        return case_type_labels(self.case_type_code)

    @property
    def case_type_display(self) -> str:
        from app.case_types import case_type_display

        return case_type_display(self.case_type_code, self.project_type)

    @property
    def legacy_project_type_note(self) -> str:
        """
        历史「项目类型」字段实际曾用于自由备注。
        新标签启用前由系统写入的标准路径不重复作为备注展示，其余原文完整保留。
        """
        legacy = (self.project_type or "").strip()
        if not legacy:
            return ""
        from app.case_types import case_type_display

        canonical = case_type_display(self.case_type_code)
        return "" if canonical and legacy == canonical else legacy

    @property
    def case_type_needs_completion(self) -> bool:
        return bool(self.legacy_project_type_note) and not self.case_type_labels


class Task(db.Model):
    """任务：与案件 1:1；通过 phase_status 表示当前阶段（含系统维护的超期状态）。

    `due_at` 留空代表优先沿用案件应返稿时间，再兜底项目 `due_at`。
    """

    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), unique=True, nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    assignee_label = db.Column(db.String(120), nullable=True)
    phase_status = db.Column(db.String(40), nullable=False, default="in_progress")
    due_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    case = db.relationship("Case", back_populates="task")
    assignee = db.relationship("User", back_populates="assigned_tasks", foreign_keys=[assignee_id])

    @property
    def assignee_display(self) -> str:
        """承办人展示名：账号还在用实时姓名，否则用指派时的快照。"""
        from app.case_trace import display_trace

        return display_trace(self.assignee, self.assignee_label)


class CaseReviewLog(db.Model):
    """案件审核留痕：记录通过/打回动作与备注。"""

    __tablename__ = "case_review_logs"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, index=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    operator_label = db.Column(db.String(120), nullable=True)
    # 打回通知接收人；通过记录及历史记录可为空。
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    recipient_label = db.Column(db.String(120), nullable=True)
    action = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text, nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    case = db.relationship("Case", back_populates="review_logs")
    operator = db.relationship("User", foreign_keys=[operator_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    @property
    def operator_display(self) -> str:
        from app.case_trace import display_trace

        return display_trace(self.operator, self.operator_label)

    @property
    def recipient_display(self) -> str:
        from app.case_trace import display_trace

        return display_trace(self.recipient, self.recipient_label)

    @property
    def action_label(self) -> str:
        from app.case_trace import TRACE_ACTION_LABELS

        return TRACE_ACTION_LABELS.get(self.action, self.action)

    @property
    def action_tone(self) -> str:
        from app.case_trace import TRACE_ACTION_TONES

        return TRACE_ACTION_TONES.get(self.action, "secondary")


class CaseMaterial(db.Model):
    """案件材料留痕：记录文件版本、备注和上传人。"""

    __tablename__ = "case_materials"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    uploaded_by_label = db.Column(db.String(120), nullable=True)
    uploaded_by_role = db.Column(db.String(20), nullable=True)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    version_tag = db.Column(db.String(20), nullable=False, default="draft")
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    case = db.relationship("Case", back_populates="materials")
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])

    @property
    def uploader_display(self) -> str:
        from app.case_trace import display_trace

        return display_trace(self.uploaded_by, self.uploaded_by_label)
    download_logs = db.relationship(
        "CaseMaterialDownloadLog",
        back_populates="material",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def staff_upload_filename_visible_to(self, viewer) -> bool:
        """员工上传的材料：管理端与上传者本人可见文件名/版本。"""
        uploader = self.uploaded_by
        if uploader is None or uploader.role != "staff":
            return True
        if viewer is None:
            return False
        if viewer.role == "admin":
            return True
        if viewer.role == "staff":
            return self.uploaded_by_id == viewer.id
        return True


class CaseMaterialDownloadLog(db.Model):
    """材料下载留痕：记录谁在何时下载了哪个文件。"""

    __tablename__ = "case_material_download_logs"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, index=True)
    material_id = db.Column(db.Integer, db.ForeignKey("case_materials.id"), nullable=False, index=True)
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    operator_label = db.Column(db.String(120), nullable=True)
    operator_role = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    case = db.relationship("Case", back_populates="material_download_logs")
    material = db.relationship("CaseMaterial", back_populates="download_logs")
    operator = db.relationship("User", foreign_keys=[operator_id])

    @property
    def operator_display(self) -> str:
        from app.case_trace import display_trace

        return display_trace(self.operator, self.operator_label)


class User(UserMixin, db.Model):
    """系统用户：角色由 `role` 区分；客户端账号通过 `customer_id` 绑定客户。"""

    __tablename__ = "users"

    STAFF_KIND_FORMAL = "formal"
    STAFF_KIND_OUTSOURCE = "outsource"
    STAFF_KINDS = frozenset({STAFF_KIND_FORMAL, STAFF_KIND_OUTSOURCE})
    STAFF_KIND_LABELS = {
        STAFF_KIND_FORMAL: "正式",
        STAFF_KIND_OUTSOURCE: "外包",
    }

    STAFF_FUNCTION_WRITER = "writer"
    STAFF_FUNCTION_PROCESS = "process"
    STAFF_FUNCTION_BUSINESS = "business"
    STAFF_FUNCTIONS = frozenset(
        {STAFF_FUNCTION_WRITER, STAFF_FUNCTION_PROCESS, STAFF_FUNCTION_BUSINESS}
    )
    STAFF_FUNCTION_LABELS = {
        STAFF_FUNCTION_WRITER: "撰写师",
        STAFF_FUNCTION_PROCESS: "流程人员",
        STAFF_FUNCTION_BUSINESS: "业务人员",
    }

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="client")
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True, index=True)
    staff_function = db.Column(db.String(40), nullable=True)
    # 员工编制：formal=正式 / outsource=外包；仅 role=staff 有意义，默认按正式处理。
    staff_kind = db.Column(db.String(20), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # 管理员最后查看待审核中心的时间，用于计算个人未读红点。
    review_inbox_seen_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    customer = db.relationship("Customer", back_populates="users", foreign_keys="User.customer_id")
    created_customers = db.relationship("Customer", back_populates="created_by", foreign_keys="Customer.created_by_id")
    assigned_tasks = db.relationship("Task", back_populates="assignee", foreign_keys=[Task.assignee_id])

    def set_password(self, password: str):
        """以默认 werkzeug 算法保存散列密码（不直接存明文）。"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """校验明文密码是否匹配当前散列。"""
        return check_password_hash(self.password_hash, password)

    @property
    def is_authenticated(self) -> bool:
        """会话加载到该用户即为已登录；离职/冻结由 is_active 与请求钩子作废会话。"""
        return True

    _PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

    @staticmethod
    def normalize_phone(raw: str | None) -> str | None:
        """规范化手机号：去空白与非数字字符；空串视为未设置。"""
        digits = re.sub(r"\D", "", (raw or "").strip())
        if not digits:
            return None
        return digits[:20]

    @classmethod
    def is_valid_phone(cls, value: str) -> bool:
        """校验是否为 11 位中国大陆手机号。"""
        return bool(cls._PHONE_RE.fullmatch(value))

    @classmethod
    def normalize_staff_kind(cls, raw: str | None) -> str:
        """规范化员工编制；非法或空值回落为正式。"""
        value = (raw or "").strip()
        if value in cls.STAFF_KINDS:
            return value
        return cls.STAFF_KIND_FORMAL

    @classmethod
    def normalize_staff_function(cls, raw: str | None) -> str:
        """规范化员工职能；非法或空值仅在读取时回落为撰写师。"""
        value = (raw or "").strip()
        if value in cls.STAFF_FUNCTIONS:
            return value
        return cls.STAFF_FUNCTION_WRITER

    @classmethod
    def find_by_login(cls, login: str) -> "User | None":
        """按账号名或手机号查找用户；手机号登录仅限员工与客户。"""
        identifier = (login or "").strip()
        if not identifier:
            return None
        user = cls.query.filter_by(username=identifier).first()
        if user is not None:
            return user
        phone = cls.normalize_phone(identifier)
        if phone is None:
            return None
        return cls.query.filter_by(phone=phone).filter(cls.role.in_(["staff", "client"])).first()

    @property
    def staff_kind_normalized(self) -> str:
        """员工编制（仅 staff 有意义）；缺省按正式。"""
        if self.role != "staff":
            return ""
        return self.normalize_staff_kind(self.staff_kind)

    @property
    def staff_kind_label(self) -> str:
        """员工编制中文标签；非员工为空串。"""
        kind = self.staff_kind_normalized
        return self.STAFF_KIND_LABELS.get(kind, "")

    @property
    def staff_function_normalized(self) -> str:
        """员工职能（仅 staff 有意义）；缺省或非法值按撰写师。"""
        if self.role != "staff":
            return ""
        return self.normalize_staff_function(self.staff_function)

    @property
    def staff_function_label(self) -> str:
        """员工职能中文标签；非员工为空串。"""
        function = self.staff_function_normalized
        return self.STAFF_FUNCTION_LABELS.get(function, "")

    @property
    def is_assignable_writer(self) -> bool:
        """案件分配池仅含在职撰写师；流程人员、业务人员不进入派单。"""
        active = getattr(self, "is_active", True)
        if active is None:
            active = True
        return (
            self.role == "staff"
            and bool(active)
            and self.staff_function_normalized == self.STAFF_FUNCTION_WRITER
        )

    @property
    def home_endpoint(self) -> str:
        """登录后首页 endpoint：管理员、三类员工职能与客户各不相同。"""
        if self.role == "admin":
            return "admin.dashboard"
        if self.role == "staff":
            function = self.staff_function_normalized
            if function == self.STAFF_FUNCTION_PROCESS:
                return "staff.process_dashboard"
            if function == self.STAFF_FUNCTION_BUSINESS:
                return "staff.business_dashboard"
            return "staff.dashboard"
        return "client.dashboard"

    @property
    def display_label(self) -> str:
        """展示用名称：有手机号时「手机号（账号名）」，否则仅账号名。"""
        phone = (self.phone or "").strip()
        if phone:
            return f"{phone}（{self.username}）"
        return self.username

    @property
    def account_status_label(self) -> str:
        """账号状态：员工为在职/离职，客户为有效/冻结。"""
        active = getattr(self, "is_active", True)
        if self.role == "staff":
            return "在职" if active else "离职"
        if self.role == "client":
            return "有效" if active else "冻结"
        return "在职" if active else "停用"

    @property
    def account_reactivate_button_label(self) -> str:
        if self.role == "staff":
            return "恢复在职"
        if self.role == "client":
            return "恢复有效"
        return "恢复启用"

    def inactive_login_message(self) -> str:
        if self.role == "staff":
            return "员工账号已离职，请联系管理员。"
        if self.role == "client":
            return "客户账号已冻结，请联系管理员。"
        return "账号已停用，请联系管理员。"


class LoginThrottle(db.Model):
    """登录失败计数：按账号或 IP 分桶，用于锁定暴力破解。"""

    SCOPE_LOGIN = "login"
    SCOPE_IP = "ip"

    __tablename__ = "login_throttles"
    __table_args__ = (db.UniqueConstraint("scope", "key", name="uq_login_throttles_scope_key"),)

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(16), nullable=False)
    key = db.Column(db.String(80), nullable=False)
    fail_count = db.Column(db.Integer, nullable=False, default=0)
    window_started_at = db.Column(db.DateTime, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False)


@login_manager.user_loader
def load_user(user_id):
    """Flask-Login 用户回填：把 session 里的 user_id 还原成 User 实例。"""
    return db.session.get(User, int(user_id))
