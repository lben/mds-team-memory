"""The admin ML queue screen shows the worker's live step, the queue and finished work."""

import sqlite3
import sys
import time
from pathlib import Path

from playwright.sync_api import Browser, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.ml import activity  # noqa: E402
from app.ml.queue import Claim  # noqa: E402

SOURCES = ("e2e-queue-note", "e2e-queue-private")


def test_admin_watches_the_ml_queue(browser: Browser, base_url_server):
    database = base_url_server.data_dir / "e2e.sqlite3"
    base_url_server.create_admin("queue-admin", "queue-admin-password")
    now = time.time()
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO profiles(id,display_name,claim_locked,created_at) VALUES ('e2e-queue-author','Queue Author',0,datetime('now'))")
        for item_id, body, visibility in zip(SOURCES, ("Citrine Pump feeds the cooling loop.", "Private salary details."),
                                             ("team", "private")):
            db.execute("""INSERT INTO knowledge_items(id,kind,body,visibility,author_profile_id,created_at,updated_at)
              VALUES (?,'note',?,?,'e2e-queue-author',datetime('now'),datetime('now'))""", (item_id, body, visibility))
        db.execute("DELETE FROM ml_jobs")
        db.execute("""INSERT INTO ml_jobs(source_kind,source_id,priority,available_at,created_at,updated_at,lease_token,lease_until)
          VALUES ('item',?,0,?,?,?,'claim',?)""", (SOURCES[0], now - 30, now - 30, now, now + 600))
        db.execute("""INSERT INTO ml_jobs(source_kind,source_id,priority,available_at,created_at,updated_at)
          VALUES ('item',?,10,?,?,?)""", (SOURCES[1], now - 20, now - 20, now))
        db.execute("UPDATE ml_state SET worker_token='e2e-worker', worker_lease_until=?, status='running' WHERE id=1",
                   (now + 600,))
    recorder = activity.Recorder(activity.activity_path(database))
    recorder.lease("e2e-worker")
    recorder.start_job(Claim("item", SOURCES[0], 1, "claim", now + 600, "e2e-worker"))
    recorder.stage("running inference")
    recorder.progress({"step": "extracting relations", "window": 2, "windows": 3})
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    try:
        page.goto(base_url_server.url + "/admin/ml-queue")
        expect(page.get_by_test_id("ml-queue-auth")).to_contain_text("Administrator sign-in required")
        expect(page.get_by_test_id("admin-nav")).to_have_count(0)
        page.get_by_test_id("profile-button").click()
        page.get_by_test_id("auth-username").fill("queue-admin")
        page.get_by_test_id("auth-password").fill("queue-admin-password")
        page.get_by_test_id("do-sign-in").click()
        expect(page.get_by_test_id("admin-nav").get_by_role("link", name="ML Queue")).to_be_visible()

        expect(page.get_by_test_id("ml-worker-state")).to_contain_text("Running since")
        expect(page.get_by_test_id("ml-current-job")).to_contain_text("Note: Citrine Pump feeds the cooling loop.")
        expect(page.get_by_test_id("ml-current-stage")).to_contain_text("running inference — window 2 of 3: extracting relations")
        # Signing in also queues the admin's own profile, so match rows by content.
        rows = page.get_by_test_id("ml-job-row")
        expect(rows.first).to_contain_text("processing")
        expect(rows.first).to_contain_text("Note: Citrine Pump feeds the cooling loop.")
        private = rows.filter(has_text="Private contribution (withdrawing its findings)")
        expect(private).to_contain_text("reprocessing older content")
        expect(page.locator("main")).not_to_contain_text("salary")

        # The screen keeps itself current: finished work and its decision appear without a reload.
        with sqlite3.connect(database) as db:
            db.execute("DELETE FROM ml_jobs WHERE source_id=?", (SOURCES[0],))
            db.execute("""INSERT INTO ml_sources(kind,id,content_hash,valid,model_version,result,updated_at)
              VALUES ('item',?,'hash',1,'model','{}',datetime('now'))""", (SOURCES[0],))
            db.execute("""INSERT INTO ml_findings(key,kind,payload,state,score,calibrated,policy_version,created_at,updated_at)
              VALUES ('e2e-queue-finding','concept','{"name":"Citrine Pump"}','active',0,0,'policy',datetime('now'),datetime('now'))""")
            db.execute("""INSERT INTO ml_evidence(key,finding_key,source_kind,source_id,source_hash,group_key,start,end,
                raw_score,polarity,features,model_version)
              VALUES ('e2e-queue-evidence','e2e-queue-finding','item',?,'hash','group',0,12,0.999,'positive',
                '{"text_hash":"t","grounded":true,"label":"named entity","eligibility_margin":0.91}','model')""",
                (SOURCES[0],))
        recorder.finish_job("done")
        expect(page.get_by_test_id("ml-recent-row").first).to_contain_text("done", timeout=6000)
        decisions = page.get_by_test_id("ml-recent-row").first.get_by_test_id("ml-decisions")
        expect(decisions).to_contain_text("Concept confirmed by the model Citrine Pump")
        expect(decisions).to_contain_text("The model is 99.9% sure this is a named thing.")
        expect(page.get_by_test_id("ml-current-job")).to_have_count(0)
        expect(rows.filter(has_text="Citrine Pump")).to_have_count(0)
    finally:
        context.close()
        with sqlite3.connect(database) as db:
            db.execute("DELETE FROM ml_evidence WHERE key='e2e-queue-evidence'")
            db.execute("DELETE FROM ml_findings WHERE key='e2e-queue-finding'")
            db.execute("DELETE FROM ml_sources WHERE id=?", (SOURCES[0],))
            db.execute("DELETE FROM knowledge_items WHERE id IN (?,?)", SOURCES)
            db.execute("DELETE FROM profiles WHERE id='e2e-queue-author'")
            db.execute("DELETE FROM ml_jobs")
            db.execute("UPDATE ml_state SET worker_token=NULL, worker_lease_until=NULL, status='stopped' WHERE id=1")
        activity.activity_path(database).unlink(missing_ok=True)
