"""Kanban of MySpace, scope ``kanban.read``. Each tool is one GET of ``/ext/v1/kanban/...``.

The answer is MySpace's JSON as it is: MySpace already made it short and text-only for a model.
"""

from typing import Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from .tooling import ScopedTools

KANBAN_READ = "kanban.read"
# MySpace ids are PostgreSQL integer: a larger number is the model's mistake, not a MySpace call.
MAX_ID = 2_147_483_647

BoardId = Annotated[
    int, Field(gt=0, le=MAX_ID, description="id доски из kanban_list_boards (поле boards[].id).")
]


def register_kanban_tools(server: MCPServer, tools: ScopedTools) -> None:
    async def kanban_list_boards(ctx: Context) -> dict[str, Any]:
        return await tools.read(ctx, "/ext/v1/kanban/boards/")

    async def kanban_get_board(board_id: BoardId, ctx: Context) -> dict[str, Any]:
        return await tools.read(ctx, f"/ext/v1/kanban/boards/{board_id}/")

    async def kanban_list_cards(
        board_id: BoardId,
        ctx: Context,
        column_id: Annotated[
            int | None,
            Field(
                gt=0,
                le=MAX_ID,
                description="Только карточки этой колонки: id из kanban_get_board (columns[].id). "
                "Не указывать — все колонки.",
            ),
        ] = None,
        mine: Annotated[
            bool,
            Field(
                description="true — только карточки, где исполнитель — сам сотрудник, "
                "от имени которого идёт разговор («мои задачи»)."
            ),
        ] = False,
        overdue: Annotated[
            bool, Field(description="true — только просроченные карточки (срок прошёл).")
        ] = False,
        executor_id: Annotated[
            int | None,
            Field(
                gt=0,
                le=MAX_ID,
                description="Только карточки, где исполнитель — этот сотрудник. id сотрудника "
                "берётся из kanban_get_card (executors[].id). Для себя используйте mine.",
            ),
        ] = None,
        search: Annotated[
            str | None,
            Field(
                max_length=200,
                description="Поиск по тексту карточек, как строка поиска на доске MySpace.",
            ),
        ] = None,
        include_all_done: Annotated[
            bool,
            Field(
                description="true — все карточки колонок «готово». По умолчанию, как на экране "
                "MySpace, из них видны только перенесённые туда за последние 7 дней."
            ),
        ] = False,
        limit: Annotated[
            int, Field(ge=1, le=200, description="Сколько карточек вернуть, 1–200.")
        ] = 50,
        offset: Annotated[
            int,
            Field(ge=0, description="Сколько карточек пропустить: для следующей страницы."),
        ] = 0,
    ) -> dict[str, Any]:
        params = {"limit": str(limit), "offset": str(offset)}
        if column_id is not None:
            params["column_id"] = str(column_id)
        if mine:
            params["mine"] = "1"
        if overdue:
            params["overdue"] = "1"
        if executor_id is not None:
            params["executor_id"] = str(executor_id)
        if search and search.strip():
            params["search"] = search.strip()
        if include_all_done:
            params["all_done"] = "1"
        return await tools.read(ctx, f"/ext/v1/kanban/boards/{board_id}/cards/", params)

    async def kanban_get_card(
        card_id: Annotated[
            int,
            Field(gt=0, le=MAX_ID, description="id карточки из kanban_list_cards (cards[].id)."),
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        return await tools.read(ctx, f"/ext/v1/kanban/cards/{card_id}/")

    async def kanban_list_overdue(board_id: BoardId, ctx: Context) -> dict[str, Any]:
        return await tools.read(ctx, f"/ext/v1/kanban/boards/{board_id}/overdue/")

    tools.add(
        server,
        kanban_list_boards,
        scope=KANBAN_READ,
        title="Доски канбана",
        description=(
            "Доски канбана MySpace, которые сотрудник видит в своей текущей компании. "
            "Возвращает {boards: [{id, name, owner, url}]}. Вызывайте первым, если пользователь "
            "не назвал доску: id доски нужен остальным инструментам канбана."
        ),
    )
    tools.add(
        server,
        kanban_get_board,
        scope=KANBAN_READ,
        title="Сводка по доске",
        description=(
            "Сводка по одной доске: колонки с числом видимых сотруднику карточек "
            "(is_done — колонка «готово»), всего карточек, сколько просрочено, права сотрудника "
            "на доске (my_rights). Используйте, чтобы узнать id колонок и общую картину "
            "перед выборкой карточек."
        ),
    )
    tools.add(
        server,
        kanban_list_cards,
        scope=KANBAN_READ,
        title="Карточки доски",
        description=(
            "Карточки доски, которые видит сотрудник, постранично; фильтры складываются. "
            "Возвращает {board_id, total, limit, offset, cards: [...]}: у карточки название, "
            "колонка, исполнители, срок (deadline), приоритет, метки, просрочка, число "
            "комментариев, чек-лист и ссылка. Если total больше limit + offset, есть следующая "
            "страница. Описание, комментарии и поля карточки — в kanban_get_card."
        ),
    )
    tools.add(
        server,
        kanban_get_card,
        scope=KANBAN_READ,
        title="Карточка",
        description=(
            "Одна карточка целиком: описание текстом, колонка и статус, автор, сроки, "
            "исполнители с id, метки, чек-лист, поля, последние 20 комментариев, файлы (число), "
            "родительская и дочерние карточки, ссылка в MySpace. Используйте, когда нужны "
            "подробности или комментарии по конкретной карточке. Если карточка скрыта от "
            "сотрудника, вернётся ошибка «не найдена или нет доступа»."
        ),
    )
    tools.add(
        server,
        kanban_list_overdue,
        scope=KANBAN_READ,
        title="Просроченные карточки",
        description=(
            "Просроченные карточки доски, которые видит сотрудник: название, колонка, приоритет, "
            "срок и на сколько дней просрочено (days_overdue). Используйте для вопросов «что "
            "горит» и «что просрочено» по доске; для просроченных среди своих задач — "
            "kanban_list_cards с mine и overdue."
        ),
    )
