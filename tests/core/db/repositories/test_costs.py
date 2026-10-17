"""Contract tests for CostsRepository project_id scoping."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from dovo.core.db.repositories.costs import CostsRepository


class CostsRepositoryTests:
    """Contract tests proving CostsRepository queries never cross project_id boundaries."""

    def test_get_session_total_cost_scopes_to_repository_project_id(self, db_path: Path, db_engine: Engine) -> None:
        repo_a = CostsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_b = CostsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-b")
        repo_a.record_token_usage(
            session_id="s1",
            branch_name="deploy",
            model_id="claude-sonnet-5",
            prompt_tokens=10,
            completion_tokens=5,
            estimated_usd_cost=0.5,
        )

        totals_b = repo_b.get_session_total_cost("s1")

        assert totals_b == {
            "total_prompt_tokens": 0.0,
            "total_completion_tokens": 0.0,
            "total_tokens": 0.0,
            "total_usd_cost": 0.0,
        }

    def test_record_token_usage_persists_project_id(self, db_path: Path, db_engine: Engine) -> None:
        repo_a = CostsRepository(db_path=db_path, db_engine=db_engine, project_id="proj-a")
        repo_a.record_token_usage(
            session_id="s1",
            branch_name="deploy",
            model_id="claude-sonnet-5",
            prompt_tokens=10,
            completion_tokens=5,
            estimated_usd_cost=0.5,
        )

        totals_a = repo_a.get_session_total_cost("s1")

        assert totals_a == {
            "total_prompt_tokens": 10.0,
            "total_completion_tokens": 5.0,
            "total_tokens": 15.0,
            "total_usd_cost": 0.5,
        }
