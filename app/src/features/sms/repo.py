from src.php_compat.pdo_values import NO_MODULE_CODE, pg_row
from src.repository.base import SpRows
from src.shared.cron_run import CronRepository

SEND_RUN_LOCK = "sms:sms-notify"
STATUS_RUN_LOCK = "sms:sms-notify-status"


class SmsRepository(CronRepository):
    async def sms_notify__read(self) -> SpRows:
        rows = await self.call_sp(
            "notify.sms_notify__read", cursor=True, module_code=NO_MODULE_CODE
        )
        return [pg_row(row) for row in rows]

    async def sms_notify_result__set(
        self,
        *,
        ids: list[int],
        batch_id: str | None,
        created_at: str | None,
        response: str,
        results: str | None,
    ) -> None:
        await self.call_sp(
            "notify.sms_notify_result__set",
            ids,
            batch_id,
            created_at,
            response,
            results,
            module_code=NO_MODULE_CODE,
        )

    async def sms_notify_batch__read(self) -> SpRows:
        rows = await self.call_sp(
            "notify.sms_notify_batch__read",
            cursor=True,
            module_code=NO_MODULE_CODE,
        )
        return [pg_row(row) for row in rows]

    async def sms_notify_status__set(self, messages: str) -> None:
        await self.call_sp("notify.sms_notify_status__set", messages, module_code=NO_MODULE_CODE)
