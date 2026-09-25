# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import uuid
from pathlib import Path

from openclear.bulk_import.file_storage import read_uploaded_file, save_uploaded_file


def test_save_and_read_roundtrip():
    tenant_id = uuid.uuid4()
    upload_id = uuid.uuid4()
    data = b"email,security_group,password\nnew@example.com,Preparer,hunter2\n"

    path = save_uploaded_file(tenant_id, upload_id, "users.csv", data)

    assert read_uploaded_file(path) == data
    assert str(upload_id) in path
    assert path.endswith("users.csv")


def test_different_tenants_do_not_collide():
    upload_id = uuid.uuid4()
    path_a = save_uploaded_file(uuid.uuid4(), upload_id, "same-name.csv", b"a")
    path_b = save_uploaded_file(uuid.uuid4(), upload_id, "same-name.csv", b"b")

    assert path_a != path_b
    assert read_uploaded_file(path_a) == b"a"
    assert read_uploaded_file(path_b) == b"b"


def test_path_traversal_shaped_filename_is_stored_safely(tmp_path, monkeypatch):
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "bulk_upload_storage_dir", str(tmp_path))
    tenant_id = uuid.uuid4()
    upload_id = uuid.uuid4()

    path = save_uploaded_file(tenant_id, upload_id, "../../../../../../tmp/pwned.txt", b"malicious content")

    stored_path = Path(path)
    # stored safely under the intended tenant directory, not escaped to /tmp
    assert stored_path.parent == tmp_path / str(tenant_id)
    assert read_uploaded_file(path) == b"malicious content"
    assert not (Path("/tmp") / "pwned.txt").exists()


def test_filename_with_no_usable_basename_falls_back_to_a_default(tmp_path, monkeypatch):
    from openclear.config import get_settings

    monkeypatch.setattr(get_settings(), "bulk_upload_storage_dir", str(tmp_path))
    path = save_uploaded_file(uuid.uuid4(), uuid.uuid4(), "../../", b"data")
    assert read_uploaded_file(path) == b"data"
