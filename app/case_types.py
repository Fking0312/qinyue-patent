"""案件类型固定分类树：叶子编码、中文路径、校验与旧值兼容。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CaseTypeLeaf:
    code: str
    primary: str
    primary_label: str
    secondary: str | None = None
    secondary_label: str | None = None
    channel: str | None = None
    channel_label: str | None = None
    domain: str | None = None
    domain_label: str | None = None

    @property
    def labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        for label in (
            self.primary_label,
            self.secondary_label,
            self.channel_label,
            self.domain_label,
        ):
            if label and label not in labels:
                labels.append(label)
        return tuple(labels)

    @property
    def display(self) -> str:
        return " / ".join(self.labels)

    def as_ui_dict(self) -> dict:
        return {
            "code": self.code,
            "segments": [
                {"value": value, "label": label} if value and label else None
                for value, label in (
                    (self.primary, self.primary_label),
                    (self.secondary, self.secondary_label),
                    (self.channel, self.channel_label),
                    (self.domain, self.domain_label),
                )
            ],
        }


PRIMARY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("invention", "发明"),
    ("utility", "实用新型"),
    ("trademark", "商标"),
    ("ic_layout", "集成电路布图设计"),
    ("other", "其他"),
)

_FIELDS = (
    ("mechanical", "机械类"),
    ("software", "软通类"),
    ("chemical", "化工类"),
)
_INVENTION_SECONDARIES = (
    ("risk", "风险发明"),
    ("nonrisk", "非风险发明"),
    ("high_quality", "高质量发明"),
)
_INVENTION_CHANNELS = (
    ("precheck", "预审通道"),
    ("priority", "优审通道"),
    ("normal", "普通通道"),
    ("unknown", "不确定"),
)


def _build_leaves() -> tuple[CaseTypeLeaf, ...]:
    leaves: list[CaseTypeLeaf] = []
    for secondary, secondary_label in _INVENTION_SECONDARIES:
        for channel, channel_label in _INVENTION_CHANNELS:
            for domain, domain_label in _FIELDS:
                leaves.append(
                    CaseTypeLeaf(
                        code=f"invention_{secondary}_{channel}_{domain}",
                        primary="invention",
                        primary_label="发明",
                        secondary=secondary,
                        secondary_label=secondary_label,
                        channel=channel,
                        channel_label=channel_label,
                        domain=domain,
                        domain_label=domain_label,
                    )
                )
    leaves.extend(
        (
            CaseTypeLeaf(
                "utility_utility_model",
                "utility",
                "实用新型",
                "utility_model",
                "实用新型",
            ),
            CaseTypeLeaf(
                "utility_design",
                "utility",
                "实用新型",
                "design",
                "外观专利",
            ),
            CaseTypeLeaf(
                "trademark_trademark",
                "trademark",
                "商标",
                "trademark",
                "商标",
            ),
            CaseTypeLeaf(
                "trademark_software_copyright",
                "trademark",
                "商标",
                "software_copyright",
                "软著",
            ),
            CaseTypeLeaf("ic_layout", "ic_layout", "集成电路布图设计"),
            CaseTypeLeaf("other", "other", "其他"),
        )
    )
    return tuple(leaves)


CASE_TYPE_LEAVES = _build_leaves()
CASE_TYPE_BY_CODE = {leaf.code: leaf for leaf in CASE_TYPE_LEAVES}
CASE_TYPE_UI_CONFIG = [leaf.as_ui_dict() for leaf in CASE_TYPE_LEAVES]
VALID_CASE_TYPE_CODES = frozenset(CASE_TYPE_BY_CODE)
PRIMARY_LABELS = dict(PRIMARY_OPTIONS)

_LEGACY_CODE_ALIASES = {
    # 旧版非风险发明没有“通道”层级，新分类中归入“不确定”以完整保留领域。
    "invention_nonrisk_mechanical": "invention_nonrisk_unknown_mechanical",
    "invention_nonrisk_software": "invention_nonrisk_unknown_software",
    "invention_nonrisk_chemical": "invention_nonrisk_unknown_chemical",
}

_LEGACY_TO_CODE = {
    "实用新型": "utility_utility_model",
    "外观": "utility_design",
    "外观专利": "utility_design",
    "商标": "trademark_trademark",
    "软著": "trademark_software_copyright",
    "软件著作权": "trademark_software_copyright",
    "集成电路": "ic_layout",
    "集成电路布图设计": "ic_layout",
    "其他": "other",
}

_LEGACY_PRIMARY_VALUES = {
    "invention": ("发明", "发明专利"),
    "utility": ("实用新型", "外观", "外观专利"),
    "trademark": ("商标", "软著", "软件著作权"),
    "ic_layout": ("集成电路", "集成电路布图设计"),
    "other": ("其他",),
}


def get_case_type(code: str | None) -> CaseTypeLeaf | None:
    normalized = (code or "").strip()
    return CASE_TYPE_BY_CODE.get(_LEGACY_CODE_ALIASES.get(normalized, normalized))


def normalize_case_type_code(code: str | None) -> str:
    """将历史编码转换为当前标准编码；未知编码原样返回。"""
    normalized = (code or "").strip()
    return _LEGACY_CODE_ALIASES.get(normalized, normalized)


def validate_case_type_code(code: str | None) -> tuple[CaseTypeLeaf | None, str | None]:
    leaf = get_case_type(code)
    if leaf is None:
        return None, "请选择完整、有效的案件类型。"
    return leaf, None


def case_type_labels(code: str | None) -> tuple[str, ...]:
    leaf = get_case_type(code)
    return leaf.labels if leaf else ()


def case_type_display(code: str | None, legacy_value: str | None = None) -> str:
    leaf = get_case_type(code)
    if leaf:
        return leaf.display
    return (legacy_value or "").strip()


def case_type_primary(code: str | None) -> str | None:
    leaf = get_case_type(code)
    return leaf.primary if leaf else None


def legacy_case_type_code(value: str | None) -> str | None:
    """仅映射能确定完整叶子路径的旧自由文本；单独“发明”不可安全推断。"""
    normalized = (value or "").strip().replace(" ", "")
    return _LEGACY_TO_CODE.get(normalized)


def codes_for_primary(primary: str | None) -> tuple[str, ...]:
    value = (primary or "").strip()
    current = [leaf.code for leaf in CASE_TYPE_LEAVES if leaf.primary == value]
    legacy = [
        old_code
        for old_code, current_code in _LEGACY_CODE_ALIASES.items()
        if CASE_TYPE_BY_CODE[current_code].primary == value
    ]
    return tuple(current + legacy)


def legacy_values_for_primary(primary: str | None) -> tuple[str, ...]:
    return _LEGACY_PRIMARY_VALUES.get((primary or "").strip(), ())
