---
labels: [enhancement]
---

## Cel
Funkcja `suma` zwraca sumę dwóch liczb całkowitych, a test to potwierdza.

## Kontekst
Moduł `app/kalkulator.py` nie ma jeszcze żadnej funkcji.

## Zakres
- `app/kalkulator.py`
- `tests/test_kalkulator.py`

## Poza zakresem
Interfejs użytkownika.

## Powiązane
Brak.

## Kryteria akceptacji
```bash
python3 -m pytest tests/test_kalkulator.py -q  # exit 0
python3 -c 'from app.kalkulator import suma; print(suma(2, 3))'  # 5
```
