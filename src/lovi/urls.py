import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


class InvalidLink(ValueError):
    pass


def normalize_url(text: str) -> str:
    urls = re.findall(r"https?://[^\s<>]+", text.strip())
    if len(urls) != 1:
        raise InvalidLink("Отправьте одну ссылку на ролик.")
    raw = urls[0].rstrip(".,!;)")
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError
    except ValueError as exc:
        raise InvalidLink(
            "В ссылке не должно быть логина, пароля или нестандартного порта."
        ) from exc

    path = parsed.path.rstrip("/")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})", path)
            video_id = match[1] if match else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise InvalidLink("Нужна ссылка на один ролик или Shorts, а не канал или плейлист.")
        return f"https://www.youtube.com/watch?v={video_id}"
    if host in {"youtu.be", "www.youtu.be"} and re.fullmatch(r"/[A-Za-z0-9_-]{11}", path):
        return f"https://www.youtube.com/watch?v={path[1:]}"
    if host in {"instagram.com", "www.instagram.com"}:
        if re.fullmatch(r"/(?:reel|reels|p)/[A-Za-z0-9_-]+", path):
            path = path.replace("/reels/", "/reel/")
            return f"https://www.instagram.com{path}/"
    if host in {"www.tiktok.com", "tiktok.com", "m.tiktok.com"}:
        if re.fullmatch(r"/@[^/?#]+/video/\d+", path) or re.fullmatch(r"/t/[A-Za-z0-9]+", path):
            return urlunsplit(("https", "www.tiktok.com", path, "", ""))
    if host in {"vm.tiktok.com", "vt.tiktok.com"}:
        if re.fullmatch(r"/[A-Za-z0-9]+", path):
            return f"https://{host}{path}/"
    raise InvalidLink("Поддерживаются YouTube, Shorts, TikTok и Instagram Reels.")
