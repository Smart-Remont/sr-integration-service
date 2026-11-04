"""``cron-auto-sign-operator``: failed commit, cancellation, overlap, hung steps and the brake."""

import asyncio

import pytest
from asyncpg.exceptions import RaiseError
from httpx import AsyncClient

from src.features.auto_sign.failure_brake import PAUSE_SECONDS, SigningFailureBrake
from src.features.auto_sign.mynca_signature import MyncaSignature
from src.shared.mynca_client import MyncaClient
from tests.auto_sign.fakes import (
    FIND_SIGN,
    INSERT_SIGN,
    LOCK,
    MODIFY_SIGN,
    MYNCA_TOKEN,
    MYNCA_URL,
    NEW_PROCESS,
    NEW_SIGN_ID,
    OLD_SESSION,
    OPERATOR_ROUTE,
    READ_KEY,
    READ_ROWS,
    ROLLBACK_PATH,
    SESSION_ALIVE,
    SIGN_ID,
    SIGN_SAVE_PATH,
    TERMINATE,
    Database,
    Mynca,
    assert_empty,
    fail_after_dry_run,
    sign_row,
)

ALIVE = [{"pg_stat_get_activity": "(...)"}]


@pytest.mark.usefixtures("short_read_back")
class TestFailedCommit:
    @pytest.fixture(autouse=True)
    def commit_fails(self, db: Database) -> None:
        db.commit_error = ConnectionResetError("lost")
        db.responses[READ_ROWS] = [sign_row(sign_id=SIGN_ID), sign_row(sign_id=SIGN_ID + 1)]

    async def test_row_found_means_no_rollback_and_the_run_ends(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.responses[FIND_SIGN] = [{"sign_id": NEW_SIGN_ID}]

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert ROLLBACK_PATH not in mynca.paths()
        assert (FIND_SIGN, (NEW_PROCESS,)) in db.separate.calls
        assert mynca.paths().count(SIGN_SAVE_PATH) == 1

    async def test_old_session_is_terminated_and_checked_on_another_connection(
        self,
        client: AsyncClient,
        db: Database,
    ) -> None:
        assert_empty(await client.get(OPERATOR_ROUTE))

        assert (TERMINATE, (OLD_SESSION,)) in db.separate.calls
        assert (SESSION_ALIVE, (OLD_SESSION,)) in db.separate.calls
        assert db.calls_of(TERMINATE) == [(OLD_SESSION,)]

    async def test_it_reads_several_times_before_deciding(
        self,
        client: AsyncClient,
        db: Database,
    ) -> None:
        assert_empty(await client.get(OPERATOR_ROUTE))

        assert len(db.calls_of(FIND_SIGN)) > 1

    async def test_row_that_appears_late_means_no_rollback(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        reads = iter([[], [], [{"sign_id": NEW_SIGN_ID}]])
        db.responses[FIND_SIGN] = lambda _: next(reads)

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert ROLLBACK_PATH not in mynca.paths()

    async def test_session_gone_and_row_absent_is_the_only_rollback(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.paths().count(ROLLBACK_PATH) == 1
        assert db.committed == []
        assert mynca.paths().count(SIGN_SAVE_PATH) == 1

    async def test_empty_reads_with_the_session_alive_prove_nothing(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
        log_lines: list[str],
    ) -> None:
        db.responses[SESSION_ALIVE] = ALIVE

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert ROLLBACK_PATH not in mynca.paths()
        critical = [line for line in log_lines if line.startswith("CRITICAL")]
        assert any(
            "state unknown" in line and NEW_PROCESS in line and f"sign_id={SIGN_ID}" in line
            for line in critical
        )

    @pytest.mark.parametrize("function", [TERMINATE, SESSION_ALIVE, FIND_SIGN])
    async def test_failed_check_leaves_the_signature_alone(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
        log_lines: list[str],
        function: str,
    ) -> None:
        db.responses[function] = OSError("still down")

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert ROLLBACK_PATH not in mynca.paths()
        assert any("state unknown" in line for line in log_lines)

    async def test_failed_last_read_is_not_taken_for_an_absent_row(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        def reads(_: tuple[object, ...]) -> list[dict[str, int]]:
            if len(db.calls_of(FIND_SIGN)) > 1:
                raise OSError("down again")
            return []

        db.responses[FIND_SIGN] = reads

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert ROLLBACK_PATH not in mynca.paths()


async def test_cancellation_during_sign_save_is_logged_and_passes_through(
    db: Database,
    log_lines: list[str],
) -> None:
    signatures = MyncaSignature(CancelledOnSignSave(), SigningFailureBrake())

    with pytest.raises(asyncio.CancelledError):
        await signatures._post_cms_sign_save(SIGN_ID, 501, {})

    assert any(
        line.startswith("CRITICAL") and "cancelled" in line and f"sign_id={SIGN_ID}" in line
        for line in log_lines
    )


class CancelledOnSignSave(MyncaClient):
    def __init__(self) -> None:
        super().__init__(MYNCA_URL, MYNCA_TOKEN)

    async def cms_sign_save(self, payload: dict[str, object]) -> None:
        raise asyncio.CancelledError


class TestOverlap:
    async def test_second_run_touches_nothing_and_answers_like_php(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.lock_is_held = True

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert db.calls_of(READ_ROWS) == []
        assert mynca.requests == []

    async def test_lock_that_cannot_be_taken_skips_the_run_as_php_does(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.responses[LOCK] = OSError("down")

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert db.calls_of(READ_ROWS) == []
        assert mynca.requests == []

    async def test_run_arriving_during_a_run_signs_nothing(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.pause_function = READ_KEY
        first = asyncio.create_task(client.get(OPERATOR_ROUTE))
        await db.paused.wait()

        assert_empty(await client.get(OPERATOR_ROUTE))
        assert mynca.requests == []

        db.resume.set()
        assert_empty(await first)
        assert mynca.paths().count(SIGN_SAVE_PATH) == 1


@pytest.mark.usefixtures("short_limits")
class TestHungDatabase:
    async def test_hung_read_ends_the_run_and_the_next_one_proceeds(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.hung_functions = {READ_ROWS: 1}

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.requests == []
        assert (db.released, db.lock_is_held) == (1, False)

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.paths().count(SIGN_SAVE_PATH) == 1

    async def test_hung_lock_ends_the_run(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.hung_functions = {LOCK: 1}

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.requests == []
        assert db.lock_is_held is False

    async def test_hung_dry_run_is_a_refusal_and_nothing_is_signed(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.hung_functions = {INSERT_SIGN: 1}

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.requests == []
        assert db.committed == []

    async def test_hung_key_read_skips_the_item_and_the_loop_goes_on(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
        db.hung_functions = {READ_KEY: 1}

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.paths().count(SIGN_SAVE_PATH) == 1
        assert db.open_transactions == 0

    @pytest.mark.parametrize(
        ("function", "passing"),
        [(INSERT_SIGN, 1), (MODIFY_SIGN, 0)],
    )
    async def test_hung_record_rolls_the_signature_back(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
        function: str,
        passing: int,
    ) -> None:
        db.passing_calls = {function: passing}
        db.hung_functions = {function: 1}

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.paths().count(ROLLBACK_PATH) == 1
        assert db.committed == []
        assert db.calls_of(FIND_SIGN) == []
        assert (db.released, db.lock_is_held) == (1, False)

    @pytest.mark.usefixtures("short_read_back")
    async def test_hung_commit_is_resolved_by_reading_the_row_back(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
    ) -> None:
        db.commit_hangs = True
        db.responses[FIND_SIGN] = [{"sign_id": NEW_SIGN_ID}]

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert db.calls_of(FIND_SIGN)[0] == (NEW_PROCESS,)
        assert ROLLBACK_PATH not in mynca.paths()


class TestBrake:
    async def test_three_rollbacks_pause_the_document_and_say_so(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
        log_lines: list[str],
    ) -> None:
        db.responses[INSERT_SIGN] = fail_after_dry_run(RaiseError("{refused}"))

        for _ in range(5):
            assert_empty(await client.get(OPERATOR_ROUTE))
            db.lock_is_held = False

        assert mynca.paths().count(SIGN_SAVE_PATH) == 3
        assert mynca.paths().count(ROLLBACK_PATH) == 3
        assert sum(line.startswith("CRITICAL") and "paused" in line for line in log_lines) == 1

    async def test_paused_document_does_not_stop_the_others(
        self,
        client: AsyncClient,
        db: Database,
        mynca: Mynca,
        brake: SigningFailureBrake,
    ) -> None:
        db.responses[READ_ROWS] = [sign_row(sign_id=1), sign_row(sign_id=2)]
        for _ in range(3):
            brake.note_failure(1)

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert mynca.paths().count(SIGN_SAVE_PATH) == 1
        assert db.calls_of(INSERT_SIGN)[0][-1] == "2"

    async def test_success_clears_the_count(
        self,
        client: AsyncClient,
        mynca: Mynca,
        brake: SigningFailureBrake,
    ) -> None:
        brake.note_failure(SIGN_ID)
        brake.note_failure(SIGN_ID)

        assert_empty(await client.get(OPERATOR_ROUTE))

        assert brake.note_failure(SIGN_ID) is False
        assert mynca.paths().count(SIGN_SAVE_PATH) == 1

    async def test_failures_before_signing_do_not_count(
        self,
        client: AsyncClient,
        db: Database,
        brake: SigningFailureBrake,
    ) -> None:
        db.responses[READ_KEY] = RaiseError("{no key}")

        for _ in range(4):
            assert_empty(await client.get(OPERATOR_ROUTE))
            db.lock_is_held = False

        assert brake.is_paused(SIGN_ID) is False

    def test_the_pause_ends_after_an_hour(self) -> None:
        now = [100.0]
        brake = SigningFailureBrake(clock=lambda: now[0])
        assert [brake.note_failure(1) for _ in range(3)] == [False, False, True]
        assert brake.is_paused(1) is True

        now[0] += PAUSE_SECONDS - 1
        assert brake.is_paused(1) is True

        now[0] += 1
        assert brake.is_paused(1) is False
        assert brake.note_failure(1) is False
