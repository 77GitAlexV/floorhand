# Intake quality / Качество приёма заказов

FACT / ФАКТ: 72 cases / примеров; 84 gold orders / эталонных заказов.
Requested / Запрошено: `offline`. Actual / Фактически: `{'offline-rules': 72}`.

CALC / РАСЧЁТ: each aligned order contributes six fields; extra orders also count. Silent rate = wrong fields with status ok / all compared fields (target ≤ 1%). Asked = status check, not an error. Missed = absent field/order. Empty optional fields are correct only when gold is null. Orders are aligned one-to-one by minimum cost, preferring product matches.

Каждый заказ даёт шесть полей; лишние заказы тоже учитываются. Тихая ошибка — неверное поле со статусом ok. Вопрос (check) не ошибка. Пропуск — отсутствующее поле или заказ. Пустое необязательное поле верно только при null в эталоне.

CALC / РАСЧЁТ: silent error rate / доля тихих ошибок: **2.35%**.

## overall

CALC / РАСЧЁТ

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 39 | 2 | 1 | 43 |
| qty | 39 | 10 | 4 | 32 |
| customer | 45 | 5 | 3 | 32 |
| due_date | 34 | 7 | 3 | 41 |
| fabric | 36 | 0 | 1 | 48 |
| frame | 44 | 0 | 0 | 41 |
| TOTAL / ВСЕГО | 237 | 24 | 12 | 237 |

## kind:email

CALC / РАСЧЁТ

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 14 | 0 | 1 | 14 |
| qty | 12 | 1 | 2 | 14 |
| customer | 12 | 1 | 2 | 14 |
| due_date | 9 | 0 | 3 | 17 |
| fabric | 14 | 0 | 0 | 15 |
| frame | 14 | 0 | 0 | 15 |
| TOTAL / ВСЕГО | 75 | 2 | 8 | 89 |

## lang:en

CALC / РАСЧЁТ

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 39 | 2 | 1 | 1 |
| qty | 32 | 6 | 4 | 1 |
| customer | 36 | 3 | 3 | 1 |
| due_date | 26 | 4 | 3 | 10 |
| fabric | 35 | 0 | 1 | 7 |
| frame | 37 | 0 | 0 | 6 |
| TOTAL / ВСЕГО | 205 | 15 | 12 | 26 |

## lang:ru

CALC / РАСЧЁТ

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 0 | 0 | 0 | 42 |
| qty | 7 | 4 | 0 | 31 |
| customer | 9 | 2 | 0 | 31 |
| due_date | 8 | 3 | 0 | 31 |
| fabric | 1 | 0 | 0 | 41 |
| frame | 7 | 0 | 0 | 35 |
| TOTAL / ВСЕГО | 32 | 9 | 0 | 211 |

## kind:chat

CALC / РАСЧЁТ

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 12 | 2 | 0 | 16 |
| qty | 11 | 1 | 2 | 16 |
| customer | 13 | 0 | 1 | 16 |
| due_date | 8 | 0 | 0 | 22 |
| fabric | 12 | 0 | 1 | 17 |
| frame | 14 | 0 | 0 | 16 |
| TOTAL / ВСЕГО | 70 | 3 | 4 | 103 |

## kind:table

CALC / РАСЧЁТ

| Field / Поле | Correct / Верно | Asked / Вопрос | SILENT ERROR / Тихая ошибка | Missed / Пропуск |
|---|---:|---:|---:|---:|
| product | 13 | 0 | 0 | 13 |
| qty | 16 | 8 | 0 | 2 |
| customer | 20 | 4 | 0 | 2 |
| due_date | 17 | 7 | 0 | 2 |
| fabric | 10 | 0 | 0 | 16 |
| frame | 16 | 0 | 0 | 10 |
| TOTAL / ВСЕГО | 92 | 19 | 0 | 45 |

## Silent errors / Тихие ошибки

- `en-email-04` (0): qty: expected / ожидалось `3`, got / получено `2`
- `en-email-05` (0): due_date: expected / ожидалось `2026-10-30`, got / получено `2026-10-29`
- `en-email-05` (None): product: expected / ожидалось `None`, got / получено `Bruno chair` (extra order / лишний заказ)
- `en-email-05` (None): qty: expected / ожидалось `None`, got / получено `8` (extra order / лишний заказ)
- `en-email-05` (None): customer: expected / ожидалось `None`, got / получено `Pine Street Store` (extra order / лишний заказ)
- `en-email-05` (None): due_date: expected / ожидалось `None`, got / получено `2026-10-29` (extra order / лишний заказ)
- `en-email-12` (1): customer: expected / ожидалось `Urban Loft`, got / получено `Home & Co`
- `en-email-12` (1): due_date: expected / ожидалось `2026-11-09`, got / получено `2026-11-03`
- `en-chat-04` (0): qty: expected / ожидалось `3`, got / получено `2`
- `en-chat-05` (1): customer: expected / ожидалось `Olive Market`, got / получено `Field & Fern`
- `en-chat-09` (0): qty: expected / ожидалось `2`, got / получено `1`
- `en-chat-11` (0): fabric: expected / ожидалось `velvet beige`, got / получено `None`

## Failures / Сбои

None / Нет.
