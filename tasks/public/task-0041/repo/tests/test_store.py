from paygrade_sync.store import Store


def test_upsert_and_flush(tmp_path):
    store = Store(tmp_path)
    store.employees.upsert(source_id="emp_0001", data={"f_name": "Ada"}, updated_at=100)
    store.flush()

    reloaded = Store(tmp_path)
    row = reloaded.employees.get_row("emp_0001")
    assert row is not None
    assert row["data"]["f_name"] == "Ada"
    assert row["updated_at"] == 100
    assert row["is_deleted"] is False


def test_tombstone_retains_last_known_data(tmp_path):
    store = Store(tmp_path)
    store.employees.upsert(source_id="emp_0002", data={"f_name": "Blaise"}, updated_at=200)
    store.employees.tombstone(source_id="emp_0002")
    row = store.employees.get_row("emp_0002")
    assert row["is_deleted"] is True
    assert row["data"]["f_name"] == "Blaise"
    assert row["updated_at"] == 200  # untouched by the delete


def test_tombstone_unknown_id_creates_minimal_row(tmp_path):
    store = Store(tmp_path)
    store.employees.tombstone(source_id="emp_9999")
    row = store.employees.get_row("emp_9999")
    assert row["is_deleted"] is True


def test_watermarks_are_independent(tmp_path):
    store = Store(tmp_path)
    store.set_state("employee_mod_ms", 111)
    store.set_state("tombstone_since_ms", 222)
    store.flush()

    reloaded = Store(tmp_path)
    assert reloaded.get_state("employee_mod_ms") == 111
    assert reloaded.get_state("tombstone_since_ms") == 222
