from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

from app import create_app
from app.extensions import db
from app.models import StaffDocument, User


def _make_user(*, username: str, role: str, password: str = "secret", **kwargs) -> User:
    user = User(username=username, role=role, **kwargs)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def _seed(*, suffix: str):
    admin = _make_user(username=f"_sd_admin_{suffix}", role="admin")
    writer = _make_user(
        username=f"_sd_writer_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_WRITER,
    )
    process = _make_user(
        username=f"_sd_process_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_PROCESS,
    )
    business = _make_user(
        username=f"_sd_biz_{suffix}",
        role="staff",
        staff_function=User.STAFF_FUNCTION_BUSINESS,
    )
    client = _make_user(username=f"_sd_client_{suffix}", role="client")
    empty_fn = _make_user(
        username=f"_sd_empty_{suffix}",
        role="staff",
        staff_function=None,
    )
    return {
        "admin_name": admin.username,
        "writer_name": writer.username,
        "process_name": process.username,
        "business_name": business.username,
        "client_name": client.username,
        "empty_name": empty_fn.username,
    }


def _login(app, username: str):
    client = app.test_client()
    client.post("/auth/login", data={"username": username, "password": "secret"})
    return client


def _upload(admin_client, *, title: str, audience: str, filename: str = "guide.pdf", body: bytes = b"%PDF-1.4 staff-doc"):
    return admin_client.post(
        "/admin/staff-docs",
        data={
            "title": title,
            "note": "内部说明",
            "audience": audience,
            "file": (BytesIO(body), filename),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def test_admin_staff_docs_page_and_sidebar():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)

    admin = _login(app, seeded["admin_name"])
    page = admin.get("/admin/staff-docs")
    assert page.status_code == 200
    text = page.data.decode("utf-8")
    assert "内部资料库" in text
    assert "可见范围" in text
    assert "仅撰写师" in text
    assert "仅流程人员" in text
    assert "仅业务人员" in text
    assert 'name="file"' in text
    assert "上传资料" in text

    accounts = admin.get("/admin/accounts")
    assert "内部资料库".encode("utf-8") in accounts.data


def test_admin_upload_visibility_download_and_delete():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)

    admin = _login(app, seeded["admin_name"])
    writer = _login(app, seeded["writer_name"])
    process = _login(app, seeded["process_name"])
    business = _login(app, seeded["business_name"])
    client = _login(app, seeded["client_name"])
    empty_fn = _login(app, seeded["empty_name"])

    writer_title = f"_sd_writer_{suffix}"
    all_title = f"_sd_all_{suffix}"
    process_title = f"_sd_process_{suffix}"
    assert _upload(admin, title=writer_title, audience="writer").status_code in (302, 303)
    assert _upload(admin, title=all_title, audience="all", filename="handbook.docx", body=b"all-staff").status_code in (302, 303)
    assert _upload(admin, title=process_title, audience="process", filename="flow.txt", body=b"process-only").status_code in (302, 303)

    with app.app_context():
        writer_doc = StaffDocument.query.filter_by(title=writer_title).one()
        all_doc = StaffDocument.query.filter_by(title=all_title).one()
        process_doc = StaffDocument.query.filter_by(title=process_title).one()
        assert writer_doc.audience == "writer"
        assert all_doc.audience == "all"
        writer_id = writer_doc.id
        all_id = all_doc.id
        process_id = process_doc.id

    admin_list = admin.get("/admin/staff-docs").data.decode("utf-8")
    assert writer_title in admin_list
    assert all_title in admin_list
    assert process_title in admin_list

    writer_page = writer.get("/staff/staff-docs")
    assert writer_page.status_code == 200
    writer_text = writer_page.data.decode("utf-8")
    assert writer_title in writer_text
    assert all_title in writer_text
    assert process_title not in writer_text
    empty_text = empty_fn.get("/staff/staff-docs").data.decode("utf-8")
    assert writer_title in empty_text
    assert all_title in empty_text
    assert process_title not in empty_text
    assert "上传资料" not in writer_text
    assert 'name="file"' not in writer_text

    process_page = process.get("/staff/staff-docs").data.decode("utf-8")
    assert process_title in process_page
    assert all_title in process_page
    assert writer_title not in process_page

    business_page = business.get("/staff/staff-docs").data.decode("utf-8")
    assert all_title in business_page
    assert writer_title not in business_page
    assert process_title not in business_page

    writer_dl = writer.get(f"/staff/staff-docs/{writer_id}")
    assert writer_dl.status_code == 200
    assert writer_dl.data.startswith(b"%PDF-1.4")

    all_dl = process.get(f"/staff/staff-docs/{all_id}")
    assert all_dl.status_code == 200

    assert process.get(f"/staff/staff-docs/{writer_id}").status_code == 404
    assert business.get(f"/staff/staff-docs/{writer_id}").status_code == 404
    assert writer.get(f"/staff/staff-docs/{process_id}").status_code == 404

    assert client.get("/staff/staff-docs").status_code == 403
    assert client.get("/admin/staff-docs").status_code == 403
    assert client.get(f"/staff/staff-docs/{all_id}").status_code == 403

    staff_upload = writer.post(
        "/admin/staff-docs",
        data={
            "title": "should-not-save",
            "audience": "all",
            "file": (BytesIO(b"nope"), "nope.txt"),
        },
        content_type="multipart/form-data",
    )
    assert staff_upload.status_code == 403
    with app.app_context():
        assert StaffDocument.query.filter_by(title="should-not-save").count() == 0

    deleted = admin.post(f"/admin/staff-docs/{writer_id}/delete", follow_redirects=False)
    assert deleted.status_code in (302, 303)
    assert "已删除" in unquote(deleted.headers.get("Location", ""))
    with app.app_context():
        assert db.session.get(StaffDocument, writer_id) is None
    assert writer.get(f"/staff/staff-docs/{writer_id}").status_code == 404


def test_admin_rejects_invalid_audience_and_missing_file():
    app = create_app()
    suffix = uuid4().hex[:8]
    with app.app_context():
        seeded = _seed(suffix=suffix)

    admin = _login(app, seeded["admin_name"])
    missing_file = admin.post(
        "/admin/staff-docs",
        data={"title": f"_sd_missing_{suffix}", "audience": "writer"},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert missing_file.status_code in (302, 303)
    assert "请选择要上传的文件" in unquote(missing_file.headers.get("Location", ""))

    bad_audience = _upload(admin, title=f"_sd_bad_{suffix}", audience="client")
    assert bad_audience.status_code in (302, 303)
    assert "请选择可见范围" in unquote(bad_audience.headers.get("Location", ""))

    with app.app_context():
        assert StaffDocument.query.filter(StaffDocument.title.like(f"_sd_%_{suffix}")).count() == 0
