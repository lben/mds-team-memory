"""Synthetic UAT imports must remain removable without destroying human data."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def uat(app_modules, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app import config, testdata
    from app.db import _set_sqlite_pragma

    url = "sqlite:///" + str(tmp_path / "mds.sqlite3")
    monkeypatch.setenv("MDS_ENVIRONMENT", "uat")
    monkeypatch.setattr(config, "DATABASE_URL", url)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cfg = Config(str(ROOT / "backend/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    event.listen(engine, "connect", _set_sqlite_pragma)
    monkeypatch.setattr(testdata, "engine", engine)
    monkeypatch.setattr(testdata, "SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    yield testdata
    engine.dispose()


def quiet(*args, **kwargs):
    pass


def selection(percent=1, seed=42):
    from app.testdata_catalog import select_dataset
    return select_dataset("expanded", percent, seed)


def test_selection_is_repeatable_nested_and_preserves_scenarios():
    from app.testdata_catalog import select_dataset
    first, cases = select_dataset("expanded", 25, 42)
    same, again = select_dataset("expanded", "25.0", 42)
    half, _ = select_dataset("expanded", 50, 42)
    other, _ = select_dataset("expanded", 25, 43)
    assert first == same and cases == again
    assert first["selected_units"] == 75 and first["posts"] == 378
    assert set(first["case_ids"]) <= set(half["case_ids"])
    assert first["fingerprint"] != other["fingerprint"]
    for case in cases:
        for index, post in enumerate(case["posts"]):
            if "parent" in post:
                assert post["parent"] < index and case["posts"][post["parent"]]["kind"] == "question"
    filtered, _ = select_dataset("cross-domain", 25, 42, ["astronomy", "software"])
    assert filtered["eligible_units"] == 10 and filtered["selected_units"] == 3
    with pytest.raises(ValueError, match="Unknown topics"):
        select_dataset("expanded", 10, topics=["does-not-exist"])
    for percent in (0, -1, 101, "NaN", "Infinity"):
        with pytest.raises(ValueError):
            select_dataset("expanded", percent)


def test_capacity_fraction_and_content_identity():
    from app.testdata_catalog import select_dataset
    one, cases = select_dataset("capacity", 0.01, 42)
    assert one["selected_units"] == 5 and one["posts"] == 5
    assert all(c["posts"][0]["kind"] == "note" for c in cases)
    larger, more = select_dataset("capacity", 0.02, 42)
    by_id = {c["id"]: c for c in more}
    assert all(c == by_id[c["id"]] for c in cases)


def test_server_prod_guard_before_journal(uat, monkeypatch):
    monkeypatch.setenv("MDS_ENVIRONMENT", "prod")
    assert uat.main(["batches"]) == 1
    with uat.engine.connect() as db:
        assert not db.execute(text("SELECT name FROM sqlite_master WHERE name='testdata_batches'")).first()


def test_stale_client_fingerprint_refuses_before_creating_batch(uat):
    assert uat.main(["add", "--dataset", "expanded", "--percent", "1", "--expected-fingerprint", "0" * 64]) == 1
    with uat.engine.connect() as db:
        assert not db.execute(text("SELECT name FROM sqlite_master WHERE name='testdata_batches'")).first()


def test_client_local_commands_and_prod_never_connect(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("testdata_client", ROOT / "tools/testdata.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    monkeypatch.setattr(client, "Session", lambda *a: pytest.fail("Unexpected SSH connection"))
    monkeypatch.setattr(client, "configured_target", lambda *a: pytest.fail("Unexpected config read"))
    assert client.main(["datasets"]) == 0
    assert client.main(["UAT", "preview", "--dataset", "expanded", "--percent", "25"]) == 0
    assert client.main(["PROD", "add", "--dataset", "expanded", "--percent", "25"]) == 1
    assert "refuses PROD" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["uat", "prod"])
def test_deployment_environment_is_managed(tmp_path, monkeypatch, target):
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import deploy
    from deploylib import Target
    monkeypatch.setattr(deploy, "BUILD_DIR", tmp_path)
    value = Target(target, {"host": "tester@127.0.0.1", "root": "/tmp/mds-test"})
    assert f"MDS_ENVIRONMENT={target}\n" in deploy.write_env_file(value).read_text()
    value.env["MDS_ENVIRONMENT"] = "uat"
    with pytest.raises(ValueError, match="manages MDS_ENVIRONMENT"):
        deploy.write_env_file(value)


def test_import_questions_feedback_and_overlap(uat):
    from app.models import KnowledgeItem, ImpactEvent, Account
    plan, cases = selection()
    result = uat.import_batch(plan, cases, quiet)
    assert result["state"] == "active" and result["remaining_posts"] == plan["posts"]
    assert result["pending_jobs"] > 0
    with uat.SessionLocal() as db:
        assert not db.query(Account).filter_by(is_admin=True).first()
        assert db.query(KnowledgeItem).filter_by(kind="answer").count() > 0
        assert db.query(ImpactEvent).count() > 0
        assert db.query(KnowledgeItem).filter(KnowledgeItem.accepted_answer_id.isnot(None)).count() > 0
    with pytest.raises(ValueError, match="overlaps"):
        uat.import_batch(plan, cases, quiet)
    with pytest.raises(ValueError, match="overlaps"):
        uat.import_batch(*selection(2), progress=quiet)
    assert len(uat.batches()) == 1
    uat.remove_batch(result["id"], quiet)
    with uat.SessionLocal() as db:
        assert db.query(KnowledgeItem).count() == 0
        assert db.query(Account).count() == 0
        assert db.query(ImpactEvent).count() == 0
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
        assert db.execute(text("PRAGMA quick_check")).scalar() == "ok"


def test_interrupted_import_remains_tracked_and_removable(uat, monkeypatch):
    from app import knowledge
    from app.models import Account, KnowledgeItem, Profile
    monkeypatch.setattr(knowledge, "process_after_save", lambda *a: (_ for _ in ()).throw(RuntimeError("injected import interruption")))
    with pytest.raises(RuntimeError, match="interruption"):
        uat.import_batch(*selection(), progress=quiet)
    batch = uat.batches()[0]
    assert batch["state"] == "failed"
    assert uat.status(batch["id"])["remaining_posts"] == 1
    uat.remove_batch(batch["id"], quiet)
    with uat.SessionLocal() as db:
        assert not db.query(KnowledgeItem).first()
        assert not db.query(Profile).first()
        assert not db.query(Account).first()


def test_interrupted_cleanup_reports_cause_and_can_resume(uat, monkeypatch):
    from app import knowledge
    from app.models import KnowledgeItem
    result = uat.import_batch(*selection(), progress=quiet)
    original = knowledge.delete_item
    def interrupted(db, item):
        original(db, item)
        raise RuntimeError("injected cleanup interruption")
    monkeypatch.setattr(knowledge, "delete_item", interrupted)
    with pytest.raises(RuntimeError, match="cleanup interruption"):
        uat.remove_batch(result["id"], quiet)
    state = uat.status(result["id"])
    assert state["state"] == "removing" and state["processing"] == "cleanup-blocked"
    with pytest.raises(ValueError, match="cleanup interruption"):
        uat.wait(result["id"], 1, quiet)
    monkeypatch.setattr(knowledge, "delete_item", original)
    uat.remove_batch(result["id"], quiet)
    with uat.SessionLocal() as db:
        assert not db.query(KnowledgeItem).first()
    assert uat.status(result["id"])["error"] is None


def test_concurrent_cleanup_refusal_does_not_poison_running_batch(uat):
    result = uat.import_batch(*selection(), progress=quiet)
    uat.set_state(result["id"], "removing")
    with uat.mutation_lock():
        with pytest.raises(ValueError, match="Another TestData"):
            uat.remove_batch(result["id"], quiet)
    state = uat.status(result["id"])
    assert state["state"] == "removing" and state["error"] is None


def test_cleanup_preserves_human_contributions_and_manual_concepts(uat):
    from app.knowledge import delete_item, process_after_save
    from app.models import Account, Concept, ConceptTerm, KnowledgeItem, Profile
    result = uat.import_batch(*selection(), progress=quiet)
    with uat.SessionLocal() as db:
        human = Profile(id="a" * 32, display_name="Real contributor", claim_locked=True)
        db.add(human)
        concept = Concept(id="b" * 32)
        db.add(concept)
        db.flush()
        db.add(ConceptTerm(concept_id=concept.id, term="preservation marker", display="Preservation Marker", is_canonical=True))
        question = db.query(KnowledgeItem).filter_by(kind="question").first()
        answer = KnowledgeItem(id="c" * 32, author_profile_id=human.id, parent_id=question.id, kind="answer", body="Human answer that must survive", visibility="team")
        marker = KnowledgeItem(id="d" * 32, author_profile_id=human.id, kind="note", body="Preservation Marker remains", visibility="team")
        db.add_all([answer, marker]); db.commit()
        process_after_save(db, marker)
    with pytest.raises(ValueError, match="non-test contribution"):
        uat.remove_batch(result["id"], quiet)
    assert uat.status(result["id"])["state"] == "active"
    with uat.SessionLocal() as db:
        assert db.get(KnowledgeItem, "c" * 32) is not None
        delete_item(db, db.get(KnowledgeItem, "c" * 32))
    uat.remove_batch(result["id"], quiet)
    with uat.SessionLocal() as db:
        assert db.get(KnowledgeItem, "d" * 32).body == "Preservation Marker remains"
        assert db.get(Profile, "a" * 32) is not None
        assert db.get(Concept, "b" * 32).name == "Preservation Marker"
        assert db.query(Account).count() == 0


def test_promoted_account_and_non_test_scratchpad_block_cleanup(uat):
    from app.models import Account, Profile, Scratchpad
    result = uat.import_batch(*selection(), progress=quiet)
    with uat.SessionLocal() as db:
        account = db.query(Account).first(); account.is_admin = True; db.commit()
    with pytest.raises(ValueError, match="changed test account"):
        uat.remove_batch(result["id"], quiet)
    with uat.SessionLocal() as db:
        account = db.query(Account).first(); account.is_admin = False
        db.add(Scratchpad(profile_id=db.query(Profile).first().id, content="Human scratchpad", is_default=False))
        db.commit()
    with pytest.raises(ValueError, match="non-test scratchpad"):
        uat.remove_batch(result["id"], quiet)


def test_private_scenario_is_scratchpad_not_team_content(uat):
    from app.testdata_catalog import select_dataset
    from app.models import KnowledgeItem, Scratchpad
    plan, cases = select_dataset("cross-domain", 100, topics=["home_automation"])
    result = uat.import_batch(plan, cases, quiet)
    private = sum(p.get("visibility") == "private" for c in cases for p in c["posts"])
    assert private > 0
    with uat.SessionLocal() as db:
        assert db.query(Scratchpad).count() == private
        assert db.query(KnowledgeItem).count() == plan["posts"] - private
    uat.remove_batch(result["id"], quiet)
    with uat.SessionLocal() as db:
        assert not db.query(Scratchpad).first()


def test_wait_stopped_worker_and_timeout_are_explicit(uat):
    result = uat.import_batch(*selection(), progress=quiet)
    with pytest.raises(ValueError, match="worker-stopped"):
        uat.wait(result["id"], 0.1, quiet)
    with uat.SessionLocal() as db:
        import time
        db.execute(text("UPDATE ml_state SET worker_lease_until=:until,status='idle' WHERE id=1"), {"until":time.time()+120}); db.commit()
    with pytest.raises(TimeoutError, match="still pending"):
        uat.wait(result["id"], 0.001, quiet)
