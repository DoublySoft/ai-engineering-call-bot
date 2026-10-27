import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from voice_lab.persistence.models import ConversationMessage


class TranscriptStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], session_id: uuid.UUID):
        self._sessionmaker = sessionmaker
        self._session_id = session_id
        self._seq = 0

    async def add(self, role: str, text: str, interrupted: bool = False) -> int:
        self._seq += 1
        async with self._sessionmaker() as db:
            db.add(ConversationMessage(session_id=self._session_id, seq=self._seq, role=role, text=text, interrupted=interrupted))
            await db.commit()
        return self._seq
