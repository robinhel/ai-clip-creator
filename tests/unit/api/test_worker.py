from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from clipsmith.api.worker import _emit
from clipsmith.db.models import Base, Run, RunStatus


def test_a_run_that_finishes_after_a_restart_loses_the_restart_error() -> None:
    # The server marks a going run failed when it restarts, but a CLI run keeps going and finishes.
    # Done with the old "restarted" error showed a finished project as broken.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = Run(vod_id="v", status=RunStatus.failed, error="The server restarted in the middle of the project. Press Run again.")
        db.add(run)
        db.commit()
        _emit(db, run.id, "done", 100.0, "Klart", status=RunStatus.done)
        db.refresh(run)
        assert (run.status, run.error) == (RunStatus.done, None)
