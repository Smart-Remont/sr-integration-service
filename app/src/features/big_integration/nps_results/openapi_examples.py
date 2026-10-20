NPS_RESULTS_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": "Опросы за период, по возрастанию даты прохождения.",
        "content": {
            "application/json": {
                "example": {
                    "data": [
                        {
                            "nps_survey_id": 10467,
                            "survey_type": "REPAIR",
                            "remont_id": 51234,
                            "client_request_id": 2916069,
                            "client_name": "Иванов Иван Иванович",
                            "resident_name": "ЖК Пример",
                            "resident_guid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                            "flat_num": "125",
                            "flat_guid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                            "techproject_employee_fio": "Петров Пётр",
                            "nps_question_cnt": 6,
                            "nps_answer_cnt": 6,
                            "evaluation_datetime": "2026-07-31T11:18:15+05:00",
                            "repair_type": "Опциональный",
                            "quality_materials_rating": None,
                            "quality_materials_max_rating": None,
                            "quality_materials_comment": None,
                            "feedback_rating": None,
                            "feedback_max_rating": None,
                            "feedback_comment": None,
                            "recommendation_rating": 9,
                            "recommendation_max_rating": 10,
                            "recommendation_comment": None,
                            "warranty_rating": None,
                            "warranty_max_rating": None,
                            "warranty_comment": None,
                            "answers": [
                                {
                                    "question_code": "SUPPORT_NPS",
                                    "question": "Если Ваши друзья или родственники будут "
                                    "планировать ремонт, насколько вероятно, что Вы "
                                    "порекомендуете им Smart Remont?",
                                    "rating": 9,
                                    "max_rating": 10,
                                    "comment": None,
                                    "aspects": [],
                                }
                            ],
                        }
                    ],
                    "response": True,
                    "error": {"message": ""},
                }
            }
        },
    },
    400: {
        "description": "Неверные даты или слишком длинный период.",
        "content": {
            "application/json": {
                "example": {
                    "data": None,
                    "response": False,
                    "error": {"message": "date_from не может быть позже date_to"},
                }
            }
        },
    },
}
