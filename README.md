# TwinCatcher

> Быстрый поиск и безопасное удаление дубликатов файлов.  
> Fast duplicate finder with safe cleanup.

[![Release](https://img.shields.io/github/v/release/ВАШ_НИК/TwinCatcher)](...)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](...)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](...)

## 🇷🇺 О проекте
TwinCatcher находит одинаковые файлы по размеру и хешу, а изображения — опционально по пиксельному SHA-256. Ничего не удаляет без подтверждения: файлы уходят в корзину. Работает локально, без сети.

### Возможности
- Поиск по размеру + BLAKE2b + SQLite-кэш (90 дней).
- Пиксельный SHA-256 для изображений (ловит перекодированные копии).
- Выбор оригинала: oldest / newest / shortest path / longest path / in folder.
- Просмотр групп изображений с превью и чекбоксами.
- Тёмная/светлая тема, переключение RU/EN.
- Удаление пустых папок.
- Безопасное удаление через `send2trash`.

### Установка (Windows)
1. Скачайте `TwinCatcher.exe` из [Releases](...).
2. Запустите. Python не требуется.

### Запуск из исходников
```bash
git clone https://github.com/SantidioEsanti/TwinCatcher.git
cd TwinCatcher
pip install pillow send2trash
python dup_finder.py
---
## ☕ Поддержать проект / Support the project

**RU:** Если TwinCatcher помог вам навести порядок в файлах — вы можете угостить автора чашкой кофе. Спасибо!

**EN:** If TwinCatcher helped you clean up your files — you can buy the author a coffee. Thank you!

[![Поддержать на Boosty](https://img.shields.io/badge/Boosty-Поддержать-orange?logo=boosty&logoColor=white)](https://boosty.to/santidioesanti)
[![Support on Boosty](https://img.shields.io/badge/Boosty-Support-orange?logo=boosty&logoColor=white)](https://boosty.to/santidioesanti)

👉 **https://boosty.to/santidioesanti**
