```markdown
# TwinCatcher

> Быстрый поиск и безопасное удаление дубликатов файлов.  
> Fast duplicate finder with safe cleanup.

[![Latest release](https://img.shields.io/github/v/release/SantidioEsanti/TwinCatcher?label=release)](https://github.com/SantidioEsanti/TwinCatcher/releases)
[![Downloads](https://img.shields.io/github/downloads/SantidioEsanti/TwinCatcher/total)](https://github.com/SantidioEsanti/TwinCatcher/releases)
[![License](https://img.shields.io/github/license/SantidioEsanti/TwinCatcher)](https://github.com/SantidioEsanti/TwinCatcher/blob/main/LICENSE)

---

## 🇷🇺 О проекте

TwinCatcher находит одинаковые файлы по размеру и хешу, а изображения — опционально по пиксельному SHA-256. Ничего не удаляет без подтверждения: файлы уходят в корзину. Работает локально, без сети.

### Возможности

- Поиск по размеру + BLAKE2b + SQLite-кэш (90 дней).
- Пиксельный SHA-256 для изображений — ловит перекодированные копии.
- Выбор оригинала: oldest / newest / shortest path / longest path / in folder.
- Просмотр групп изображений с превью и чекбоксами.
- Тёмная/светлая тема, переключение RU/EN «на лету».
- Удаление пустых папок.
- Безопасное удаление через `send2trash`.

### Установка (Windows)

1. Скачайте `TwinCatcher.exe` из [Releases](https://github.com/SantidioEsanti/TwinCatcher/releases).
2. Запустите. Python не требуется.

### Запуск из исходников

```bash
git clone https://github.com/SantidioEsanti/TwinCatcher.git
cd TwinCatcher
pip install pillow send2trash
python dup_finder.py
```

### Сборка `.exe`

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name TwinCatcher --icon Dup_logo_2.ico --add-data "Dup_logo_2.png;." dup_finder.py
```

---

## 🇬🇧 About

TwinCatcher finds duplicate files by size + hash, and optionally compares images by pixel-level SHA-256. It never deletes without confirmation — files go to Trash. Fully local, no network.

### Features

- Size + BLAKE2b + SQLite cache (90 days).
- Pixel SHA-256 for images — catches re-encoded copies.
- Original selection: oldest / newest / shortest path / longest path / in folder.
- Image group viewer with thumbnails and checkboxes.
- Light/dark theme, instant RU/EN switch.
- Empty folder cleanup.
- Safe deletion via `send2trash`.

### Install (Windows)

1. Download `TwinCatcher.exe` from [Releases](https://github.com/SantidioEsanti/TwinCatcher/releases).
2. Run it. No Python required.

### Run from source

```bash
git clone https://github.com/SantidioEsanti/TwinCatcher.git
cd TwinCatcher
pip install pillow send2trash
python dup_finder.py
```

### Build `.exe`

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name TwinCatcher --icon Dup_logo_2.ico --add-data "Dup_logo_2.png;." dup_finder.py
```

---

## ☕ Поддержать проект / Support the project

**RU:** Если TwinCatcher помог вам навести порядок в файлах — вы можете угостить автора чашкой кофе. Спасибо!

**EN:** If TwinCatcher helped you clean up your files — you can buy the author a coffee. Thank you!

[![Поддержать на Boosty](https://img.shields.io/badge/Boosty-Поддержать-orange?logo=boosty&logoColor=white)](https://boosty.to/santidioesanti)
[![Support on Boosty](https://img.shields.io/badge/Boosty-Support-orange?logo=boosty&logoColor=white)](https://boosty.to/santidioesanti)

👉 **https://boosty.to/santidioesanti**

---

## License

MIT — см. [LICENSE](https://github.com/ВАШ_НИК/TwinCatcher/blob/main/LICENSE).
```
