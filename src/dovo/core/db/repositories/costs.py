"""Repository managing workflow token usage and cost tracking using SQLModel."""

from sqlalchemy import func
from sqlmodel import select

from dovo.core.db.models import CostRecord
from dovo.core.db.repositories.base import BaseRepository


class CostsRepository(BaseRepository):
    """Repository managing workflow token usage and cost tracking in SQLite."""

    def record_token_usage(
        self,
        session_id: str,
        branch_name: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_usd_cost: float,
    ) -> int:
        """Insert a CostRecord and return the integer primary key.

        Returns the auto-incremented primary key ID of the inserted record.
        """
        total_tokens = prompt_tokens + completion_tokens
        record = CostRecord(
            project_id=self.project_id,
            session_id=session_id,
            branch_name=branch_name,
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_usd_cost=estimated_usd_cost,
        )
        with self.session() as session:
            self._commit(session, record, "Failed to record workflow token usage")
            if record.id is None:
                raise RuntimeError("Failed to retrieve generated id for CostRecord.")
            return record.id

    def get_session_total_cost(self, session_id: str) -> dict[str, float]:
        """Calculate aggregated token counts and dollar spend for a session."""
        with self.session() as session:
            statement = select(
                func.coalesce(func.sum(CostRecord.prompt_tokens), 0),
                func.coalesce(func.sum(CostRecord.completion_tokens), 0),
                func.coalesce(func.sum(CostRecord.total_tokens), 0),
                func.coalesce(func.sum(CostRecord.estimated_usd_cost), 0.0),
            ).where(CostRecord.session_id == session_id, CostRecord.project_id == self.project_id)
            row = session.exec(statement).one()
            return {
                "total_prompt_tokens": float(row[0]),
                "total_completion_tokens": float(row[1]),
                "total_tokens": float(row[2]),
                "total_usd_cost": float(row[3]),
            }
