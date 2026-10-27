# Intake quality / Качество приёма заказов

FACT / ФАКТ: 72 cases / примеров; 84 gold orders / эталонных заказов.
Requested / Запрошено: `offline`. Actual / Фактически: `{'offline-rules': 72}`.

CALC / РАСЧЁТ: each aligned order contributes six fields; extra orders also count. Silent rate = wrong fields with status ok / all compared fields (target ≤ 1%). Asked = status check, not an error. Missed = absent field/order. Empty optional fields are correct only when gold is null. Orders are aligned one-to-one by minimum cost, preferring product matches.

Каждый заказ даёт шесть полей; лишние заказы тоже учитываются. Тихая ошибка — неверное поле со статусом ok. Вопрос (check) не ошибка. Пропуск — отсутствующее поле или заказ. Пустое необязательное поле верно только при null в эталоне.

CALC / РАСЧЁТ: silent error rate / доля тихих ошибок: **0.00%**.

CALC / РАСЧЁТ: correct / верно **95.44%**; asked / вопросов **23**; missed / пропусков **0**.

This is a fixed development corpus, not an independent generalization benchmark. Weekdays mean the upcoming occurrence; next Friday on Friday means the following week. Unknown customers and intentionally incomplete or ambiguous fields should ask.

Это фиксированный набор для разработки, а не независимая оценка обобщения. Дни недели означают ближайшее наступление; «в следующую пятницу» в пятницу — через неделю. Неизвестный клиент и неоднозначное поле требуют вопроса.

## overall

CALC / РАСЧЁТ

Silent / Тихие: 0.00%.

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 81 | 3 | 0 | 0 |
| qty | 78 | 6 | 0 | 0 |
| customer | 78 | 6 | 0 | 0 |
| due_date | 80 | 4 | 0 | 0 |
| fabric | 82 | 2 | 0 | 0 |
| frame | 82 | 2 | 0 | 0 |
| TOTAL / ВСЕГО | 481 | 23 | 0 | 0 |

## kind:email

CALC / РАСЧЁТ

Silent / Тихие: 0.00%.

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 28 | 0 | 0 | 0 |
| qty | 26 | 2 | 0 | 0 |
| customer | 26 | 2 | 0 | 0 |
| due_date | 26 | 2 | 0 | 0 |
| fabric | 28 | 0 | 0 | 0 |
| frame | 28 | 0 | 0 | 0 |
| TOTAL / ВСЕГО | 162 | 6 | 0 | 0 |

## lang:en

CALC / РАСЧЁТ

Silent / Тихие: 0.00%.

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 40 | 2 | 0 | 0 |
| qty | 39 | 3 | 0 | 0 |
| customer | 39 | 3 | 0 | 0 |
| due_date | 40 | 2 | 0 | 0 |
| fabric | 41 | 1 | 0 | 0 |
| frame | 41 | 1 | 0 | 0 |
| TOTAL / ВСЕГО | 240 | 12 | 0 | 0 |

## lang:ru

CALC / РАСЧЁТ

Silent / Тихие: 0.00%.

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 41 | 1 | 0 | 0 |
| qty | 39 | 3 | 0 | 0 |
| customer | 39 | 3 | 0 | 0 |
| due_date | 40 | 2 | 0 | 0 |
| fabric | 41 | 1 | 0 | 0 |
| frame | 41 | 1 | 0 | 0 |
| TOTAL / ВСЕГО | 241 | 11 | 0 | 0 |

## kind:chat

CALC / РАСЧЁТ

Silent / Тихие: 0.00%.

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 27 | 3 | 0 | 0 |
| qty | 28 | 2 | 0 | 0 |
| customer | 30 | 0 | 0 | 0 |
| due_date | 30 | 0 | 0 | 0 |
| fabric | 28 | 2 | 0 | 0 |
| frame | 28 | 2 | 0 | 0 |
| TOTAL / ВСЕГО | 171 | 9 | 0 | 0 |

## kind:table

CALC / РАСЧЁТ

Silent / Тихие: 0.00%.

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 26 | 0 | 0 | 0 |
| qty | 24 | 2 | 0 | 0 |
| customer | 22 | 4 | 0 | 0 |
| due_date | 24 | 2 | 0 | 0 |
| fabric | 26 | 0 | 0 | 0 |
| frame | 26 | 0 | 0 | 0 |
| TOTAL / ВСЕГО | 148 | 8 | 0 | 0 |

## Silent errors / Тихие ошибки

None / Нет.

## Failures / Сбои

None / Нет.
