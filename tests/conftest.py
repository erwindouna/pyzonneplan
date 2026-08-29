"""Pytest configuration for pyzonneplan tests."""

from collections.abc import AsyncGenerator

import pytest
from aiohttp import ClientSession
from syrupy.assertion import SnapshotAssertion

from pyzonneplan import Zonneplan

from .syrupy import ZonneplanSnapshotExtension


@pytest.fixture(name="snapshot")
def snapshot_assertion(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return snapshot assertion fixture with the Zonneplan extension."""
    return snapshot.use_extension(ZonneplanSnapshotExtension)


@pytest.fixture(name="zonneplan_client")
async def client() -> AsyncGenerator[Zonneplan, None]:
    """Return a pyzonneplan client."""
    async with (
        ClientSession() as session,
        Zonneplan(email="user@example.com", session=session, max_retries=0) as zonneplan_client,
    ):
        yield zonneplan_client
