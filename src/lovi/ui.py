from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from lovi.models import MediaInfo, Selection

WELCOME = (
    "<b>ЛОВИ</b>  /  медиа по ссылке\n\n"
    "Тот самый ролик — теперь у тебя.\n"
    "Отправь ссылку, а дальше выбирай:\n\n"
    "<b>🎬 Видео</b> · доступное качество\n"
    "<b>🎧 Аудио</b> · отдельный MP3\n"
    "<b>🖼 Обложка</b> · картинка без видео\n\n"
    "<blockquote>YouTube · Shorts · TikTok · Reels</blockquote>\n"
    "<i>Одна ссылка. Пара нажатий. Готово.</i>"
)

HELP = (
    "<b>На связи, это ЛОВИ ✦</b>\n\n"
    "<b>01</b> Скопируй ссылку на ролик.\n"
    "<b>02</b> Отправь её в этот чат.\n"
    "<b>03</b> Выбери качество, MP3 или обложку.\n\n"
    "У каждого ролика свой набор форматов. Кнопка 720p означает качество до 720p; "
    "исходное разрешение не увеличивается.\n\n"
    "Если файл большой — попробуй качество ниже или аудио. "
    "Закрытые, удалённые ролики и прямые эфиры не поддерживаются.\n\n"
    "Ссылки и карточки хранятся временно, скачанные файлы удаляются после отправки. "
    "Telegram хранит уже отправленные медиа по своим правилам.\n"
    "Скачивай свои материалы или те, которые разрешил сохранять автор."
)


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✦ Как это работает", callback_data="help"),
            ]
        ]
    )


def media_keyboard(info: MediaInfo, session_id: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buttons = [
        InlineKeyboardButton(
            text=(f"{'⚡ ' if height == 720 else '🎬 '}{height}p" if height else "🎬 Оригинал"),
            callback_data=f"get:{session_id}:v{height}",
        )
        for height in info.heights
    ]
    for i in range(0, len(buttons), 3):
        rows.append(buttons[i : i + 3])
    extras = []
    if info.has_audio:
        extras.append(
            InlineKeyboardButton(text="🎧 Только MP3", callback_data=f"get:{session_id}:audio")
        )
    if info.has_thumbnail:
        extras.append(
            InlineKeyboardButton(text="🖼 Обложка", callback_data=f"get:{session_id}:cover")
        )
    if extras:
        rows.append(extras)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def duration_label(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def media_card(info: MediaInfo) -> str:
    details = escape(info.platform)
    if info.duration:
        details += f"  ·  {duration_label(info.duration)}"
    else:
        details += "  ·  Длительность проверю после загрузки"
    if info.author:
        details += f"  ·  {escape(info.author[:80])}"
    hint = (
        "⚡ 720p — хороший баланс качества и размера."
        if 720 in info.heights
        else "Выбери один из доступных форматов ниже."
    )
    return (
        f"<b>Нашёл ✦</b>\n\n<b>{escape(info.title[:180])}</b>\n{details}\n\n"
        f"Что забираем?\n<i>{hint}</i>"
    )


def delivery_caption(info: MediaInfo, selection: Selection) -> str:
    label = {
        "video": f"🎬 До {selection.height}p" if selection.height else "🎬 Оригинал · MP4",
        "audio": "🎧 MP3 · 192 кбит/с",
        "cover": "🖼 Обложка",
    }
    return (
        f"<b>{escape(info.title[:180])}</b>\n"
        f"{escape(info.platform)} · {label[selection.kind]}\n\n<b>ЛОВИ</b> ✦ Сохранено для тебя"
    )


ERRORS = {
    "login": "Сайт требует входа для этого запроса. Администратору нужно настроить cookies.",
    "rate_limit": "Сайт ограничил частоту запросов. Попробуй позже.",
    "site_access": (
        "Сайт ограничил доступ или вернул пустой ответ. "
        "Администратору нужно проверить прокси и необходимость cookies."
    ),
    "proxy_auth": "Прокси отклонил авторизацию. Администратору нужно проверить его настройки.",
    "network": "Не удалось подключиться к сайту. Администратору нужно проверить сеть и прокси.",
    "unavailable": (
        "Не удалось открыть ролик. Он может быть закрыт, удалён или временно недоступен."
    ),
    "live": "Прямые эфиры пока не скачиваю. Пришли запись после завершения трансляции.",
    "duration": "Этот ролик длиннее установленного лимита. Пришли более короткое видео.",
    "duration_unknown": "Не удалось проверить длительность файла. Попробуй другой ролик.",
    "too_large": "Файл слишком большой для отправки. Выбери качество ниже или только аудио.",
    "timeout": "Сайт отвечает слишком долго. Попробуй чуть позже или выбери качество ниже.",
    "disk": "Сейчас нет места для загрузки. Попробуй позже — очередь скоро освободится.",
    "format": "Этот формат сейчас недоступен. Отправь ссылку ещё раз и выбери другой вариант.",
    "internal": "Не получилось завершить загрузку. Попробуй позже.",
}


def error_text(code: str) -> str:
    return f"<b>Не поймал с первого раза ☁️</b>\n\n{ERRORS.get(code, ERRORS['internal'])}"
