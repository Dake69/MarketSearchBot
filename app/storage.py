from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite


class Storage:
    """Small async SQL adapter for local SQLite and Heroku Postgres."""

    def __init__(self, location: str | Path):
        self.location = location
        self.is_postgres = str(location).startswith(("postgres://", "postgresql://"))
        self.connection: Any = None

    async def open(self) -> None:
        if self.is_postgres:
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row

            self.connection = await AsyncConnection.connect(
                str(self.location), autocommit=True, row_factory=dict_row
            )
            return

        path = Path(self.location)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")

    def _query(self, query: str) -> str:
        if not self.is_postgres:
            return query
        if query.strip().upper() == "BEGIN IMMEDIATE":
            return "BEGIN"
        return query.replace("?", "%s")

    async def execute(self, query: str, params: Iterable[Any] = ()) -> Any:
        assert self.connection
        return await self.connection.execute(self._query(query), tuple(params))

    async def executemany(self, query: str, params: Iterable[Iterable[Any]]) -> Any:
        assert self.connection
        values = [tuple(row) for row in params]
        if self.is_postgres:
            async with self.connection.cursor() as cursor:
                await cursor.executemany(self._query(query), values)
            return None
        return await self.connection.executemany(self._query(query), values)

    async def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                await self.execute(statement)

    async def fetchone(self, query: str, params: Iterable[Any] = ()) -> Any:
        cursor = await self.execute(query, params)
        return await cursor.fetchone()

    async def fetchall(self, query: str, params: Iterable[Any] = ()) -> list[Any]:
        cursor = await self.execute(query, params)
        return list(await cursor.fetchall())

    async def commit(self) -> None:
        assert self.connection
        if self.is_postgres:
            await self.connection.execute("COMMIT")
        else:
            await self.connection.commit()

    async def rollback(self) -> None:
        assert self.connection
        if self.is_postgres:
            await self.connection.execute("ROLLBACK")
        else:
            await self.connection.rollback()

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()
            self.connection = None
